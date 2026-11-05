"""Constrained rewriting of the bullets of an extractive answer (step 4-2)."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from rag_core import llm

MAX_LINES = 8
LENGTH_RANGE = (0.5, 1.6)

_BULLET = re.compile(r"^(-\s*)(.*?)\s*((?:\[S\d+\]\s*)+)$")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_LATIN = re.compile(r"[a-z][a-z0-9._:/-]*")
_KATAKANA = re.compile(r"[ァ-ヶー]{3,}")
_KANJI = re.compile(r"[一-龥々〆]{3,}")
_NEGATION = re.compile(r"ない|ません|なかっ|せず|禁止|不可|不要|できず|無い|無し|いけ|ならな")

SYSTEM = (
    "あなたは文章の校正者です。与えられた文を、意味・数値・固有名詞・否定・条件を変えずに、自然で読みやすい日本語に"
    "書き直してください。新しい情報を足してはいけません。文の数と順序は変えず、書き直した文だけを入れた"
    "JSON の文字列配列（例: [\"...\", \"...\"]）だけを返してください。"
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


@dataclass
class RewriteOutcome:
    raw: str
    rewritten: int = 0
    kept: int = 0
    reason: str = ""
    violations: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        """For the trace: counts and rule names, never the text."""
        return {"rewritten": self.rewritten, "kept_original": self.kept, "reason": self.reason,
                "violations": dict(self.violations)}


def check_rewrite(original: str, rewritten: str, *, question: str = "") -> List[str]:
    """The rules a rewritten sentence breaks (empty when it may replace the original)."""
    if not str(rewritten or "").strip():
        return ["empty"]
    old, new, asked = _norm(original), _norm(rewritten), _norm(question)
    allowed = old + "\n" + asked
    broken: List[str] = []
    ratio = len(new) / max(1, len(old))
    if not LENGTH_RANGE[0] <= ratio <= LENGTH_RANGE[1]:
        broken.append("length")
    if sorted(_NUMBER.findall(new)) != sorted(_NUMBER.findall(old)):
        broken.append("numbers")
    if any(word not in allowed for word in _LATIN.findall(new)):
        broken.append("identifier")
    if any(term not in allowed for term in _KATAKANA.findall(new)):
        broken.append("katakana")
    if any(term not in allowed for term in _KANJI.findall(new)):
        broken.append("new_term")
    if bool(_NEGATION.search(old)) != bool(_NEGATION.search(new)):
        broken.append("negation")
    return broken


def parse_bullets(raw: str) -> List[Tuple[int, str, str, str]]:
    """(line index, "- " prefix, sentence, tags) of every bullet that ends in citation tags."""
    found = []
    for index, line in enumerate(raw.splitlines()):
        match = _BULLET.match(line.strip())
        if match:
            found.append((index, match.group(1), match.group(2), match.group(3).strip()))
    return found


def _parse_list(text: str, count: int) -> Optional[List[str]]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        items = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(items, list) or len(items) != count or not all(isinstance(item, str) for item in items):
        return None
    return items


def rewrite_answer(
    raw: str, question: str, *, tenant_id: str = "default", client: Any = None
) -> RewriteOutcome:
    """The extractive `raw` with each bullet put in natural wording where the checks allow it, else `raw` itself."""
    bullets = parse_bullets(raw)
    if not bullets:
        return RewriteOutcome(raw, reason="no_bullets")
    if len(bullets) > MAX_LINES:
        return RewriteOutcome(raw, reason="too_many_lines")
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps([sentence for _i, _p, sentence, _t in bullets], ensure_ascii=False)},
    ]
    try:
        text = llm.complete(messages, feature="rewrite", tenant_id=tenant_id, client=client)
    except llm.LLMNotAllowed as exc:
        return RewriteOutcome(raw, reason=f"not_allowed:{exc.decision.layer}")
    except Exception as exc:  # noqa: BLE001 - any failure leaves the extractive answer as it is
        return RewriteOutcome(raw, reason=f"error:{type(exc).__name__}")
    items = _parse_list(text, len(bullets))
    if items is None:
        return RewriteOutcome(raw, reason="bad_output")
    lines = raw.splitlines()
    outcome = RewriteOutcome(raw, reason="ok")
    for (index, prefix, sentence, tags), candidate in zip(bullets, items):
        broken = check_rewrite(sentence, candidate, question=question)
        if broken:
            outcome.kept += 1
            for rule in broken:
                outcome.violations[rule] = outcome.violations.get(rule, 0) + 1
            continue
        lines[index] = f"{prefix}{candidate.strip()} {tags}"
        outcome.rewritten += 1
    outcome.raw = "\n".join(lines)
    return outcome
