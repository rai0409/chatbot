from __future__ import annotations

import argparse
import json
import re
import sys


def _load_text(path: str, key: str | None) -> str:
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if key and key in data:
                return str(data[key])
            if "answer" in data:
                return str(data["answer"])
            return json.dumps(data, ensure_ascii=False)
        if isinstance(data, list):
            if key:
                parts = []
                for item in data:
                    if isinstance(item, dict) and key in item:
                        parts.append(str(item[key]))
                return "\n".join(parts)
            return json.dumps(data, ensure_ascii=False)
        return str(data)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _validate(text: str) -> bool:
    if "[S" in text:
        return False
    if "参考資料:" not in text:
        return False
    if "不足:" not in text and "不明:" not in text:
        return False
    if "[" not in text or "]" not in text:
        return False
    if not re.search(r"\[\d+\]", text):
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--key", default=None)
    args = parser.parse_args()
    try:
        text = _load_text(args.path, args.key)
    except Exception:
        return 1
    ok = _validate(text)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
