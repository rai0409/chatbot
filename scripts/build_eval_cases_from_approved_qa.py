from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from rag_core.ja_text import normalize_japanese_text


# Katakana runs, kanji runs, or alnum tokens — deterministic candidates for
# an "answer-only" retrieval probe term.
_TERM_PATTERN = re.compile(r"[ァ-ヴー]{3,}|[一-龯]{3,}|[A-Za-z0-9]{2,}")

PAIR_CHUNK_ID_PREFIX = "approved_qa_pair:"


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_jsonl(path: str | Path) -> List[dict]:
    records: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError(f"{path}: JSONL records must be objects")
                records.append(obj)
    return records


def extract_answer_only_term(question: str, answer: str) -> str | None:
    """Longest deterministic term that appears in the answer but not the question."""
    question_norm = normalize_japanese_text(question)
    answer_norm = normalize_japanese_text(answer)
    candidates: List[str] = []
    for match in _TERM_PATTERN.finditer(answer_norm):
        term = match.group(0)
        if term not in question_norm and term not in candidates:
            candidates.append(term)
    if not candidates:
        return None
    return sorted(candidates, key=lambda t: (-len(t), answer_norm.index(t)))[0]


def validate_case_ids(rows: List[dict]) -> None:
    seen: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        case_id = _compact(row.get("case_id"))
        if not case_id:
            raise ValueError(f"case {idx}: missing case_id")
        if case_id in seen:
            raise ValueError(f"case {idx}: duplicate case_id: {case_id}")
        seen.add(case_id)


def build_cases(
    *,
    approved_records: List[dict],
    pair_chunks: List[dict],
) -> tuple[List[dict], Dict[str, int]]:
    pair_by_id = {_compact(row.get("id")): row for row in pair_chunks}
    pair_norm_texts = {
        chunk_id: normalize_japanese_text(_compact(row.get("searchable_text") or row.get("text")))
        for chunk_id, row in pair_by_id.items()
    }

    cases: List[dict] = []
    skipped: Dict[str, int] = {}

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for record in approved_records:
        qa_id = _compact(record.get("qa_id"))
        question = _compact(record.get("question"))
        answer = _compact(record.get("approved_answer"))
        if _compact(record.get("status")) != "approved":
            _skip(f"status={_compact(record.get('status')) or 'missing'}")
            continue
        pair_chunk_id = PAIR_CHUNK_ID_PREFIX + qa_id
        pair_row = pair_by_id.get(pair_chunk_id)
        if pair_row is None:
            _skip("missing_pair_chunk")
            continue
        gold_doc = _compact(pair_row.get("source_doc"))

        cases.append(
            {
                "case_id": f"real_q_{qa_id}",
                "category": "real approved stored question",
                "query": question,
                "gold_chunk_ids": [pair_chunk_id],
                "gold_doc_ids": [gold_doc] if gold_doc else [],
                "notes": (
                    f"Generated from approved record {qa_id}: stored question wording; "
                    "gold = its qa_pair chunk. Label source: data/approved_qa JSONL."
                ),
            }
        )

        term = extract_answer_only_term(question, answer)
        if term is None:
            _skip("no_answer_only_term")
            continue
        # Honest gold labels: every pair chunk containing the probe term, not
        # just the originating record.
        gold_ids = sorted(
            chunk_id for chunk_id, norm in pair_norm_texts.items() if term in norm
        )
        gold_docs = sorted(
            {
                _compact(pair_by_id[chunk_id].get("source_doc"))
                for chunk_id in gold_ids
                if _compact(pair_by_id[chunk_id].get("source_doc"))
            }
        )
        cases.append(
            {
                "case_id": f"real_a_{qa_id}",
                "category": "real approved answer-only term",
                "query": f"{term} について",
                "gold_chunk_ids": gold_ids,
                "gold_doc_ids": gold_docs,
                "notes": (
                    f"Generated from approved record {qa_id}: probe term 「{term}」 appears "
                    "in the approved answer but not the stored question; a gold hit proves "
                    "Q and A are retrievable together. Label source: data/approved_qa JSONL."
                ),
            }
        )

    return cases, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate eval-runner retrieval cases from approved Q&A records and their "
            "qa_pair chunks. Generated cases carry gold labels only; strict guard/fallback "
            "expectations stay hand-authored (pass them via --extra-cases)."
        )
    )
    parser.add_argument("--approved", required=True, help="approved-QA JSONL (e.g. data/approved_qa/default.jsonl)")
    parser.add_argument("--pair-chunks", required=True, help="qa_pair chunk JSONL from scripts/approved_qa_to_pair_chunks.py")
    parser.add_argument("--extra-cases", default=None, help="optional hand-authored case JSONL merged after generated cases")
    parser.add_argument("--output", required=True, help="case JSONL to write")
    args = parser.parse_args()

    approved_records = _read_jsonl(args.approved)
    pair_chunks = _read_jsonl(args.pair_chunks)
    cases, skipped = build_cases(approved_records=approved_records, pair_chunks=pair_chunks)

    extra_count = 0
    if args.extra_cases:
        extra = _read_jsonl(args.extra_cases)
        extra_count = len(extra)
        cases = cases + extra

    validate_case_ids(cases)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(
        "Summary: "
        f"approved_records={len(approved_records)} "
        f"generated_cases={len(cases) - extra_count} "
        f"extra_cases={extra_count} "
        f"total_cases={len(cases)} "
        f"skipped_by_reason={json.dumps(skipped, ensure_ascii=False, sort_keys=True)} "
        f"output_path={out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
