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
from collections import Counter
from pathlib import Path

from rag_core.document_converters import SUPPORTED_FORMATS, convert_file_to_canonical_chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert one document (csv/xlsx/docx/pptx/pdf) into canonical chunk JSONL "
            "compatible with the existing ingest/eval pipeline. Conversion only — "
            "nothing is written to the vectorstore and the source file is not modified."
        )
    )
    parser.add_argument("--input", required=True, dest="input_path", help="document file path")
    parser.add_argument("--output", required=True, dest="output_path", help="canonical chunk JSONL to write")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--doc-type", default=None)
    parser.add_argument("--source-doc", default=None, help="override source_doc (default: input filename)")
    parser.add_argument(
        "--format",
        default="auto",
        choices=("auto",) + SUPPORTED_FORMATS,
        help="input format; auto = detect by extension",
    )
    args = parser.parse_args(argv)

    chunks = convert_file_to_canonical_chunks(
        args.input_path,
        tenant_id=args.tenant_id,
        source_doc=args.source_doc,
        doc_type=args.doc_type,
        source_format=None if args.format == "auto" else args.format,
    )

    out_path = Path(args.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    by_kind = Counter(
        (str(c.get("source_type")), str(c.get("chunk_type"))) for c in chunks
    )
    kind_summary = ", ".join(
        f"{src}/{kind}={count}" for (src, kind), count in sorted(by_kind.items())
    )
    print(
        "Summary: "
        f"input={args.input_path} "
        f"chunks={len(chunks)} "
        f"by_type=[{kind_summary}] "
        f"tenant_id={args.tenant_id} "
        f"output={out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
