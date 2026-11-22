#!/usr/bin/env python3
"""Convert mail_ingest.py results to chatbot-compatible canonical JSONL.

Read-only with respect to the source and any search index. Intentionally does
not register, create or mutate collections. Run from a chatbot worktree with
rag_core/mail_ingest.py present; use explicit --input/--out/--tenant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _input_files(source: Path, max_files: int, max_total_bytes: int) -> tuple[int, int]:
    if not source.exists():
        raise ValueError("input_does_not_exist")
    if source.is_symlink():
        raise ValueError("symlink_input_forbidden")
    if source.is_dir():
        # mail_ingest.iter_inputs() scans .eml recursively, but does not impose
        # aggregate resource bounds. Mirror the same file selection here.
        files = sorted(p for p in source.rglob("*") if p.suffix.lower() == ".eml")
    elif source.is_file() and source.suffix.lower() in {".eml", ".csv", ".tsv", ".xlsx", ".xlsm"}:
        files = [source]
    else:
        raise ValueError("unsupported_input_type")
    if not files:
        raise ValueError("no_email_inputs")
    if len(files) > max_files:
        raise ValueError("too_many_files")
    total = 0
    for item in files:
        if item.is_symlink() or not item.is_file():
            raise ValueError("symlink_or_nonfile_input_forbidden")
        sz = item.stat().st_size
        if sz > 50 * 1024 * 1024:
            raise ValueError("file_exceeds_50mb")
        total += sz
        if total > max_total_bytes:
            raise ValueError("aggregate_input_too_large")
    return len(files), total


def _address_text(address: Any) -> str:
    return str(address.label() if address else "").strip()


def _identity(message: Any, source: Path) -> str:
    """Stable within a fixed source naming scheme. Message-ID preferred."""
    mid = str(message.meta.message_id or "").strip().strip("<>")
    if mid:
        return "message-id:" + mid
    origin = str(message.source)
    if source.is_dir():
        try:
            origin = str(Path(origin).relative_to(source))
        except ValueError:
            origin = Path(origin).name
    else:
        origin = Path(str(message.source).split(":", 1)[0]).name + ":" + str(message.key)
    return "source:" + origin.replace("\\", "/")


def _canonical_rows(result: Any, tenant: str, source: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from rag_core import mail_ingest as mail

    originals = [m for m in result.messages if not m.restored]
    identities = {m.key: _identity(m, source) for m in originals}
    source_doc_for = {key: "mail:" + sha(tenant + "\n" + val)[:32] for key, val in identities.items()}

    # 'root.key' is a batch-local thread identifier, not a persistent global ID.
    conv = mail.conversation_rows(result.messages)
    local_threads = {r["key"]: str(r["thread_id"]) for r in conv}
    quotes_by_key: dict[str, list[str]] = {}
    for seg in result.segments:
        if seg.get("kind") != "quoted" or seg.get("status") not in {"restored", "empty"}:
            continue
        quote = str(seg.get("text") or "").strip()
        if quote:
            quotes_by_key.setdefault(str(seg.get("key") or ""), []).append(quote)
    rows: list[dict[str, Any]] = []
    seen_docs: dict[str, str] = {}
    duplicate_identical = 0
    skipped_empty = 0
    for m in originals:
        own_body = str(m.text or "").strip()
        quoted_parts: list[str] = []
        if m.context:
            quoted_parts.append("インライン引用文脈（原本メール内）:\n" + str(m.context))
        if m.quoted_extra:
            quoted_parts.append("引用の追加情報（原本メール内）:\n" + str(m.quoted_extra))
        if quotes_by_key.get(m.key):
            quoted_parts.append("未照合の引用（原本メール内。原著者は未検証）:\n" + "\n".join(quotes_by_key[m.key]))
        body = "\n\n".join([v for v in [own_body, *quoted_parts] if v]).strip()
        if not body:
            skipped_empty += 1
            continue
        doc = source_doc_for[m.key]
        digest = sha("\n".join([str(m.meta.message_id or ""), str(m.meta.subject or ""),
                                   str(m.meta.date_iso or m.meta.date_raw or ""),
                                   _address_text(m.meta.sender), body]))
        if doc in seen_docs:
            if seen_docs[doc] != digest:
                raise ValueError("conflicting_message_identity")
            duplicate_identical += 1
            continue
        seen_docs[doc] = digest

        subject = str(m.meta.subject or "").strip()
        sender = _address_text(m.meta.sender)
        date = str(m.meta.date_iso or m.meta.date_raw or "").strip()
        parent = source_doc_for.get(m.parent, "")
        root_key = local_threads.get(m.key, m.key)
        # A root inferred from a quote is not a guaranteed stable email thread.
        root_identity = identities.get(root_key, "restored-root:" + root_key)
        thread_hint = "thread-batch:" + sha(tenant + "\n" + root_identity)[:32]
        heading = "\n".join(filter(None, [
            f"件名: {subject}" if subject else "",
            f"差出人: {sender}" if sender else "",
            f"送信日時: {date}" if date else "",
        ]))
        searchable = "\n".join(p for p in [heading, body] if p)
        # The source identifier prevents a legitimate repeated short reply
        # (e.g. 了解しました) from becoming a duplicate-text manifest collision.
        evidence = "\n".join([f"原本識別子: {doc}", searchable])
        rows.append({
            "id": f"{tenant}:email:{sha(doc)[:24]}:h{digest[:12]}",
            "text": evidence,
            "source_doc": doc,
            "doc_id": doc,
            "source_pages": [-1],
            "chunk_index": 1,
            "searchable": 1,
            "type": "email",
            "source_type": "email",
            "quality": "standard",
            "doc_type": "email",
            "chunk_type": "mail_body",
            "chunk_role": "child",
            "parent_chunk_id": "",
            "parser": "mail-ingest-v2",
            "title": subject or doc,
            "section_path": [],
            "language": "ja",
            "tenant_id": tenant,
            "display_text": body,
            "searchable_text": searchable,
            "has_unverified_quote": bool(quotes_by_key.get(m.key)),
            "message_id": str(m.meta.message_id or "").strip().strip("<>"),
            "thread_hint": thread_hint,
            "thread_id_scope": "batch_only",
            "parent_source_doc": parent,
            "parent_basis": str(m.parent_basis or ""),
            "sender": sender,
            "date": date,
            "subject": subject,
            "restored": False,
            "content_sha256": digest,
        })
    if not rows:
        raise ValueError("no_indexable_original_messages")
    # A missing parent in current batch is preserved as an unresolved link,
    # never silently converted to a proven relationship.
    return rows, {
        "original_messages": len(originals),
        "restored_excluded": len(result.messages) - len(originals),
        "duplicate_identical_excluded": duplicate_identical,
        "empty_excluded": skipped_empty,
        "canonical_rows": len(rows),
        "threads_in_batch": len(set(local_threads.values())),
    }


def _write_private_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("output_already_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".mail-canonical-", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Fail closed if a different process created the target meanwhile.
        try:
            os.link(temp_name, path)  # no overwrites; atomic creation on same FS
        except FileExistsError as exc:
            raise ValueError("output_already_exists") from exc
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mail to canonical JSONL (no vectorstore mutation)")
    parser.add_argument("--input", required=True, help="EML directory, .eml, email table .csv/.tsv/.xlsx/.xlsm")
    parser.add_argument("--out", required=True, help="NEW private .jsonl path; existing files are never overwritten")
    parser.add_argument("--tenant", required=True, help="Tenant ID; separate mail sources by tenant")
    parser.add_argument("--sheet", default="", help="Spreadsheet worksheet (default first sheet)")
    parser.add_argument("--col", action="append", default=[], help="Column override, e.g. text=本文")
    parser.add_argument("--max-files", type=int, default=500)
    parser.add_argument("--max-total-mb", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.tenant):
            raise ValueError("invalid_tenant")
        if args.max_files < 1 or args.max_total_mb < 1:
            raise ValueError("invalid_limits")
        source = Path(args.input).expanduser().absolute()
        output = Path(args.out).expanduser().absolute()
        if not output.name.endswith(".jsonl"):
            raise ValueError("output_must_be_jsonl")
        if output == source or (source.is_dir() and output.is_relative_to(source)):
            raise ValueError("output_inside_input")
        num_files, num_bytes = _input_files(source, args.max_files, args.max_total_mb * 1024 * 1024)
        from rag_core import mail_ingest as mail
        failures: list[dict[str, Any]] = []
        overrides = {}
        for item in args.col:
            if "=" not in item:
                raise ValueError("column_override_must_use_key_equals_header")
            key, value = item.split("=", 1)
            if key not in mail.COLUMN_ALIASES or not value.strip():
                raise ValueError("invalid_column_override")
            overrides[key] = value
        raws = mail.iter_inputs(source, overrides, sheet=args.sheet, failures=failures)
        if failures:
            raise ValueError("read_failure")
        result = mail.process(
            raws,
            model=None,
            profile=mail.PROFILES["evidence"],
            threshold=0.9,
            smooth=0.0,
            sentence_split=False,
            match_threshold=0.6,
            jobs=1,
            failures=failures,
        )
        if result.failures:
            raise ValueError("mail_processing_failure")
        rows, counts = _canonical_rows(result, args.tenant, source)
        _write_private_jsonl(output, rows)
        reread = [json.loads(x) for x in output.read_text(encoding="utf-8").splitlines() if x.strip()]
        if len(reread) != len(rows) or [r["id"] for r in reread] != [r["id"] for r in rows]:
            raise ValueError("artifact_readback_mismatch")
        print(json.dumps({"status":"PASS", "input_files":num_files, "input_bytes":num_bytes,
                          "output":str(output), "readback":"PASS", **counts}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(json.dumps({"status":"BLOCKED", "reason":str(exc)[:120]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
