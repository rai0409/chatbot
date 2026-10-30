from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import config
from rag_core.question_normalization import exact_match_key

# 本番のインデックスは APPROVED_QA_EXACT_INDEX_PATH で明示したものだけを読む。
# 未設定なら完全一致は無効（空を返す）。評価用成果物（fixed_qa_eval）は評価の
# 正解から作られているため、本番設定では読み込みを拒否する。
EXACT_INDEX_PATH_ENV = "APPROVED_QA_EXACT_INDEX_PATH"
_EVAL_ARTIFACT_MARKERS = ("fixed_qa_eval",)
_ENABLED_VALUES = {"1", "true", "yes", "on"}
_DEFAULT_TENANT_ID = "default"
_NEAR_MATCH_MIN_CHARS = 18
_NEAR_MATCH_MIN_LENGTH_RATIO = 0.8

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "data": {}}


def normalize_exact_question(text: Any) -> str:
    """確定QA用の強正規化（定義は question_normalization.exact_match_key に一本化）。"""
    return exact_match_key(text)


def _env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in _ENABLED_VALUES


def _normalize_tenant(value: Any) -> str:
    return str(value or "").strip() or _DEFAULT_TENANT_ID


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(config.BASE_DIR) / p
    return p.resolve()


def configured_exact_index_path() -> Path | None:
    raw = str(os.getenv(EXACT_INDEX_PATH_ENV, "")).strip()
    if not raw:
        return None
    path = _resolve(raw)
    if any(marker in path.parts for marker in _EVAL_ARTIFACT_MARKERS):
        if not _env_enabled("APPROVED_QA_EXACT_ALLOW_EVAL_ARTIFACTS"):
            return None
    return path


def near_match_enabled() -> bool:
    return _env_enabled("APPROVED_QA_EXACT_NEAR_MATCH")


def load_exact_index(path: str | Path) -> dict[str, list[dict]]:
    # 更新時刻とサイズをキーにキャッシュ。ファイル更新後の最初の呼び出しで読み直す。
    p = _resolve(path)
    try:
        stat = p.stat()
    except FileNotFoundError:
        return {}
    key = (str(p), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        if _CACHE["key"] == key:
            return _CACHE["data"]
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    with _CACHE_LOCK:
        _CACHE["key"] = key
        _CACHE["data"] = data
    return data


def clear_exact_index_cache() -> None:
    with _CACHE_LOCK:
        _CACHE["key"] = None
        _CACHE["data"] = {}


def _copy_hit(item: dict, *, rank: int, match_type: str) -> dict:
    copied = dict(item)
    copied["score"] = 0.0

    meta = dict(copied.get("metadata") or {})
    meta["retrieval_source"] = "approved_qa_exact"
    meta["exact_match"] = True
    meta["exact_rank"] = rank
    meta["exact_match_type"] = match_type

    copied["metadata"] = meta
    return copied


def _item_question(item: dict) -> str:
    meta = item.get("metadata") or {}
    return (
        meta.get("question_text")
        or item.get("question_text")
        or item.get("question")
        or ""
    )


def _item_tenant(item: dict) -> str:
    meta = item.get("metadata") or {}
    return _normalize_tenant(meta.get("tenant_id") or item.get("tenant_id"))


def _item_id(item: dict, question: str) -> str:
    return str((item.get("metadata") or {}).get("id") or item.get("id") or question)


def _near_match(key: str, q_key: str) -> bool:
    if len(key) < _NEAR_MATCH_MIN_CHARS or not q_key:
        return False
    if not (key in q_key or q_key in key):
        return False
    shorter, longer = sorted((len(key), len(q_key)))
    return shorter / longer >= _NEAR_MATCH_MIN_LENGTH_RATIO


def lookup_approved_qa_exact(
    query: str,
    *,
    tenant_id: str,
    limit: int = 5,
    index_path: str | Path | None = None,
    allow_near_match: bool | None = None,
) -> list[dict]:
    # tenant_id は必須。インデックスの項目は tenant_id（無ければ default）が
    # 一致するものだけを返す。他テナントの承認回答は返さない。
    key = normalize_exact_question(query)
    if not key:
        return []
    path = Path(index_path) if index_path is not None else configured_exact_index_path()
    if path is None:
        return []
    tenant = _normalize_tenant(tenant_id)
    index = load_exact_index(path)

    # 1. index key の完全一致
    direct_hits = [item for item in index.get(key, []) if _item_tenant(item) == tenant]
    if direct_hits:
        return [
            _copy_hit(item, rank=rank, match_type="key_exact")
            for rank, item in enumerate(direct_hits[:limit], start=1)
        ]

    # 2. index作成時の正規化が古い場合に備え、question_text を再正規化して完全一致
    out: list[dict] = []
    seen: set[str] = set()
    tenant_items = [
        item for items in index.values() for item in items if _item_tenant(item) == tenant
    ]
    for item in tenant_items:
        q = _item_question(item)
        if normalize_exact_question(q) != key:
            continue
        item_id = _item_id(item, q)
        if item_id in seen:
            continue
        seen.add(item_id)
        out.append(_copy_hit(item, rank=len(out) + 1, match_type="question_exact"))
        if len(out) >= limit:
            return out

    # 3. near-exact（既定は無効）。包含に加えて長さの比を要求し、短い質問の誤爆を防ぐ。
    use_near = near_match_enabled() if allow_near_match is None else bool(allow_near_match)
    if use_near and not out:
        for item in tenant_items:
            q = _item_question(item)
            if not _near_match(key, normalize_exact_question(q)):
                continue
            item_id = _item_id(item, q)
            if item_id in seen:
                continue
            seen.add(item_id)
            out.append(_copy_hit(item, rank=len(out) + 1, match_type="near_exact_contains"))
            if len(out) >= limit:
                return out

    return out
