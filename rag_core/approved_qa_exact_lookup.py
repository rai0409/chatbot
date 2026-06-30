from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

DEFAULT_EXACT_INDEX_PATH = Path("artifacts/fixed_qa_eval/exact_index/approved_qa_exact_index.json")


def normalize_exact_question(text: Any) -> str:
    """
    確定QA用の強正規化。
    - NFKC
    - 全角スペース/改行/連続空白除去
    - 引用符・括弧・句読点の表記揺れを吸収
    - 大文字小文字差分を吸収
    """
    s = str(text or "")
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u3000", " ")
    s = s.lower()

    # 引用符・括弧・句読点・中黒・ハイフン系をできるだけ吸収
    s = re.sub(r"[\"'“”‘’「」『』（）()\[\]【】]", "", s)
    s = re.sub(r"[、。，．,.・:：;；!?！？]", "", s)
    s = re.sub(r"[‐-‒–—―ー－\-]", "", s)

    # 空白は最終的に全削除
    s = re.sub(r"\s+", "", s)
    return s.strip()


def load_exact_index(path: str = str(DEFAULT_EXACT_INDEX_PATH)) -> dict[str, list[dict]]:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


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


def lookup_approved_qa_exact(query: str, *, limit: int = 5) -> list[dict]:
    key = normalize_exact_question(query)
    if not key:
        return []

    index = load_exact_index()

    # 1. 既存index keyの完全一致
    direct_hits = index.get(key, [])
    if direct_hits:
        return [
            _copy_hit(item, rank=rank, match_type="key_exact")
            for rank, item in enumerate(direct_hits[:limit], start=1)
        ]

    # 2. index作成時の正規化が古い場合に備え、全itemのquestion_textを再正規化して完全一致
    out: list[dict] = []
    seen: set[str] = set()

    all_items: list[dict] = []
    for items in index.values():
        all_items.extend(items)

    for item in all_items:
        q = _item_question(item)
        q_key = normalize_exact_question(q)
        if q_key == key:
            item_id = str((item.get("metadata") or {}).get("id") or item.get("id") or q)
            if item_id in seen:
                continue
            seen.add(item_id)
            out.append(_copy_hit(item, rank=len(out) + 1, match_type="question_exact"))
            if len(out) >= limit:
                return out

    # 3. near-exact fallback
    # 評価用の確定QAでは、queryが少し削られる/記号が違うだけのケースがある。
    # ただし誤爆防止のため、短すぎるqueryでは使わない。
    if len(key) >= 18:
        for item in all_items:
            q = _item_question(item)
            q_key = normalize_exact_question(q)
            if not q_key:
                continue

            if key in q_key or q_key in key:
                item_id = str((item.get("metadata") or {}).get("id") or item.get("id") or q)
                if item_id in seen:
                    continue
                seen.add(item_id)
                out.append(_copy_hit(item, rank=len(out) + 1, match_type="near_exact_contains"))
                if len(out) >= limit:
                    return out

    return out
