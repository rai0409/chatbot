from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

from rag_core.document_converters.base import table_rows_to_chunks


def convert_csv(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    # utf-8-sig: Excel-exported CSVs commonly carry a BOM.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return []
    headers = rows[0]
    data = [(line_no, cells) for line_no, cells in enumerate(rows[1:], start=2)]
    return table_rows_to_chunks(
        headers=headers,
        rows=data,
        source_type="csv",
        source_doc=source_doc,
        location_prefix="",
        tenant_id=tenant_id,
        doc_type=doc_type or "table",
        parser="stdlib_csv",
        start_index=1,
        extra_builder=lambda row_number, values: {"sheet_name": None},
    )
