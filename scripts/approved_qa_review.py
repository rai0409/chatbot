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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from rag_core.approved_qa import validate_approved_qa_records
from rag_core.question_normalization import normalize_question_for_exact_match


ALLOWED_STATUSES = {"draft", "approved", "rejected"}


def _compact(value: Any) -> str:
    return str(value or "").strip()


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_jsonl(path: str | Path) -> List[dict]:
    records: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError("JSONL records must be objects")
                records.append(obj)
    return records


def write_jsonl(
    path: str | Path,
    records: Iterable[dict],
    *,
    overwrite: bool = False,
    input_path: str | Path | None = None,
) -> None:
    out_path = Path(path)
    if input_path is not None and out_path.resolve() == Path(input_path).resolve() and not overwrite:
        raise FileExistsError("refusing to modify input in place without --in-place")
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"output exists: {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def ensure_normalized_questions(records: Sequence[dict]) -> List[dict]:
    normalized_records: List[dict] = []
    for record in records:
        item = dict(record)
        normalized = _compact(item.get("normalized_question"))
        if normalized:
            item["normalized_question"] = normalize_question_for_exact_match(normalized)
        else:
            item["normalized_question"] = normalize_question_for_exact_match(_compact(item.get("question")))
        normalized_records.append(item)
    return normalized_records


def review_validation_errors(records: Sequence[dict]) -> List[str]:
    normalized_records = ensure_normalized_questions(records)
    errors = validate_approved_qa_records(list(normalized_records))
    seen: dict[tuple[str, str], str] = {}
    for idx, record in enumerate(normalized_records, start=1):
        status = _compact(record.get("status"))
        if status not in ALLOWED_STATUSES:
            errors.append(f"line {idx}: invalid status: {status}")
        if not _compact(record.get("question")):
            errors.append(f"line {idx}: missing question")
        if not _compact(record.get("approved_answer")):
            errors.append(f"line {idx}: missing approved_answer")
        normalized = _compact(record.get("normalized_question"))
        if not normalized:
            errors.append(f"line {idx}: missing normalized_question")
            continue
        tenant_id = _compact(record.get("tenant_id")) or "default"
        qa_id = _compact(record.get("qa_id"))
        key = (tenant_id, normalized)
        previous = seen.get(key)
        if previous is not None and previous != qa_id:
            errors.append(
                f"line {idx}: duplicate normalized_question for tenant_id={tenant_id}: {normalized}"
            )
        else:
            seen[key] = qa_id
    return errors


def validate_or_raise(records: Sequence[dict]) -> List[dict]:
    normalized_records = ensure_normalized_questions(records)
    errors = review_validation_errors(normalized_records)
    if errors:
        raise ValueError("invalid approved QA review records: " + "; ".join(errors))
    return normalized_records


def status_counts(records: Sequence[dict]) -> dict[str, int]:
    return dict(Counter(_compact(record.get("status")) or "(missing)" for record in records))


def list_records(
    records: Sequence[dict],
    *,
    status: str = "draft",
    tenant_id: str | None = None,
    query: str | None = None,
    limit: int = 20,
) -> List[dict]:
    if status != "all" and status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status}")
    needle = _compact(query).lower()
    out: List[dict] = []
    for record in ensure_normalized_questions(records):
        if status != "all" and _compact(record.get("status")) != status:
            continue
        if tenant_id and (_compact(record.get("tenant_id")) or "default") != tenant_id:
            continue
        if needle:
            candidate_metadata = record.get("candidate_metadata")
            candidate_aliases = (
                candidate_metadata.get("aliases")
                if isinstance(candidate_metadata, dict) and isinstance(candidate_metadata.get("aliases"), list)
                else []
            )
            haystack = " ".join(
                [
                    _compact(record.get("qa_id")),
                    _compact(record.get("question")),
                    _compact(record.get("approved_answer")),
                    _compact(record.get("notes")),
                    " ".join(_compact(alias) for alias in candidate_aliases),
                    " ".join(_compact(alias) for alias in (record.get("approved_aliases") or [])),
                ]
            ).lower()
            if needle not in haystack:
                continue
        out.append(record)
        if limit is not None and len(out) >= limit:
            break
    return out


def _source_doc(record: dict) -> str:
    citations = record.get("approved_citations")
    if isinstance(citations, list) and citations and isinstance(citations[0], dict):
        return _compact(citations[0].get("source_doc"))
    return ""


def _source_pages(record: dict) -> Any:
    citations = record.get("approved_citations")
    if isinstance(citations, list) and citations and isinstance(citations[0], dict):
        return citations[0].get("source_pages") or []
    return []


def _preview(text: str, limit: int = 80) -> str:
    value = " ".join(_compact(text).split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "..."


def print_list(records: Sequence[dict], *, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(list(records), ensure_ascii=False, indent=2))
        return
    for record in records:
        pages = _source_pages(record)
        candidate_metadata = record.get("candidate_metadata")
        candidate_aliases = (
            candidate_metadata.get("aliases")
            if isinstance(candidate_metadata, dict) and isinstance(candidate_metadata.get("aliases"), list)
            else []
        )
        approved_aliases = record.get("approved_aliases") if isinstance(record.get("approved_aliases"), list) else []
        print(
            "\t".join(
                [
                    _compact(record.get("qa_id")),
                    _compact(record.get("status")),
                    _compact(record.get("question")),
                    _source_doc(record),
                    json.dumps(pages, ensure_ascii=False),
                    _preview(_compact(record.get("approved_answer"))),
                    f"candidate_aliases={len(candidate_aliases)}",
                    json.dumps(candidate_aliases, ensure_ascii=False),
                    f"approved_aliases={len(approved_aliases)}",
                    json.dumps(approved_aliases, ensure_ascii=False),
                ]
            )
        )


def _merge_review_notes(existing: Any, new_note: str) -> str:
    current = _compact(existing)
    note = _compact(new_note)
    if not note:
        return current
    if not current:
        return note
    return current + "\n" + note


def update_record_status(
    records: Sequence[dict],
    *,
    qa_id: str,
    status: str,
    reviewer: str,
    notes: str = "",
    reason: str = "",
    reviewed_at: str | None = None,
    approve_aliases: bool = False,
) -> List[dict]:
    if status not in {"approved", "rejected"}:
        raise ValueError(f"unsupported review status: {status}")
    if not _compact(qa_id):
        raise ValueError("qa_id is required")
    if not _compact(reviewer):
        raise ValueError("reviewer is required")

    timestamp = reviewed_at or _timestamp()
    found = False
    out: List[dict] = []
    for record in ensure_normalized_questions(records):
        item = dict(record)
        if _compact(item.get("qa_id")) == qa_id:
            found = True
            item["status"] = status
            item["reviewed_at"] = timestamp
            item["reviewed_by"] = reviewer
            if notes:
                item["review_notes"] = _merge_review_notes(item.get("review_notes"), notes)
            if status == "rejected":
                item["rejection_reason"] = _compact(reason)
                item.pop("approved_aliases", None)
            elif approve_aliases:
                candidate_metadata = item.get("candidate_metadata")
                aliases = candidate_metadata.get("aliases") if isinstance(candidate_metadata, dict) else []
                if aliases and not isinstance(aliases, list):
                    raise ValueError("candidate_metadata.aliases must be a list")
                item["approved_aliases"] = list(aliases or [])
        out.append(item)
    if not found:
        raise KeyError(f"qa_id not found: {qa_id}")
    return validate_or_raise(out)


def promote_all_records(
    records: Sequence[dict],
    *,
    reviewer: str,
    notes: str = "",
    yes: bool = False,
    reviewed_at: str | None = None,
    approve_aliases: bool = False,
) -> List[dict]:
    if not yes:
        raise ValueError("promote-all requires --yes")
    if not _compact(reviewer):
        raise ValueError("reviewer is required")
    timestamp = reviewed_at or _timestamp()
    out: List[dict] = []
    for record in ensure_normalized_questions(records):
        item = dict(record)
        if _compact(item.get("status")) == "draft":
            item["status"] = "approved"
            item["reviewed_at"] = timestamp
            item["reviewed_by"] = reviewer
            if notes:
                item["review_notes"] = _merge_review_notes(item.get("review_notes"), notes)
            if approve_aliases:
                candidate_metadata = item.get("candidate_metadata")
                aliases = candidate_metadata.get("aliases") if isinstance(candidate_metadata, dict) else []
                if aliases and not isinstance(aliases, list):
                    raise ValueError("candidate_metadata.aliases must be a list")
                item["approved_aliases"] = list(aliases or [])
        out.append(item)
    return validate_or_raise(out)


def export_approved(records: Sequence[dict]) -> List[dict]:
    approved = []
    for record in ensure_normalized_questions(records):
        if _compact(record.get("status")) != "approved":
            continue
        item = dict(record)
        item.pop("candidate_metadata", None)
        approved.append(item)
    return validate_or_raise(approved)


def validate_file(path: str | Path, *, allow_errors: bool = False) -> dict:
    records = ensure_normalized_questions(read_jsonl(path))
    errors = review_validation_errors(records)
    summary = {
        "total": len(records),
        "status_counts": status_counts(records),
        "errors": errors,
        "error_count": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors and not allow_errors:
        raise SystemExit(1)
    return summary


def _write_changed(
    *,
    input_path: str | Path,
    output_path: str | Path | None,
    records: Sequence[dict],
    overwrite: bool = False,
    in_place: bool = False,
) -> Path:
    if in_place:
        target = Path(input_path)
        write_jsonl(target, records, overwrite=True, input_path=input_path)
        return target
    if output_path is None:
        raise ValueError("--out is required unless --in-place is used")
    target = Path(output_path)
    write_jsonl(target, records, overwrite=overwrite, input_path=input_path)
    return target


def _run_list(args: argparse.Namespace) -> int:
    records = read_jsonl(args.input_path)
    matches = list_records(
        records,
        status=args.status,
        tenant_id=args.tenant_id,
        query=args.query,
        limit=args.limit,
    )
    print_list(matches, json_output=args.json)
    return 0


def _run_promote(args: argparse.Namespace) -> int:
    records = update_record_status(
        read_jsonl(args.input_path),
        qa_id=args.qa_id,
        status="approved",
        reviewer=args.reviewer,
        notes=args.notes or "",
        approve_aliases=args.approve_aliases,
    )
    target = _write_changed(
        input_path=args.input_path,
        output_path=args.output_path,
        records=records,
        overwrite=args.overwrite,
        in_place=args.in_place,
    )
    print(f"promoted qa_id={args.qa_id} output={target}")
    return 0


def _run_reject(args: argparse.Namespace) -> int:
    records = update_record_status(
        read_jsonl(args.input_path),
        qa_id=args.qa_id,
        status="rejected",
        reviewer=args.reviewer,
        reason=args.reason or "",
    )
    target = _write_changed(
        input_path=args.input_path,
        output_path=args.output_path,
        records=records,
        overwrite=args.overwrite,
        in_place=args.in_place,
    )
    print(f"rejected qa_id={args.qa_id} output={target}")
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    validate_file(args.input_path, allow_errors=args.allow_errors)
    return 0


def _run_export_approved(args: argparse.Namespace) -> int:
    records = export_approved(read_jsonl(args.input_path))
    write_jsonl(args.output_path, records, overwrite=args.overwrite, input_path=args.input_path)
    print(f"approved_count={len(records)} output={args.output_path}")
    return 0


def _run_promote_all(args: argparse.Namespace) -> int:
    records = promote_all_records(
        read_jsonl(args.input_path),
        reviewer=args.reviewer,
        notes=args.notes or "",
        yes=args.yes,
        approve_aliases=args.approve_aliases,
    )
    target = _write_changed(
        input_path=args.input_path,
        output_path=args.output_path,
        records=records,
        overwrite=args.overwrite,
        in_place=args.in_place,
    )
    print(f"promoted_all output={target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and promote approved-QA candidate JSONL.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List QA records for review.")
    list_parser.add_argument("--in", dest="input_path", required=True)
    list_parser.add_argument("--status", default="draft", choices=["draft", "approved", "rejected", "all"])
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--tenant-id")
    list_parser.add_argument("--query")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=_run_list)

    promote_parser = subparsers.add_parser("promote", help="Promote one QA record to approved.")
    promote_parser.add_argument("--in", dest="input_path", required=True)
    promote_parser.add_argument("--out", dest="output_path")
    promote_parser.add_argument("--qa-id", required=True)
    promote_parser.add_argument("--reviewer", required=True)
    promote_parser.add_argument("--notes", default="")
    promote_parser.add_argument(
        "--approve-aliases",
        action="store_true",
        help="Explicitly approve candidate_metadata.aliases into approved_aliases.",
    )
    promote_parser.add_argument("--overwrite", action="store_true")
    promote_parser.add_argument("--in-place", action="store_true")
    promote_parser.set_defaults(func=_run_promote)

    reject_parser = subparsers.add_parser("reject", help="Reject one QA record.")
    reject_parser.add_argument("--in", dest="input_path", required=True)
    reject_parser.add_argument("--out", dest="output_path")
    reject_parser.add_argument("--qa-id", required=True)
    reject_parser.add_argument("--reviewer", required=True)
    reject_parser.add_argument("--reason", default="")
    reject_parser.add_argument("--overwrite", action="store_true")
    reject_parser.add_argument("--in-place", action="store_true")
    reject_parser.set_defaults(func=_run_reject)

    validate_parser = subparsers.add_parser("validate", help="Validate review JSONL.")
    validate_parser.add_argument("--in", dest="input_path", required=True)
    validate_parser.add_argument("--allow-errors", action="store_true")
    validate_parser.set_defaults(func=_run_validate)

    export_parser = subparsers.add_parser("export-approved", help="Export approved-only JSONL.")
    export_parser.add_argument("--in", dest="input_path", required=True)
    export_parser.add_argument("--out", dest="output_path", required=True)
    export_parser.add_argument("--overwrite", action="store_true")
    export_parser.set_defaults(func=_run_export_approved)

    promote_all_parser = subparsers.add_parser("promote-all", help="Promote all draft records to approved.")
    promote_all_parser.add_argument("--in", dest="input_path", required=True)
    promote_all_parser.add_argument("--out", dest="output_path")
    promote_all_parser.add_argument("--reviewer", required=True)
    promote_all_parser.add_argument("--notes", default="")
    promote_all_parser.add_argument("--yes", action="store_true")
    promote_all_parser.add_argument(
        "--approve-aliases",
        action="store_true",
        help="Explicitly approve aliases for every promoted draft record.",
    )
    promote_all_parser.add_argument("--overwrite", action="store_true")
    promote_all_parser.add_argument("--in-place", action="store_true")
    promote_all_parser.set_defaults(func=_run_promote_all)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
