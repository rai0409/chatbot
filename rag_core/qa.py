from __future__ import annotations

import re
import time
import uuid
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

import config
from rag_core import embedding_provider
from rag_core.keyword_scorer import apply_keyword_boost, classify_query_type, score_keyword_match
from rag_core.reranker import rerank_chunks
from rag_core.retrieval import QueryEmbeddingBatch, RetrievedChunk, add_neighbor_chunks, expand_parent_chunks, hybrid_retrieve, vector_retrieve
from rag_core.utils import ensure_openai_client
from rag_grounded import Chunk, build_citation_payloads, build_evidence_blocks, build_prompt, extractive_fallback, merge_by_page, rewrite_query, strip_reference_block, strip_source_tags, to_footnotes, validate_output
from schemas import AnswerResult, CitationOut, RetrievedChunkOut


def retrieve_chunks(question: str, client, top_k: int, allowed_types=None, allowed_qualities=None) -> List[RetrievedChunk]:
    # Vector-only helper kept for backward compatibility (/search endpoint).
    # The primary answer path in answer_query() uses hybrid_retrieve().
    return vector_retrieve(
        question,
        client,
        top_k=top_k,
        allowed_types=allowed_types,
        allowed_qualities=allowed_qualities,
    )


def infer_intent(question: str) -> str:
    q = question
    reset_terms = config.RESET_TERMS
    change_terms = config.CHANGE_TERMS
    if any(t in q for t in reset_terms):
        return "reset"
    if any(t in q for t in change_terms):
        return "change"
    negation_patterns = [
        r"(手順|方法|やり方|操作)\s*ではない",
        r"(手順|方法|やり方|操作)\s*ではありません",
        r"(手順|方法|やり方|操作)\s*じゃない",
    ]
    if any(re.search(p, q) for p in negation_patterns):
        return "other"
    if any(t in q for t in config.PROCEDURE_STRONG_TERMS):
        return "procedure"
    if any(t in q for t in config.PROCEDURE_WEAK_TERMS):
        return "procedure"
    if any(t in q for t in config.DEFINITION_TERMS):
        return "other"
    return "other"


def _soft_threshold(intent: str) -> float:
    if intent == "reset":
        return config.RAG_SOFT_DIST_RESET
    if intent == "change":
        return config.RAG_SOFT_DIST_CHANGE
    if intent == "procedure":
        return config.RAG_SOFT_DIST_PROCEDURE
    return config.RAG_SOFT_DIST_OTHER


def _salient_terms(question: str) -> List[str]:
    terms = []
    terms += re.findall(r"「([^」]+)」", question)
    terms += re.findall(r'"([^"]+)"', question)
    terms += re.findall(r"[A-Za-z0-9]+", question)
    def _norm(t: str) -> str:
        t = re.sub(r"[\s\[\]\(\)（）]", "", t)
        return t
    out = []
    for t in terms:
        nt = _norm(t)
        if nt and nt not in config.STOP_SALIENT_TERMS:
            out.append(nt)
    return out


def _code_like_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,}", text or ""):
        token = raw.lower().rstrip("._:/-")
        if re.fullmatch(r"[a-z0-9][a-z0-9._:/-]{1,}", token):
            tokens.add(token)
    return tokens


def _short_lookup_core(question: str) -> str:
    core = re.sub(r"[?？。!！]", "", question or "")
    core = core.strip().strip("「」\"'")
    core = re.sub(r"\s+", "", core)
    for suffix in ("とは何か", "とは", "って何", "の意味", "の定義", "の仕様", "の確認方法"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    return core.strip("「」\"'")


def _should_bypass_too_general(question: str, retrieved: Sequence[RetrievedChunk]) -> bool:
    if not retrieved:
        return False
    top_text = retrieved[0].text or ""
    second_text = retrieved[1].text if len(retrieved) > 1 else ""
    top_code_tokens = _code_like_tokens(top_text)

    quoted_terms = []
    quoted_terms += re.findall(r"「([^」]+)」", question)
    quoted_terms += re.findall(r'"([^"]+)"', question)
    quoted_terms += re.findall(r"'([^']+)'", question)
    for term in quoted_terms:
        code = re.sub(r"\s+", "", term or "").lower()
        if not code:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9._:/-]{1,}", code) and re.search(r"\d", code):
            if code in top_code_tokens:
                return True

    for code in _code_like_tokens(question):
        if re.search(r"\d", code) and code in top_code_tokens:
            return True

    # Japanese short lookup queries can be meaningful if top-1 evidence has a localized exact hit.
    lookup_core = _short_lookup_core(question)
    if 2 <= len(lookup_core) <= 12:
        if re.search(r"\d", lookup_core) or re.search(r"[一-龥々〆〤ァ-ヴー]{2,}", lookup_core):
            top_norm = re.sub(r"\s+", "", top_text)
            second_norm = re.sub(r"\s+", "", second_text)
            if lookup_core in top_norm and lookup_core not in second_norm:
                return True

    glossary_terms = []
    glossary_terms += re.findall(r"[ァ-ヴー]{2,}語", question)
    glossary_terms += re.findall(r"[一-龥々〆〤]{2,}語", question)
    seen = set()
    for term in glossary_terms:
        if term in seen:
            continue
        seen.add(term)
        if term in top_text and term not in second_text:
            return True
    return False


def _guard_evidence(retrieved: Sequence[RetrievedChunk]) -> Dict[str, object]:
    # Real evidence signals for the guard. Rank-derived pseudo-distances on
    # RetrievedChunk.score are ordering artifacts, never confidence evidence.
    best_vector_distance: Optional[float] = None
    best_bm25_score: Optional[float] = None
    has_lexical_match = False
    for ch in retrieved:
        meta = ch.metadata or {}
        vector_distance = meta.get("vector_distance")
        if vector_distance is not None:
            value = float(vector_distance)
            if best_vector_distance is None or value < best_vector_distance:
                best_vector_distance = value
        bm25_score = meta.get("bm25_score")
        if bm25_score is not None:
            value = float(bm25_score)
            if best_bm25_score is None or value > best_bm25_score:
                best_bm25_score = value
        details = meta.get("score_details")
        if not has_lexical_match and isinstance(details, dict):
            if float(details.get("keyword_score") or 0.0) > 0.0 or list(
                details.get("matched_terms") or []
            ):
                has_lexical_match = True
    return {
        "best_vector_distance": best_vector_distance,
        "best_bm25_score": best_bm25_score,
        "has_lexical_match": has_lexical_match,
    }


def guard_merged_top(question: str, intent: str, chunks: Sequence[Chunk], retrieved: Sequence[RetrievedChunk]) -> Optional[str]:
    if config.DISABLE_GUARD:
        return None
    if not retrieved:
        return "no_results"
    evidence = _guard_evidence(retrieved)
    best_vector_distance = evidence["best_vector_distance"]
    if best_vector_distance is not None:
        best = float(best_vector_distance)
        hard = config.RAG_HARD_MAX_DIST
        if intent == "procedure":
            has_pages = any(ch.source_pages for ch in chunks)
            if has_pages:
                hard = hard + config.RAG_HARD_MAX_DIST_PROCEDURE_DELTA
        if best > hard:
            return "hard_distance"
        soft = _soft_threshold(intent)
        if best > soft:
            if intent == "change":
                if any(t in question for t in config.PREFER_SORT_TERMS_CHANGE):
                    return None
            return "soft_distance"
    else:
        # No vector evidence (stubbed or empty): never fabricate a distance.
        # Require explicit keyword evidence instead.
        best_bm25_score = evidence["best_bm25_score"]
        if (
            best_bm25_score is not None
            and float(best_bm25_score) <= config.RAG_MIN_KEYWORD_EVIDENCE_BM25
            and not evidence["has_lexical_match"]
        ):
            return "weak_keyword_evidence"
    salient = _salient_terms(question)
    if salient:
        evidence = " ".join(ch.text for ch in chunks)
        if not any(t in evidence for t in salient):
            return "salient_mismatch"
    if intent == "other":
        if len(re.findall(r"[A-Za-z0-9ぁ-んァ-ン一-龥]+", question)) <= 2:
            req = ["ID", "番号", "許容値", "フラグ"]
            if not any(r in question for r in req):
                if _should_bypass_too_general(question, retrieved):
                    return None
                return "too_general"
    return None


def _compose_query(question: str, intent: str) -> str:
    q = question
    if intent in {"reset", "change"}:
        q = q + " 手順 方法"
    if intent == "other":
        q = q + " " + " ".join(config.OTHER_QUERY_BOOST_TERMS)
    return q


def _unique_chunks(chunks: Sequence[RetrievedChunk]) -> List[RetrievedChunk]:
    seen = set()
    out = []
    for ch in chunks:
        key = ch.metadata.get("id") or (ch.text[:120] + str(ch.metadata.get("doc_id")))
        if key in seen:
            continue
        seen.add(key)
        out.append(ch)
    return out


def _to_grounded(chunks: Sequence[RetrievedChunk]) -> List[Chunk]:
    out = []
    for idx, ch in enumerate(chunks, start=1):
        meta = ch.metadata or {}
        pages = meta.get("source_pages") or meta.get("pages") or []
        if isinstance(pages, str):
            pages = [p for p in re.findall(r"\d+", pages)]
        pages = tuple(int(p) for p in pages) if pages else tuple()
        citation_id = str(meta.get("primary_child_chunk_id") or meta.get("id") or idx)
        out.append(
            Chunk(
                id=citation_id,
                text=ch.text,
                source_doc=str(meta.get("source_doc") or meta.get("doc") or "unknown"),
                source_pages=pages,
                score=ch.score,
            )
        )
    return out


def _page_diversity(chunks: Sequence[Chunk], max_per_page: int = 3) -> List[Chunk]:
    counts: Dict[Tuple[str, Tuple[int, ...]], int] = {}
    out = []
    for ch in chunks:
        if ch.source_pages:
            key = (ch.source_doc, ch.source_pages)
        else:
            key = (ch.source_doc, ch.source_pages, ch.id)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= max_per_page:
            out.append(ch)
    return out


def _prefer_sort(chunks: Sequence[Chunk], intent: str) -> List[Chunk]:
    if intent == "reset":
        terms = config.PREFER_SORT_TERMS_RESET
    elif intent == "change":
        terms = config.PREFER_SORT_TERMS_CHANGE
    elif intent == "procedure":
        terms = config.PREFER_SORT_TERMS_PROCEDURE
    else:
        terms = config.PREFER_SORT_TERMS_OTHER

    def key(ch: Chunk):
        hit = any(t in ch.text for t in terms)
        score = ch.score if ch.score is not None else 1.0
        return (0 if hit else 1, score)

    return sorted(chunks, key=key)


def _cut_context(chunks: Sequence[Chunk], max_chars: int) -> List[Chunk]:
    total = 0
    out = []
    for ch in chunks:
        if total >= max_chars:
            break
        out.append(ch)
        total += len(ch.text)
    return out


class _RetrievalTraceState(NamedTuple):
    normalized_query: str
    intent: str
    query_type: str
    rewritten_query: str
    augmented_query: str
    retrieved: List[RetrievedChunk]
    grounded_candidates: List[Chunk]
    selected_context: List[Chunk]
    guard_reason: Optional[str]
    used_fallback: bool


def _to_retrieved_out(chunks: Sequence[RetrievedChunk]) -> List[RetrievedChunkOut]:
    out: List[RetrievedChunkOut] = []
    for ch in chunks:
        meta = ch.metadata or {}
        pages = meta.get("source_pages") or meta.get("pages") or []
        if isinstance(pages, str):
            pages = [p for p in re.findall(r"\d+", pages)]
        out.append(
            RetrievedChunkOut(
                text=ch.text,
                metadata=meta,
                score=float(ch.score),
                source_doc=str(meta.get("source_doc") or meta.get("doc") or "unknown"),
                source_pages=[int(p) for p in pages] if pages else [],
            )
        )
    return out


def _decorate_score_details(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    query_type: str,
) -> List[RetrievedChunk]:
    out: List[RetrievedChunk] = []
    for ch in chunks:
        meta = dict(ch.metadata or {})
        details = score_keyword_match(query, ch.text, meta, query_type=query_type)
        details["query_type"] = query_type
        details["base_score"] = float(ch.score)
        if meta.get("retrieval_source") is not None:
            details["retrieval_source"] = meta.get("retrieval_source")
        if meta.get("rrf_score") is not None:
            details["rrf_score"] = meta.get("rrf_score")
        if meta.get("rerank_score") is not None:
            details["rerank_score"] = meta.get("rerank_score")
        if meta.get("bm25_score") is not None:
            details["bm25_score"] = meta.get("bm25_score")
        details.setdefault("keyword_boost_applied", False)
        details.setdefault("keyword_boost_value", 0.0)
        if details.get("score_before_keyword_boost") is None:
            details["score_before_keyword_boost"] = float(ch.score)
        if details.get("score_after_keyword_boost") is None:
            details["score_after_keyword_boost"] = float(ch.score)
        details.setdefault("boost_reason", [])
        details["final_debug_score"] = float(ch.score)
        meta["score_details"] = details
        out.append(RetrievedChunk(text=ch.text, metadata=meta, score=float(ch.score)))
    return out


def _build_answer_result(
    answer_text: str,
    answer_chunks: Sequence[Chunk],
    *,
    intent: str,
    guard_reason: Optional[str],
    used_fallback: bool,
    retrieved: Sequence[RetrievedChunk],
    rewritten_query: str,
    augmented_query: str,
) -> AnswerResult:
    citations = [CitationOut(**item) for item in build_citation_payloads(answer_text, answer_chunks)]
    return AnswerResult(
        answer_text=strip_source_tags(answer_text),
        answer_with_footnotes=to_footnotes(answer_text, answer_chunks),
        intent=intent,
        guard_reason=guard_reason,
        used_fallback=used_fallback,
        citations=citations,
        retrieved=_to_retrieved_out(retrieved),
        rewritten_query=rewritten_query,
        augmented_query=augmented_query,
    )


def _retrieve_and_rerank(
    question: str,
    augmented_query: str,
    *,
    scoring_query: str,
    client,
    top_k: int,
    intent: str,
    query_type: str,
) -> Tuple[List[RetrievedChunk], List[RetrievedChunk], List[RetrievedChunk]]:
    if augmented_query == question:
        # Identical queries would produce identical passes; retrieve once.
        base = hybrid_retrieve(
            question,
            client,
            top_k=top_k,
            vector_top_k=config.VECTOR_TOP_K,
            bm25_top_k=config.BM25_TOP_K,
            rrf_k=config.HYBRID_RRF_K,
        )
        aug = base
    else:
        # Lazy batch: both queries are embedded in one embed_queries call the
        # first time vector retrieval asks for an embedding; stubbed or
        # keyword-only paths never trigger embedding.
        embedding_batch = QueryEmbeddingBatch([question, augmented_query], client=client)
        base = hybrid_retrieve(
            question,
            client,
            top_k=top_k,
            vector_top_k=config.VECTOR_TOP_K,
            bm25_top_k=config.BM25_TOP_K,
            rrf_k=config.HYBRID_RRF_K,
            query_embedding=lambda: embedding_batch.get(question),
        )
        aug = hybrid_retrieve(
            augmented_query,
            client,
            top_k=top_k,
            vector_top_k=config.VECTOR_TOP_K,
            bm25_top_k=config.BM25_TOP_K,
            rrf_k=config.HYBRID_RRF_K,
            query_embedding=lambda: embedding_batch.get(augmented_query),
        )
    before_rerank = _unique_chunks(base + aug)
    if intent == "procedure":
        before_rerank = _unique_chunks(add_neighbor_chunks(before_rerank))
    before_rerank = _decorate_score_details(scoring_query, before_rerank, query_type=query_type)
    child_ranked = rerank_chunks(question, before_rerank, intent=intent)
    if config.KEYWORD_BOOST_ENABLED and query_type in set(config.KEYWORD_BOOST_QUERY_TYPES):
        child_ranked = apply_keyword_boost(
            child_ranked,
            query_type=query_type,
            max_boost=config.KEYWORD_BOOST_MAX_DELTA,
        )
    context_ranked = expand_parent_chunks(
        child_ranked,
        max_parent_chunks=getattr(config, "MAX_PARENT_EXPANDED_CHUNKS", top_k),
        max_parent_context_chars=getattr(config, "MAX_PARENT_CONTEXT_CHARS", max(1200, top_k * 400)),
    )
    context_ranked = _decorate_score_details(scoring_query, context_ranked, query_type=query_type)
    return before_rerank, child_ranked, context_ranked


def _set_final_trace(
    trace: Dict[str, object],
    started_at: float,
    answer_chunks: Sequence[Chunk],
    *,
    guard_reason: Optional[str],
    used_fallback: bool,
    answer_mode: str,
    citations_count: int,
) -> None:
    # Option B semantics:
    # - selected_context_*: final chunks passed to answer generation/fallback
    # - grounded_candidate_chunk_ids: broader grounded candidates before final selection
    trace["final_guard_reason"] = guard_reason
    trace["final_used_fallback"] = used_fallback
    trace["answer_mode"] = answer_mode
    trace["selected_context_chunk_ids"] = [ch.id for ch in answer_chunks]
    trace["selected_context_chars"] = sum(len(ch.text) for ch in answer_chunks)
    trace["selected_context_preview"] = [ch.text[:160] for ch in answer_chunks[:3]]
    trace["citations_count"] = citations_count
    trace["latency_ms"] = int((time.perf_counter() - started_at) * 1000)


def _build_retrieval_trace(
    question: str,
    *,
    client,
    top_k: int,
    max_context_chars: int,
    intent_override: Optional[str] = None,
    started_at: Optional[float] = None,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, object], _RetrievalTraceState, float]:
    started_at = started_at if started_at is not None else time.perf_counter()
    request_id = request_id or uuid.uuid4().hex[:12]
    original_question = question

    q = question.strip()
    q = re.sub(r"^質問\s*:\s*", "", q)
    classification_query = q.strip()
    q = classification_query.strip("「」\"'")
    q = re.sub(r"\s+", " ", q)
    intent = intent_override or infer_intent(q)
    query_type = classify_query_type(classification_query, intent=intent)
    rewritten = rewrite_query(q)
    augmented = _compose_query(rewritten, intent)

    if client is None and not embedding_provider.is_local_provider():
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
    before_rerank, retrieved, context_candidates = _retrieve_and_rerank(
        q,
        augmented,
        scoring_query=classification_query,
        client=client,
        top_k=top_k,
        intent=intent,
        query_type=query_type,
    )

    trace: Dict[str, object] = {
        "request_id": request_id,
        "original_query": original_question,
        "normalized_query": q,
        "question": q,
        "intent": intent,
        "query_type": query_type,
        "rewritten_query": rewritten,
        "augmented_query": augmented,
        "before_rerank": before_rerank,
        "after_rerank": retrieved,
        "after_parent_expansion": context_candidates,
        "retrieval_before_rerank_count": len(before_rerank),
        "retrieval_after_rerank_count": len(retrieved),
        "retrieval_after_parent_expansion_count": len(context_candidates),
    }

    guard_grounded = _to_grounded(retrieved)
    guard_grounded = merge_by_page(guard_grounded)
    if intent == "procedure":
        max_per_page = config.PROCEDURE_MAX_PER_PAGE
    elif intent in {"change", "reset"}:
        max_per_page = config.CHANGE_RESET_MAX_PER_PAGE
    else:
        max_per_page = config.OTHER_MAX_PER_PAGE
    guard_grounded = _page_diversity(guard_grounded, max_per_page=max_per_page)
    trace["guard_candidate_chunk_ids"] = [ch.id for ch in guard_grounded]

    grounded = _to_grounded(context_candidates)
    grounded = merge_by_page(grounded)
    grounded = _page_diversity(grounded, max_per_page=max_per_page)
    trace["grounded_candidate_chunk_ids"] = [ch.id for ch in grounded]

    guard_reason = None
    used_fallback = False
    selected_context: List[Chunk]

    if intent in {"change", "reset"}:
        if not any(term in " ".join(ch.text for ch in grounded) for term in config.PROCEDURE_STRONG_TERMS):
            guard_reason = "missing_procedure_evidence"
            used_fallback = True

    if guard_reason is None:
        guard_reason = guard_merged_top(q, intent, guard_grounded, retrieved)
        used_fallback = guard_reason is not None

    if guard_reason == "missing_procedure_evidence":
        selected_context = grounded[:1] or [Chunk("1", "OCR", "unknown", tuple(), None)]
    elif guard_reason:
        selected_context = grounded[:1] or [Chunk("1", "該当なし", "unknown", tuple(), None)]
    else:
        selected_context = _prefer_sort(grounded, intent)
        selected_context = _cut_context(selected_context, max_context_chars)

    state = _RetrievalTraceState(
        normalized_query=q,
        intent=intent,
        query_type=query_type,
        rewritten_query=rewritten,
        augmented_query=augmented,
        retrieved=retrieved,
        grounded_candidates=grounded,
        selected_context=selected_context,
        guard_reason=guard_reason,
        used_fallback=used_fallback,
    )
    return trace, state, started_at


def debug_retrieve_with_trace(
    question: str,
    client=None,
    top_k: int = 20,
    max_context_chars: int = 8000,
    intent_override: Optional[str] = None,
) -> Dict[str, object]:
    trace, state, started_at = _build_retrieval_trace(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
    )
    _set_final_trace(
        trace,
        started_at,
        state.selected_context,
        guard_reason=state.guard_reason,
        used_fallback=state.used_fallback,
        answer_mode="debug_retrieval_only",
        citations_count=0,
    )
    return trace


def _answer_query_impl(
    question: str,
    client=None,
    top_k: int = 20,
    max_context_chars: int = 8000,
    intent_override: Optional[str] = None,
) -> Tuple[AnswerResult, Dict[str, object]]:
    started_at = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]
    trace, state, started_at = _build_retrieval_trace(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
        started_at=started_at,
        request_id=request_id,
    )
    q = state.normalized_query
    intent = state.intent
    rewritten = state.rewritten_query
    augmented = state.augmented_query
    retrieved = state.retrieved

    if state.guard_reason == "missing_procedure_evidence":
        fallback = "- 手順の記載が見つかりません。OCR版を確認してください。 [S1]"
        raw = fallback + "\n不明: 手順不明 [S1]\n不足: OCR版確認 [S1]"
        answer_chunks = state.selected_context
        result = _build_answer_result(
            raw,
            answer_chunks,
            intent=intent,
            guard_reason=state.guard_reason,
            used_fallback=True,
            retrieved=retrieved,
            rewritten_query=rewritten,
            augmented_query=augmented,
        )
        _set_final_trace(
            trace,
            started_at,
            answer_chunks,
            guard_reason=result.guard_reason,
            used_fallback=result.used_fallback,
            answer_mode="fallback",
            citations_count=len(result.citations),
        )
        return result, trace

    if state.guard_reason:
        body = f"- 関連情報が見つかりませんでした。理由: {state.guard_reason} [S1]"
        raw = body + "\n不明: 根拠不足 [S1]\n不足: 関連記載なし [S1]"
        answer_chunks = state.selected_context
        result = _build_answer_result(
            raw,
            answer_chunks,
            intent=intent,
            guard_reason=state.guard_reason,
            used_fallback=True,
            retrieved=retrieved,
            rewritten_query=rewritten,
            augmented_query=augmented,
        )
        _set_final_trace(
            trace,
            started_at,
            answer_chunks,
            guard_reason=result.guard_reason,
            used_fallback=result.used_fallback,
            answer_mode="fallback",
            citations_count=len(result.citations),
        )
        return result, trace

    grounded = state.selected_context

    evidence_blocks = build_evidence_blocks(grounded)
    prompt = build_prompt(q, evidence_blocks)
    if client is None:
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    raw = resp.choices[0].message.content or ""
    raw = strip_reference_block(raw)
    used_fallback = False
    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):
        raw = extractive_fallback(q, grounded)
        used_fallback = True

    if "不足:" not in raw and "不明:" not in raw:
        raw = raw.strip() + "\n不足: なし [S1]"
    answer_chunks = grounded
    result = _build_answer_result(
        raw,
        answer_chunks,
        intent=intent,
        guard_reason=None,
        used_fallback=used_fallback,
        retrieved=retrieved,
        rewritten_query=rewritten,
        augmented_query=augmented,
    )
    _set_final_trace(
        trace,
        started_at,
        answer_chunks,
        guard_reason=result.guard_reason,
        used_fallback=result.used_fallback,
        answer_mode="fallback" if used_fallback else "grounded",
        citations_count=len(result.citations),
    )
    return result, trace


def answer_query(question: str, client=None, top_k: int = 20, max_context_chars: int = 8000, intent_override: Optional[str] = None) -> AnswerResult:
    result, _ = _answer_query_impl(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
    )
    return result


def answer_query_with_trace(
    question: str,
    client=None,
    top_k: int = 20,
    max_context_chars: int = 8000,
    intent_override: Optional[str] = None,
) -> Tuple[AnswerResult, Dict[str, object]]:
    return _answer_query_impl(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
    )
