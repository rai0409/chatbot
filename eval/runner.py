from __future__ import annotations

import argparse
import json
import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config
from rag_core import qa, retrieval
from rag_core import embedder, embedding_provider, store
from rag_core.embedding_fingerprint import embedding_dim_from_collection, source_jsonl_sha256
from rag_core.retrieval import RetrievedChunk
from rag_core.utils import ensure_openai_client
from scripts.ingest_canonical_jsonl import ingest_canonical_rows

SCHEMA_VERSION = "eval_runner.v1"
RUNNER_VERSION = "pr5a-lightweight"
_COMPACT_ID_LIMIT = 5
_RETRIEVAL_MODES = ("bm25_only", "dense_only", "hybrid", "hybrid_rerank", "hybrid_rerank_ce")
DEFAULT_EVAL_COLLECTION_NAME = "eval_smoke_chunks_real_vector_v1"
DEFAULT_EVAL_TENANT_ID = "default"
_EXPECTATION_FIELDS = (
    "expected_top_chunk_id",
    "expected_top_source_doc",
    "expected_guard_reason",
    "expected_used_fallback",
    "answer_must_contain",
    "answer_must_not_contain",
    "selected_context_must_contain",
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    query: str
    intent_override: Optional[str]
    expected_top_chunk_id: Optional[str]
    expected_top_source_doc: Optional[str]
    expected_guard_reason: Optional[str]
    expected_used_fallback: Optional[bool]
    answer_must_contain: Tuple[str, ...]
    answer_must_not_contain: Tuple[str, ...]
    selected_context_must_contain: Tuple[str, ...]
    gold_doc_ids: Tuple[str, ...]
    gold_chunk_ids: Tuple[str, ...]
    should_abstain: Optional[bool]
    expected_abstain: Optional[bool]
    answerable: Optional[bool]
    query_type: Optional[str]
    expected_answer: Optional[str]
    notes: str
    expectation_keys: Tuple[str, ...]


class _StubEmbeddingItem:
    def __init__(self, embedding: List[float]):
        self.embedding = embedding


class _StubEmbeddingsResponse:
    def __init__(self, n_items: int):
        self.data = [_StubEmbeddingItem([0.0] * 8) for _ in range(n_items)]


class _StubEmbeddings:
    def create(self, model: str, input: Sequence[str]):
        return _StubEmbeddingsResponse(len(list(input)))


class _StubChatMessage:
    def __init__(self, content: str):
        self.content = content


class _StubChatChoice:
    def __init__(self, content: str):
        self.message = _StubChatMessage(content)


class _StubChatResponse:
    def __init__(self, content: str):
        self.choices = [_StubChatChoice(content)]


class _StubChatCompletions:
    def create(self, model: str, messages: Sequence[Dict[str, str]], temperature: float = 0, **kwargs):
        del model, messages, temperature, kwargs
        return _StubChatResponse("- 根拠に基づく回答です [S1]\n不足: なし [S1]")


class _StubChat:
    def __init__(self):
        self.completions = _StubChatCompletions()


class _StubClient:
    def __init__(self):
        self.embeddings = _StubEmbeddings()
        self.chat = _StubChat()


class _ClientWithStubbedChat:
    def __init__(self, base_client: Any):
        self.embeddings = base_client.embeddings
        self.chat = _StubChat()


def _default_cases_path() -> Path:
    return Path(__file__).resolve().parent / "cases" / "smoke_cases.jsonl"


def _default_chunks_path() -> Path:
    return Path(__file__).resolve().parent / "cases" / "smoke_chunks.jsonl"


def _normalize_case_record(record: Dict[str, Any], line_no: int) -> EvalCase:
    required = ["case_id", "category", "query"]
    missing = [k for k in required if str(record.get(k, "")).strip() == ""]
    if missing:
        raise ValueError(f"case line {line_no}: missing required fields: {', '.join(missing)}")

    present_expectations = tuple(k for k in _EXPECTATION_FIELDS if k in record)
    must_contain = tuple(str(x) for x in (record.get("answer_must_contain") or []))
    must_not_contain = tuple(str(x) for x in (record.get("answer_must_not_contain") or []))
    context_must_contain = tuple(str(x) for x in (record.get("selected_context_must_contain") or []))

    expected_used_fallback = record.get("expected_used_fallback")
    if expected_used_fallback is not None:
        expected_used_fallback = bool(expected_used_fallback)
    should_abstain = record.get("should_abstain")
    if should_abstain is not None:
        should_abstain = bool(should_abstain)
    expected_abstain = record.get("expected_abstain")
    if expected_abstain is not None:
        expected_abstain = bool(expected_abstain)
    answerable = record.get("answerable")
    if answerable is not None:
        answerable = bool(answerable)
    gold_doc_raw = record.get("gold_doc_ids") or []
    if isinstance(gold_doc_raw, str):
        gold_doc_raw = [gold_doc_raw]
    gold_chunk_raw = record.get("gold_chunk_ids") or []
    if isinstance(gold_chunk_raw, str):
        gold_chunk_raw = [gold_chunk_raw]

    return EvalCase(
        case_id=str(record["case_id"]),
        category=str(record["category"]),
        query=str(record["query"]),
        intent_override=(
            str(record["intent_override"]) if record.get("intent_override") not in (None, "") else None
        ),
        expected_top_chunk_id=(
            str(record["expected_top_chunk_id"])
            if record.get("expected_top_chunk_id") not in (None, "")
            else None
        ),
        expected_top_source_doc=(
            str(record["expected_top_source_doc"])
            if record.get("expected_top_source_doc") not in (None, "")
            else None
        ),
        expected_guard_reason=(
            str(record["expected_guard_reason"])
            if record.get("expected_guard_reason") not in (None, "")
            else None
        ),
        expected_used_fallback=expected_used_fallback,
        answer_must_contain=must_contain,
        answer_must_not_contain=must_not_contain,
        selected_context_must_contain=context_must_contain,
        gold_doc_ids=tuple(str(x) for x in gold_doc_raw),
        gold_chunk_ids=tuple(str(x) for x in gold_chunk_raw),
        should_abstain=should_abstain,
        expected_abstain=expected_abstain,
        answerable=answerable,
        query_type=(str(record["query_type"]) if record.get("query_type") not in (None, "") else None),
        expected_answer=(
            str(record["expected_answer"]) if record.get("expected_answer") not in (None, "") else None
        ),
        notes=str(record.get("notes") or record.get("why") or ""),
        expectation_keys=present_expectations,
    )


def load_cases(path: Path) -> List[EvalCase]:
    cases: List[EvalCase] = []
    with path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            cases.append(_normalize_case_record(obj, idx))
    return cases


def _reset_keyword_index_cache() -> None:
    retrieval._INDEX_CACHE["path"] = None  # type: ignore[attr-defined]
    retrieval._INDEX_CACHE["mtime"] = None  # type: ignore[attr-defined]
    retrieval._INDEX_CACHE["index"] = None  # type: ignore[attr-defined]


@contextmanager
def _generation_mode_runtime(real_generation: bool):
    """Isolate deterministic eval generation from the production default."""
    previous_mode = config.CHAT_GENERATION_MODE

    if not real_generation:
        config.CHAT_GENERATION_MODE = "llm"

    try:
        yield
    finally:
        config.CHAT_GENERATION_MODE = previous_mode


@contextmanager
def _eval_runtime(
    chunks_jsonl: Optional[Path],
    stub_vector: bool,
    eval_collection_name: Optional[str] = None,
):
    prev_chunks_path = config.CHUNKS_JSONL_PATH
    prev_vector_retrieve = retrieval.vector_retrieve
    prev_get_vectorstore = retrieval.store.get_vectorstore

    if chunks_jsonl is not None:
        config.CHUNKS_JSONL_PATH = str(chunks_jsonl.resolve())
    _reset_keyword_index_cache()

    if stub_vector:
        def _vector_stub(*args, **kwargs):
            del args, kwargs
            return []

        retrieval.vector_retrieve = _vector_stub  # type: ignore[assignment]
    elif eval_collection_name:
        def _eval_vectorstore(*args, **kwargs):
            requested = kwargs.pop("collection_name", None)
            kwargs.pop("create_if_missing", None)
            if requested not in (None, "", eval_collection_name):
                raise RuntimeError(
                    "real-vector evaluation attempted to access a collection outside its evaluation scope"
                )
            return prev_get_vectorstore(
                collection_name=eval_collection_name,
                create_if_missing=False,
                **kwargs,
            )

        retrieval.store.get_vectorstore = _eval_vectorstore  # type: ignore[assignment]

    try:
        yield
    finally:
        retrieval.vector_retrieve = prev_vector_retrieve
        retrieval.store.get_vectorstore = prev_get_vectorstore  # type: ignore[assignment]
        config.CHUNKS_JSONL_PATH = prev_chunks_path
        _reset_keyword_index_cache()


def _build_eval_client(real_vector: bool, real_generation: bool):
    if real_vector and not real_generation and embedder.is_local_provider():
        # Local MiniLM embedding needs no remote client; retain deterministic chat.
        return _StubClient()
    if real_vector or real_generation:
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        if real_vector and not real_generation:
            return _ClientWithStubbedChat(client)
        return client
    return _StubClient()


def _load_eval_corpus_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError(f"corpus line {line_no}: expected JSON object")
            chunk_id = str(record.get("id") or "").strip()
            text = str(record.get("text") or "").strip()
            if not chunk_id or not text:
                raise ValueError(f"corpus line {line_no}: id and text are required")
            if chunk_id in ids:
                raise ValueError(f"corpus line {line_no}: duplicate chunk id: {chunk_id}")
            ids.add(chunk_id)
            rows.append(record)
    if not rows:
        raise ValueError("evaluation corpus contains no chunks")
    return rows


def _assert_eval_collection_name(collection_name: str) -> None:
    production_name = config.resolve_chroma_collection_name()
    if collection_name == production_name:
        raise ValueError("evaluation collection must not be the production collection")


def _validate_eval_inputs(
    cases: Sequence[EvalCase], rows: Sequence[Dict[str, Any]], tenant_id: str
) -> None:
    corpus_chunk_ids = {str(row["id"]) for row in rows}
    corpus_doc_ids = {str(row.get("doc_id") or row.get("source_doc") or "") for row in rows}
    missing_chunks = sorted({gold for case in cases for gold in case.gold_chunk_ids if gold not in corpus_chunk_ids})
    missing_docs = sorted({gold for case in cases for gold in case.gold_doc_ids if gold not in corpus_doc_ids})
    if missing_chunks:
        raise ValueError(f"gold chunk ids missing from evaluation corpus: {', '.join(missing_chunks)}")
    if missing_docs:
        raise ValueError(f"gold document ids missing from evaluation corpus: {', '.join(missing_docs)}")
    normalized_tenant = str(tenant_id or DEFAULT_EVAL_TENANT_ID).strip() or DEFAULT_EVAL_TENANT_ID
    visible = [
        row for row in rows
        if str(row.get("tenant_id") or DEFAULT_EVAL_TENANT_ID).strip() == normalized_tenant
    ]
    if not visible:
        raise ValueError(f"tenant filter excludes every evaluation corpus record: {normalized_tenant}")


def build_real_vector_eval_collection(
    *,
    chunks_jsonl: Path,
    cases_path: Path,
    collection_name: str = DEFAULT_EVAL_COLLECTION_NAME,
    tenant_id: str = DEFAULT_EVAL_TENANT_ID,
    client=None,
) -> Dict[str, Any]:
    """Rebuild an isolated Chroma collection used only by --real-vector eval."""
    collection_name = str(collection_name).strip() or DEFAULT_EVAL_COLLECTION_NAME
    _assert_eval_collection_name(collection_name)
    rows = _load_eval_corpus_rows(chunks_jsonl)
    cases = load_cases(cases_path)
    _validate_eval_inputs(cases, rows, tenant_id)

    ingest_canonical_rows(
        rows,
        collection_name=collection_name,
        reset=True,
        source_jsonl_path=str(chunks_jsonl),
        client=client,
    )
    collection = store.get_vectorstore(
        collection_name=collection_name,
        verify_embedding_fingerprint=False,
        create_if_missing=False,
    )
    inserted_count = int(collection.count())
    if inserted_count != len(rows):
        raise RuntimeError(
            f"evaluation collection count mismatch: corpus={len(rows)} vectors={inserted_count}"
        )
    collection_dimension = embedding_dim_from_collection(collection)
    metadata = dict(getattr(collection, "metadata", None) or {})
    metadata_dimension = metadata.get("embedding_dim")
    try:
        stamped_dimension = int(metadata_dimension)
    except (TypeError, ValueError):
        stamped_dimension = None
    if collection_dimension is None or stamped_dimension != int(collection_dimension):
        raise RuntimeError("evaluation collection embedding dimension metadata does not match stored vectors")
    if not cases:
        raise ValueError("evaluation cases contain no records")
    query_dimension = len(embedder.embed_queries([cases[0].query], client=client)[0])
    if query_dimension != collection_dimension:
        raise RuntimeError(
            f"query embedding dimension mismatch: query={query_dimension} collection={collection_dimension}"
        )

    fingerprint = embedding_provider.active_fingerprint()
    eval_metadata = {
        "embedding_provider": fingerprint["embed_provider"],
        "embedding_model": fingerprint["embed_model"],
        "embedding_dimension": collection_dimension,
        "normalization": (
            "l2"
            if fingerprint["embed_provider"] in {embedding_provider.LOCAL_PROVIDER, embedding_provider.BGE_M3_PROVIDER}
            else "provider_default"
        ),
        "corpus_fingerprint": source_jsonl_sha256(chunks_jsonl),
        "collection_name": collection_name,
        "inserted_record_count": inserted_count,
    }
    mutable_metadata = {
        key: value for key, value in metadata.items() if not str(key).startswith("hnsw:")
    }
    mutable_metadata.update(eval_metadata)
    collection.modify(metadata=mutable_metadata)
    return eval_metadata


@contextmanager
def _retrieval_mode_runtime(mode: str):
    if mode not in _RETRIEVAL_MODES:
        raise ValueError(f"unsupported retrieval mode: {mode}")
    prev_hybrid = qa.hybrid_retrieve
    prev_rerank = qa.rerank_chunks
    prev_cross_encoder_enabled = config.CROSS_ENCODER_RERANK_ENABLED

    def _hybrid_bridge(
        question: str,
        client: Any,
        top_k: int,
        allowed_types=None,
        allowed_qualities=None,
        vector_top_k: Optional[int] = None,
        bm25_top_k: Optional[int] = None,
        rrf_k: Optional[int] = None,
        query_embedding=None,
        tenant_id: str = "default",
    ):
        return retrieval.hybrid_retrieve(
            question,
            client,
            top_k=top_k,
            allowed_types=allowed_types,
            allowed_qualities=allowed_qualities,
            vector_top_k=vector_top_k,
            bm25_top_k=bm25_top_k,
            rrf_k=rrf_k,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
        )

    def _bm25_only_bridge(
        question: str,
        client: Any,
        top_k: int,
        allowed_types=None,
        allowed_qualities=None,
        vector_top_k: Optional[int] = None,
        bm25_top_k: Optional[int] = None,
        rrf_k: Optional[int] = None,
        query_embedding=None,
        tenant_id: str = "default",
    ):
        del client, vector_top_k, rrf_k, query_embedding
        return retrieval.keyword_retrieve(
            question,
            top_k=bm25_top_k or top_k,
            allowed_types=allowed_types,
            allowed_qualities=allowed_qualities,
            tenant_id=tenant_id,
        )

    def _dense_only_bridge(
        question: str,
        client: Any,
        top_k: int,
        allowed_types=None,
        allowed_qualities=None,
        vector_top_k: Optional[int] = None,
        bm25_top_k: Optional[int] = None,
        rrf_k: Optional[int] = None,
        query_embedding=None,
        tenant_id: str = "default",
    ):
        del bm25_top_k, rrf_k
        return retrieval.vector_retrieve(
            question,
            client,
            top_k=vector_top_k or top_k,
            allowed_types=allowed_types,
            allowed_qualities=allowed_qualities,
            query_embedding=query_embedding,
            tenant_id=tenant_id,
        )

    def _identity_rerank(
        question: str,
        chunks: Sequence[RetrievedChunk],
        intent: str = "other",
    ):
        del question, intent
        return list(chunks)

    if mode == "bm25_only":
        qa.hybrid_retrieve = _bm25_only_bridge  # type: ignore[assignment]
        qa.rerank_chunks = _identity_rerank  # type: ignore[assignment]
    elif mode == "dense_only":
        qa.hybrid_retrieve = _dense_only_bridge  # type: ignore[assignment]
        qa.rerank_chunks = _identity_rerank  # type: ignore[assignment]
    elif mode == "hybrid":
        qa.hybrid_retrieve = _hybrid_bridge  # type: ignore[assignment]
        qa.rerank_chunks = _identity_rerank  # type: ignore[assignment]
    elif mode == "hybrid_rerank_ce":
        # Heuristic rerank plus the optional cross-encoder stage; compare
        # against hybrid_rerank via eval/rerank_promotion_gate.py before any
        # default change.
        qa.hybrid_retrieve = _hybrid_bridge  # type: ignore[assignment]
        config.CROSS_ENCODER_RERANK_ENABLED = True
    else:
        qa.hybrid_retrieve = _hybrid_bridge  # type: ignore[assignment]

    try:
        yield
    finally:
        qa.hybrid_retrieve = prev_hybrid  # type: ignore[assignment]
        qa.rerank_chunks = prev_rerank  # type: ignore[assignment]
        config.CROSS_ENCODER_RERANK_ENABLED = prev_cross_encoder_enabled


def _chunk_to_view(rank: int, ch: RetrievedChunk) -> Dict[str, Any]:
    meta = ch.metadata or {}
    return {
        "rank": rank,
        "chunk_id": str(meta.get("id") or ""),
        "source_doc": str(meta.get("source_doc") or meta.get("doc") or "unknown"),
        "score": float(ch.score),
        "retrieval_source": str(meta.get("retrieval_source") or ""),
    }


def top_entries(chunks: Sequence[RetrievedChunk], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rank, ch in enumerate(chunks[:limit], start=1):
        out.append(_chunk_to_view(rank, ch))
    return out


def _expectations_dict(case: EvalCase) -> Dict[str, Any]:
    values = {
        "expected_top_chunk_id": case.expected_top_chunk_id,
        "expected_top_source_doc": case.expected_top_source_doc,
        "expected_guard_reason": case.expected_guard_reason,
        "expected_used_fallback": case.expected_used_fallback,
        "answer_must_contain": list(case.answer_must_contain),
        "answer_must_not_contain": list(case.answer_must_not_contain),
        "selected_context_must_contain": list(case.selected_context_must_contain),
    }
    return {k: values[k] for k in case.expectation_keys}


def evaluate_expectations(
    case: EvalCase,
    *,
    after_rerank_top: Sequence[Dict[str, Any]],
    guard_reason: Optional[str],
    used_fallback: bool,
    answer_text: str,
    selected_context_preview: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    checks: Dict[str, Dict[str, Any]] = {}
    top = after_rerank_top[0] if after_rerank_top else {}

    if "expected_top_chunk_id" in case.expectation_keys:
        expected = case.expected_top_chunk_id
        actual = top.get("chunk_id") if top else None
        checks["expected_top_chunk_id"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "expected_top_source_doc" in case.expectation_keys:
        expected = case.expected_top_source_doc
        actual = top.get("source_doc") if top else None
        checks["expected_top_source_doc"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "expected_guard_reason" in case.expectation_keys:
        expected = case.expected_guard_reason
        actual = guard_reason
        checks["expected_guard_reason"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "expected_used_fallback" in case.expectation_keys:
        expected = case.expected_used_fallback
        actual = used_fallback
        checks["expected_used_fallback"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "answer_must_contain" in case.expectation_keys:
        missing = [term for term in case.answer_must_contain if term not in answer_text]
        checks["answer_must_contain"] = {
            "pass": not missing,
            "expected": list(case.answer_must_contain),
            "actual": {"missing": missing},
        }

    if "answer_must_not_contain" in case.expectation_keys:
        present = [term for term in case.answer_must_not_contain if term in answer_text]
        checks["answer_must_not_contain"] = {
            "pass": not present,
            "expected": list(case.answer_must_not_contain),
            "actual": {"present": present},
        }

    if "selected_context_must_contain" in case.expectation_keys:
        context_blob = "\n".join(str(x) for x in selected_context_preview)
        missing = [term for term in case.selected_context_must_contain if term not in context_blob]
        checks["selected_context_must_contain"] = {
            "pass": not missing,
            "expected": list(case.selected_context_must_contain),
            "actual": {"missing": missing},
        }

    return checks


def _overall_pass(checks: Dict[str, Dict[str, Any]]) -> bool:
    if not checks:
        return True
    return all(bool(v.get("pass")) for v in checks.values())


def _answer_summary(answer_text: str, max_chars: int = 80) -> str:
    lines = [ln.strip() for ln in answer_text.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    if len(first) <= max_chars:
        return first
    return first[: max_chars - 1] + "…"


def _format_top(items: Sequence[Dict[str, Any]]) -> str:
    if not items:
        return "-"
    return ", ".join(f"{it.get('chunk_id') or '?'}@{it.get('source_doc') or '?'}" for it in items)


def _chunk_id(ch: RetrievedChunk) -> str:
    meta = ch.metadata or {}
    return str(meta.get("id") or "")


def _source_doc(ch: RetrievedChunk) -> str:
    meta = ch.metadata or {}
    return str(meta.get("source_doc") or meta.get("doc") or "unknown")


def _best_rank_by_chunk_id(chunks: Sequence[RetrievedChunk], gold_chunk_ids: Sequence[str]) -> Optional[int]:
    if not gold_chunk_ids:
        return None
    targets = {str(x) for x in gold_chunk_ids if str(x)}
    if not targets:
        return None
    for rank, ch in enumerate(chunks, start=1):
        if _chunk_id(ch) in targets:
            return rank
    return None


def _best_rank_by_doc_id(chunks: Sequence[RetrievedChunk], gold_doc_ids: Sequence[str]) -> Optional[int]:
    if not gold_doc_ids:
        return None
    targets = {str(x) for x in gold_doc_ids if str(x)}
    if not targets:
        return None
    for rank, ch in enumerate(chunks, start=1):
        if _source_doc(ch) in targets:
            return rank
    return None


def _rerank_gain(before_rank: Optional[int], after_rank: Optional[int]) -> Optional[int]:
    if before_rank is None or after_rank is None:
        return None
    return before_rank - after_rank


def _trace_chunk_list(trace: Dict[str, Any], key: str) -> Optional[Sequence[RetrievedChunk]]:
    raw = trace.get(key)
    if isinstance(raw, (list, tuple)):
        return raw
    return None


def _compact_ids(chunks: Optional[Sequence[RetrievedChunk]], limit: int = _COMPACT_ID_LIMIT) -> Optional[List[str]]:
    if chunks is None:
        return None
    return [_chunk_id(ch) for ch in chunks[:limit]]


def _expected_abstain(case: EvalCase) -> Optional[bool]:
    if case.expected_abstain is not None:
        return bool(case.expected_abstain)
    if case.should_abstain is not None:
        return bool(case.should_abstain)
    if case.answerable is not None:
        return not bool(case.answerable)
    return None


def _best_rank_from_ids(ranked_ids: Sequence[str], gold_ids: Sequence[str]) -> Optional[int]:
    if not gold_ids:
        return None
    targets = {str(x) for x in gold_ids if str(x)}
    if not targets:
        return None
    for rank, value in enumerate(ranked_ids, start=1):
        if value in targets:
            return rank
    return None


def _hit_at_k(best_rank: Optional[int], k: int) -> Optional[bool]:
    if best_rank is None:
        return None
    return best_rank <= k


def _mrr_at_k(best_rank: Optional[int], k: int) -> Optional[float]:
    if best_rank is None:
        return None
    if best_rank > k:
        return 0.0
    return 1.0 / float(best_rank)


def _binary_relevance_gain(value: str, relevant_ids: set[str]) -> float:
    return 1.0 if value in relevant_ids else 0.0


def _dcg_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    dcg = 0.0
    seen_relevant_ids: set[str] = set()
    for rank, value in enumerate(ranked_ids[:k], start=1):
        if value in seen_relevant_ids:
            continue
        gain = _binary_relevance_gain(value, relevant_ids)
        if gain:
            seen_relevant_ids.add(value)
            dcg += gain / math.log2(rank + 1.0)
    return dcg


def _ndcg_at_k(ranked_ids: Sequence[str], gold_ids: Sequence[str], k: int) -> Optional[float]:
    relevant_ids = {str(value) for value in gold_ids if str(value)}
    if not relevant_ids or k <= 0:
        return None
    dcg = _dcg_at_k(ranked_ids, relevant_ids, k)
    idcg = _dcg_at_k(list(relevant_ids), relevant_ids, k)
    if idcg <= 0:
        return None
    return dcg / idcg


def run_eval(
    *,
    cases_path: Path,
    chunks_jsonl: Optional[Path],
    output_path: Path,
    top_k: int,
    max_context_chars: int,
    top_n: int,
    real_vector: bool,
    real_generation: bool,
    eval_collection_name: str = DEFAULT_EVAL_COLLECTION_NAME,
    tenant_id: str = DEFAULT_EVAL_TENANT_ID,
    quiet: bool = False,
) -> Dict[str, Any]:
    cases = load_cases(cases_path)
    eval_collection_metadata = None
    client = _build_eval_client(real_vector=real_vector, real_generation=real_generation)
    if real_vector:
        if chunks_jsonl is None:
            raise ValueError("--real-vector requires --chunks-jsonl")
        eval_collection_metadata = build_real_vector_eval_collection(
            chunks_jsonl=chunks_jsonl,
            cases_path=cases_path,
            collection_name=eval_collection_name,
            tenant_id=tenant_id,
            client=client,
        )
    mode = {
        "intent": "real",
        "query_rewrite": "real",
        "retrieval": (
            "real_hybrid(vector+keyword)"
            if real_vector
            else "real_hybrid(keyword_real,vector_stubbed_empty)"
        ),
        "unique_chunk_handling": "real",
        "neighbor_expansion": "real",
        "rerank": "real",
        "grounded_shaping": "real",
        "guard": "real",
        "fallback": "real",
        "generation": "real" if real_generation else "stubbed_deterministic",
    }

    results: List[Dict[str, Any]] = []

    with (
        _generation_mode_runtime(real_generation=real_generation),
        _eval_runtime(
            chunks_jsonl=chunks_jsonl,
            stub_vector=not real_vector,
            eval_collection_name=eval_collection_metadata["collection_name"] if eval_collection_metadata else None,
        ),
    ):
        for case in cases:
            answer, trace = qa.answer_query_with_trace(
                case.query,
                client=client,
                top_k=top_k,
                max_context_chars=max_context_chars,
                intent_override=case.intent_override,
                tenant_id=tenant_id,
            )
            before_raw = _trace_chunk_list(trace, "before_rerank")
            after_raw = _trace_chunk_list(trace, "after_rerank")
            before = top_entries(before_raw or [], top_n)
            after = top_entries(after_raw or [], top_n)

            gold_chunk_best_rank_before = _best_rank_by_chunk_id(before_raw or [], case.gold_chunk_ids)
            gold_chunk_best_rank_after = _best_rank_by_chunk_id(after_raw or [], case.gold_chunk_ids)
            gold_doc_best_rank_before = _best_rank_by_doc_id(before_raw or [], case.gold_doc_ids)
            gold_doc_best_rank_after = _best_rank_by_doc_id(after_raw or [], case.gold_doc_ids)

            rerank_gain: Optional[int] = None
            if case.gold_chunk_ids:
                rerank_gain = _rerank_gain(gold_chunk_best_rank_before, gold_chunk_best_rank_after)
            elif case.gold_doc_ids:
                rerank_gain = _rerank_gain(gold_doc_best_rank_before, gold_doc_best_rank_after)
            abstain_check_pass: Optional[bool] = None
            if case.should_abstain is not None:
                abstain_check_pass = bool(answer.used_fallback) == bool(case.should_abstain)

            checks = evaluate_expectations(
                case,
                after_rerank_top=after,
                guard_reason=answer.guard_reason,
                used_fallback=answer.used_fallback,
                answer_text=answer.answer_text,
                selected_context_preview=trace.get("selected_context_preview", []),
            )
            case_pass = _overall_pass(checks)
            before_top_id = before[0].get("chunk_id") if before else None
            after_top_id = after[0].get("chunk_id") if after else None
            rerank_top_changed: Optional[bool] = None
            if before_top_id is not None and after_top_id is not None:
                rerank_top_changed = bool(before_top_id != after_top_id)

            record = {
                "case_id": case.case_id,
                "category": case.category,
                "query": case.query,
                "intent": answer.intent,
                "before_rerank_top": before,
                "after_rerank_top": after,
                "final_guard_reason": answer.guard_reason,
                "final_used_fallback": answer.used_fallback,
                "final_answer_summary": _answer_summary(answer.answer_text),
                "selected_context_preview": trace.get("selected_context_preview", []),
                "expectations": _expectations_dict(case),
                "checks": checks,
                "overall_pass": case_pass,
                "evaluation_mode": mode,
                "rewritten_query": answer.rewritten_query,
                "augmented_query": answer.augmented_query,
                "gold_doc_ids": list(case.gold_doc_ids),
                "gold_chunk_ids": list(case.gold_chunk_ids),
                "should_abstain": case.should_abstain,
                "gold_chunk_hit": (
                    (gold_chunk_best_rank_after is not None) if case.gold_chunk_ids else None
                ),
                "gold_chunk_best_rank": gold_chunk_best_rank_after,
                "gold_chunk_best_rank_before": gold_chunk_best_rank_before,
                "gold_chunk_best_rank_after": gold_chunk_best_rank_after,
                "gold_doc_hit": (gold_doc_best_rank_after is not None) if case.gold_doc_ids else None,
                "gold_doc_best_rank": gold_doc_best_rank_after,
                "gold_doc_best_rank_before": gold_doc_best_rank_before,
                "gold_doc_best_rank_after": gold_doc_best_rank_after,
                "abstain_check_pass": abstain_check_pass,
                "rerank_top_changed": rerank_top_changed,
                "rerank_gain": rerank_gain,
                "before_rerank_ids": _compact_ids(before_raw),
                "after_rerank_ids": _compact_ids(after_raw),
            }
            results.append(record)

            if not quiet:
                status = "PASS" if case_pass else "FAIL"
                print(
                    f"[{status}] {case.case_id} ({case.category}) "
                    f"before={_format_top(before)} after={_format_top(after)} "
                    f"guard={answer.guard_reason} fallback={answer.used_fallback} "
                    f"answer={record['final_answer_summary']}"
                )

    passed = sum(1 for r in results if r["overall_pass"])
    failed = len(results) - passed
    summary = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed_cases": passed,
        "failed_cases": failed,
        "gold_chunk_cases": sum(1 for r in results if r.get("gold_chunk_ids")),
        "gold_chunk_hits": sum(1 for r in results if r.get("gold_chunk_hit") is True),
        "gold_doc_cases": sum(1 for r in results if r.get("gold_doc_ids")),
        "gold_doc_hits": sum(1 for r in results if r.get("gold_doc_hit") is True),
        "abstain_cases": sum(1 for r in results if r.get("should_abstain") is not None),
        "abstain_passes": sum(1 for r in results if r.get("abstain_check_pass") is True),
    }
    payload = {
        "summary": summary,
        "cases": results,
    }
    if eval_collection_metadata:
        payload["evaluation_collection"] = eval_collection_metadata

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not quiet:
        print(
            f"\nSummary: passed={passed}/{len(results)} failed={failed} "
            f"output={output_path}"
        )
    return payload


def run_retrieval_aware_eval(
    *,
    cases_path: Path,
    chunks_jsonl: Optional[Path],
    per_query_output_path: Path,
    summary_output_path: Path,
    modes: Sequence[str],
    top_k: int,
    max_context_chars: int,
    real_vector: bool,
    real_generation: bool,
    eval_collection_name: str = DEFAULT_EVAL_COLLECTION_NAME,
    tenant_id: str = DEFAULT_EVAL_TENANT_ID,
    eval_k: int = 5,
    quiet: bool = False,
) -> Dict[str, Any]:
    mode_list = [str(m).strip() for m in modes if str(m).strip()]
    if not mode_list:
        raise ValueError("at least one retrieval mode is required")
    invalid = [m for m in mode_list if m not in _RETRIEVAL_MODES]
    if invalid:
        raise ValueError(f"unsupported retrieval modes: {', '.join(invalid)}")

    client = _build_eval_client(real_vector=real_vector, real_generation=real_generation)
    eval_collection_metadata = None
    if real_vector:
        if chunks_jsonl is None:
            raise ValueError("--real-vector requires --chunks-jsonl")
        eval_collection_metadata = build_real_vector_eval_collection(
            chunks_jsonl=chunks_jsonl,
            cases_path=cases_path,
            collection_name=eval_collection_name,
            tenant_id=tenant_id,
            client=client,
        )
    cases = load_cases(cases_path)
    rows: List[Dict[str, Any]] = []

    with _eval_runtime(
        chunks_jsonl=chunks_jsonl,
        stub_vector=not real_vector,
        eval_collection_name=eval_collection_metadata["collection_name"] if eval_collection_metadata else None,
    ):
        for mode in mode_list:
            with _retrieval_mode_runtime(mode):
                for case in cases:
                    answer, trace = qa.answer_query_with_trace(
                        case.query,
                        client=client,
                        top_k=top_k,
                        max_context_chars=max_context_chars,
                        intent_override=case.intent_override,
                        tenant_id=tenant_id,
                    )
                    before_raw = _trace_chunk_list(trace, "before_rerank")
                    after_raw = _trace_chunk_list(trace, "after_rerank")

                    before_chunk_ids = _compact_ids(before_raw)
                    after_chunk_ids = _compact_ids(after_raw)
                    before_chunk_ids_full = [_chunk_id(ch) for ch in (before_raw or [])]
                    after_chunk_ids_full = [_chunk_id(ch) for ch in (after_raw or [])]
                    before_doc_ids = (
                        [_source_doc(ch) for ch in before_raw[:_COMPACT_ID_LIMIT]]
                        if before_raw is not None
                        else None
                    )
                    after_doc_ids = (
                        [_source_doc(ch) for ch in after_raw[:_COMPACT_ID_LIMIT]]
                        if after_raw is not None
                        else None
                    )
                    before_doc_ids_full = [_source_doc(ch) for ch in (before_raw or [])]
                    after_doc_ids_full = [_source_doc(ch) for ch in (after_raw or [])]

                    gold_chunk_best_rank_before = _best_rank_from_ids(before_chunk_ids_full, case.gold_chunk_ids)
                    gold_chunk_best_rank_after = _best_rank_from_ids(after_chunk_ids_full, case.gold_chunk_ids)
                    gold_doc_best_rank_before = _best_rank_from_ids(before_doc_ids_full, case.gold_doc_ids)
                    gold_doc_best_rank_after = _best_rank_from_ids(after_doc_ids_full, case.gold_doc_ids)

                    if case.gold_chunk_ids:
                        best_before = gold_chunk_best_rank_before
                        best_after = gold_chunk_best_rank_after
                        ranked_for_metric = after_chunk_ids_full
                        gold_for_metric = list(case.gold_chunk_ids)
                    else:
                        best_before = gold_doc_best_rank_before
                        best_after = gold_doc_best_rank_after
                        ranked_for_metric = after_doc_ids_full
                        gold_for_metric = list(case.gold_doc_ids)

                    expected_abstain = _expected_abstain(case)
                    abstain_correct: Optional[bool] = None
                    if expected_abstain is not None:
                        abstain_correct = bool(answer.used_fallback) == bool(expected_abstain)

                    before_top = before_chunk_ids[0] if before_chunk_ids else None
                    after_top = after_chunk_ids[0] if after_chunk_ids else None
                    rerank_top_changed: Optional[bool] = None
                    if before_top is not None and after_top is not None:
                        rerank_top_changed = bool(before_top != after_top)

                    row = {
                        "case_id": case.case_id,
                        "question": case.query,
                        "mode": mode,
                        "query_type": case.query_type,
                        "gold_doc_ids": list(case.gold_doc_ids),
                        "gold_chunk_ids": list(case.gold_chunk_ids),
                        "gold_doc_hit": _hit_at_k(gold_doc_best_rank_after, eval_k),
                        "gold_chunk_hit": _hit_at_k(gold_chunk_best_rank_after, eval_k),
                        "gold_doc_hit_at_k": _hit_at_k(gold_doc_best_rank_after, eval_k),
                        "gold_chunk_hit_at_k": _hit_at_k(gold_chunk_best_rank_after, eval_k),
                        "best_rank_before_rerank": best_before,
                        "best_rank_after_rerank": best_after,
                        "gold_doc_best_rank_before": gold_doc_best_rank_before,
                        "gold_doc_best_rank_after": gold_doc_best_rank_after,
                        "gold_chunk_best_rank_before": gold_chunk_best_rank_before,
                        "gold_chunk_best_rank_after": gold_chunk_best_rank_after,
                        "rerank_gain": _rerank_gain(best_before, best_after),
                        "rerank_top_changed": rerank_top_changed,
                        "before_rerank_ids": before_chunk_ids,
                        "after_rerank_ids": after_chunk_ids,
                        "guard_reason": answer.guard_reason,
                        "used_fallback": bool(answer.used_fallback),
                        "expected_abstain": expected_abstain,
                        "abstain_correct": abstain_correct,
                        "mrr_at_k": _mrr_at_k(best_after, eval_k),
                        "ndcg_at_k": _ndcg_at_k(ranked_for_metric, gold_for_metric, eval_k),
                        "eval_k": eval_k,
                    }
                    rows.append(row)
                    if not quiet:
                        print(
                            f"[{mode}] {case.case_id} "
                            f"gold_doc_hit={row['gold_doc_hit']} "
                            f"gold_chunk_hit={row['gold_chunk_hit']} "
                            f"guard={row['guard_reason']} fallback={row['used_fallback']}"
                        )

    summary_by_mode: Dict[str, Dict[str, Any]] = {}
    for mode in mode_list:
        mode_rows = [r for r in rows if r["mode"] == mode]
        mrr_vals = [float(v) for v in (r.get("mrr_at_k") for r in mode_rows) if isinstance(v, (int, float))]
        ndcg_vals = [float(v) for v in (r.get("ndcg_at_k") for r in mode_rows) if isinstance(v, (int, float))]
        summary_by_mode[mode] = {
            "cases": len(mode_rows),
            "gold_chunk_cases": sum(1 for r in mode_rows if r.get("gold_chunk_ids")),
            "gold_chunk_hits": sum(1 for r in mode_rows if r.get("gold_chunk_hit") is True),
            "gold_doc_cases": sum(1 for r in mode_rows if r.get("gold_doc_ids")),
            "gold_doc_hits": sum(1 for r in mode_rows if r.get("gold_doc_hit") is True),
            "abstain_labeled_cases": sum(1 for r in mode_rows if r.get("expected_abstain") is not None),
            "abstain_expected_cases": sum(1 for r in mode_rows if r.get("expected_abstain") is True),
            "abstain_passes": sum(1 for r in mode_rows if r.get("abstain_correct") is True),
            "mean_mrr_at_k": (sum(mrr_vals) / len(mrr_vals)) if mrr_vals else None,
            "mean_ndcg_at_k": (sum(ndcg_vals) / len(ndcg_vals)) if ndcg_vals else None,
        }

    summary = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_k": int(eval_k),
        "modes": mode_list,
        "total_rows": len(rows),
        "by_mode": summary_by_mode,
    }
    if eval_collection_metadata:
        summary["evaluation_collection"] = eval_collection_metadata

    dense_rows = [row for row in rows if row["mode"] == "dense_only"]
    dense_only_all_empty = bool(real_vector and dense_rows and all(not row["before_rerank_ids"] for row in dense_rows))
    if dense_only_all_empty:
        summary["status"] = "error"
        summary["error"] = "dense_only real-vector retrieval returned no candidates for every case"
    else:
        summary["status"] = "ok"

    per_query_output_path.parent.mkdir(parents=True, exist_ok=True)
    with per_query_output_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary_output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if dense_only_all_empty:
        raise RuntimeError(summary["error"])
    return {"summary": summary, "rows": rows}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Lightweight repo-native smoke evaluator for grounded JA RAG "
            "(retrieval/rerank/guard/fallback regression checks)."
        ),
        epilog=(
            "Default mode is deterministic/local-friendly: generation is stubbed and "
            "vector retrieval is stubbed empty unless --real-vector is enabled. "
            "This is not a full live end-to-end answer quality benchmark."
        ),
    )
    parser.add_argument("--cases", default=str(_default_cases_path()))
    parser.add_argument("--chunks-jsonl", default=str(_default_chunks_path()))
    parser.add_argument("--output", default="")
    parser.add_argument("--top-k", type=int, default=config.TOP_K)
    parser.add_argument("--max-context-chars", type=int, default=config.MAX_CONTEXT_CHARS)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument(
        "--real-vector",
        action="store_true",
        help=(
            "Enable real vector retrieval (default: vector retrieval is stubbed empty; "
            "keyword retrieval remains real)."
        ),
    )
    parser.add_argument(
        "--real-generation",
        action="store_true",
        help="Enable real chat generation (default: deterministic stubbed generation).",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--retrieval-aware",
        action="store_true",
        help="Run retrieval-aware eval (per-query JSONL + aggregate summary JSON).",
    )
    parser.add_argument(
        "--modes",
        default="bm25_only,dense_only,hybrid,hybrid_rerank",
        help="Comma-separated retrieval modes for retrieval-aware eval.",
    )
    parser.add_argument("--per-query-output", default="")
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--eval-k", type=int, default=5)
    parser.add_argument(
        "--eval-collection",
        default=DEFAULT_EVAL_COLLECTION_NAME,
        help="Isolated collection rebuilt for --real-vector evaluation only.",
    )
    parser.add_argument("--tenant-id", default=DEFAULT_EVAL_TENANT_ID)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    cases_path = Path(args.cases)
    chunks_arg = str(args.chunks_jsonl).strip()
    chunks_jsonl = Path(chunks_arg) if chunks_arg else None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if bool(args.retrieval_aware):
        if args.per_query_output:
            per_query_output = Path(args.per_query_output)
        else:
            per_query_output = Path(config.RUNS_DIR) / "eval" / f"retrieval_rows_{ts}.jsonl"
        if args.summary_output:
            summary_output = Path(args.summary_output)
        else:
            summary_output = Path(config.RUNS_DIR) / "eval" / f"retrieval_summary_{ts}.json"
        modes = [m.strip() for m in str(args.modes).split(",") if m.strip()]
        run_retrieval_aware_eval(
            cases_path=cases_path,
            chunks_jsonl=chunks_jsonl,
            per_query_output_path=per_query_output,
            summary_output_path=summary_output,
            modes=modes,
            top_k=args.top_k,
            max_context_chars=args.max_context_chars,
            real_vector=bool(args.real_vector),
            real_generation=bool(args.real_generation),
            eval_collection_name=str(args.eval_collection),
            tenant_id=str(args.tenant_id),
            eval_k=int(args.eval_k),
            quiet=bool(args.quiet),
        )
    else:
        if not args.output:
            output_path = Path(config.RUNS_DIR) / "eval" / f"results_{ts}.json"
        else:
            output_path = Path(args.output)
        run_eval(
            cases_path=cases_path,
            chunks_jsonl=chunks_jsonl,
            output_path=output_path,
            top_k=args.top_k,
            max_context_chars=args.max_context_chars,
            top_n=args.top_n,
            real_vector=bool(args.real_vector),
            real_generation=bool(args.real_generation),
            eval_collection_name=str(args.eval_collection),
            tenant_id=str(args.tenant_id),
            quiet=bool(args.quiet),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
