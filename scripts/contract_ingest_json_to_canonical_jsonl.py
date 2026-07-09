from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


TEXT_KEYS = ("text", "content", "body", "paragraph", "clause_text", "value")
CONTAINER_KEYS = ("pages", "blocks", "paragraphs", "clauses", "tables", "records", "items", "children")
MIN_TEXT_CHARS = 8


def _compact_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_source_pages(value: Any) -> List[int]:
    if value is None or value == "":
        return [-1]
    if isinstance(value, list):
        raw_items = value
    else:
        raw = str(value).strip()
        if not raw:
            return [-1]
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                raw_items = parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                raw_items = [raw]
        else:
            raw_items = re.split(r"[,、\s]+", raw)
    pages: List[int] = []
    for item in raw_items:
        if isinstance(item, bool):
            continue
        try:
            pages.append(int(item))
        except Exception:
            continue
    return pages or [-1]


def _record_page(record: dict, inherited_page: Any = None) -> List[int]:
    for key in ("source_pages", "pages", "page", "page_number", "page_no", "pageno"):
        if record.get(key) not in (None, ""):
            return parse_source_pages(record.get(key))
    return parse_source_pages(inherited_page)


def _record_type(record: dict, fallback: str = "record") -> str:
    for key in ("kind", "type", "record_type", "block_type", "category"):
        value = _compact_space(record.get(key))
        if value:
            return value
    return fallback


def _section_path(record: dict, inherited: List[str] | None = None) -> List[str]:
    path: List[str] = list(inherited or [])
    for key in ("section_path", "section", "heading", "title", "caption"):
        value = record.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            path.extend(_compact_space(item) for item in value if _compact_space(item))
        else:
            text = _compact_space(value)
            if text:
                path.append(text)
        break
    out: List[str] = []
    seen = set()
    for item in path:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _flatten_table(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, str):
        return _compact_space(value)
    if isinstance(value, list):
        rows: List[str] = []
        for row in value:
            if isinstance(row, dict):
                rows.append(" ".join(f"{k}={v}" for k, v in row.items() if _compact_space(v)))
            elif isinstance(row, list):
                rows.append(" ".join(_compact_space(cell) for cell in row if _compact_space(cell)))
            else:
                rows.append(_compact_space(row))
        return _compact_space(" / ".join(row for row in rows if row))
    if isinstance(value, dict):
        if isinstance(value.get("rows"), list):
            return _flatten_table(value.get("rows"))
        return _compact_space(" ".join(f"{k}={v}" for k, v in value.items() if _compact_space(v)))
    return _compact_space(value)


def _text_from_record(record: dict) -> str:
    for key in TEXT_KEYS:
        text = _compact_space(record.get(key))
        if text:
            return text
    for key in ("table", "rows", "cells"):
        text = _flatten_table(record.get(key))
        if text:
            return text
    return ""


def _has_child_containers(record: dict) -> bool:
    return any(isinstance(record.get(key), list) and record.get(key) for key in CONTAINER_KEYS)


def _shape_name(path: str, record: dict) -> str:
    if path == "root" and "pages" in record:
        return "document.pages"
    if "blocks" in record:
        return "document.blocks"
    return _record_type(record)


def _walk_record(
    record: Any,
    *,
    path: str,
    inherited_page: Any = None,
    inherited_section: List[str] | None = None,
) -> Iterable[Tuple[dict, str, Any, List[str]]]:
    if isinstance(record, list):
        for idx, item in enumerate(record):
            yield from _walk_record(
                item,
                path=f"{path}.{idx}",
                inherited_page=inherited_page,
                inherited_section=inherited_section,
            )
        return
    if not isinstance(record, dict):
        return

    page = record.get("page") or record.get("page_number") or record.get("page_no") or inherited_page
    section = _section_path(record, inherited_section)
    has_text = bool(_text_from_record(record))
    if has_text:
        yield record, path, page, section

    for key in CONTAINER_KEYS:
        children = record.get(key)
        if isinstance(children, list):
            for idx, child in enumerate(children):
                yield from _walk_record(
                    child,
                    path=f"{path}.{key}.{idx}",
                    inherited_page=page,
                    inherited_section=section,
                )


def _read_json_records(path: Path) -> Tuple[List[Tuple[dict, str, Any, List[str]]], Counter]:
    data = json.loads(path.read_text(encoding="utf-8"))
    shapes = Counter()
    records: List[Tuple[dict, str, Any, List[str]]] = []
    if isinstance(data, list):
        shapes["json.array"] += 1
    elif isinstance(data, dict):
        shapes[_shape_name("root", data)] += 1
    for item in _walk_record(data, path="root"):
        shapes[_record_type(item[0])] += 1
        records.append(item)
    return records, shapes


def _read_jsonl_records(path: Path) -> Tuple[List[Tuple[dict, str, Any, List[str]]], Counter]:
    records: List[Tuple[dict, str, Any, List[str]]] = []
    shapes = Counter({"jsonl.records": 1})
    with path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            if isinstance(obj, dict):
                shapes[_record_type(obj)] += 1
                records.append((obj, f"line{idx}", None, _section_path(obj)))
    return records, shapes


def iter_input_records(input_path: str | Path) -> Tuple[List[Tuple[dict, str, Any, List[str]]], Counter]:
    path = Path(input_path)
    all_records: List[Tuple[dict, str, Any, List[str]]] = []
    shapes: Counter = Counter()
    files: List[Path]
    if path.is_dir():
        files = sorted(
            item for item in path.iterdir() if item.suffix.lower() in {".json", ".jsonl"}
        )
        shapes["directory"] += 1
    else:
        files = [path]
    for file_path in files:
        if file_path.suffix.lower() == ".jsonl":
            records, file_shapes = _read_jsonl_records(file_path)
        else:
            records, file_shapes = _read_json_records(file_path)
        shapes.update(file_shapes)
        all_records.extend(records)
    return all_records, shapes


def _deterministic_id(source_doc: str, pages: List[int], record_type: str, text: str) -> str:
    page_part = ",".join(str(p) for p in pages)
    digest = hashlib.sha256(f"{source_doc}\n{page_part}\n{record_type}\n{text}".encode("utf-8")).hexdigest()
    safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_doc).strip("_") or "doc"
    return f"{safe_source}:contract:{digest[:16]}"


def _canonical_row(
    record: dict,
    *,
    path: str,
    inherited_page: Any,
    inherited_section: List[str],
    source_doc: str,
    title: str,
    doc_type: str,
    tenant_id: str,
    doc_version: str,
    language: str,
) -> dict:
    text = _text_from_record(record)
    pages = _record_page(record, inherited_page)
    record_type = _record_type(record)
    section_path = inherited_section or _section_path(record)
    row_id = _deterministic_id(source_doc, pages, record_type, text)
    parent_page = pages[0] if pages else -1
    parent_chunk_id = f"{source_doc}:page:{parent_page}" if parent_page != -1 else None
    return {
        "id": row_id,
        "text": text,
        "source_doc": source_doc,
        "source_pages": pages,
        "doc_id": source_doc,
        "chunk_index": None,
        "searchable": 1,
        "type": "pdf",
        "quality": "high",
        "doc_type": doc_type,
        "title": title,
        "section_path": section_path,
        "chunk_role": "child",
        "parent_chunk_id": parent_chunk_id,
        "searchable_text": text,
        "display_text": text,
        "language": language,
        "tenant_id": tenant_id,
        "doc_version": doc_version,
        "extraction_method": "contract_ingest_json",
        "original_record_type": record_type,
        "original_record_id": record.get("id") or record.get("record_id") or path,
    }


def convert_contract_ingest(
    *,
    input_path: str | Path,
    output_path: str | Path,
    source_doc: str,
    title: str,
    doc_type: str,
    tenant_id: str,
    doc_version: str,
    language: str = "ja",
    include_short: bool = False,
) -> dict:
    records, shapes = iter_input_records(input_path)
    rows: List[dict] = []
    skipped = 0
    seen_ids: set[str] = set()
    for record, path, inherited_page, inherited_section in records:
        text = _text_from_record(record)
        if not text:
            skipped += 1
            continue
        if not include_short and len(text) < MIN_TEXT_CHARS:
            skipped += 1
            continue
        row = _canonical_row(
            record,
            path=path,
            inherited_page=inherited_page,
            inherited_section=inherited_section,
            source_doc=source_doc,
            title=title,
            doc_type=doc_type,
            tenant_id=tenant_id,
            doc_version=doc_version,
            language=language,
        )
        if row["id"] in seen_ids:
            skipped += 1
            continue
        seen_ids.add(row["id"])
        rows.append(row)

    for idx, row in enumerate(rows, start=1):
        row["chunk_index"] = idx
        if not row.get("searchable_text") or not row.get("display_text"):
            raise ValueError(f"row {idx}: missing text fields")
        pages = row.get("source_pages")
        if not isinstance(pages, list) or not all(isinstance(p, int) for p in pages):
            raise ValueError(f"row {idx}: invalid source_pages")
        for key in ("tenant_id", "doc_version", "language"):
            if not row.get(key):
                raise ValueError(f"row {idx}: missing {key}")

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    summary = {
        "input_records": len(records),
        "written_records": len(rows),
        "skipped_records": skipped,
        "detected_shapes": dict(shapes),
        "output_path": str(output_path),
    }
    print(
        "Summary: "
        f"input_records={summary['input_records']} "
        f"written_records={summary['written_records']} "
        f"skipped_records={summary['skipped_records']} "
        f"detected_shapes={summary['detected_shapes']} "
        f"output_path={summary['output_path']}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert contract-ingest JSON outputs to chatbot canonical JSONL.")
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    parser.add_argument("--source-doc", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--doc-type", required=True)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--doc-version", required=True)
    parser.add_argument("--language", default="ja")
    parser.add_argument("--include-short", action="store_true")
    args = parser.parse_args()
    convert_contract_ingest(
        input_path=args.input_path,
        output_path=args.output_path,
        source_doc=args.source_doc,
        title=args.title,
        doc_type=args.doc_type,
        tenant_id=args.tenant_id,
        doc_version=args.doc_version,
        language=args.language,
        include_short=args.include_short,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
