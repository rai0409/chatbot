from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import config


_MAX_STRING_CHARS = 1000
_MAX_LIST_ITEMS = 20
_MAX_DICT_ITEMS = 40


def _audit_path(kind: str) -> Path | None:
    if kind == "chat":
        if not getattr(config, "AUDIT_CHAT_ENABLED", True):
            return None
        return Path(config.RUNS_DIR) / "audit" / "chat_events.jsonl"
    if kind == "search_debug":
        if not getattr(config, "AUDIT_SEARCH_DEBUG_ENABLED", True):
            return None
        return Path(config.RUNS_DIR) / "audit" / "search_debug_events.jsonl"
    return Path(config.RUNS_DIR) / "audit" / "events.jsonl"


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > _MAX_STRING_CHARS:
            return value[:_MAX_STRING_CHARS] + "...[truncated]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_bounded(item) for item in value[:_MAX_LIST_ITEMS]]
    if isinstance(value, tuple):
        return [_bounded(item) for item in list(value)[:_MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= _MAX_DICT_ITEMS:
                break
            out[str(key)] = _bounded(item)
        return out
    return str(value)


def append_audit_event(kind: str, event: dict) -> None:
    try:
        path = _audit_path(kind)
        if path is None:
            return
        payload = _bounded(dict(event or {}))
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload["kind"] = kind
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return


def append_product_preview_chat_audit_event(event: dict) -> bool:
    try:
        if not getattr(config, "AUDIT_CHAT_ENABLED", True):
            return True
        path = Path(config.RUNS_DIR) / "audit" / "chat_events.jsonl"
        payload = _bounded(dict(event or {}))
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        payload["kind"] = "product_preview_chat"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        return False
