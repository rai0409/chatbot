from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse

from scripts.approved_qa_to_canonical_jsonl import convert_approved_qa_to_canonical


def convert_approved_qa_to_pair_chunks(*, input_path, output_path) -> dict:
    # The canonical converter is the single source of truth for the qa_pair
    # chunk schema (Q+A in text/searchable_text, deterministic
    # approved_qa_pair:<qa_id> ids, tenant_id propagation, citation
    # source_doc/source_pages, doc_type="approved_qa_pair" /
    # chunk_type="qa_pair"). This entry point exists under the
    # roadmap-facing name and adds nothing schema-wise.
    return convert_approved_qa_to_canonical(input_path=input_path, output_path=output_path)


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
