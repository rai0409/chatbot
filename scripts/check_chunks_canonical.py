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
from collections import Counter, defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()

    type_dist = Counter()
    quality_dist = Counter()
    flag_dist = Counter()
    missing_source_doc = 0
    missing_required = Counter()
    total = 0

    required_fields = ["id", "text", "type", "source_doc"]

    with open(args.path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            obj = json.loads(line)
            type_dist[obj.get("type")] += 1
            quality_dist[obj.get("quality")] += 1
            for flag in obj.get("flags", []) or []:
                flag_dist[flag] += 1
            if not obj.get("source_doc"):
                missing_source_doc += 1
            for field in required_fields:
                if obj.get(field) in (None, "", []):
                    missing_required[field] += 1

    print("total:", total)
    print("type distribution:", dict(type_dist))
    print("quality distribution:", dict(quality_dist))
    print("flag distribution:", dict(flag_dist))
    print("missing source_doc:", missing_source_doc)
    print("missing required fields:", dict(missing_required))


if __name__ == "__main__":
    main()
