#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_core.canonical_metadata import normalize_record, validate_required_retrieval_metadata


REQUIRED_OUTPUT_FIELDS = (
    "id",
    "text",
    "display_text",
    "searchable_text",
    "source_doc",
    "source_file",
    "source_type",
    "parser",
    "doc_type",
    "chunk_type",
    "tenant_id",
    "source_pages",
    "source_page_start",
    "source_page_end",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        if not isinstance(obj, dict):
            raise SystemExit(f"{path}:{line_no}: expected object")
        rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_report(path: Path, *, input_path: Path, output_path: Path, rows: list[dict[str, Any]]) -> None:
    missing_counter: Counter[str] = Counter()
    source_docs: Counter[str] = Counter()
    source_types: Counter[str] = Counter()
    for row in rows:
        missing_counter.update(validate_required_retrieval_metadata(row))
        source_docs[str(row.get("source_doc") or "")] += 1
        source_types[str(row.get("source_type") or "")] += 1
    complete = sum(1 for row in rows if not validate_required_retrieval_metadata(row))
    lines = [
        "# Metadata Normalization Report",
        "",
        "## Summary",
        f"- input: `{input_path}`",
        f"- output: `{output_path}`",
        f"- total_chunks: {len(rows)}",
        f"- complete_required_retrieval_metadata: {complete}/{len(rows)}",
        "",
        "## Missing Required Retrieval Metadata After Normalization",
    ]
    if missing_counter:
        lines.extend(f"- {key}: {count}" for key, count in sorted(missing_counter.items()))
    else:
        lines.append("- none")
    lines.extend(["", "## Source Type Distribution"])
    lines.extend(f"- {key or '(empty)'}: {count}" for key, count in sorted(source_types.items()))
    lines.extend(["", "## Source Doc Distribution"])
    lines.extend(f"- {key or '(empty)'}: {count}" for key, count in sorted(source_docs.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    rows = [normalize_record(row) for row in read_jsonl(args.input)]
    for row in rows:
        missing_output = [field for field in REQUIRED_OUTPUT_FIELDS if field not in row]
        if missing_output:
            raise SystemExit(f"normalized row missing output fields: {missing_output}: {row.get('id')}")
    write_jsonl(args.output, rows)
    build_report(args.report, input_path=args.input, output_path=args.output, rows=rows)
    print(json.dumps({"input": str(args.input), "output": str(args.output), "chunks": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
