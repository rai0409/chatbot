"""Availability of the service in business hours, from the log of an external probe (S7).

The service cannot measure its own downtime: when it is down it writes nothing. So a probe running on ANOTHER host asks `/ready`
at a fixed interval (scripts/external_probe.py) and keeps a line for each answer; this module turns that log into the figure the service
level is written in: "weekdays 08:00-21:00, 99.0%".

How the log is read:
  - a probe answer holds until the next one, but for at most `max_gap_seconds`; time beyond that is "unmonitored" (the probe itself did
    not run) and is counted neither as up nor as down, and is reported, so a dead probe cannot make the figure look good;
  - only business hours count (weekdays, first to last hour of the local day, `utc_offset_hours` from UTC); holidays are not known;
  - a probe answer is "down" when the service did not say ready (HTTP 503, any other answer, no answer); "degraded" (the LLM is unavailable and the
    answers are extractive) is still up, as the service level defines it, and is only counted on the side.

Standard library only: it runs on the probe host with just the repository files.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def business_seconds(start: datetime, end: datetime, *, utc_offset_hours: float = 9.0, first_hour: int = 8, last_hour: int = 21) -> float:
    """Seconds of [start, end) inside the business hours: weekdays, `first_hour` to `last_hour` local time."""
    if end <= start:
        return 0.0
    offset = timedelta(hours=float(utc_offset_hours))
    local_start, local_end = start + offset, end + offset
    day = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    while day < local_end:
        if day.weekday() < 5:
            window_start, window_end = day + timedelta(hours=first_hour), day + timedelta(hours=last_hour)
            overlap = min(window_end, local_end) - max(window_start, local_start)
            total += max(0.0, overlap.total_seconds())
        day += timedelta(days=1)
    return total


def read_probes(path: Any) -> List[Dict[str, Any]]:
    """The probe lines of a log file (and its rotated copy `<file>.1` if there is one), oldest first. Unreadable lines are skipped."""
    rows: List[Dict[str, Any]] = []
    base = Path(path)
    for candidate in (Path(str(base) + ".1"), base):
        if not candidate.exists():
            continue
        with candidate.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    row["_t"] = datetime.fromisoformat(row["t"])
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(row, dict) and "ok" in row:
                    rows.append(row)
    return sorted(rows, key=lambda r: r["_t"])


def _minutes(seconds: float) -> float:
    return round(seconds / 60.0, 1)


def report(
    rows: Iterable[Dict[str, Any]], *, start: datetime, end: datetime, max_gap_seconds: float = 300.0, utc_offset_hours: float = 9.0,
    first_hour: int = 8, last_hour: int = 21, target_percent: float = 99.0,
) -> Dict[str, Any]:
    """Availability in business hours between `start` and `end` (timezone-aware), from probe rows (each with `_t`, `ok`, optional `degraded`)."""
    def bh(a: datetime, b: datetime) -> float:
        return business_seconds(max(a, start), min(b, end), utc_offset_hours=utc_offset_hours, first_hour=first_hour, last_hour=last_hour)

    probes = [r for r in rows if start <= r["_t"] <= end]
    gap = timedelta(seconds=float(max_gap_seconds))
    up = down = degraded = 0.0
    outages: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for i, row in enumerate(probes):
        holds_until = min(probes[i + 1]["_t"] if i + 1 < len(probes) else end, row["_t"] + gap, end)
        span = bh(row["_t"], holds_until)
        if row["ok"]:
            up += span
            degraded += span if row.get("degraded") else 0.0
            if current is not None:  # the outage ends at the first answer that is ready
                current["end"] = row["_t"]
                outages.append(current)
                current = None
        else:
            down += span
            if current is None:
                current = {"start": row["_t"], "end": None, "business_seconds": 0.0, "error": row.get("error") or (f"HTTP {row['status']}" if row.get("status") else "")}
            current["business_seconds"] += span
        next_t = probes[i + 1]["_t"] if i + 1 < len(probes) else end
        if next_t - row["_t"] > gap:
            gaps.append({"start": row["_t"] + gap, "end": next_t, "business_seconds": bh(row["_t"] + gap, next_t)})
    if current is not None:
        current["end"] = None  # still down at the end of the window
        outages.append(current)
    if probes and probes[0]["_t"] > start:
        gaps.insert(0, {"start": start, "end": probes[0]["_t"], "business_seconds": bh(start, probes[0]["_t"])})
    if not probes:
        gaps = [{"start": start, "end": end, "business_seconds": bh(start, end)}]
    total = bh(start, end)
    monitored = up + down
    percent = round(up / monitored * 100.0, 3) if monitored > 0 else None
    meets = (percent >= float(target_percent)) if percent is not None else None
    iso = lambda t: t.isoformat() if t is not None else None  # noqa: E731
    return {
        "window": {"start": iso(start), "end": iso(end)},
        "probes": len(probes),
        "business_hours": {"utc_offset_hours": utc_offset_hours, "first_hour": first_hour, "last_hour": last_hour, "weekdays_only": True,
                           "holidays_known": False},
        "business_minutes": {"total": _minutes(total), "up": _minutes(up), "down": _minutes(down),
                             "unmonitored": _minutes(max(0.0, total - monitored)), "degraded_but_up": _minutes(degraded)},
        "availability_percent": percent,
        "coverage_percent": round(monitored / total * 100.0, 2) if total > 0 else None,
        "target_percent": float(target_percent),
        "meets_target": meets,
        "outages": [{"start": iso(o["start"]), "end": iso(o["end"]), "business_minutes": _minutes(o["business_seconds"]), "error": o["error"]}
                    for o in outages if o["business_seconds"] > 0 or o["end"] is None],
        "unmonitored_gaps": [{"start": iso(g["start"]), "end": iso(g["end"]), "business_minutes": _minutes(g["business_seconds"])}
                             for g in gaps if g["business_seconds"] > 0][:20],
    }


def report_for_days(path: Any, days: int, *, now: Optional[datetime] = None, **options: Any) -> Dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    return report(read_probes(path), start=clock - timedelta(days=max(1, int(days))), end=clock, **options)
