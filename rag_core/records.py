"""Records of questions and answers, for evaluation and for learning from use (S3).

Who asked (a pseudonym, never a name), what, what the system answered on what evidence, and later whether it was right.
These records feed the golden questions and the approved answers; they are not a log of what people do.

The record level is one setting that decides what is kept:

  off        nothing is kept (the counters of the dashboard still count)
  anonymous  the shape of the exchange only: answer mode, guard reason, counts, time. The asker is a salted hash. No text.
  standard   + the asker's pseudonym (the key fingerprint, one per person with single sign-on), the question, the answer text and
             its sources. E-mail addresses are masked; digits are kept (a clause number or an amount is what an evaluation needs).
  detailed   + the address and browser of the request. Kept for a shorter time (RECORDS_DETAILED_RETENTION_DAYS).

The level of a tenant, else the global one, else RECORD_LEVEL_DEFAULT (off), and never above RECORD_LEVEL_MAX, which only the
environment sets. Records are files of JSON lines, one file a day (RECORDS_DIR, default <RUNS_DIR>/records). A person's judgement
(correct, partly, wrong, and why) is a separate line in reviews.jsonl, so it survives the anonymizing of the record it is about.
Nothing is purged by itself: `purge` runs when an administrator or a scheduled job asks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import config
from rag_core import audit_log, runtime_settings

LEVELS = ("off", "anonymous", "standard", "detailed")
VERDICTS = ("correct", "partial", "incorrect")
REASONS = ("wrong_source", "outdated", "hallucination", "missing_info", "too_vague", "other")
EXPIRY_ACTIONS = ("anonymize", "delete")
LEVEL_KEY = "records.level"
MAX_TEXT = 4000
MAX_CITATIONS = 10
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PERSONAL_FIELDS = ("question", "answer_text", "citations", "user", "ip", "user_agent")
_DEVICE_FIELDS = ("ip", "user_agent")


class RecordError(ValueError):
    """The request is not valid (a level, a verdict, an id)."""


# --- the level -------------------------------------------------------------------------------------------------------------------


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def level_cap() -> str:
    cap = str(getattr(config, "RECORD_LEVEL_MAX", "standard") or "standard").strip().lower()
    return cap if cap in LEVELS else "standard"


def default_level() -> str:
    level = str(getattr(config, "RECORD_LEVEL_DEFAULT", "off") or "off").strip().lower()
    return level if level in LEVELS else "off"


def requested_level(tenant_id: Optional[str] = None) -> str:
    value = runtime_settings.get(LEVEL_KEY, None, tenant_id=tenant_id)
    return value if value in LEVELS else default_level()


def effective_level(tenant_id: Optional[str] = None) -> str:
    """The level that applies: what was asked for, but never above the environment's ceiling."""
    asked = requested_level(tenant_id)
    return asked if _rank(asked) <= _rank(level_cap()) else level_cap()


def set_level(level: Optional[str], *, by: str, reason: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """Set the level (None: back to the global one or the default). A level above the ceiling is refused."""
    if level is not None:
        if level not in LEVELS:
            raise RecordError(f"the level is one of: {', '.join(LEVELS)}")
        if _rank(level) > _rank(level_cap()):
            raise RecordError(f"the level '{level}' is above RECORD_LEVEL_MAX ({level_cap()}); only the environment can raise it")
    return runtime_settings.set_value(LEVEL_KEY, level, by=by, reason=reason, tenant_id=tenant_id)


def level_overview(tenants: Iterable[str] = ()) -> Dict[str, Any]:
    data = runtime_settings.load()
    overrides = {t: v[LEVEL_KEY]["value"] for t, v in data["tenants"].items() if LEVEL_KEY in v}
    return {"default": default_level(), "ceiling": level_cap(), "global": requested_level(), "effective": effective_level(),
            "tenants": {t: {"requested": requested_level(t), "effective": effective_level(t)} for t in sorted(set(overrides) | set(tenants))}}


# --- storage ---------------------------------------------------------------------------------------------------------------------


def records_dir() -> Path:
    configured = str(getattr(config, "RECORDS_DIR", "") or "").strip()
    return Path(configured) if configured else Path(config.RUNS_DIR) / "records"


def _day_file(day: date) -> Path:
    return records_dir() / f"records-{day.isoformat()}.jsonl"


def _reviews_file() -> Path:
    return records_dir() / "reviews.jsonl"


def _append(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
    with audit_log._FileLock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def _salt() -> str:
    configured = str(getattr(config, "RECORDS_HASH_SALT", "") or "").strip()
    if configured:
        return configured
    path = records_dir() / ".salt"
    with audit_log._FileLock(path):
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            path.parent.mkdir(parents=True, exist_ok=True)
            salt = secrets.token_hex(32)
            path.write_text(salt, encoding="utf-8")
            return salt


def pseudonym(value: Optional[str]) -> Optional[str]:
    """A salted hash: the same person gives the same value, and the value does not lead back to the person."""
    if not value:
        return None
    return "h:" + hashlib.sha256((_salt() + str(value)).encode("utf-8")).hexdigest()[:16]


def mask_emails(text: str) -> str:
    return _EMAIL_RE.sub("[email]", text)


def _text(value: Any) -> str:
    return mask_emails(str(value or ""))[:MAX_TEXT]


def _citations(items: Any) -> List[Dict[str, Any]]:
    out = []
    for item in list(items or [])[:MAX_CITATIONS]:
        if isinstance(item, Mapping):
            out.append({k: item.get(k) for k in ("source_doc", "page", "chunk_id", "status") if item.get(k) is not None})
    return out


def record_interaction(
    *, tenant_id: str, request_id: Optional[str] = None, trace_id: Optional[str] = None, user: Optional[str] = None,
    question: str = "", answer_text: str = "", answer_mode: Optional[str] = None, guard_reason: Optional[str] = None,
    used_fallback: bool = False, citations: Any = None, latency_ms: Optional[int] = None, generation_mode: Optional[str] = None,
    degraded: bool = False, intent: Optional[str] = None, question_type: Optional[str] = None, source: str = "chat",
    client_ip: Optional[str] = None, user_agent: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Keep one exchange as the tenant's record level says. Returns the record, or None when the level is off."""
    level = effective_level(tenant_id)
    if level == "off":
        return None
    stamp = now or datetime.now(timezone.utc)
    cited = _citations(citations)
    record: Dict[str, Any] = {
        "id": "rec-" + uuid.uuid4().hex[:20], "t": stamp.isoformat(), "tenant_id": tenant_id, "level": level, "source": source,
        "request_id": request_id, "trace_id": trace_id, "answer_mode": answer_mode, "guard_reason": guard_reason,
        "used_fallback": bool(used_fallback), "citations_count": len(list(citations or [])), "latency_ms": latency_ms,
        "generation_mode": generation_mode, "degraded": bool(degraded), "intent": intent, "question_type": question_type,
    }
    if level == "anonymous":
        record["user"] = pseudonym(user)
    else:
        record.update(user=user, question=_text(question), answer_text=_text(answer_text), citations=cited)
    if level == "detailed":
        record.update(ip=(client_ip or "")[:64] or None, user_agent=(user_agent or "")[:200] or None)
    _append(_day_file(stamp.date()), record)
    return record


# --- reading ---------------------------------------------------------------------------------------------------------------------


def _read_file(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def iter_records(days: int = 7, *, today: Optional[date] = None) -> Iterable[Dict[str, Any]]:
    """The records of the last `days` days (today included), oldest first."""
    last = today or datetime.now(timezone.utc).date()
    for offset in range(max(1, int(days)) - 1, -1, -1):
        yield from _read_file(_day_file(last - timedelta(days=offset)))


def latest_reviews() -> Dict[str, Dict[str, Any]]:
    """The newest judgement of each record, by record id."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_file(_reviews_file()):
        if row.get("record_id"):
            out[str(row["record_id"])] = row
    return out


def feedback_by_token(*, limit: int = 50000) -> Dict[str, Dict[str, Any]]:
    """The newest feedback of each token (the request id or trace id the user's screen sent), from the audit log."""
    base = Path(config.RUNS_DIR) / "audit" / "feedback_events.jsonl"
    files = sorted(base.parent.glob(f"{base.stem}.*{base.suffix}")) + ([base] if base.exists() else [])
    out: Dict[str, Dict[str, Any]] = {}
    seen = 0
    for path in files:
        for row in _read_file(path):
            seen += 1
            if seen > limit:
                return out
            for key in ("feedback_token", "request_id", "trace_id"):
                if row.get(key):
                    out[str(row[key])] = {"type": row.get("feedback_type"), "bad_reason": row.get("bad_reason"),
                                          "comment": row.get("comment"), "t": row.get("timestamp")}
    return out


def _joined(record: Dict[str, Any], reviews: Mapping[str, Any], feedback: Mapping[str, Any]) -> Dict[str, Any]:
    fb = next((feedback[str(record[k])] for k in ("request_id", "trace_id") if record.get(k) and str(record[k]) in feedback), None)
    review = reviews.get(record["id"])
    return {**record, "feedback": fb, "review": ({k: review.get(k) for k in ("verdict", "reasons", "note", "expected_answer", "reviewer", "at")}
                                                  if review else None)}


def list_records(
    *, days: int = 7, tenants: Optional[Iterable[str]] = None, tenant_id: Optional[str] = None, answer_mode: Optional[str] = None,
    verdict: Optional[str] = None, feedback: Optional[str] = None, reviewed: Optional[bool] = None, abstained: Optional[bool] = None,
    text: Optional[str] = None, limit: int = 50, offset: int = 0, today: Optional[date] = None,
) -> Dict[str, Any]:
    """Records, newest first, with the feedback and the judgement joined in. `tenants` limits to an administrator's tenants."""
    allowed = set(tenants) if tenants is not None else None
    reviews, fb = latest_reviews(), feedback_by_token()
    wanted = (text or "").strip().lower()
    matched: List[Dict[str, Any]] = []
    for record in iter_records(days, today=today):
        if allowed is not None and record.get("tenant_id") not in allowed:
            continue
        if tenant_id and record.get("tenant_id") != tenant_id:
            continue
        if answer_mode and record.get("answer_mode") != answer_mode:
            continue
        row = _joined(record, reviews, fb)
        if verdict and (row["review"] or {}).get("verdict") != verdict:
            continue
        if feedback and (row["feedback"] or {}).get("type") != feedback:
            continue
        if reviewed is not None and bool(row["review"]) != reviewed:
            continue
        if abstained is not None and bool(record.get("guard_reason")) != abstained:
            continue
        if wanted and wanted not in (str(record.get("question") or "") + " " + str(record.get("answer_text") or "")).lower():
            continue
        matched.append(row)
    matched.reverse()
    start = max(0, int(offset))
    return {"total": len(matched), "offset": start, "records": matched[start: start + max(1, min(int(limit), 200))]}


def get_record(record_id: str, *, days: int = 400, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    if not _ID_RE.fullmatch(str(record_id or "")):
        return None
    for record in iter_records(days, today=today):
        if record.get("id") == record_id:
            return _joined(record, latest_reviews(), feedback_by_token())
    return None


def review(record_id: str, *, verdict: str, reviewer: str, reasons: Iterable[str] = (), note: str = "",
           expected_answer: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Keep a person's judgement of a record (the newest one counts). The record must exist."""
    if verdict not in VERDICTS:
        raise RecordError(f"the verdict is one of: {', '.join(VERDICTS)}")
    reason_list = [r for r in reasons if r]
    if any(r not in REASONS for r in reason_list):
        raise RecordError(f"a reason is one of: {', '.join(REASONS)}")
    if verdict != "correct" and not reason_list and not note.strip():
        raise RecordError("a verdict other than 'correct' needs a reason or a note")
    if get_record(record_id) is None:
        raise RecordError("the record does not exist (or has been purged)")
    row = {"record_id": record_id, "verdict": verdict, "reasons": reason_list, "note": _text(note)[:1000],
           "expected_answer": _text(expected_answer)[:2000], "reviewer": str(reviewer)[:100],
           "at": (now or datetime.now(timezone.utc)).isoformat()}
    _append(_reviews_file(), row)
    return row


def stats(days: int = 7, *, tenants: Optional[Iterable[str]] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """Counts for the dashboard: exchanges, answer modes, abstentions, degraded answers, feedback, judgements."""
    allowed = set(tenants) if tenants is not None else None
    reviews, fb = latest_reviews(), feedback_by_token()
    out: Dict[str, Any] = {"days": days, "total": 0, "answer_modes": {}, "abstained": 0, "degraded": 0, "feedback": {}, "verdicts": {},
                           "unreviewed_with_negative_feedback": 0}
    for record in iter_records(days, today=today):
        if allowed is not None and record.get("tenant_id") not in allowed:
            continue
        row = _joined(record, reviews, fb)
        out["total"] += 1
        mode = str(record.get("answer_mode") or "unknown")
        out["answer_modes"][mode] = out["answer_modes"].get(mode, 0) + 1
        out["abstained"] += 1 if record.get("guard_reason") else 0
        out["degraded"] += 1 if record.get("degraded") else 0
        if row["feedback"]:
            kind = str(row["feedback"].get("type") or "unknown")
            out["feedback"][kind] = out["feedback"].get(kind, 0) + 1
            if kind == "bad" and not row["review"]:
                out["unreviewed_with_negative_feedback"] += 1
        if row["review"]:
            verdict = str(row["review"]["verdict"])
            out["verdicts"][verdict] = out["verdicts"].get(verdict, 0) + 1
    return out


# --- retention -------------------------------------------------------------------------------------------------------------------


def _rewrite(path: Path, rows: List[Dict[str, Any]]) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, default=str, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


def purge(*, now: Optional[datetime] = None, dry_run: bool = True, action: Optional[str] = None) -> Dict[str, Any]:
    """Apply the retention periods to the day files.

    Older than RECORDS_RETENTION_DAYS: anonymized (text, sources and identity removed, the counts stay) or deleted, as
    RECORDS_EXPIRY_ACTION (or `action`) says. Older than RECORDS_DETAILED_RETENTION_DAYS: the address and browser of
    detailed records are removed. A dry run only reports. The judgements (reviews.jsonl) are not touched."""
    chosen = action or str(getattr(config, "RECORDS_EXPIRY_ACTION", "anonymize") or "anonymize").strip().lower()
    if chosen not in EXPIRY_ACTIONS:
        raise RecordError(f"the expiry action is one of: {', '.join(EXPIRY_ACTIONS)}")
    today = (now or datetime.now(timezone.utc)).date()
    keep, keep_detail = int(config.RECORDS_RETENTION_DAYS), int(config.RECORDS_DETAILED_RETENTION_DAYS)
    summary = {"dry_run": dry_run, "action": chosen, "files_deleted": 0, "records_anonymized": 0, "device_info_removed": 0}
    directory = records_dir()
    if not directory.exists():
        return summary
    for path in sorted(directory.glob("records-*.jsonl")):
        try:
            day = date.fromisoformat(path.stem[len("records-"):])
        except ValueError:
            continue
        age = (today - day).days
        rows = list(_read_file(path))
        if age > keep and chosen == "delete":
            summary["files_deleted"] += 1
            if not dry_run:
                path.unlink()
            continue
        changed = False
        for row in rows:
            if age > keep and not row.get("anonymized"):
                for field in _PERSONAL_FIELDS:
                    row.pop(field, None)
                row["anonymized"] = True
                summary["records_anonymized"] += 1
                changed = True
            elif age > keep_detail and any(row.get(f) for f in _DEVICE_FIELDS):
                for field in _DEVICE_FIELDS:
                    row.pop(field, None)
                summary["device_info_removed"] += 1
                changed = True
        if changed and not dry_run:
            _rewrite(path, rows)
    return summary
