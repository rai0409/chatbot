from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


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
DEFAULT_CHROMA_COLLECTION = "chatbot_chunks_v1_aligned_candidate"
VECTORSTORE_COLLECTION_NAME = getenv_first(
    "VECTORSTORE_COLLECTION_NAME", default=DEFAULT_CHROMA_COLLECTION
)


def resolve_chroma_collection_name(explicit: Optional[str] = None) -> str:
    """Resolve the active Chroma collection without discarding runtime config.

    Precedence:
    1. Explicit function argument.
    2. CHROMA_COLLECTION runtime override.
    3. The collection of the active generation (rag_core.index_generation; only
       when its pointer file exists and CHROMA_COLLECTION is not set).
    4. The already-resolved VECTORSTORE_COLLECTION_NAME setting.

    VECTORSTORE_COLLECTION_NAME is intentionally not re-read from os.environ
    here. This preserves config monkeypatches and other controlled runtime
    overrides used by tests and staging workflows.
    """
    name = str(explicit or "").strip()
    if name:
        return name

    env_override = str(os.getenv("CHROMA_COLLECTION") or "").strip()
    if env_override:
        return env_override

    from rag_core import index_generation

    pointed = index_generation.active_collection()
    if pointed:
        return pointed

    configured = str(VECTORSTORE_COLLECTION_NAME or "").strip()
    return configured or DEFAULT_CHROMA_COLLECTION


_CHUNKS_JSONL_PATH_RAW = getenv_first(
    "CHUNKS_JSONL_PATH", default="index/chunks.canonical.bytype.dedup.jsonl"
)
CHUNKS_JSONL_PATH = str((BASE_DIR / _CHUNKS_JSONL_PATH_RAW).resolve())
RUNS_DIR = str((BASE_DIR / "runs").resolve())
INDEX_DIR = str((BASE_DIR / "index").resolve())

OPENAI_API_KEY = getenv_first("OPENAI_API_KEY", default="")
OPENAI_BASE_URL = getenv_first("OPENAI_BASE_URL", default=None)
CHAT_MODEL = getenv_first("CHAT_MODEL", default="gpt-4o-mini")
CHAT_COMPLETION_TIMEOUT_SECONDS = _get_float("CHAT_COMPLETION_TIMEOUT_SECONDS", default=30.0)
# Stage 14: OpenAI embeddings (EMBED_PROVIDER=openai) had no timeout.
OPENAI_EMBED_TIMEOUT_SECONDS = _get_float("OPENAI_EMBED_TIMEOUT_SECONDS", default=30.0)
CHAT_COMPLETION_MAX_RETRIES = int(getenv_first("CHAT_COMPLETION_MAX_RETRIES", default="1"))
CHAT_COMPLETION_RETRY_BACKOFF_SECONDS = _get_float(
    "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS", default=1.0
)
CHAT_COMPLETION_MAX_TOKENS = int(getenv_first("CHAT_COMPLETION_MAX_TOKENS", default="1024"))
CHAT_GENERATION_MODE = getenv_first("CHAT_GENERATION_MODE", default="extractive")
EMBED_MODEL = getenv_first("EMBED_MODEL", default="text-embedding-3-small")
OPENAI_EMBED_MODEL = getenv_first("OPENAI_EMBED_MODEL", default=EMBED_MODEL)


def resolve_chat_generation_mode(explicit: Optional[str] = None) -> str:
    mode = str(explicit or CHAT_GENERATION_MODE or "").strip().lower()
    if mode in {"extractive", "llm"}:
        return mode
    return "extractive"

TOP_K = int(getenv_first("TOP_K", default="20"))
ENABLE_HYBRID_RETRIEVAL = _get_bool("ENABLE_HYBRID_RETRIEVAL", default=True)
VECTOR_TOP_K = int(getenv_first("VECTOR_TOP_K", default=str(TOP_K)))
BM25_TOP_K = int(getenv_first("BM25_TOP_K", default=str(TOP_K)))
HYBRID_RRF_K = int(getenv_first("HYBRID_RRF_K", default="60"))
# Hybrid fusion (replaces the former hand-tuned score boosts).
# "weighted_rrf": sum of weight / (HYBRID_RRF_K + rank) over the dense,
# keyword and exact-match lanes. "dbsf": per-lane mean±3σ score
# normalization summed with the same weights. Tune weights and k with
# `python -m eval.bench`; defaults keep every lane at weight 1.0.
HYBRID_FUSION = getenv_first("HYBRID_FUSION", default="weighted_rrf")
HYBRID_DENSE_WEIGHT = _get_float("HYBRID_DENSE_WEIGHT", default=1.0)
HYBRID_KEYWORD_WEIGHT = _get_float("HYBRID_KEYWORD_WEIGHT", default=1.0)
HYBRID_EXACT_WEIGHT = _get_float("HYBRID_EXACT_WEIGHT", default=1.0)
# Run the dense and keyword lanes concurrently (latency = slower lane).
HYBRID_PARALLEL = _get_bool("HYBRID_PARALLEL", default=False)
# Keyword (BM25) tokenizer. "auto" uses Sudachi when SudachiPy and a
# dictionary are installed (requirements-ja.txt) and otherwise falls back to
# the legacy heuristic tokenizer with one warning; "sudachi" fails loudly when
# Sudachi is unavailable; "heuristic" keeps the legacy character bigrams.
KEYWORD_TOKENIZER = getenv_first("KEYWORD_TOKENIZER", default="auto")
SUDACHI_DICT = getenv_first("SUDACHI_DICT", default="core")
SUDACHI_SPLIT_MODE = getenv_first("SUDACHI_SPLIT_MODE", default="A")
KEYWORD_NGRAM_ENABLED = _get_bool("KEYWORD_NGRAM_ENABLED", default=True)
# Add quantity tokens (q:money:15000JPY, q:date:2024-04-01, ...) so equal
# amounts and dates match across notations (rag_core/quantities.py).
KEYWORD_NUMERIC_TOKENS = _get_bool("KEYWORD_NUMERIC_TOKENS", default=False)
# "rank_bm25" scores every document for every query term; "inverted" scores
# only documents that contain a query term (rag_core/bm25_index.py). Stage 14:
# inverted is the default (rank_bm25 does not scale to 200,000 chunks).
KEYWORD_INDEX_ENGINE = getenv_first("KEYWORD_INDEX_ENGINE", default="inverted")
BM25_K1 = _get_float("BM25_K1", default=1.5)
BM25_B = _get_float("BM25_B", default=0.75)
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

# Keyword-only minimum evidence: when no vector evidence exists, the guard
# requires best BM25 above this (or any lexical match). 0.0 = abstain only
# when there is literally zero term overlap.
RAG_MIN_KEYWORD_EVIDENCE_BM25 = _get_float("RAG_MIN_KEYWORD_EVIDENCE_BM25", default=0.0)

# Stage 14: the distance thresholds below are cosine distances of the best
# vector hit and depend on the embedding model. The defaults were tuned on runs
# with this model; after switching models (the product default is ruri-v3),
# calibrate them (scripts/calibrate_distance_guard.py) and set this to the
# model they were calibrated for. /admin/health reports a mismatch.
RAG_DIST_CALIBRATED_MODEL = getenv_first(
    "RAG_DIST_CALIBRATED_MODEL", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
RAG_SOFT_DIST_OTHER = _get_float("RAG_SOFT_DIST_OTHER", default=0.65)
RAG_SOFT_DIST_RESET = _get_float("RAG_SOFT_DIST_RESET", default=0.80)
RAG_SOFT_DIST_CHANGE = _get_float("RAG_SOFT_DIST_CHANGE", default=0.90)
RAG_SOFT_DIST_PROCEDURE = _get_float(
    "RAG_SOFT_DIST_PROCEDURE", default=RAG_SOFT_DIST_CHANGE
)

NEGATION_PHRASES = _get_list(
    "NEGATION_PHRASES",
    ["しない", "できない", "不要", "禁止", "不要です", "しなくてよい", "やらない"],
)
DEFINITION_TERMS = _get_list(
    "DEFINITION_TERMS", ["とは", "意味", "定義", "概要", "教えて", "何ですか"]
)
PROCEDURE_STRONG_TERMS = _get_list(
    "PROCEDURE_STRONG_TERMS", ["手順", "方法", "流れ", "操作", "手続き", "やり方"]
)
PROCEDURE_WEAK_TERMS = _get_list("PROCEDURE_WEAK_TERMS", ["設定", "登録", "変更", "削除", "更新"])
INQUIRY_TERMS = _get_list("INQUIRY_TERMS", ["問い合わせ", "質問", "連絡", "サポート", "窓口"])
INQUIRY_TO_PROCEDURE_HINT_TERMS = _get_list(
    "INQUIRY_TO_PROCEDURE_HINT_TERMS", ["手順", "方法", "どうやって"]
)
OTHER_QUERY_BOOST_TERMS = _get_list("OTHER_QUERY_BOOST_TERMS", ["定義", "意味", "概要", "説明"])

RESET_TERMS = _get_list("RESET_TERMS", ["再設定", "再発行", "リセット", "初期化"])
CHANGE_TERMS = _get_list("CHANGE_TERMS", ["変更", "更新", "切替", "変更方法"])
PROCEDURE_MAX_PER_PAGE = int(getenv_first("PROCEDURE_MAX_PER_PAGE", default="8"))
CHANGE_RESET_MAX_PER_PAGE = int(getenv_first("CHANGE_RESET_MAX_PER_PAGE", default="5"))
OTHER_MAX_PER_PAGE = int(getenv_first("OTHER_MAX_PER_PAGE", default="3"))

STOP_SALIENT_TERMS = _get_list(
    "STOP_SALIENT_TERMS",
    ["概要", "教えて", "ページ番号", "内容", "確認", "資料", "どこ", "何", "一覧"],
)

PREFER_SORT_TERMS_OTHER = _get_list("PREFER_SORT_TERMS_OTHER", ["定義", "意味", "概要", "説明"])
PREFER_SORT_TERMS_RESET = _get_list("PREFER_SORT_TERMS_RESET", ["再設定", "再発行"])
PREFER_SORT_TERMS_CHANGE = _get_list("PREFER_SORT_TERMS_CHANGE", ["変更", "更新"])
PREFER_SORT_TERMS_PROCEDURE = _get_list("PREFER_SORT_TERMS_PROCEDURE", ["手順", "方法", "操作"])

RERANK_BOOST_FAQ_SHORT_LOOKUP = int(getenv_first("RERANK_BOOST_FAQ_SHORT_LOOKUP", default="2"))
RERANK_BOOST_PROCEDURE_DOC_TYPE = int(getenv_first("RERANK_BOOST_PROCEDURE_DOC_TYPE", default="1"))
RERANK_MAX_LIFT_STRONG = int(getenv_first("RERANK_MAX_LIFT_STRONG", default="3"))
RERANK_MAX_LIFT_WEAK = int(getenv_first("RERANK_MAX_LIFT_WEAK", default="2"))
RERANK_MAX_LIFT_OTHER_WEAK = int(getenv_first("RERANK_MAX_LIFT_OTHER_WEAK", default="2"))

KEYWORD_BOOST_ENABLED = _get_bool("KEYWORD_BOOST_ENABLED", default=True)
KEYWORD_BOOST_QUERY_TYPES = _get_list("KEYWORD_BOOST_QUERY_TYPES", ["exact_lookup", "identifier"])
KEYWORD_BOOST_MAX_DELTA = _get_float("KEYWORD_BOOST_MAX_DELTA", default=0.05)
# Run the retrieval_boost_rules of the RAG profile (rag_core/profile_engine.py). Off until a profile is evaluated
# with it. Largest score (distance) change the rules can make to one chunk.
PROFILE_BOOST_ENABLED = _get_bool("PROFILE_BOOST_ENABLED", default=False)
PROFILE_BOOST_MAX_DELTA = _get_float("PROFILE_BOOST_MAX_DELTA", default=0.05)
# Multi-query retrieval with RRF (rag_core/multi_query.py). Off until evaluated; a "deep" request turns it on for itself.
# MAX_VARIANTS counts the queries in all, the question included.
MULTI_QUERY_ENABLED = _get_bool("MULTI_QUERY_ENABLED", default=False)
MULTI_QUERY_MAX_VARIANTS = int(getenv_first("MULTI_QUERY_MAX_VARIANTS", default="3"))
MULTI_QUERY_RRF_K = int(getenv_first("MULTI_QUERY_RRF_K", default="60"))
MULTI_QUERY_VARIANT_PENALTY = _get_float("MULTI_QUERY_VARIANT_PENALTY", default=0.05)
# Pick the weights of the hybrid lanes by query type (rag_core/query_route.py). Off until evaluated.
QUERY_ROUTING_ENABLED = _get_bool("QUERY_ROUTING_ENABLED", default=False)
# Where an LLM may be used (rag_core/llm.py). LLM_ENABLED unset: the historical switch decides (CHAT_GENERATION_MODE=llm
# is on); LLM_ENABLED=false is a kill switch. Tenant lists: empty allowlist = every tenant. LLM_FEATURES: what may use it
# (generate | rewrite | verify | judge). In production an external endpoint needs LLM_EXTERNAL_ALLOWED=true.
LLM_ENABLED = _get_bool("LLM_ENABLED", default=False) if os.getenv("LLM_ENABLED", "").strip() else None
LLM_TENANT_ALLOWLIST = _get_list("LLM_TENANT_ALLOWLIST", [])
LLM_TENANT_DENYLIST = _get_list("LLM_TENANT_DENYLIST", [])
LLM_FEATURES = _get_list("LLM_FEATURES", ["generate"])
LLM_EXTERNAL_ALLOWED = _get_bool("LLM_EXTERNAL_ALLOWED", default=False)
LLM_INTERNAL_HOSTS = _get_list("LLM_INTERNAL_HOSTS", ["localhost"])
LLM_FEATURE_MAX_TOKENS = int(getenv_first("LLM_FEATURE_MAX_TOKENS", default="512"))
# Sentence-level grounding check of LLM-generated answers (rag_core/grounding.py). Off until evaluated.
GROUNDING_VERIFY_ENABLED = _get_bool("GROUNDING_VERIFY_ENABLED", default=False)
GROUNDING_MIN_COVERAGE = _get_float("GROUNDING_MIN_COVERAGE", default=0.5)
# Mass-change guard of source sync (rag_core/source_apply.py): a plan with at least MIN_DELETES deletions that are more
# than MAX_DELETE_FRACTION of the applied files, or more than MAX_CHANGES changes, is held for approval.
SOURCE_MAX_DELETE_FRACTION = _get_float("SOURCE_MAX_DELETE_FRACTION", default=0.2)
SOURCE_MIN_DELETES_FOR_GUARD = int(getenv_first("SOURCE_MIN_DELETES_FOR_GUARD", default="5"))
SOURCE_MAX_CHANGES = int(getenv_first("SOURCE_MAX_CHANGES", default="500"))

# Optional semantic rerank stage (off by default; needs the optional
# sentence-transformers dependency only when enabled). The default model is
# cl-nagoya/ruri-v3-reranker-310m (Apache-2.0; JQaRA nDCG@10 86.9 on its
# model card vs 67.3 for BAAI/bge-reranker-v2-m3 in the same table). On a
# CPU-only host consider cl-nagoya/ruri-reranker-base (check its license).
CROSS_ENCODER_RERANK_ENABLED = _get_bool("CROSS_ENCODER_RERANK_ENABLED", default=False)
CROSS_ENCODER_MODEL = getenv_first("CROSS_ENCODER_MODEL", default="cl-nagoya/ruri-v3-reranker-310m")
CROSS_ENCODER_TOP_N = int(getenv_first("CROSS_ENCODER_TOP_N", default="30"))
# Caps query+passage length per pair; 0 keeps the model default.
CROSS_ENCODER_MAX_LENGTH = int(getenv_first("CROSS_ENCODER_MAX_LENGTH", default="512"))
# Skip reranking when the fused-score gap between the top two candidates is
# at least this value (0 = never skip). Derive it from
# `python -m eval.bench calibrate --base-config-id ...` (rerank_skip).
CROSS_ENCODER_SKIP_MARGIN = _get_float("CROSS_ENCODER_SKIP_MARGIN", default=0.0)

APPROVED_QA_ENABLED = _get_bool("APPROVED_QA_ENABLED", default=False)
_APPROVED_QA_PATH_RAW = getenv_first("APPROVED_QA_PATH", default="eval/cases/approved_qa_sample.jsonl")
APPROVED_QA_PATH = str((BASE_DIR / _APPROVED_QA_PATH_RAW).resolve())
APPROVED_SIMILAR_CANDIDATES_ENABLED = _get_bool("APPROVED_SIMILAR_CANDIDATES_ENABLED", default=False)
APPROVED_SIMILAR_CANDIDATES_TOP_K = int(getenv_first("APPROVED_SIMILAR_CANDIDATES_TOP_K", default="5"))

ANSWER_CACHE_ENABLED = _get_bool("ANSWER_CACHE_ENABLED", default=False)
ANSWER_CACHE_MAX_ENTRIES = int(getenv_first("ANSWER_CACHE_MAX_ENTRIES", default="256"))

# Default OFF. When true, /chat and /chat/stream resolve the per-tenant product
# profile at runtime (safest-profile fallback) instead of running the unprofiled
# default pipeline. Behavior is byte-for-byte unchanged while false.
CHAT_USE_TENANT_PROFILE = _get_bool("CHAT_USE_TENANT_PROFILE", default=False)

DISABLE_GUARD = _get_bool("DISABLE_GUARD", default=False)
IGNORE_SEARCHABLE = _get_bool("IGNORE_SEARCHABLE", default=False)
# When true, the default tenant also gets a DB-side tenant_id="default"
# filter. Run scripts/backfill_tenant_id.py first: records without tenant_id
# would otherwise disappear from default-tenant vector results.
TENANT_FILTER_STRICT = _get_bool("TENANT_FILTER_STRICT", default=False)
LOG_WHERE = _get_bool("LOG_WHERE", default=False)
AUDIT_CHAT_ENABLED = _get_bool("AUDIT_CHAT_ENABLED", default=True)
AUDIT_SEARCH_DEBUG_ENABLED = _get_bool("AUDIT_SEARCH_DEBUG_ENABLED", default=True)

# --- Deployment-specific endpoints and timeouts (S1) --------------------------------------------------------------------
# These were fixed in the code. They are settings of one deployment (a sovereign cloud, a proxy, a slow link), so they
# live here and in the environment; every default equals the former fixed value. Secrets stay in the environment only.
# Microsoft Graph (rag_core/connectors): API base, token endpoint ({tenant} is filled in), scope, HTTP timeout, longest wait.
GRAPH_BASE_URL = getenv_first("GRAPH_BASE_URL", default="https://graph.microsoft.com/v1.0")
GRAPH_TOKEN_URL = getenv_first("GRAPH_TOKEN_URL", default="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token")
GRAPH_SCOPE = getenv_first("GRAPH_SCOPE", default="https://graph.microsoft.com/.default")
GRAPH_HTTP_TIMEOUT_SECONDS = _get_float("GRAPH_HTTP_TIMEOUT_SECONDS", default=60.0)
GRAPH_MAX_WAIT_SECONDS = _get_float("GRAPH_MAX_WAIT_SECONDS", default=60.0)
# Alert notifications (webapi/notifications.py).
NOTIFY_SLACK_TIMEOUT_SECONDS = _get_float("NOTIFY_SLACK_TIMEOUT_SECONDS", default=10.0)
NOTIFY_SMTP_TIMEOUT_SECONDS = _get_float("NOTIFY_SMTP_TIMEOUT_SECONDS", default=15.0)
# OIDC (webapi/oidc_auth.py): HTTP timeout of the token and JWKS calls, and the wait before the one JWKS retry.
OIDC_HTTP_TIMEOUT_SECONDS = _get_float("OIDC_HTTP_TIMEOUT_SECONDS", default=15.0)
OIDC_RETRY_WAIT_SECONDS = _get_float("OIDC_RETRY_WAIT_SECONDS", default=0.5)
# Remote embeddings may use their own endpoint (for example a local server) while the LLM uses another. Empty: the
# embedding client keeps using OPENAI_BASE_URL, as before.
EMBEDDING_BASE_URL = getenv_first("EMBEDDING_BASE_URL", default="")

# --- External integrations and runtime settings (S1, rag_core/integrations.py, rag_core/runtime_settings.py) ------------
# The environment layer of the integration switches: only the names listed here may use the network at all
# (sharepoint, outlook_draft, smtp, slack). Empty = none. A console switch and a tenant switch can only narrow it.
EXTERNAL_INTEGRATIONS = [name.lower() for name in _get_list("EXTERNAL_INTEGRATIONS", [])]
# Where the console's runtime switches are kept (a small JSON file). Empty: <RUNS_DIR>/settings.
RUNTIME_SETTINGS_DIR = getenv_first("RUNTIME_SETTINGS_DIR", default="")

# --- LLM gateway (S2, rag_core/llm_gateway.py) ---------------------------------------------------------------------------
# Model profiles: a JSON file of the deployment (config/deploy/llm_profiles.example.json is the model). Empty: one implicit
# profile made of OPENAI_BASE_URL / CHAT_MODEL, as before.
LLM_PROFILES_FILE = getenv_first("LLM_PROFILES_FILE", default="")
# Circuit breaker per profile: open after this many failures in a row, stay open this long, then let this many trial
# calls through.
LLM_BREAKER_ENABLED = _get_bool("LLM_BREAKER_ENABLED", default=True)
LLM_BREAKER_FAILURE_THRESHOLD = int(getenv_first("LLM_BREAKER_FAILURE_THRESHOLD", default="3"))
LLM_BREAKER_OPEN_SECONDS = _get_float("LLM_BREAKER_OPEN_SECONDS", default=60.0)
LLM_BREAKER_PROBES = int(getenv_first("LLM_BREAKER_PROBES", default="1"))
# Usage log: one line per call (no question, no answer), a file per day. Empty directory: <RUNS_DIR>/usage.
LLM_USAGE_ENABLED = _get_bool("LLM_USAGE_ENABLED", default=True)
LLM_USAGE_DIR = getenv_first("LLM_USAGE_DIR", default="")

# --- Records of questions and answers for evaluation (S3, rag_core/records.py) ----------------------------------------------
# The record level: off | anonymous | standard | detailed. DEFAULT is what applies until the console sets one (per tenant
# or for all); MAX is the highest level the console may set (the environment decides, not the console). Off by default:
# recording what people ask needs a notice to them and a decision on who may read it (docs/operations/records.md).
RECORD_LEVEL_DEFAULT = (getenv_first("RECORD_LEVEL_DEFAULT", default="off") or "off").strip().lower()
RECORD_LEVEL_MAX = (getenv_first("RECORD_LEVEL_MAX", default="standard") or "standard").strip().lower()
RECORDS_DIR = getenv_first("RECORDS_DIR", default="")
# Retention in days (a record older than this is anonymized or deleted by the purge); the detailed level keeps the
# device information (address, browser) for the shorter period only.
RECORDS_RETENTION_DAYS = int(getenv_first("RECORDS_RETENTION_DAYS", default="365"))
RECORDS_DETAILED_RETENTION_DAYS = int(getenv_first("RECORDS_DETAILED_RETENTION_DAYS", default="30"))
# What the purge does with an expired record: anonymize (keeps counts, drops text and identity) | delete.
RECORDS_EXPIRY_ACTION = (getenv_first("RECORDS_EXPIRY_ACTION", default="anonymize") or "anonymize").strip().lower()
# Secret salt of the pseudonyms of the anonymous level. Empty: a salt is made once and kept beside the runtime settings.
RECORDS_HASH_SALT = getenv_first("RECORDS_HASH_SALT", default="")
# Retention of the LLM usage files, in days (13 months).
LLM_USAGE_RETENTION_DAYS = int(getenv_first("LLM_USAGE_RETENTION_DAYS", default="400"))

# --- Golden questions, business hours (S4) ------------------------------------------------------------------------------------
# Where the golden questions kept in the console live: one JSON-lines file per tenant. Empty: <BASE_DIR>/data/golden_qa.
GOLDEN_QA_DIR = getenv_first("GOLDEN_QA_DIR", default="")
# Business hours for the availability figures (weekdays only; holidays are not known): the offset from UTC of the local
# time, and the first and last hour (08:00 to 21:00 by default).
BUSINESS_HOURS_UTC_OFFSET = _get_float("BUSINESS_HOURS_UTC_OFFSET", default=9.0)
BUSINESS_HOURS_START = int(getenv_first("BUSINESS_HOURS_START", default="8"))
BUSINESS_HOURS_END = int(getenv_first("BUSINESS_HOURS_END", default="21"))

# --- LLM limits, assignments and batches (S6, rag_core/llm_quota.py, llm_assignments.py, llm_batch.py) ----------------------------
# Daily limits (0 = no limit). The console can set them for all or for one tenant (runtime settings, with a reason); these are
# what applies until it does. The day is the local day of the business hours (BUSINESS_HOURS_UTC_OFFSET). User limits need an
# identified caller: without one (API authentication off) only the tenant and global limits apply.
LLM_QUOTA_USER_DAILY_CALLS = int(getenv_first("LLM_QUOTA_USER_DAILY_CALLS", default="0"))
LLM_QUOTA_USER_DAILY_TOKENS = int(getenv_first("LLM_QUOTA_USER_DAILY_TOKENS", default="0"))
LLM_QUOTA_TENANT_DAILY_TOKENS = int(getenv_first("LLM_QUOTA_TENANT_DAILY_TOKENS", default="0"))
LLM_QUOTA_TENANT_DAILY_COST = _get_float("LLM_QUOTA_TENANT_DAILY_COST", default=0.0)
LLM_QUOTA_GLOBAL_DAILY_COST = _get_float("LLM_QUOTA_GLOBAL_DAILY_COST", default=0.0)
# From this share of a limit the screens and the alerts warn; features that no limit applies to (scoring runs of an evaluation).
LLM_QUOTA_WARN_RATIO = _get_float("LLM_QUOTA_WARN_RATIO", default=0.8)
LLM_QUOTA_EXEMPT_FEATURES = [name.lower() for name in _get_list("LLM_QUOTA_EXEMPT_FEATURES", ["judge"])]
# How long the used amounts are kept before they are read again from the usage files (other workers write there too).
LLM_QUOTA_CACHE_SECONDS = _get_float("LLM_QUOTA_CACHE_SECONDS", default=10.0)
# A provider's batch API is cheaper; this factor is applied to the cost of a batched call (an assumption: check the provider's price).
LLM_BATCH_COST_FACTOR = _get_float("LLM_BATCH_COST_FACTOR", default=0.5)

# --- Two-person approval of turning an external integration on (S7, rag_core/approvals.py) ----------------------------------
# How many people a change that widens what may leave the system needs: 1 = it takes effect at once (a reason and the audit log
# remain); 2 = one person asks and a different person approves. Turning an integration OFF never waits for anyone.
INTEGRATION_APPROVALS_REQUIRED = int(getenv_first("INTEGRATION_APPROVALS_REQUIRED", default="2"))
# A request that nobody decided in this many hours expires.
APPROVAL_EXPIRY_HOURS = _get_float("APPROVAL_EXPIRY_HOURS", default=72.0)
# true: only people identified by single sign-on can approve (a name typed in the console is not enough to prove who someone is).
APPROVAL_REQUIRE_SSO = _get_bool("APPROVAL_REQUIRE_SSO", default=False)

# --- Availability measured by an external probe (S7, rag_core/availability.py, scripts/external_probe.py) -----------------------------
# The probe runs on another host and keeps a log; the service reads it to show the availability in business hours. Empty: not shown.
PROBE_LOG_FILE = getenv_first("PROBE_LOG_FILE", default="")
AVAILABILITY_TARGET_PERCENT = _get_float("AVAILABILITY_TARGET_PERCENT", default=99.0)
# A probe answer holds for at most this long; time beyond that is reported as unmonitored (the probe did not run).
PROBE_MAX_GAP_SECONDS = _get_float("PROBE_MAX_GAP_SECONDS", default=300.0)
