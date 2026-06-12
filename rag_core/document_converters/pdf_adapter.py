"""Adapter around the existing PDF converter (scripts/pdf_to_canonical_jsonl).

The existing converter is the production-verified PDF path (Japanese
doc-type-aware chunking, page metadata, parent/child roles). It only exposes
an argparse main(), so this adapter invokes it in-process with a patched argv
and a temporary output file, then annotates source_type/parser. It is NOT
rewritten here.

Known limitations surfaced as-is from the underlying converter:
- image-only PDFs need --ocr (not enabled through this adapter)
- PDF chunks keep their own chunk_role parent/child structure
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List


@contextmanager
def _patched_argv(argv: List[str]):
    original = sys.argv
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = original


def convert_pdf(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    # Lazy import: pulls in config/chunking (and fitz inside its main); must
    # not run at package import time.
    from scripts import pdf_to_canonical_jsonl

    path = Path(path)
    with tempfile.NamedTemporaryFile(
        mode="r", suffix=".jsonl", encoding="utf-8", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        argv = [
            "pdf_to_canonical_jsonl.py",
            "--pdf",
            str(path),
            "--out",
            str(tmp_path),
            "--tenant-id",
            tenant_id or "default",
        ]
        if doc_type:
            argv += ["--doc-type", doc_type]
        with _patched_argv(argv):
            exit_code = pdf_to_canonical_jsonl.main()
        if exit_code not in (0, None):
            raise RuntimeError(f"pdf_to_canonical_jsonl failed with exit code {exit_code}")
        rows: List[Dict] = []
        with tmp_path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if raw:
                    rows.append(json.loads(raw))
    finally:
        tmp_path.unlink(missing_ok=True)

    for row in rows:
        row.setdefault("source_type", "pdf")
        row.setdefault("parser", "pdf_to_canonical_jsonl")
        if source_doc:
            row["source_doc"] = source_doc
            row["doc_id"] = source_doc
    return rows
