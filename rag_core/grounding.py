"""Sentence-level grounding check of a generated answer (step 4-3)."""
from __future__ import annotations

import json
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config
from rag_core import llm
from rag_grounded import Chunk, _compact, _terms  # the same term extraction the evidence checks of the answer use

_BULLET = re.compile(r"^-\s*(.*?)\s*((?:\[S\d+\]\s*)*)$")
_TAG = re.compile(r"\[S(\d+)\]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_LATIN = re.compile(r"[a-z][a-z0-9._:/-]*")
_KATAKANA = re.compile(r"[ァ-ヶー]{3,}")
_KANJI = re.compile(r"[一-龥々〆]{3,}")

SYSTEM = (
    "あなたは根拠の確認者です。「文」が「根拠」に書かれている内容だけで支えられているかを判定してください。"
    "根拠に無い事実・数値・条件を含むなら支えられていません。{\"supported\": true} または {\"supported\": false} "
    "の JSON だけを返してください。"
)

_LOCK = threading.Lock()
_STATS = {"checked": 0, "dropped": 0, "rescued": 0}


def stats() -> Dict[str, int]:
    with _LOCK:
        return dict(_STATS)


def reset_stats() -> None:
    with _LOCK:
        for key in _STATS:
            _STATS[key] = 0


def _bump(key: str, amount: int = 1) -> None:
    with _LOCK:
        _STATS[key] += amount


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


@dataclass
class VerifyResult:
    raw: str
    kept: int = 0
    total: int = 0
    dropped: List[Dict[str, Any]] = field(default_factory=list)  # {"index": line, "reasons": [...]}: never the text
    rescued: int = 0


def check_claim(claim: str, evidence: str, *, min_coverage: float) -> Tuple[List[str], float]:
    """(hard rules broken, the share of the claim's terms found in the evidence)."""
    claim_n, evidence_n = _norm(claim), _norm(evidence)
    evidence_compact = _compact(evidence)
    broken: List[str] = []
    if sorted(set(_NUMBER.findall(claim_n))) != sorted(set(_NUMBER.findall(claim_n)) & set(_NUMBER.findall(evidence_n))):
        broken.append("numbers")
    if any(word not in evidence_n for word in _LATIN.findall(claim_n)):
        broken.append("identifier")
    if any(term not in evidence_n for term in _KATAKANA.findall(claim_n)):
        broken.append("katakana")
    if any(term not in evidence_n for term in _KANJI.findall(claim_n)):
        broken.append("new_term")
    terms = _terms(claim)
    coverage = sum(1 for term in terms if _compact(term) in evidence_compact) / len(terms) if terms else 1.0
    return broken, coverage


def _llm_supports(claim: str, evidence: str, *, tenant_id: str, client: Any) -> bool:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"<根拠>\n{evidence}\n</根拠>\n\n<文>\n{claim}\n</文>"}]
    try:
        text = llm.complete(messages, feature="verify", tenant_id=tenant_id, client=client, max_tokens=32)
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start:end + 1]).get("supported") is True
    except Exception:  # noqa: BLE001 - a refusal, a failure or an unreadable answer is "not supported"
        return False


def verify_answer(
    raw: str, chunks: Sequence[Chunk], *, tenant_id: str = "default", client: Any = None,
    min_coverage: Optional[float] = None,
) -> VerifyResult:
    """The answer with every bullet the cited evidence does not support removed (other lines are kept)."""
    threshold = float(getattr(config, "GROUNDING_MIN_COVERAGE", 0.5) if min_coverage is None else min_coverage)
    result = VerifyResult(raw="")
    lines: List[str] = []
    for index, line in enumerate(raw.splitlines()):
        stripped = line.strip()
        match = _BULLET.match(stripped) if stripped.startswith("-") else None
        if match is None:
            lines.append(line)
            continue
        result.total += 1
        _bump("checked")
        claim, tags = match.group(1), [int(n) for n in _TAG.findall(match.group(2))]
        if not tags or any(not 1 <= n <= len(chunks) for n in tags):
            result.dropped.append({"index": index, "reasons": ["no_citation"]})
            _bump("dropped")
            continue
        evidence = "\n".join(chunks[n - 1].text for n in tags)
        broken, coverage = check_claim(claim, evidence, min_coverage=threshold)
        reasons = list(broken)
        if not broken and coverage < threshold:
            if _llm_supports(claim, evidence, tenant_id=tenant_id, client=client):
                result.rescued += 1
                _bump("rescued")
            else:
                reasons.append("coverage")
        if reasons:
            result.dropped.append({"index": index, "reasons": reasons})
            _bump("dropped")
            continue
        result.kept += 1
        lines.append(line)
    result.raw = "\n".join(lines)
    return result
