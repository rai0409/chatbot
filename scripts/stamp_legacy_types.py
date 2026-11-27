"""Stamp a `type` on legacy rows that carry none of type / source_type / doc_type.

Purpose: promotion refuses untyped rows (fail-closed). Stamping them once, with a report, is preferred over
PROMOTION_ALLOW_UNTYPED=true (see docs/operations/index_generations.md).

Usage:
  python scripts/stamp_legacy_types.py --collection NAME                 # dry run (default), writes a report only
  python scripts/stamp_legacy_types.py --collection NAME --execute       # stamp vector rows + keyword corpus
  options: --keyword-index PATH, --report PATH, --allow-production, --batch N

Input: the vector collection and its keyword corpus JSONL.
Output: a JSON report (default reports/stamp_legacy_types_<collection>_<timestamp>.json inside the repo; ids and
document names only, no chunk text; do not commit it, move it to 即削除/ when no longer needed).

Type inference (first match): the `parser` name, the extension of source_doc / doc_id, the chunk id
(pdf / docx / xlsx / csv / pptx / faq), otherwise "document". Mail-looking rows
(rag_core.mail_access.is_private_mail_record) are reported and never changed. Refuses on a production
collection unless --allow-production.
"""
from __future__ import annotations

import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import argparse  # noqa: E402  repo-root bootstrap above
import json  # noqa: E402  repo-root bootstrap above
import time  # noqa: E402  repo-root bootstrap above
from pathlib import Path  # noqa: E402  repo-root bootstrap above
from typing import Any, Dict, Iterable, List, Optional, Set  # noqa: E402  repo-root bootstrap above

_TYPE_FIELDS = ("type", "source_type", "doc_type")
_EXTENSIONS = {
    ".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".csv": "csv", ".pptx": "pptx",
}
_NAMES = ("pdf", "docx", "xlsx", "csv", "pptx", "faq")


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def is_untyped(meta: Dict[str, Any]) -> bool:
    return not any(_present(meta.get(k)) for k in _TYPE_FIELDS)


def infer_type(meta: Dict[str, Any]) -> str:
    parser = str(meta.get("parser") or "").casefold()
    for name in _NAMES:
        if name in parser:
            return name
    for field in ("source_doc", "doc_id"):
        text = str(meta.get(field) or "").strip().casefold()
        for ext, name in _EXTENSIONS.items():
            if text.endswith(ext):
                return name
    cid = str(meta.get("id") or "").casefold()
    for name in _NAMES:
        if name in cid:
            return name
    return "document"


def _is_mail(meta: Dict[str, Any], row_id: str) -> bool:
    from rag_core.mail_access import is_private_mail_record

    return is_private_mail_record(meta, row_id)


def _classify(row_id: str, meta: Dict[str, Any], plan: Dict[str, Any]) -> None:
    meta = dict(meta or {})
    meta.setdefault("id", row_id)
    plan["scanned"] += 1
    if not is_untyped(meta):
        return
    if _is_mail(meta, row_id):
        plan["mail_skipped"].append(row_id)
        return
    inferred = infer_type(meta)
    plan["stamp"][row_id] = inferred
    plan["by_type"][inferred] = plan["by_type"].get(inferred, 0) + 1


def _new_plan() -> Dict[str, Any]:
    return {"scanned": 0, "stamp": {}, "mail_skipped": [], "by_type": {}}


def plan_vector(store, batch: int = 500) -> Dict[str, Any]:
    plan = _new_plan()
    offset = 0
    while True:
        res = store.get(include=["metadatas"], limit=batch, offset=offset)
        ids = list(res.get("ids") or [])
        if not ids:
            break
        for row_id, meta in zip(ids, res.get("metadatas") or []):
            _classify(row_id, meta or {}, plan)
        offset += len(ids)
        if len(ids) < batch:
            break
    return plan


def apply_vector(store, plan: Dict[str, Any], batch: int = 200) -> int:
    items = list(plan["stamp"].items())
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        store.update(ids=[c[0] for c in chunk], metadatas=[{"type": c[1]} for c in chunk])
    return len(items)


def _read_keyword(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def plan_keyword(path: Path) -> Dict[str, Any]:
    plan = _new_plan()
    plan["rows"] = {}
    if not path.exists():
        return plan
    for row in _read_keyword(path):
        before = len(plan["stamp"])
        _classify(str(row.get("id") or ""), row, plan)
        if len(plan["stamp"]) > before:
            plan["rows"][str(row.get("id") or "")] = row
    return plan


def apply_keyword(path: Path, plan: Dict[str, Any]) -> int:
    from scripts.ingest_canonical_jsonl import _rewrite_keyword_corpus  # fsynced, locked, atomic rename

    if not plan["stamp"]:
        return 0
    stamped = []
    for row_id, row in plan["rows"].items():
        stamped.append({**row, "type": plan["stamp"][row_id]})
    ids: Set[str] = set(plan["rows"])
    _rewrite_keyword_corpus(path, lambda old: str(old.get("id") or "") in ids, stamped)
    return len(stamped)


def _summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {"scanned": plan["scanned"], "to_stamp": len(plan["stamp"]), "by_type": plan["by_type"],
            "mail_skipped_count": len(plan["mail_skipped"]), "mail_skipped_ids": plan["mail_skipped"],
            "stamp_ids": plan["stamp"]}


def run(collection: str, *, store, keyword_path: Optional[Path], execute: bool, allow_production: bool,
        report_path: Path, batch: int = 500) -> Dict[str, Any]:
    from webapi.ingestion_jobs import is_production_collection

    if is_production_collection(collection) and not allow_production:
        raise PermissionError(f"{collection or 'default'} is a production collection; pass --allow-production")
    vector = plan_vector(store, batch)
    keyword = plan_keyword(keyword_path) if keyword_path else _new_plan()
    report: Dict[str, Any] = {
        "collection": collection, "mode": "execute" if execute else "dry-run", "ts": time.time(),
        "vector": _summary(vector), "keyword": _summary(keyword),
    }
    if execute:
        report["vector"]["written"] = apply_vector(store, vector)
        report["keyword"]["written"] = apply_keyword(keyword_path, keyword) if keyword_path else 0
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collection", required=True)
    parser.add_argument("--keyword-index", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--execute", action="store_true", help="write (default is a dry run)")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--batch", type=int, default=500)
    args = parser.parse_args(argv)

    from rag_core import store as store_mod
    from rag_core.retrieval import keyword_index_path_for_collection

    report_path = Path(args.report) if args.report else (
        _ROOT / "reports" / f"stamp_legacy_types_{args.collection}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    keyword_path = Path(args.keyword_index or keyword_index_path_for_collection(args.collection))
    try:
        vectors = store_mod.get_vectorstore(collection_name=args.collection, create_if_missing=False)
        report = run(args.collection, store=vectors, keyword_path=keyword_path, execute=args.execute,
                     allow_production=args.allow_production, report_path=report_path, batch=args.batch)
    except PermissionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: report[k] for k in ("collection", "mode")}, ensure_ascii=False),
          f"vector_to_stamp={report['vector']['to_stamp']} keyword_to_stamp={report['keyword']['to_stamp']}",
          f"mail_skipped={report['vector']['mail_skipped_count'] + report['keyword']['mail_skipped_count']}",
          f"report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
