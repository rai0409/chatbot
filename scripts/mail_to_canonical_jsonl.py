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
    """Message-ID preferred; otherwise a content-based origin (EML bytes hash,
    table bytes hash + row, or a long table id column). Never a file name alone:
    two different uploads named e.g. mails.csv must not collide."""
    mid = str(message.meta.message_id or "").strip().strip("<>")
    if mid:
        return "message-id:" + mid
    origin_id = str(getattr(message, "origin_id", "") or "")
    if origin_id:
        return "source:" + origin_id
    origin = str(message.source)
    if source.is_dir():
        try:
            origin = str(Path(origin).relative_to(source))
        except ValueError:
            origin = Path(origin).name
    else:
        origin = Path(str(message.source).split(":", 1)[0]).name + ":" + str(message.key)
    return "source:" + origin.replace("\\", "/")


_SKETCH_DEFAULT = 512


def _sketch(text: str, hasher: Any, cap: int) -> tuple[list[str], int]:
    """Bottom-k sketch of the hashed 4-grams of a text (no plaintext)."""
    from rag_core import mail_ingest as mail

    hashed = sorted({hasher(x) for x in mail._shingles(text)})
    return hashed[:cap], len(hashed)


def _scrub(text: str, secrets: set[str]) -> str:
    parts = [re.escape(x) for x in sorted(secrets, key=len, reverse=True) if x]
    if not parts or not text:
        return text
    return re.sub("|".join(parts), "[非表示]", text, flags=re.IGNORECASE)


def _canonical_rows(result: Any, tenant: str, source: Path, *, hasher: Any = None,
                    keep_latest_only: bool = False, sketch_cap: int = _SKETCH_DEFAULT,
                    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows carry private plaintext fields `_acl_addresses` / `_sender_address`
    (and `_mail_state` when a hasher is given). They MUST be replaced by digests
    with rag_core.mail_to_canonical.finalize_acl_rows() before a row is written."""
    from rag_core import mail_ingest as mail

    originals = [m for m in result.messages if not m.restored]
    by_key = {m.key: m for m in result.messages}

    def holder(key: str) -> str:
        m = by_key.get(key)
        while m is not None and m.restored and m.from_key in by_key:
            m = by_key[m.from_key]
        return m.key if m is not None else ""

    bcc_by_key: dict[str, set[str]] = {}
    for m in result.messages:
        owner = holder(m.key) if m.restored else m.key
        bcc_by_key.setdefault(owner, set()).update(
            v for a in m.meta.bcc for v in (a.addr.strip().lower(),) if v)
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
        inline = [t for _status, t in getattr(m, "quoted_inline", []) if str(t).strip()]
        if inline:
            quoted_parts.append("照合済みの引用（原本メール内に保持）:\n" + "\n".join(inline))
        quotes_dropped = bool(keep_latest_only and getattr(m, "superseded_by", "")
                              and getattr(m, "quotes_redundant", False) and own_body and quoted_parts)
        if quotes_dropped:
            # The superseding (later) message holds this quoted text; the new
            # content of this message is never dropped.
            quoted_parts = []
        body = "\n\n".join([v for v in [own_body, *quoted_parts] if v]).strip()
        visible = {a for a in getattr(m, "participants", frozenset())}
        visible |= {a.addr.strip().lower() for a in [m.meta.sender, *m.meta.to, *m.meta.cc] if a.addr}
        bcc_only = bcc_by_key.get(m.key, set()) - visible
        body = _scrub(body, bcc_only)
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

        subject = _scrub(str(m.meta.subject or "").strip(), bcc_only)
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
            "date_iso": str(m.meta.date_iso or ""),
            "superseded_by": "",
            "quote_refs": [],
            "quoted_text_dropped": quotes_dropped,
            "_key": m.key,
            "_superseded_by_key": str(getattr(m, "superseded_by", "") or ""),
            "_quote_ref_keys": list(getattr(m, "quote_refs", []) or []),
            "_acl_addresses": sorted(a for a in getattr(m, "participants", frozenset()) if a),
            "_sender_address": str(m.meta.sender.addr or "").strip().lower(),
        })
        if hasher is not None:
            sh, n = _sketch(own_body, hasher, sketch_cap)
            rows[-1]["_mail_state"] = {
                "sh": sh, "n": n,
                "participants_h": sorted({hasher("addr:" + a) for a in rows[-1]["_acl_addresses"]}),
            }
    if not rows:
        raise ValueError("no_indexable_original_messages")
    # Batch keys -> stable source documents (a restored quote lives in the row
    # of the message that quoted it; a prior import is referenced by its doc).
    for row in rows:
        sup = row.pop("_superseded_by_key")
        row["superseded_by"] = source_doc_for.get(sup, "") if sup else ""
        refs: list[str] = []
        for ref in row.pop("_quote_ref_keys"):
            doc = ref[len("prior:"):] if ref.startswith("prior:") else source_doc_for.get(holder(ref), "")
            if doc and doc != row["source_doc"] and doc not in refs:
                refs.append(doc)
        row["quote_refs"] = refs
        row.pop("_key", None)
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


def _convert_isolated(source: Path, tenant: str, overrides: dict[str, str], sheet: str):
    """Parse in the isolated mail_batch worker (default for every entry point)."""
    from rag_core.document_converters import isolated
    from rag_core.mail_to_canonical import mail_convert_memory_bytes, mail_convert_timeout

    payload = {"paths": [str(source)], "tenant_id": tenant, "mailbox_owner": "", "prior": [], "salt_hex": "",
               "keep_latest_only": False, "overrides": overrides, "sheet": sheet,
               "identity_root": str(source), "validate": False}
    try:
        return isolated.run_task("mail_batch", payload, timeout=mail_convert_timeout() or 300.0,
                                 memory_limit=mail_convert_memory_bytes())
    except isolated.ConversionFailed as exc:
        raise ValueError(exc.error_type if exc.error_type != "ValueError" else str(exc)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mail to canonical JSONL (no vectorstore mutation)")
    parser.add_argument("--input", required=True, help="EML directory, .eml, email table .csv/.tsv/.xlsx/.xlsm")
    parser.add_argument("--out", required=True, help="NEW private .jsonl path; existing files are never overwritten")
    parser.add_argument("--tenant", required=True, help="Tenant ID; separate mail sources by tenant")
    parser.add_argument("--sheet", default="", help="Spreadsheet worksheet (default first sheet)")
    parser.add_argument("--col", action="append", default=[], help="Column override, e.g. text=本文")
    parser.add_argument("--max-files", type=int, default=500)
    parser.add_argument("--max-total-mb", type=int, default=200)
    parser.add_argument("--in-process", action="store_true",
                        help="debugging only: parse in this process instead of the isolated worker "
                             "(time/memory limited, secrets removed from its environment)")
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
        if args.in_process:
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
        else:
            rows, counts = _convert_isolated(source, args.tenant, overrides, args.sheet)
        from rag_core import mail_access
        from rag_core.mail_to_canonical import finalize_acl_rows

        key, key_id = mail_access.acl_key()
        rows = finalize_acl_rows(rows, key, key_id)
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
