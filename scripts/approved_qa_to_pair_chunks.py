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
from pathlib import Path

from scripts.approved_qa_to_canonical_jsonl import convert_approved_qa_to_canonical


def _stamp_status(output_path) -> int:
    """Write status="approved" into every row (the converter writes approved records only).

    rag_core.approved_similar searches pair rows with a status=approved clause
    (APPROVED_SIMILAR_REQUIRE_STATUS, default true): rows ingested before this
    field existed are not found until re-converted and re-ingested."""
    path = Path(output_path)
    if not path.is_file():
        return 0
    rows = []
    stamped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("status") != "approved":
            row["status"] = "approved"
            stamped += 1
        rows.append(row)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return stamped


def convert_approved_qa_to_pair_chunks(*, input_path, output_path) -> dict:
    # The canonical converter is the single source of truth for the qa_pair
    # chunk schema (Q+A in text/searchable_text, deterministic
    # approved_qa_pair:<qa_id> ids, tenant_id propagation, citation
    # source_doc/source_pages, doc_type="approved_qa_pair" /
    # chunk_type="qa_pair"). This entry point adds the status field the
    # similar-candidate search filters on.
    summary = convert_approved_qa_to_canonical(input_path=input_path, output_path=output_path)
    stamped = _stamp_status(output_path)
    if isinstance(summary, dict):
        summary["status_stamped_records"] = stamped
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert approved Q&A JSONL records into canonical Q+A pair RAG chunks "
            "(question and approved answer retrievable together in one chunk). "
            "Output is a canonical chunk JSONL for the existing manual ingest "
            "workflow; nothing is written to the vectorstore."
        )
    )
    parser.add_argument("--input", required=True, dest="input_path", help="approved-QA JSONL (e.g. data/approved_qa/default.jsonl)")
    parser.add_argument("--output", required=True, dest="output_path", help="canonical qa_pair chunk JSONL to write")
    args = parser.parse_args()
    convert_approved_qa_to_pair_chunks(input_path=args.input_path, output_path=args.output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
