from __future__ import annotations

# Smallest safe local conversation-history boundary (Prompt042).
#
# Per-(tenant, identity)-isolated thread storage as JSONL files under
# RUNS_DIR/conversations/.v2/<tenant digest>/<identity digest>/<thread digest>.jsonl. Isolation is
# enforced structurally by the path (tenant + identity fingerprint are part of
# the directory), and every read is scoped to the caller's (tenant, identity).
#
# Does NOT reuse the vector store. Stores only the caller's own question/answer
# text (scoped to them) plus safe metadata — never API keys, SSO secrets, trust
# tokens, or another tenant's data. Default-off as a runtime surface; the chat
# path is unchanged unless CONVERSATION_HISTORY_ENABLED is set.

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


_ENABLED_VALUES = {"1", "true", "yes", "on"}
_DEFAULT_MAX_THREADS = 50
_DEFAULT_MAX_AGE_DAYS = 90
_MAX_TURNS_PER_THREAD = 500
_MAX_TEXT_CHARS = 8000
_MAX_SOURCE_DOCS = 20
REDACTED_NOTICE = "（削除された文書に基づく回答のため非表示）"


def _retention_days() -> int:
    # CONVERSATION_RETENTION_DAYS: 0 (default) keeps turns; N>0 drops turns older than N days. This is the only
    # cleanup for legacy turns that have no recorded source_docs.
    try:
        return max(0, int(os.getenv("CONVERSATION_RETENTION_DAYS", "") or 0))
    except ValueError:
        return 0


def _clean_source_docs(source_docs: Any) -> List[str]:
    out: List[str] = []
    if source_docs is None:
        return out
    if not isinstance(source_docs, (list, tuple)):
        raise ValueError("invalid source_docs")
    for doc in source_docs:
        if not isinstance(doc, str):
            raise ValueError("invalid source_docs")
        text = doc.strip()
        if not text or len(text) > 512:
            raise ValueError("invalid source_docs")
        if text not in out:
            out.append(text)
        if len(out) > _MAX_SOURCE_DOCS:
            raise ValueError("too many source_docs")
    return out


def conversation_history_enabled() -> bool:
    return str(os.getenv("CONVERSATION_HISTORY_ENABLED", "")).strip().lower() in _ENABLED_VALUES


def _max_threads() -> int:
    try:
        v = int(os.getenv("CONVERSATION_HISTORY_MAX_THREADS", "") or _DEFAULT_MAX_THREADS)
        return v if v > 0 else _DEFAULT_MAX_THREADS
    except ValueError:
        return _DEFAULT_MAX_THREADS


def _max_age_days() -> int:
    try:
        v = int(os.getenv("CONVERSATION_HISTORY_MAX_AGE_DAYS", "") or _DEFAULT_MAX_AGE_DAYS)
        return v if v > 0 else _DEFAULT_MAX_AGE_DAYS
    except ValueError:
        return _DEFAULT_MAX_AGE_DAYS


def _root() -> Path:
    return Path(config.RUNS_DIR) / "conversations"


def _storage_key(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid conversation storage identifier")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tenant_dir(tenant_id: str) -> Path:
    return _root() / ".v2" / _storage_key(tenant_id)


def _owner_dir(tenant_id: str, identity: str) -> Path:
    return _tenant_dir(tenant_id) / _storage_key(identity)


def _thread_path(tenant_id: str, identity: str, thread_id: str) -> Path:
    return _owner_dir(tenant_id, identity) / f"{_storage_key(thread_id)}.jsonl"


@contextmanager
def _tenant_lock(tenant_dir: Path):
    """Serialize JSONL mutations, including retention and redaction across owners."""
    from rag_core.index_generation import flock_exclusive

    tenant_dir.mkdir(parents=True, exist_ok=True)
    with (tenant_dir / ".history.lock").open("a+b") as handle:
        flock_exclusive(handle, what="conversation history")
        yield


def _clock() -> float:
    return time.time()


def append_turn(
    tenant_id: str,
    identity: str,
    thread_id: str,
    *,
    question: str,
    answer_text: str = "",
    answer_mode: Optional[str] = None,
    abstained: bool = False,
    citations_count: int = 0,
    source_docs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # Persist one Q/A turn scoped to (tenant, identity). Only the caller's own
    # text is stored; no keys/secrets. Returns the stored record.
    source_docs = _clean_source_docs(source_docs)
    if int(citations_count or 0) > 0 and not source_docs:
        raise ValueError("citations require source_docs")
    if _pg():
        return _pg_append_turn(
            tenant_id, identity, thread_id, question=question, answer_text=answer_text,
            answer_mode=answer_mode, abstained=abstained, citations_count=citations_count,
            source_docs=source_docs,
        )
    path = _thread_path(tenant_id, identity, thread_id)
    record = {
        "ts": _clock(),
        "thread_id": thread_id,
        "question": str(question or "")[:_MAX_TEXT_CHARS],
        "answer_text": str(answer_text or "")[:_MAX_TEXT_CHARS],
        "answer_mode": str(answer_mode) if answer_mode else None,
        "abstained": bool(abstained),
        "citations_count": int(citations_count or 0),
        "source_docs": _clean_source_docs(source_docs),
    }
    with _tenant_lock(path.parent.parent):
        _apply_markers(record, _read_markers(path.parent.parent))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _purge_locked(path.parent, None, None)
    return record


def get_thread(tenant_id: str, identity: str, thread_id: str) -> List[Dict[str, Any]]:
    # Reads are scoped to (tenant, identity) by path construction; another
    # tenant/identity can never reach this owner's directory.
    if _pg():
        return _pg_get_thread(tenant_id, identity, thread_id)
    path = _thread_path(tenant_id, identity, thread_id)
    with _tenant_lock(path.parent.parent):
        markers = _read_markers(path.parent.parent)
        if not path.exists():
            return []
        turns: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("thread_id") == thread_id:
                _apply_markers(rec, markers)
                turns.append(rec)
        return turns[-_MAX_TURNS_PER_THREAD:]


def list_threads(tenant_id: str, identity: str) -> List[Dict[str, Any]]:
    if _pg():
        return _pg_list_threads(tenant_id, identity)
    owner = _owner_dir(tenant_id, identity)
    out: List[Dict[str, Any]] = []
    with _tenant_lock(owner.parent):
        _read_markers(owner.parent)
        for path in owner.glob("*.jsonl"):
            try:
                records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not records or any(not isinstance(r, dict) or not isinstance(r.get("thread_id"), str)
                                      or _storage_key(r["thread_id"]) != path.stem for r in records):
                    continue
                out.append({"thread_id": records[0]["thread_id"], "updated_at": path.stat().st_mtime,
                            "turns": len(records), "title": str(records[0].get("question") or "")[:120]})
            except (OSError, ValueError, TypeError):
                continue
    out.sort(key=lambda t: t["updated_at"], reverse=True)
    return out


def delete_thread(tenant_id: str, identity: str, thread_id: str) -> bool:
    if _pg():
        return _pg_delete_thread(tenant_id, identity, thread_id)
    path = _thread_path(tenant_id, identity, thread_id)
    with _tenant_lock(path.parent.parent):
        try:
            path.unlink()
        except FileNotFoundError:
            # Concurrent delete or purge already removed it.
            return False
    return True


def purge(
    tenant_id: str,
    identity: str,
    *,
    max_threads: Optional[int] = None,
    max_age_days: Optional[int] = None,
) -> int:
    # Retention: drop threads older than max_age_days, then keep only the most
    # recent max_threads. Returns the number of threads removed.
    if _pg():
        return _pg_purge(tenant_id, identity, max_threads=max_threads, max_age_days=max_age_days)
    owner = _owner_dir(tenant_id, identity)
    with _tenant_lock(owner.parent):
        return _purge_locked(owner, max_threads, max_age_days)


def _purge_locked(owner: Path, max_threads: Optional[int], max_age_days: Optional[int]) -> int:
    cap = max_threads if max_threads is not None else _max_threads()
    age_days = max_age_days if max_age_days is not None else _max_age_days()
    cutoff = _clock() - age_days * 86400
    removed = 0
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return -1.0

    def _remove(p: Path) -> int:
        try:
            p.unlink()
        except FileNotFoundError:
            return 0
        return 1

    # Files can disappear between listing and removal (concurrent delete);
    # that is not an error.
    stamped = [(p, _mtime(p)) for p in owner.glob("*.jsonl")]
    stamped = sorted((item for item in stamped if item[1] >= 0), key=lambda item: item[1], reverse=True)
    kept: List[Path] = []
    for path, mtime in stamped:
        if mtime < cutoff:
            removed += _remove(path)
        else:
            kept.append(path)
    for path in kept[cap:]:
        removed += _remove(path)
    return removed + _prune_old_turns(owner)


def _rewrite_lines(path: Path, lines: List[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _prune_old_turns(owner: Path) -> int:
    # CONVERSATION_RETENTION_DAYS: drop individual turns older than the limit (0 = keep). Returns turns removed.
    days = _retention_days()
    if days <= 0 or not owner.exists():
        return 0
    cutoff = _clock() - days * 86400
    dropped = 0
    for path in owner.glob("*.jsonl"):
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            continue
        keep: List[str] = []
        for line in lines:
            try:
                ts = float(json.loads(line).get("ts") or 0)
            except (ValueError, TypeError, AttributeError):
                ts = 0.0
            if ts and ts < cutoff:
                dropped += 1
            else:
                keep.append(line)
        if len(keep) == len(lines):
            continue
        try:
            if keep:
                _rewrite_lines(path, keep)
            else:
                path.unlink()
        except FileNotFoundError:
            continue
    return dropped


def _read_markers(tenant_dir: Path) -> set[str]:
    path = tenant_dir / ".redactions.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    data = json.loads(raw)
    if not isinstance(data, list) or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in data):
        raise ValueError("invalid conversation redaction markers")
    return set(data)


def _write_markers(tenant_dir: Path, markers: set[str]) -> None:
    _rewrite_lines(tenant_dir / ".redactions.json", [json.dumps(sorted(markers))])
    # Persist both the marker rename and newly created namespace directories.
    for directory in (tenant_dir, tenant_dir.parent, _root(), _root().parent, _root().parent.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _apply_markers(record: Dict[str, Any], markers: set[str]) -> None:
    docs = record.get("source_docs", [])
    if not isinstance(docs, list) or any(not isinstance(doc, str) or not doc for doc in docs):
        raise ValueError("invalid conversation source metadata")
    if any(_storage_key(doc) in markers for doc in docs):
        record["answer_text"] = REDACTED_NOTICE
        record["redacted"] = True


def redact_source_doc(tenant_id: str, source_doc: str) -> int:
    """Hide the answer text of every turn (all identities of the tenant) that cited ``source_doc``.

    Returns the number of turns redacted. Turns recorded before source_docs existed cannot be matched;
    CONVERSATION_RETENTION_DAYS bounds those."""
    doc = str(source_doc or "").strip()
    if not doc:
        return 0
    if _pg():
        return _pg_redact_source_doc(tenant_id, doc)
    tenant_dir = _tenant_dir(tenant_id)
    with _tenant_lock(tenant_dir):
        markers = _read_markers(tenant_dir)
        markers.add(_storage_key(doc))
        _write_markers(tenant_dir, markers)
        return _redact_locked(tenant_dir, doc)


def _redact_locked(tenant_dir: Path, doc: str) -> int:
    redacted = 0
    for path in tenant_dir.glob("*/*.jsonl"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        changed = 0
        out: List[str] = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            if isinstance(rec, dict) and not rec.get("redacted") and doc in (rec.get("source_docs") or []):
                rec["answer_text"] = REDACTED_NOTICE
                rec["redacted"] = True
                changed += 1
                line = json.dumps(rec, ensure_ascii=False)
            out.append(line)
        if changed:
            _rewrite_lines(path, out)
            redacted += changed
    return redacted


# --- Postgres backend (CONVERSATION_STORE_BACKEND=postgres, several nodes) -----

def _pg() -> bool:
    return str(os.getenv("CONVERSATION_STORE_BACKEND", "local")).strip().lower() == "postgres"


def _pg_conn():
    from webapi import state_backend

    return state_backend.postgres()


def _pg_lock_key(scope: str) -> int:
    return int.from_bytes(hashlib.sha256(scope.encode("utf-8")).digest()[:8], "big", signed=True)


def _pg_schema_exists(conn) -> bool:
    return bool(conn.execute(
        "SELECT to_regclass('conversation_turns') IS NOT NULL"
        " AND to_regclass('conversation_turns_owner') IS NOT NULL"
        " AND to_regclass('conversation_redactions_v2') IS NOT NULL"
    ).fetchone()[0])


def _pg_ensure(conn) -> None:
    # No process-global readiness flag: DDL may roll back, or a different
    # connection may target a different database/schema.
    if _pg_schema_exists(conn):
        return
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_pg_lock_key("conversation-schema-v2"),))
    if _pg_schema_exists(conn):
        return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversation_turns ("
        " id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, identity TEXT NOT NULL,"
        " thread_id TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL, record JSONB NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS conversation_turns_owner ON conversation_turns (tenant_id, identity, thread_id, ts)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversation_redactions_v2 ("
        " tenant_id TEXT NOT NULL, source_key TEXT NOT NULL, PRIMARY KEY (tenant_id, source_key))"
    )


@contextmanager
def _pg_transaction(tenant: str):
    # Commit schema creation separately, before acquiring a tenant lock. A
    # failed transaction cannot cache a false-positive schema-ready state.
    with _pg_conn() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        _pg_ensure(conn)
    with _pg_conn() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        # state_backend supplies a bounded statement_timeout. Read committed
        # ensures the statement after the lock observes the previous commit.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_pg_lock_key("conversation-tenant:" + tenant),))
        yield conn


def _pg_markers(conn, tenant: str) -> set[str]:
    markers = {row[0] for row in conn.execute(
        "SELECT source_key FROM conversation_redactions_v2 WHERE tenant_id = %s", (tenant,)
    ).fetchall()}
    if any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) for key in markers):
        raise ValueError("invalid conversation redaction markers")
    return markers


def _owner_key(tenant_id: str, identity: str) -> tuple:
    return "v2:" + _storage_key(tenant_id), _storage_key(identity)


def _pg_append_turn(tenant_id, identity, thread_id, *, question, answer_text, answer_mode, abstained, citations_count,
                    source_docs=None):
    tenant, owner = _owner_key(tenant_id, identity)
    record = {
        "ts": _clock(),
        "thread_id": thread_id,
        "question": str(question or "")[:_MAX_TEXT_CHARS],
        "answer_text": str(answer_text or "")[:_MAX_TEXT_CHARS],
        "answer_mode": str(answer_mode) if answer_mode else None,
        "abstained": bool(abstained),
        "citations_count": int(citations_count or 0),
        "source_docs": _clean_source_docs(source_docs),
    }
    with _pg_transaction(tenant) as conn:
        _apply_markers(record, _pg_markers(conn, tenant))
        conn.execute(
            "INSERT INTO conversation_turns (tenant_id, identity, thread_id, ts, record) VALUES (%s, %s, %s, %s, %s)",
            (tenant, owner, _storage_key(thread_id), record["ts"], json.dumps(record, ensure_ascii=False)),
        )
        _pg_purge_locked(conn, tenant, owner)
    return record


def _pg_redact_source_doc(tenant_id, doc):
    tenant = "v2:" + _storage_key(tenant_id)
    # Commit the visibility boundary first. Even if the physical rewrite
    # fails, subsequent reads and appends must observe the marker.
    with _pg_transaction(tenant) as conn:
        conn.execute(
            "INSERT INTO conversation_redactions_v2 (tenant_id, source_key) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (tenant, _storage_key(doc)),
        )
    with _pg_transaction(tenant) as conn:
        cur = conn.execute(
            "UPDATE conversation_turns SET record = record || %s::jsonb"
            " WHERE tenant_id = %s AND NOT COALESCE((record->>'redacted')::boolean, false)"
            " AND record->'source_docs' ? %s",
            (json.dumps({"answer_text": REDACTED_NOTICE, "redacted": True}, ensure_ascii=False), tenant, doc),
        )
    return int(cur.rowcount or 0)


def _pg_get_thread(tenant_id, identity, thread_id):
    tenant, owner = _owner_key(tenant_id, identity)
    with _pg_transaction(tenant) as conn:
        markers = _pg_markers(conn, tenant)
        rows = conn.execute(
            "SELECT record FROM conversation_turns WHERE tenant_id = %s AND identity = %s AND thread_id = %s"
            " ORDER BY ts DESC, id DESC LIMIT %s",
            (tenant, owner, _storage_key(thread_id), _MAX_TURNS_PER_THREAD),
        ).fetchall()
        turns = []
        for row in reversed(rows):
            record = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            if isinstance(record, dict) and record.get("thread_id") == thread_id:
                _apply_markers(record, markers)
                turns.append(record)
        return turns


def _pg_list_threads(tenant_id, identity):
    tenant, owner = _owner_key(tenant_id, identity)
    with _pg_transaction(tenant) as conn:
        _pg_markers(conn, tenant)
        rows = conn.execute(
            "SELECT thread_id, MAX(ts), COUNT(*), (ARRAY_AGG(record->>'question' ORDER BY ts, id))[1], (ARRAY_AGG(record->>'thread_id' ORDER BY ts, id))[1]"
            " FROM conversation_turns WHERE tenant_id = %s AND identity = %s GROUP BY thread_id ORDER BY MAX(ts) DESC",
            (tenant, owner),
        ).fetchall()
    return [
        {"thread_id": r[4], "updated_at": float(r[1]), "turns": int(r[2]), "title": str(r[3] or "")[:120]} for r in rows if isinstance(r[4], str) and _storage_key(r[4]) == r[0]
    ]


def _pg_delete_thread(tenant_id, identity, thread_id):
    tenant, owner = _owner_key(tenant_id, identity)
    with _pg_transaction(tenant) as conn:
        cur = conn.execute(
            "DELETE FROM conversation_turns WHERE tenant_id = %s AND identity = %s AND thread_id = %s",
            (tenant, owner, _storage_key(thread_id)),
        )
    return bool(cur.rowcount)


def _pg_purge(tenant_id, identity, *, max_threads=None, max_age_days=None):
    tenant, owner = _owner_key(tenant_id, identity)
    with _pg_transaction(tenant) as conn:
        return _pg_purge_locked(conn, tenant, owner, max_threads=max_threads, max_age_days=max_age_days)


def _pg_purge_locked(conn, tenant, owner, *, max_threads=None, max_age_days=None):
    cap = max_threads if max_threads is not None else _max_threads()
    age_days = max_age_days if max_age_days is not None else _max_age_days()
    cutoff = _clock() - age_days * 86400
    old = conn.execute(
        "DELETE FROM conversation_turns WHERE tenant_id = %s AND identity = %s AND thread_id IN ("
        " SELECT thread_id FROM conversation_turns WHERE tenant_id = %s AND identity = %s"
        " GROUP BY thread_id HAVING MAX(ts) < %s)",
        (tenant, owner, tenant, owner, cutoff),
    ).rowcount
    extra = conn.execute(
        "DELETE FROM conversation_turns WHERE tenant_id = %s AND identity = %s AND thread_id IN ("
        " SELECT thread_id FROM conversation_turns WHERE tenant_id = %s AND identity = %s"
        " GROUP BY thread_id ORDER BY MAX(ts) DESC, MAX(id) DESC OFFSET %s)",
        (tenant, owner, tenant, owner, cap),
    ).rowcount
    aged = 0
    days = _retention_days()
    if days > 0:
        aged = conn.execute(
            "DELETE FROM conversation_turns WHERE tenant_id = %s AND identity = %s AND ts < %s",
            (tenant, owner, _clock() - days * 86400),
        ).rowcount
    return int(old or 0) + int(extra or 0) + int(aged or 0)
