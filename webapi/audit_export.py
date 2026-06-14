from __future__ import annotations

# Safe security/compliance audit export (Prompt062).
#
# Audit JSONL contains user question text (content). A security/compliance export
# must NOT leak that content, API keys, identity, or document text. This exporter
# produces a REDACTED AGGREGATE view: counts grouped by (date, tenant_id, kind,
# answer_mode, guard_reason, ...) using a strict field allowlist. Raw question
# text and any non-allowlisted field are dropped. No secrets are read or emitted.

from collections import Counter
from typing import Any, Dict, Iterable, List

# Only these fields ever leave the exporter (all enum-like / counts / ids).
_GROUP_FIELDS = ("date", "tenant_id", "kind", "answer_mode", "guard_reason",
                 "used_fallback", "error_type", "cache_hit", "streamed")


def _date(ts: Any) -> str:
    s = str(ts or "")
    return s[:10] if len(s) >= 10 else "unknown"


def _safe_value(key: str, event: Dict[str, Any]) -> Any:
    if key == "date":
        return _date(event.get("timestamp"))
    v = event.get(key)
    if v is None:
        return None
    # Coerce to short stable strings; never emit free text.
    if isinstance(v, bool):
        return v
    return str(v)[:64]


def safe_export(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Returns aggregate rows: one per distinct group key, with a count and the
    # mean citations_count. Raw question/document text is never included.
    groups: Counter = Counter()
    citations: Dict[tuple, List[int]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        key = tuple(_safe_value(f, event) for f in _GROUP_FIELDS)
        groups[key] += 1
        try:
            cc = int(event.get("citations_count") or 0)
        except (TypeError, ValueError):
            cc = 0
        citations.setdefault(key, []).append(cc)
    rows: List[Dict[str, Any]] = []
    for key, count in sorted(groups.items(), key=lambda kv: str(kv[0])):
        row = dict(zip(_GROUP_FIELDS, key))
        row["count"] = count
        ccs = citations.get(key, [])
        row["avg_citations"] = round(sum(ccs) / len(ccs), 2) if ccs else 0.0
        rows.append(row)
    return rows


def export_from_lines(lines: Iterable[str]) -> List[Dict[str, Any]]:
    import json

    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return safe_export(events)
