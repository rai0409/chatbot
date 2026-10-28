"""Evaluation sets from the committed labelled case files (eval/cases/*_cases.jsonl).

A case file carries gold chunk/doc ids and abstain labels. The bench wants one JSON object per line
with a dev/test split (eval/bench/dataset.py). This turns the first into the second, deterministically:

  split     decided by a hash of (seed, group), so every question about the same document lands in the
            same split (they are not independent) and the same input always gives the same split
  relevant  gold_chunk_ids (match mode "chunk"), else gold_doc_ids ("doc"); smoke-style cases fall back to
            expected_top_chunk_id / expected_top_source_doc. A case without any label is skipped and counted.
  slices    "cat:<category>" plus heuristic tags found in the query text (auto_slices). The tags are for
            looking at results per kind of question (year, amount, negation, condition, code, katakana
            term, short query); they are not gold labels.

  python -m eval.bench from-cases --cases eval/cases/semantic_challenge_cases.jsonl --out runs/eval_set.jsonl
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

_YEAR_RE = re.compile(r"(?:令和|平成|昭和)\s*[0-9０-９元]+\s*年度?|(?:19|20)[0-9]{2}\s*年度?|[0-9０-９]+\s*年度")
_AMOUNT_RE = re.compile(r"[0-9０-９][0-9０-９,，.]*\s*(?:万円|千円|円|％|%)")
_NEGATION_RE = re.compile(r"ない|ません|できず|不可|不要|以外|除[くき外]|ではなく|なし")
_CONDITION_RE = re.compile(r"場合|とき|時は|なら|限り|以上|以下|未満")
_CODE_RE = re.compile(r"[A-Za-z]{2,}[-_]?[0-9]+|[0-9]+[A-Za-z]{2,}")
_KATAKANA_RE = re.compile(r"[ァ-ヴー]{4,}")
_SLUG_RE = re.compile(r"[^0-9a-z]+")
_SHORT_QUERY_CHARS = 12


def auto_slices(query: str) -> List[str]:
    """Heuristic tags of the question (not gold labels)."""
    text = str(query or "")
    tags: List[str] = []
    for name, pattern in (
        ("year", _YEAR_RE),
        ("amount", _AMOUNT_RE),
        ("negation", _NEGATION_RE),
        ("condition", _CONDITION_RE),
        ("code", _CODE_RE),
        ("katakana", _KATAKANA_RE),
    ):
        if pattern.search(text):
            tags.append(name)
    if len(text.strip()) <= _SHORT_QUERY_CHARS:
        tags.append("short")
    return tags


def _slug(text: str) -> str:
    slug = _SLUG_RE.sub("_", str(text or "").lower()).strip("_")
    return slug or "other"


def _is_abstain(case: Dict[str, Any]) -> bool:
    return bool(case.get("expected_abstain") or case.get("should_abstain")) or case.get("answerable") is False


def _labels(case: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(chunk-level items, doc-level items) of the gold labels of a case; either list may be empty."""
    chunks = [str(x).strip() for x in case.get("gold_chunk_ids") or [] if str(x).strip()]
    if not chunks and str(case.get("expected_top_chunk_id") or "").strip():
        chunks = [str(case["expected_top_chunk_id"]).strip()]
    docs = [str(x).strip() for x in case.get("gold_doc_ids") or [] if str(x).strip()]
    if not docs and str(case.get("expected_top_source_doc") or "").strip():
        docs = [str(case["expected_top_source_doc"]).strip()]
    return [{"chunk_id": c, "grade": 1} for c in chunks], [{"doc_id": d, "grade": 1} for d in docs]


def split_for(group: str, *, test_share: float, seed: int) -> str:
    bucket = int(hashlib.sha256(f"{seed}:{group}".encode("utf-8")).hexdigest()[:8], 16) % 10000
    return "test" if bucket < round(float(test_share) * 10000) else "dev"


def read_cases(path: str | Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: a case must be an object")
        cases.append(row)
    return cases


def build_eval_set(
    cases: Sequence[Dict[str, Any]], *, test_share: float = 0.2, seed: int = 0, match_mode: str = "auto"
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not 0.0 <= float(test_share) <= 1.0:
        raise ValueError("test_share must be between 0 and 1")
    if match_mode not in ("auto", "chunk", "doc"):
        raise ValueError("match_mode must be auto, chunk or doc")
    skipped: Dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    prepared = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        query = str(case.get("query") or "").strip()
        if not case_id or not query:
            skip("missing_case_id_or_query")
            continue
        abstain = _is_abstain(case)
        chunk_items, doc_items = _labels(case)
        if not abstain and not chunk_items and not doc_items:
            skip("no_gold_label")
            continue
        prepared.append((case_id, query, case, abstain, chunk_items, doc_items))
    answerable = [p for p in prepared if not p[3]]
    if match_mode == "auto":
        if all(p[4] for p in answerable):
            match_mode = "chunk"
        elif all(p[5] for p in answerable):
            match_mode = "doc"
        else:
            raise ValueError(
                "the answerable cases mix chunk-only and doc-only labels; the bench uses one match mode per run. "
                "Use a case file with one kind of label, or pass match_mode explicitly."
            )
    rows: List[Dict[str, Any]] = []
    for case_id, query, case, abstain, chunk_items, doc_items in prepared:
        relevant = chunk_items if match_mode == "chunk" else doc_items
        if not abstain and not relevant:
            skip(f"no_{match_mode}_label")
            continue
        docs = [item["doc_id"] for item in doc_items]
        group = docs[0] if docs else case_id
        rows.append(
            {
                "query_id": case_id,
                "query": query,
                "split": split_for(group, test_share=test_share, seed=seed),
                "tenant_id": str(case.get("tenant_id") or "default"),
                "answerable": not abstain,
                "relevant": [] if abstain else relevant,
                "slices": [f"cat:{_slug(case.get('category'))}"] + auto_slices(query),
                "group": group,
            }
        )
    summary = {
        "cases": len(cases),
        "written": len(rows),
        "skipped": skipped,
        "dev": sum(1 for r in rows if r["split"] == "dev"),
        "test": sum(1 for r in rows if r["split"] == "test"),
        "abstain": sum(1 for r in rows if not r["answerable"]),
        "match_mode": match_mode,
        "test_share": float(test_share),
        "seed": int(seed),
    }
    return rows, summary


def write_eval_set(path: str | Path, rows: Sequence[Dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
