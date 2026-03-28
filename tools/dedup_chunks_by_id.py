from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _better(a, b):
    a_dt = _parse_dt(a.get("updated_at"))
    b_dt = _parse_dt(b.get("updated_at"))
    if a_dt and b_dt:
        return a if a_dt >= b_dt else b
    if a_dt and not b_dt:
        return a
    if b_dt and not a_dt:
        return b
    a_len = len(a.get("text") or "")
    b_len = len(b.get("text") or "")
    if a_len != b_len:
        return a if a_len > b_len else b
    return a


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("report_csv")
    args = parser.parse_args()

    groups = defaultdict(list)
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            groups[obj.get("id")].append(obj)

    dedup = {}
    dup_counts = {}
    for cid, rows in groups.items():
        best = rows[0]
        for r in rows[1:]:
            best = _better(best, r)
        dedup[cid] = best
        if len(rows) > 1:
            dup_counts[cid] = len(rows)

    with open(args.output, "w", encoding="utf-8") as f:
        for cid in sorted(dedup.keys()):
            f.write(json.dumps(dedup[cid], ensure_ascii=False) + "\n")

    with open(args.report_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "count"])
        for cid, cnt in sorted(dup_counts.items()):
            writer.writerow([cid, cnt])


if __name__ == "__main__":
    main()
