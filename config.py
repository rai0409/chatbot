from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)


def getenv_first(*keys: str, default: Optional[str] = None) -> Optional[str]:
    for key in keys:
        val = os.getenv(key)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _get_list(name: str, default: Iterable[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    raw = raw.strip()
    if raw == "":
        return list(default)
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip() != ""]
        except Exception:
            pass
    if "\n" in raw:
        parts = [p.strip() for p in raw.splitlines()]
    else:
        parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p != ""]


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


_VECTORSTORE_DIR_RAW = getenv_first("VECTORSTORE_DIR", default="vectorstore/")
VECTORSTORE_DIR = str((BASE_DIR / _VECTORSTORE_DIR_RAW).resolve())
VECTORSTORE_COLLECTION_NAME = getenv_first(
    "VECTORSTORE_COLLECTION_NAME", default="chatbot_chunks_v1"
)
_CHUNKS_JSONL_PATH_RAW = getenv_first(
    "CHUNKS_JSONL_PATH", default="index/chunks.canonical.bytype.dedup.jsonl"
)
CHUNKS_JSONL_PATH = str((BASE_DIR / _CHUNKS_JSONL_PATH_RAW).resolve())
RUNS_DIR = str((BASE_DIR / "runs").resolve())
INDEX_DIR = str((BASE_DIR / "index").resolve())

OPENAI_API_KEY = getenv_first("OPENAI_API_KEY", default="")
OPENAI_BASE_URL = getenv_first("OPENAI_BASE_URL", default=None)
CHAT_MODEL = getenv_first("CHAT_MODEL", default="gpt-4o-mini")
EMBED_MODEL = getenv_first("EMBED_MODEL", default="text-embedding-3-small")
OPENAI_EMBED_MODEL = getenv_first("OPENAI_EMBED_MODEL", default=EMBED_MODEL)

TOP_K = int(getenv_first("TOP_K", default="20"))
ENABLE_HYBRID_RETRIEVAL = _get_bool("ENABLE_HYBRID_RETRIEVAL", default=True)
VECTOR_TOP_K = int(getenv_first("VECTOR_TOP_K", default=str(TOP_K)))
BM25_TOP_K = int(getenv_first("BM25_TOP_K", default=str(TOP_K)))
HYBRID_RRF_K = int(getenv_first("HYBRID_RRF_K", default="60"))
CHILD_RETRIEVAL_OVERSAMPLE = int(getenv_first("CHILD_RETRIEVAL_OVERSAMPLE", default="2"))
CHILD_CHUNK_ROLE_VALUES = _get_list("CHILD_CHUNK_ROLE_VALUES", ["child", "leaf"])
ENABLE_PARENT_EXPANSION = _get_bool("ENABLE_PARENT_EXPANSION", default=True)
MAX_PARENT_EXPANDED_CHUNKS = int(getenv_first("MAX_PARENT_EXPANDED_CHUNKS", default=str(TOP_K)))
MAX_PARENT_CONTEXT_CHARS = int(getenv_first("MAX_PARENT_CONTEXT_CHARS", default="2400"))
MAX_CONTEXT_CHARS = int(getenv_first("MAX_CONTEXT_CHARS", default="8000"))

JA_CHUNK_TARGET_FAQ_MIN_CHARS = int(getenv_first("JA_CHUNK_TARGET_FAQ_MIN_CHARS", default="80"))
JA_CHUNK_TARGET_FAQ_MAX_CHARS = int(getenv_first("JA_CHUNK_TARGET_FAQ_MAX_CHARS", default="300"))
JA_CHUNK_TARGET_PROCEDURE_MIN_CHARS = int(
    getenv_first("JA_CHUNK_TARGET_PROCEDURE_MIN_CHARS", default="300")
)
JA_CHUNK_TARGET_PROCEDURE_MAX_CHARS = int(
    getenv_first("JA_CHUNK_TARGET_PROCEDURE_MAX_CHARS", default="900")
)
JA_CHUNK_TARGET_POLICY_MIN_CHARS = int(getenv_first("JA_CHUNK_TARGET_POLICY_MIN_CHARS", default="400"))
JA_CHUNK_TARGET_POLICY_MAX_CHARS = int(getenv_first("JA_CHUNK_TARGET_POLICY_MAX_CHARS", default="1200"))
JA_CHUNK_TARGET_TABLE_MIN_CHARS = int(getenv_first("JA_CHUNK_TARGET_TABLE_MIN_CHARS", default="80"))
JA_CHUNK_TARGET_TABLE_MAX_CHARS = int(getenv_first("JA_CHUNK_TARGET_TABLE_MAX_CHARS", default="500"))
JA_CHUNK_FALLBACK_OVERLAP_CHARS = int(getenv_first("JA_CHUNK_FALLBACK_OVERLAP_CHARS", default="80"))

RAG_HARD_MAX_DIST = _get_float("RAG_HARD_MAX_DIST", default=0.95)
RAG_HARD_MAX_DIST = _get_float("RAG_MAX_DISTANCE", default=RAG_HARD_MAX_DIST)
RAG_HARD_MAX_DIST_PROCEDURE_DELTA = _get_float(
    "RAG_HARD_MAX_DIST_PROCEDURE_DELTA", default=0.03
)

RAG_SOFT_DIST_OTHER = _get_float("RAG_SOFT_DIST_OTHER", default=0.65)
RAG_SOFT_DIST_RESET = _get_float("RAG_SOFT_DIST_RESET", default=0.80)
RAG_SOFT_DIST_CHANGE = _get_float("RAG_SOFT_DIST_CHANGE", default=0.90)
RAG_SOFT_DIST_PROCEDURE = _get_float(
    "RAG_SOFT_DIST_PROCEDURE", default=RAG_SOFT_DIST_CHANGE
)

NEGATION_PHRASES = _get_list(
    "NEGATION_PHRASES",
    [
        "しない",
        "できない",
        "不要",
        "禁止",
        "不要です",
        "しなくてよい",
        "やらない",
    ],
)
DEFINITION_TERMS = _get_list(
    "DEFINITION_TERMS", ["とは", "意味", "定義", "概要", "教えて", "何ですか"]
)
PROCEDURE_STRONG_TERMS = _get_list(
    "PROCEDURE_STRONG_TERMS", ["手順", "方法", "流れ", "操作", "手続き", "やり方"]
)
PROCEDURE_WEAK_TERMS = _get_list(
    "PROCEDURE_WEAK_TERMS", ["設定", "登録", "変更", "削除", "更新"]
)
INQUIRY_TERMS = _get_list(
    "INQUIRY_TERMS", ["問い合わせ", "質問", "連絡", "サポート", "窓口"]
)
INQUIRY_TO_PROCEDURE_HINT_TERMS = _get_list(
    "INQUIRY_TO_PROCEDURE_HINT_TERMS", ["手順", "方法", "どうやって"]
)
OTHER_QUERY_BOOST_TERMS = _get_list(
    "OTHER_QUERY_BOOST_TERMS", ["定義", "意味", "概要", "説明"]
)

RESET_TERMS = _get_list("RESET_TERMS", ["再設定", "再発行", "リセット", "初期化"])
CHANGE_TERMS = _get_list("CHANGE_TERMS", ["変更", "更新", "切替", "変更方法"])
PROCEDURE_MAX_PER_PAGE = int(getenv_first("PROCEDURE_MAX_PER_PAGE", default="8"))
CHANGE_RESET_MAX_PER_PAGE = int(getenv_first("CHANGE_RESET_MAX_PER_PAGE", default="5"))
OTHER_MAX_PER_PAGE = int(getenv_first("OTHER_MAX_PER_PAGE", default="3"))

STOP_SALIENT_TERMS = _get_list(
    "STOP_SALIENT_TERMS",
    [
        "概要",
        "教えて",
        "ページ番号",
        "内容",
        "確認",
        "資料",
        "どこ",
        "何",
        "一覧",
    ],
)

PREFER_SORT_TERMS_OTHER = _get_list(
    "PREFER_SORT_TERMS_OTHER", ["定義", "意味", "概要", "説明"]
)
PREFER_SORT_TERMS_RESET = _get_list("PREFER_SORT_TERMS_RESET", ["再設定", "再発行"])
PREFER_SORT_TERMS_CHANGE = _get_list("PREFER_SORT_TERMS_CHANGE", ["変更", "更新"])
PREFER_SORT_TERMS_PROCEDURE = _get_list(
    "PREFER_SORT_TERMS_PROCEDURE", ["手順", "方法", "操作"]
)

RERANK_BOOST_FAQ_SHORT_LOOKUP = int(getenv_first("RERANK_BOOST_FAQ_SHORT_LOOKUP", default="2"))
RERANK_BOOST_PROCEDURE_DOC_TYPE = int(getenv_first("RERANK_BOOST_PROCEDURE_DOC_TYPE", default="1"))
RERANK_MAX_LIFT_STRONG = int(getenv_first("RERANK_MAX_LIFT_STRONG", default="3"))
RERANK_MAX_LIFT_WEAK = int(getenv_first("RERANK_MAX_LIFT_WEAK", default="2"))
RERANK_MAX_LIFT_OTHER_WEAK = int(getenv_first("RERANK_MAX_LIFT_OTHER_WEAK", default="2"))

DISABLE_GUARD = _get_bool("DISABLE_GUARD", default=False)
IGNORE_SEARCHABLE = _get_bool("IGNORE_SEARCHABLE", default=False)
LOG_WHERE = _get_bool("LOG_WHERE", default=False)
