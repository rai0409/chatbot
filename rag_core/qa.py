from __future__ import annotations

import os
import re
import time
import uuid
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import config
from rag_core import embedding_provider, ja_terms, query_preprocess
from rag_core.cross_encoder_reranker import rerank as cross_encoder_rerank
from rag_core.ja_text import normalize_for_index, normalize_japanese_text
from rag_core.keyword_scorer import apply_keyword_boost, classify_query_type, score_keyword_match
from rag_core import answer_rewrite, grounding, interpretation, llm, multi_query, query_route, search_mode
from rag_core.profile_engine import apply_profile_boost
from rag_core.profile_loader import load_rag_profile
from rag_core.profile_validation import validate_answer_with_profile
from rag_core.question_types import detect_question_type
from rag_core.reranker import rerank_chunks
from rag_core.retrieval import QueryEmbeddingBatch, RetrievedChunk, add_neighbor_chunks, expand_parent_chunks, fusion_scale, hybrid_retrieve, keyword_index_path_for_collection, normalize_tenant_id, vector_retrieve
from rag_core.utils import ensure_openai_client
from rag_grounded import Chunk, build_citation_payloads, build_evidence_blocks, build_messages, drop_empty_shortage_lines, extractive_fallback, merge_by_page, rewrite_query, strip_reference_block, strip_source_tags, to_footnotes, validate_output
from schemas import AnswerResult, CitationOut, RetrievedChunkOut


def retrieve_chunks(
    question: str,
    client,
    top_k: int,
    allowed_types=None,
    allowed_qualities=None,
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
    create_collection_if_missing: bool = True,
) -> List[RetrievedChunk]:
    # Vector-only helper kept for backward compatibility (/search endpoint).
    # The primary answer path in answer_query() uses hybrid_retrieve().
    return vector_retrieve(
        question,
        client,
        top_k=top_k,
        allowed_types=allowed_types,
        allowed_qualities=allowed_qualities,
        tenant_id=tenant_id,
        collection_name=collection_name,
        create_collection_if_missing=create_collection_if_missing,
    )


_ENABLED_VALUES = {"1", "true", "yes", "on"}
# Words that make a "change" question a how-to question.
_PROCEDURE_CUES = ("方法", "手順", "やり方", "どうやって", "どうすれば", "どうしたら", "したい", "するには", "できますか", "仕方")
_DEFINITION_ENDINGS = ("とは", "とは何", "って何", "ってなに", "の意味", "の定義")


def _setting(name: str, default: str) -> str:
    # Stage-5 switches: config attribute first, then the environment. Each
    # switch keeps the previous behaviour as "legacy" so a change that does
    # not improve the bench can be reverted without a code change.
    value = getattr(config, name, None)
    if value is None:
        value = os.getenv(name, default)
    return str(value if value is not None else default).strip().lower()


def infer_intent(question: str) -> str:
    q = question
    reset_terms = config.RESET_TERMS
    change_terms = config.CHANGE_TERMS
    if any(t in q for t in reset_terms):
        return "reset"
    # "変更"/"更新" alone ("規程の変更点", "更新日") is not a how-to question:
    # the change intent (and its procedure guard) needs a procedure cue.
    needs_cue = _setting("INTENT_RULES", "cue_required") in {"cue_required", "morph"}
    has_cue = any(t in q for t in _PROCEDURE_CUES) or any(t in q for t in config.PROCEDURE_STRONG_TERMS)
    if not has_cue and _setting("INTENT_RULES", "cue_required") == "morph":
        # N7: verb + たい / には / 方法 found by morphology (Sudachi), which
        # also covers inflections the cue list misses (変えたい, 直すには).
        has_cue = ja_terms.has_procedure_form(q)
    if any(t in q for t in change_terms) and (has_cue or not needs_cue):
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
    # Weak terms (設定, 登録, 変更, 削除, 更新) name an object, not a how-to;
    # they make a procedure question only together with a procedure cue.
    if any(t in q for t in config.PROCEDURE_WEAK_TERMS) and (has_cue or not needs_cue):
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

    return _evidence_coverage_bypass(question, retrieved)


# Evidence-coverage bypass thresholds, calibrated on the Prompt017 real-vector
# baseline (runs/eval/too_general_guard_analysis.json: fixes 11/16 false
# abstains, breaks 0/8 correct abstains). The too_general token-run count
# treats whole Japanese sentences as <=2 "tokens", so real business questions
# were guarded as if they were vague.
_TOO_GENERAL_CONTENT_TERM_RE = re.compile(r"[ァ-ヴー]{2,}|[一-龥]{2,}|[A-Za-z0-9]{2,}")
_TOO_GENERAL_WORD_RUN_RE = re.compile(r"[A-Za-z0-9ぁ-んァ-ヴー一-龥]+")
_TOO_GENERAL_MIN_FULL_QUESTION_CONTENT_LEN = 12
_TOO_GENERAL_MIN_SINGLE_TERM_LEN = 3


def _evidence_coverage_bypass(question: str, retrieved: Sequence[RetrievedChunk]) -> bool:
    """Bypass too_general when the top-1 evidence fully covers the query terms.

    Two conservative branches, both requiring EVERY content term (kanji /
    katakana / alnum run) of the query to appear in the top-1 chunk:
    - a full-length business question (content >= 12 chars) is not "general"
      when its terms are all evidenced;
    - a single specific lookup term (>= 3 chars) evidenced in the top chunk
      is a legitimate lookup, not vagueness.
    Genuinely vague queries keep firing: no content terms (これは？), generic
    2-char single terms (運用は？), and short multi-term overview asks
    (電子入札制度の概要は？ stays guarded because it is short with 2 terms).
    """
    top_meta = retrieved[0].metadata or {}
    top_text = str(top_meta.get("searchable_text") or "") or (retrieved[0].text or "")
    question_norm = normalize_japanese_text(question or "")
    top_norm = normalize_japanese_text(top_text)
    if not top_norm:
        return False
    terms = list(dict.fromkeys(_TOO_GENERAL_CONTENT_TERM_RE.findall(question_norm)))
    if not terms or any(t not in top_norm for t in terms):
        return False
    content_len = sum(len(run) for run in _TOO_GENERAL_WORD_RUN_RE.findall(question_norm))
    if content_len >= _TOO_GENERAL_MIN_FULL_QUESTION_CONTENT_LEN:
        return True
    return len(terms) == 1 and len(terms[0]) >= _TOO_GENERAL_MIN_SINGLE_TERM_LEN


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


_DEICTIC_ONLY_RE = re.compile(
    r"(これ|それ|あれ|この|その|あの|こちら|そちら|あちら|ここ|そこ|あそこ)"
    r"(は|って|が|を|の|とは)?(なに|何|なん)?(です|でしょう)?(か)?[?？。!！]*"
)


def distance_guard_calibration() -> Dict[str, object]:
    """Whether the distance thresholds were calibrated for the active embedding model."""
    from rag_core import embedding_provider

    try:
        active = str(embedding_provider.active_fingerprint().get("embed_model") or "")
    except Exception:  # noqa: BLE001 - provider misconfigured: reported elsewhere
        active = ""
    calibrated_for = str(getattr(config, "RAG_DIST_CALIBRATED_MODEL", "") or "")
    return {
        "active_model": active,
        "calibrated_for": calibrated_for,
        "calibrated": bool(active) and active == calibrated_for,
        "soft_other": config.RAG_SOFT_DIST_OTHER,
        "hard": config.RAG_HARD_MAX_DIST,
    }


def guard_merged_top(question: str, intent: str, chunks: Sequence[Chunk], retrieved: Sequence[RetrievedChunk]) -> Optional[str]:
    if config.DISABLE_GUARD:
        return None
    # Only a demonstrative ("これは？", "それって？"): the question does not
    # name its subject, whatever the search found (keyword hits on "これ" are
    # noise). Other questions keep the reasons below ("何か" with no hit stays
    # no_results).
    if _DEICTIC_ONLY_RE.fullmatch(normalize_for_index(question).strip()):
        return "too_general"
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
        # Both sides use the index normalization (case, width, dashes).
        evidence = normalize_for_index(" ".join(ch.text for ch in chunks))
        if not any(normalize_for_index(t) in evidence for t in salient):
            return "salient_mismatch"
    if intent == "other":
        if len(re.findall(r"[a-z0-9ぁ-んァ-ヴー一-龥々〆〤]+", normalize_for_index(question))) <= 2:
            req = ["ID", "番号", "許容値", "フラグ"]
            if not any(r in question for r in req):
                if _should_bypass_too_general(question, retrieved):
                    return None
                return "too_general"
    return None


def _is_definition_question(question: str) -> bool:
    q = re.sub(r"[?？。!！\s]+$", "", question or "")
    return q.endswith(_DEFINITION_ENDINGS) or any(t in q for t in config.DEFINITION_TERMS)


def _compose_query(question: str, intent: str) -> str:
    q = question
    if intent in {"reset", "change"}:
        q = q + " 手順 方法"
    if intent == "other":
        # Definition words are added only to definition questions; other
        # questions keep a single retrieval pass (augmented == question).
        if _setting("COMPOSE_QUERY_MODE", "definition_only") == "legacy" or _is_definition_question(q):
            q = q + " " + " ".join(config.OTHER_QUERY_BOOST_TERMS)
    return q.strip()


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

    mode = _setting("PREFER_SORT_MODE", "tiebreak")
    if mode == "off":
        return list(chunks)
    if mode == "legacy":
        def key(ch: Chunk):
            hit = any(t in ch.text for t in terms)
            score = ch.score if ch.score is not None else 1.0
            return (0 if hit else 1, score)

        return sorted(chunks, key=key)
    # tiebreak: the rerank order wins; the intent terms only reorder chunks
    # that have exactly the same score.
    out: List[Chunk] = []
    group: List[Chunk] = []
    for ch in chunks:
        if group and ch.score != group[-1].score:
            out.extend(sorted(group, key=lambda c: 0 if any(t in c.text for t in terms) else 1))
            group = []
        group.append(ch)
    out.extend(sorted(group, key=lambda c: 0 if any(t in c.text for t in terms) else 1))
    return out


def _cut_context(chunks: Sequence[Chunk], max_chars: int) -> List[Chunk]:
    total = 0
    out = []
    for ch in chunks:
        if total >= max_chars:
            break
        out.append(ch)
        total += len(ch.text)
    return out


def _is_retryable_generation_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            status = int(status_code)
        except (TypeError, ValueError):
            status = None
        if status is not None:
            # 429 and provider-side 5xx are transient; other 4xx (auth,
            # validation) never get retried.
            return status == 429 or 500 <= status <= 599
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


def _create_chat_completion(client, messages):
    attempts = 1 + max(0, int(getattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)))
    backoff = float(getattr(config, "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS", 1.0))
    for attempt in range(attempts):
        try:
            return client.chat.completions.create(
                model=config.CHAT_MODEL,
                messages=messages,
                temperature=0,
                timeout=float(getattr(config, "CHAT_COMPLETION_TIMEOUT_SECONDS", 30.0)),
                max_tokens=int(getattr(config, "CHAT_COMPLETION_MAX_TOKENS", 1024)),
            )
        except Exception as exc:
            if attempt >= attempts - 1 or not _is_retryable_generation_error(exc):
                raise
            time.sleep(backoff * (2 ** attempt))
    raise RuntimeError("unreachable: chat completion retry loop exhausted")


def _stream_chat_completion(client, messages):
    # Same timeout/max_tokens/retry policy as _create_chat_completion, but
    # retries happen only before the first content token; never mid-stream.
    attempts = 1 + max(0, int(getattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)))
    backoff = float(getattr(config, "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS", 1.0))
    for attempt in range(attempts):
        emitted = False
        try:
            stream = client.chat.completions.create(
                model=config.CHAT_MODEL,
                messages=messages,
                temperature=0,
                timeout=float(getattr(config, "CHAT_COMPLETION_TIMEOUT_SECONDS", 30.0)),
                max_tokens=int(getattr(config, "CHAT_COMPLETION_MAX_TOKENS", 1024)),
                stream=True,
            )
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                delta = ""
                if choices:
                    delta = getattr(getattr(choices[0], "delta", None), "content", None) or ""
                if delta:
                    emitted = True
                    yield delta
            return
        except Exception as exc:
            if emitted or attempt >= attempts - 1 or not _is_retryable_generation_error(exc):
                raise
            time.sleep(backoff * (2 ** attempt))


# User-facing wording per guard reason. The reason code itself stays in the
# guard_reason field (API, audit, metrics) and is never shown in the answer.
_GUARD_MESSAGES = {
    "no_results": "該当する文書が見つかりませんでした。",
    "hard_distance": "質問に十分に関連する記載が見つかりませんでした。",
    "soft_distance": "質問に十分に関連する記載が見つかりませんでした。",
    "weak_keyword_evidence": "質問の語句を含む記載が見つかりませんでした。",
    "salient_mismatch": "質問にある名称や番号を含む記載が見つかりませんでした。",
    "too_general": "質問の範囲が広いため記載を特定できませんでした。対象や項目を具体的にしてください。",
    "insufficient_evidence": "回答に必要な根拠が文書内に見つかりませんでした。",
    "llm_unavailable": "現在回答を生成できず、文書内にも十分な根拠が見つかりませんでした。",
}


def _guard_fallback_raw(guard_reason: str) -> str:
    # No-answer responses carry no citation tags: there is no evidence that
    # supports anything, so nothing may be cited.
    if guard_reason == "missing_procedure_evidence":
        fallback = "- 手順の記載が見つかりません。原本の該当箇所をご確認ください。"
        return fallback + "\n不明: 手順の記載なし"
    message = _GUARD_MESSAGES.get(guard_reason, "質問に答えられる記載を特定できませんでした。")
    return f"- 関連情報が見つかりませんでした。{message}\n不明: 根拠不足"


def _finalize_generated_answer(
    raw: str, q: str, grounded: Sequence[Chunk], intent: str, *, tenant_id: str = "default", client=None
) -> Tuple[str, bool]:
    raw = strip_reference_block(raw)
    used_fallback = False
    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):
        raw = extractive_fallback(q, grounded)
        used_fallback = True
        if not raw.strip():
            # No evidence sentence shares a term with the question.
            return _guard_fallback_raw("insufficient_evidence"), True
    if not used_fallback and getattr(config, "GROUNDING_VERIFY_ENABLED", False):
        # Step 4-3: every sentence must be supported by the evidence it cites. Unsupported ones are dropped; an
        # answer with nothing left becomes the extractive one.
        checked = grounding.verify_answer(raw, grounded, tenant_id=tenant_id, client=client)
        if checked.kept == 0:
            raw = extractive_fallback(q, grounded)
            used_fallback = True
            if not raw.strip():
                return _guard_fallback_raw("insufficient_evidence"), True
        else:
            raw = checked.raw
    # "不足: なし" is an internal marker; it is neither shown nor cited.
    return drop_empty_shortage_lines(raw), used_fallback


_EXTRACTIVE_TERM_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]{1,}|[ァ-ヴー]{2,}|[一-龥々〆〤]{2,}")
_EXTRACTIVE_STOP_TERMS = {
    "です",
    "ます",
    "ください",
    "何で",
    "何です",
    "原因",
    "概要",
    "説明",
}


def _extractive_term_mode() -> str:
    # regex (default, historical) | morph (Sudachi lemmas) | auto
    mode = _setting("EXTRACTIVE_TERM_MODE", "regex")
    return mode if mode in {"regex", "morph", "auto"} else "regex"


def _extractive_terms(question: str) -> List[str]:
    mode = _extractive_term_mode()
    if mode != "regex":
        return [t for t in ja_terms.content_terms(question, mode) if t not in _EXTRACTIVE_STOP_TERMS]
    norm = normalize_japanese_text(question or "").lower()
    terms = []
    for term in _EXTRACTIVE_TERM_RE.findall(norm):
        term = term.strip()
        if len(term) < 2 or term in _EXTRACTIVE_STOP_TERMS:
            continue
        terms.append(term)
    return list(dict.fromkeys(terms))


def _extractive_term_hit(term: str, compact_sentence: str) -> bool:
    compact_term = re.sub(r"[\s・/／、。,.]", "", term)
    relaxed_sentence = re.sub(r"[\s・/／、。,.]", "", compact_sentence)
    return term in compact_sentence or compact_term in relaxed_sentence


def _extractive_sentences(question: str, grounded: Sequence[Chunk]) -> Tuple[List[Tuple[int, str]], int]:
    terms = _extractive_terms(question)
    term_mode = _extractive_term_mode()
    question_norm = normalize_japanese_text(question or "")
    fact_question = any(x in question_norm for x in ("いくつ", "何個", "何件", "何名", "何年", "いつ", "何月", "何日"))
    scored: List[Tuple[int, int, str]] = []
    ordered: List[Tuple[int, str]] = []
    seen: set[str] = set()
    for idx, ch in enumerate(grounded, start=1):
        for sent in re.split(r"[。！？\n]+", ch.text or ""):
            sent = re.sub(r"\s+", " ", sent).strip()
            norm_sent = normalize_japanese_text(sent).lower()
            compact = re.sub(r"\s+", "", norm_sent)
            if not compact or compact in seen:
                continue
            seen.add(compact)
            ordered.append((idx, sent))
            # In morph mode lemmas also match inflected forms in the sentence.
            sentence_terms = set(ja_terms.content_terms(sent, term_mode)) if term_mode != "regex" else set()
            hits = sum(1 for term in terms if term in sentence_terms or _extractive_term_hit(term, compact))
            if hits <= 0:
                continue
            # Generic scoring only: term overlap plus a small preference for
            # sentences carrying numbers when the question asks for a fact.
            # Corpus-specific terms and answers must live in tenant profile data.
            score = hits * 10
            if re.search(r"\d", sent):
                score += 2
            if fact_question and re.search(r"\d", sent):
                score += 8
            if len(sent) > 320:
                score -= 4
            scored.append((score, idx, sent))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected: List[Tuple[int, str]] = []
    for score, idx, sent in scored:
        selected.append((idx, sent))
        if len(selected) >= 3:
            break
    expanded: List[Tuple[int, str]] = []
    selected_set = set(selected)
    for idx, sent in selected:
        expanded.append((idx, sent))
        if len(expanded) >= 3:
            break
        if not any(cue in sent for cue in ("表示されます", "どこ", "何ですか", "ありますか")):
            continue
        for pos, (ordered_idx, ordered_sent) in enumerate(ordered):
            if ordered_idx != idx or ordered_sent != sent:
                continue
            if pos + 1 < len(ordered):
                next_item = ordered[pos + 1]
                if next_item[0] == idx and next_item not in selected_set and next_item not in expanded:
                    expanded.append(next_item)
            break
    selected = expanded[:3]
    best_score = scored[0][0] if scored else 0
    return selected, best_score


def _extractive_required_terms(question: str) -> List[str]:
    norm = normalize_japanese_text(question or "").lower()
    required: List[str] = []
    required.extend(re.findall(r"[一-龥]{2,}(?:都|道|府|県|市|区|町|村)", norm))
    for alpha, number in re.findall(r"([a-z][a-z0-9._:/-]*)\s+(\d{2,})", norm):
        required.append(f"{alpha}{number}")
    for token in re.findall(r"[a-z0-9][a-z0-9._:/-]{1,}", norm):
        if re.search(r"\d", token):
            required.append(token)
    if "平均" in norm:
        required.append("平均")
    return list(dict.fromkeys(required))


_JAPANESE_ERA_YEAR_RE = re.compile(r"令和\s*\d+\s*年度?|平成\s*\d+\s*年度?|昭和\s*\d+\s*年度?")
_WESTERN_YEAR_RE = re.compile(r"(?<!\d)(20\d{2}|19\d{2})\s*年度?")
_MONEY_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:億円|万円|千円|円)")
_COUNT_COUNTERS = ("台", "件", "人", "名", "社", "個", "枚")


def _extract_requested_years(question: str) -> List[str]:
    norm = normalize_japanese_text(question or "")
    years = []
    years.extend(re.sub(r"\s+", "", item) for item in _JAPANESE_ERA_YEAR_RE.findall(norm))
    years.extend(re.sub(r"\s+", "", item) for item in _WESTERN_YEAR_RE.findall(norm))
    return list(dict.fromkeys(years))


def _sentence_has_requested_year(sentence: str, requested_years: Sequence[str]) -> bool:
    compact = re.sub(r"\s+", "", normalize_japanese_text(sentence or ""))
    return bool(requested_years) and any(year in compact for year in requested_years)


def _question_asks_money(question: str) -> bool:
    norm = normalize_japanese_text(question or "")
    return any(term in norm for term in ("予算額", "金額", "補助額", "補助上限額", "いくら", "何円", "費用"))


def _question_count_counter(question: str) -> Optional[str]:
    norm = normalize_japanese_text(question or "")
    for counter in _COUNT_COUNTERS:
        if f"何{counter}" in norm:
            return counter
    return None


def _has_answer_type_support(question: str, selected: Sequence[Tuple[int, str]], grounded: Sequence[Chunk]) -> bool:
    evidence_text = "\n".join(ch.text for ch in grounded)
    requested_years = _extract_requested_years(question)
    if requested_years:
        evidence_compact = re.sub(r"\s+", "", normalize_japanese_text(evidence_text))
        if not all(year in evidence_compact for year in requested_years):
            return False
        if not any(_sentence_has_requested_year(sent, requested_years) for _, sent in selected):
            return False

    if _question_asks_money(question):
        money_sentences = [sent for _, sent in selected if _MONEY_RE.search(normalize_japanese_text(sent))]
        if not money_sentences:
            return False
        if requested_years and not any(_sentence_has_requested_year(sent, requested_years) for sent in money_sentences):
            return False

    counter = _question_count_counter(question)
    if counter:
        count_re = re.compile(rf"\d[\d,]*\s*{re.escape(counter)}")
        count_sentences = [sent for _, sent in selected if count_re.search(normalize_japanese_text(sent))]
        if not count_sentences:
            return False

    return True


def _has_sufficient_extractive_evidence(question: str, grounded: Sequence[Chunk], *, strict: bool = False) -> bool:
    terms = _extractive_terms(question)
    selected, best_score = _extractive_sentences(question, grounded)
    if not selected:
        return False
    if not _has_answer_type_support(question, selected, grounded):
        return False
    evidence = normalize_japanese_text("\n".join(ch.text for ch in grounded)).lower()
    evidence_compact = re.sub(r"\s+", "", evidence)
    for term in _extractive_required_terms(question):
        if re.sub(r"\s+", "", term.lower()) not in evidence_compact:
            return False
    if strict or _setting("EXTRACTIVE_EVIDENCE_RULE", "coverage") == "legacy":
        # strict: overriding a guard needs the stronger (legacy) evidence
        # even when ordinary extraction uses the coverage rule.
        required_hits = 2 if len(terms) <= 3 else 3
        return best_score >= required_hits * 10
    # Short and colloquial questions have few terms; the legacy rule asked a
    # one-term question for two hits. Now: min(2, #terms) hits in the best
    # sentence and at least half of the question terms covered by the
    # selected sentences.
    required_hits = min(2, max(1, len(terms)))
    if best_score < required_hits * 10:
        return False
    selected_compact = [re.sub(r"\s+", "", normalize_japanese_text(sent).lower()) for _, sent in selected]
    covered = sum(1 for term in terms if any(_extractive_term_hit(term, c) for c in selected_compact))
    return covered / max(1, len(terms)) >= 0.5


def _should_recover_guarded_extractive(question: str, grounded: Sequence[Chunk], retrieved: Sequence[RetrievedChunk]) -> bool:
    if not grounded or not retrieved:
        return False
    if not _has_sufficient_extractive_evidence(question, grounded, strict=True):
        return False
    top_meta = retrieved[0].metadata or {}
    top_doc = str(top_meta.get("source_doc") or top_meta.get("doc") or "")
    top_pages = top_meta.get("source_pages") or top_meta.get("pages") or []
    if isinstance(top_pages, str):
        top_pages = [int(p) for p in re.findall(r"\d+", top_pages)]
    else:
        top_pages = [int(p) for p in top_pages if str(p).isdigit()]
    selected, _ = _extractive_sentences(question, grounded)
    cited_indexes = {idx for idx, _ in selected}
    for idx in cited_indexes:
        if not (1 <= idx <= len(grounded)):
            continue
        ch = grounded[idx - 1]
        if top_doc and ch.source_doc != top_doc:
            continue
        if top_pages and ch.source_pages and not (set(ch.source_pages) & set(top_pages)):
            continue
        return True
    return False


def _extractive_answer_raw(question: str, grounded: Sequence[Chunk]) -> str:
    if not grounded:
        return "文書内に十分な根拠が見つからないため、回答できません。\n不明: 根拠不足\n不足: 関連記載なし"
    selected, _ = _extractive_sentences(question, grounded)
    if selected:
        raw = "\n".join(f"- {sent} [S{idx}]" for idx, sent in selected)
    else:
        raw = extractive_fallback(question, grounded).strip()
    if not raw:
        # Nothing shares a term with the question: answer without citations.
        return "文書内に十分な根拠が見つからないため、回答できません。\n不明: 根拠不足"
    return "文書内では、以下の記載が確認できます。\n" + raw


def _build_extractive_answer_result(
    question: str,
    state: "_RetrievalTraceState",
    *,
    guard_reason: Optional[str],
    used_fallback: bool,
    retrieved: Sequence[RetrievedChunk],
) -> AnswerResult:
    raw = _extractive_answer_raw(question, state.selected_context)
    return _build_answer_result(
        raw,
        state.selected_context,
        intent=state.intent,
        guard_reason=guard_reason,
        used_fallback=used_fallback,
        retrieved=retrieved,
        rewritten_query=state.rewritten_query,
        augmented_query=state.augmented_query,
    )


def _apply_answer_rewrite(
    question: str, state: "_RetrievalTraceState", result: AnswerResult, trace: Dict[str, object], *,
    tenant_id: str, client=None,
) -> AnswerResult:
    """Step 4-2: put the quoted sentences of an extractive answer in natural wording, when the policy allows it."""
    if not llm.check("rewrite", tenant_id).allowed:
        return result
    raw = _extractive_answer_raw(question, state.selected_context)
    outcome = answer_rewrite.rewrite_answer(
        raw, question, tenant_id=tenant_id, client=client if hasattr(client, "chat") else None
    )
    trace["answer_rewrite"] = outcome.summary()
    if outcome.rewritten == 0:
        return result
    return _build_answer_result(
        outcome.raw,
        state.selected_context,
        intent=state.intent,
        guard_reason=None,
        used_fallback=False,
        retrieved=state.retrieved,
        rewritten_query=state.rewritten_query,
        augmented_query=state.augmented_query,
    )


def _llm_unavailable_result(question: str, state: "_RetrievalTraceState", retrieved: Sequence[RetrievedChunk]) -> AnswerResult:
    # Same evidence test as the extractive mode: quote evidence only when it
    # answers the question, otherwise a no-answer without citations.
    if _has_sufficient_extractive_evidence(question, state.selected_context):
        return _build_extractive_answer_result(
            question,
            state,
            guard_reason="llm_unavailable",
            used_fallback=True,
            retrieved=retrieved,
        )
    return _build_answer_result(
        _guard_fallback_raw("llm_unavailable"),
        state.selected_context,
        intent=state.intent,
        guard_reason="llm_unavailable",
        used_fallback=True,
        retrieved=retrieved,
        rewritten_query=state.rewritten_query,
        augmented_query=state.augmented_query,
    )


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


def _profile_validation_skipped(
    rag_profile_id: str,
    question_type: str,
    warnings: Sequence[str],
    reason: str,
) -> Dict[str, object]:
    return {
        "passed": True,
        "skipped": True,
        "matched_rule_id": "",
        "question_type": question_type or "other",
        "missing_any": [],
        "missing_all": [],
        "forbidden_hits": [],
        "fallback_if_failed": False,
        "reason": reason,
        "warnings": list(warnings),
        "rag_profile_id": rag_profile_id,
    }


def _apply_profile_validation(
    question: str,
    result: AnswerResult,
    trace: Dict[str, object],
    state: "_RetrievalTraceState",
    *,
    rag_profile_id: Optional[str],
    started_at: float,
) -> AnswerResult:
    profile_id = rag_profile_id or "default"
    profile = load_rag_profile(profile_id)
    warnings = profile.get("warnings") if isinstance(profile, dict) else []
    if not isinstance(warnings, list):
        warnings = []

    question_type = detect_question_type(question, profile)
    trace["rag_profile_id"] = profile_id
    trace["rag_profile_version"] = str(profile.get("profile_version") or "") if isinstance(profile, dict) else ""
    trace["question_type"] = question_type

    if warnings:
        trace["profile_validation"] = _profile_validation_skipped(
            profile_id,
            question_type,
            [str(item) for item in warnings],
            "profile load warning; validation skipped",
        )
        return result
    if result.guard_reason:
        # Stage 14: the guard decided not to answer. The profile fallback used
        # to replace that with an extract of the very chunk the guard rejected,
        # with citations; an abstention is never turned into an answer here.
        trace["profile_validation"] = _profile_validation_skipped(
            profile_id, question_type, [], "guarded answer; validation skipped"
        )
        return result

    validation_result = validate_answer_with_profile(question, result.answer_text, question_type, profile)
    trace["profile_validation"] = validation_result
    if validation_result.get("passed") or not validation_result.get("fallback_if_failed"):
        return result

    raw = extractive_fallback(question, state.selected_context)
    if not str(raw or "").strip():
        # No extract either: keep the answer (and its failed validation in the
        # trace) instead of returning an empty answer text.
        trace["profile_validation_after_fallback"] = {"skipped": True, "reason": "empty fallback"}
        return result
    fallback_result = _build_answer_result(
        raw,
        state.selected_context,
        intent=result.intent,
        guard_reason=result.guard_reason,
        used_fallback=True,
        retrieved=state.retrieved,
        rewritten_query=state.rewritten_query,
        augmented_query=state.augmented_query,
    )
    fallback_validation = validate_answer_with_profile(
        question,
        fallback_result.answer_text,
        question_type,
        profile,
    )
    trace["profile_validation_after_fallback"] = fallback_validation
    _set_final_trace(
        trace,
        started_at,
        state.selected_context,
        guard_reason=fallback_result.guard_reason,
        used_fallback=True,
        answer_mode="fallback",
        citations_count=len(fallback_result.citations),
    )
    return fallback_result


def _staging_vector_only(collection_name: Optional[str]) -> bool:
    return bool(collection_name) and not os.path.exists(keyword_index_path_for_collection(collection_name))


def _staged_margin() -> float:
    try:
        return float(_setting("STAGED_RETRIEVAL_MARGIN", "0") or 0.0)
    except ValueError:
        return 0.0


def _confident_first_pass(hits: Sequence[RetrievedChunk]) -> bool:
    # Top-1 fused score (metadata rrf_score) leads the runner-up by at least
    # STAGED_RETRIEVAL_MARGIN. 0 (default) disables the shortcut.
    margin = _staged_margin()
    if margin <= 0:
        return False
    scores = []
    for ch in hits[:2]:
        value = (ch.metadata or {}).get("rrf_score")
        if value is None:
            return False
        scores.append(float(value))
    return len(scores) == 2 and scores[0] - scores[1] >= margin


def _retrieve_and_rerank(
    question: str,
    augmented_query: str,
    *,
    scoring_query: str,
    client,
    top_k: int,
    intent: str,
    query_type: str,
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
    stats: Optional[Dict[str, object]] = None,
    rag_profile_id: Optional[str] = None,
) -> Tuple[List[RetrievedChunk], List[RetrievedChunk], List[RetrievedChunk]]:
    # A staging collection with its own keyword corpus (stage 3) gets the same
    # hybrid retrieval as the served collection; without one it stays vector
    # only (the historical staging behaviour).
    staging_mode = _staging_vector_only(collection_name)
    create_missing = not bool(collection_name)

    def _hybrid(query: str, embedding=None) -> List[RetrievedChunk]:
        return hybrid_retrieve(
            query,
            client,
            top_k=top_k,
            vector_top_k=config.VECTOR_TOP_K,
            bm25_top_k=config.BM25_TOP_K,
            rrf_k=config.HYBRID_RRF_K,
            query_embedding=embedding,
            tenant_id=tenant_id,
            collection_name=collection_name,
            create_collection_if_missing=create_missing,
        )

    def _vector(query: str, embedding=None) -> List[RetrievedChunk]:
        return vector_retrieve(
            query,
            client,
            top_k=top_k,
            query_embedding=embedding,
            tenant_id=tenant_id,
            collection_name=collection_name,
            create_collection_if_missing=False,
        )

    base_retrieve = _vector if staging_mode else _hybrid
    # Step 3-3: lane weights by query type (QUERY_ROUTING_ENABLED, default off); not for a vector-only staging pass.
    route = None if staging_mode else query_route.route_for(query_type)
    if route is not None and stats is not None:
        stats["route"] = {"name": route["name"], "scale": dict(route["scale"])}

    def retrieve(query: str, embedding=None) -> List[RetrievedChunk]:
        if route is None:
            return base_retrieve(query, embedding)
        with fusion_scale(route["scale"]):
            return base_retrieve(query, embedding)

    if augmented_query == question:
        # Identical queries would produce identical passes; retrieve once.
        base = retrieve(question)
        aug = base
    else:
        # Lazy batch: both queries are embedded in one embed_queries call the
        # first time vector retrieval asks for an embedding; stubbed or
        # keyword-only paths never trigger embedding.
        embedding_batch = QueryEmbeddingBatch([question, augmented_query], client=client)
        base = retrieve(question, lambda: embedding_batch.get(question))
        if _confident_first_pass(base):
            # v2_03: the first pass is decisive; the augmented pass is skipped.
            aug = []
            if stats is not None:
                stats["skipped_augmented_pass"] = True
        else:
            aug = retrieve(augmented_query, lambda: embedding_batch.get(augmented_query))
    extra_lists: List[List[RetrievedChunk]] = []
    if multi_query.enabled():
        # Step 3-2: rule-based variants of the question, searched as well and fused by RRF.
        variants = multi_query.build_variants(
            question, profile_id=rag_profile_id, tenant_id=tenant_id, limit=multi_query.max_queries()
        )[1:]
        extra_lists = [retrieve(variant["query"]) for variant in variants]
        if stats is not None:
            stats["multi_query"] = {"variants": variants, "deep": search_mode.is_deep()}
    if extra_lists:
        before_rerank = multi_query.fuse_rrf(
            [base] if aug is base else [base, aug],
            extra_lists,
            k=config.MULTI_QUERY_RRF_K,
            variant_penalty=config.MULTI_QUERY_VARIANT_PENALTY,
        )
    else:
        before_rerank = _unique_chunks(base + aug)
    if intent == "procedure":
        before_rerank = _unique_chunks(
            add_neighbor_chunks(
                before_rerank,
                tenant_id=tenant_id,
                collection_name=collection_name,
                create_collection_if_missing=create_missing,
            )
        )
    before_rerank = _decorate_score_details(scoring_query, before_rerank, query_type=query_type)
    child_ranked = rerank_chunks(question, before_rerank, intent=intent)
    if config.KEYWORD_BOOST_ENABLED and query_type in set(config.KEYWORD_BOOST_QUERY_TYPES):
        child_ranked = apply_keyword_boost(
            child_ranked,
            query_type=query_type,
            max_boost=config.KEYWORD_BOOST_MAX_DELTA,
        )
    if getattr(config, "PROFILE_BOOST_ENABLED", False):
        # Step 2-2: the retrieval_boost_rules of the RAG profile (default off). A profile that did not load
        # cleanly is skipped, as it is for answer validation.
        profile = load_rag_profile(rag_profile_id or "default")
        if isinstance(profile, dict) and not profile.get("warnings"):
            child_ranked, boost_trace = apply_profile_boost(
                child_ranked,
                scoring_query,
                detect_question_type(scoring_query, profile),
                profile,
                max_delta=config.PROFILE_BOOST_MAX_DELTA,
            )
            if stats is not None:
                stats["profile_boost"] = boost_trace
    if config.CROSS_ENCODER_RERANK_ENABLED:
        # Optional semantic stage over the already tenant-filtered, heuristic-
        # ranked candidates; reorders the fused top-N before parent expansion.
        child_ranked = cross_encoder_rerank(question, child_ranked)
    if staging_mode:
        # Prompt072 staging collections are Chroma-only and have no matching
        # JSONL keyword/parent index. Keep retrieval inside the selected
        # collection instead of expanding from the default served JSONL corpus.
        context_ranked = child_ranked
    else:
        context_ranked = expand_parent_chunks(
            child_ranked,
            max_parent_chunks=getattr(config, "MAX_PARENT_EXPANDED_CHUNKS", top_k),
            max_parent_context_chars=getattr(config, "MAX_PARENT_CONTEXT_CHARS", max(1200, top_k * 400)),
            tenant_id=tenant_id,
            collection_name=collection_name,
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
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
    rag_profile_id: Optional[str] = None,
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
    # N5 query preprocessing (QUERY_PREPROCESS_ENABLED, default off): the
    # cleaned query is used for retrieval, synonyms and typo candidates are
    # appended to the augmented query. Intent is inferred on the original.
    retrieval_q = q
    expansions: List[str] = []
    preprocess_trace: Optional[Dict[str, object]] = None
    if _setting("QUERY_PREPROCESS_ENABLED", "false") in _ENABLED_VALUES:
        pre = query_preprocess.preprocess_query(
            q,
            tenant_id=normalize_tenant_id(tenant_id),
            rag_profile_id=rag_profile_id,
            collection_name=collection_name,
        )
        retrieval_q = pre.query or q
        expansions = list(pre.expansions)
        preprocess_trace = pre.to_trace()
    rewritten = rewrite_query(retrieval_q)
    augmented = _compose_query(rewritten, intent)
    if expansions:
        augmented = (augmented + " " + " ".join(expansions)).strip()

    if client is None and not embedding_provider.is_local_provider():
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
    retrieval_started = time.perf_counter()
    retrieval_stats: Dict[str, object] = {}
    before_rerank, retrieved, context_candidates = _retrieve_and_rerank(
        retrieval_q,
        augmented,
        scoring_query=classification_query,
        client=client,
        top_k=top_k,
        intent=intent,
        query_type=query_type,
        tenant_id=normalize_tenant_id(tenant_id),
        collection_name=collection_name,
        stats=retrieval_stats,
        rag_profile_id=rag_profile_id,
    )
    retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)

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
        "stage_latency_ms": {"retrieval_ms": retrieval_ms},
    }
    if preprocess_trace is not None:
        trace["query_preprocess"] = preprocess_trace
    if retrieval_stats:
        trace["staged_retrieval"] = dict(retrieval_stats)
    trace["interpretation"] = interpretation.build(
        question=q, retrieval_query=retrieval_q, query_type=query_type, intent=intent, expansions=expansions,
        stats=retrieval_stats,
    )
    if collection_name:
        trace["query_collection"] = collection_name
        trace["query_collection_mode"] = "staging"

    if intent == "procedure":
        max_per_page = config.PROCEDURE_MAX_PER_PAGE
    elif intent in {"change", "reset"}:
        max_per_page = config.CHANGE_RESET_MAX_PER_PAGE
    else:
        max_per_page = config.OTHER_MAX_PER_PAGE
    # The per-page limit is applied before merging: merging first turned all
    # chunks of a page into one, so the limit never removed anything.
    guard_grounded = merge_by_page(_page_diversity(_to_grounded(retrieved), max_per_page=max_per_page))
    trace["guard_candidate_chunk_ids"] = [ch.id for ch in guard_grounded]

    grounded = merge_by_page(_page_diversity(_to_grounded(context_candidates), max_per_page=max_per_page))
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
        # Lenient guard only for vector-only staging (no keyword evidence).
        if _staging_vector_only(collection_name) and guard_reason in {"too_general", "soft_distance"} and grounded:
            trace["staging_guard_bypass_reason"] = guard_reason
            guard_reason = None
        elif guard_reason in {"too_general", "soft_distance"} and _should_recover_guarded_extractive(q, grounded, retrieved):
            trace["extractive_guard_recovery_reason"] = guard_reason
            guard_reason = None
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
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
) -> Dict[str, object]:
    trace, state, started_at = _build_retrieval_trace(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
        tenant_id=tenant_id,
        collection_name=collection_name,
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
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
    rag_profile_id: Optional[str] = None,
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
        tenant_id=tenant_id,
        collection_name=collection_name,
        rag_profile_id=rag_profile_id,
    )
    q = state.normalized_query
    intent = state.intent
    rewritten = state.rewritten_query
    augmented = state.augmented_query
    retrieved = state.retrieved

    if state.guard_reason:
        raw = _guard_fallback_raw(state.guard_reason)
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
        result = _apply_profile_validation(
            question,
            result,
            trace,
            state,
            rag_profile_id=rag_profile_id,
            started_at=started_at,
        )
        return result, trace

    grounded = state.selected_context

    if llm.generation_mode(tenant_id) == "extractive":
        if _has_sufficient_extractive_evidence(q, grounded):
            result = _build_extractive_answer_result(
                q,
                state,
                guard_reason=None,
                used_fallback=False,
                retrieved=retrieved,
            )
            result = _apply_answer_rewrite(q, state, result, trace, tenant_id=tenant_id, client=client)
            answer_mode = "grounded_extractive"
        else:
            result = _build_answer_result(
                _guard_fallback_raw("insufficient_evidence"),
                grounded,
                intent=intent,
                guard_reason="insufficient_evidence",
                used_fallback=True,
                retrieved=retrieved,
                rewritten_query=rewritten,
                augmented_query=augmented,
            )
            answer_mode = "fallback"
        trace.setdefault("stage_latency_ms", {})["generation_ms"] = 0
        trace["chat_generation_mode"] = "extractive"
        _set_final_trace(
            trace,
            started_at,
            grounded,
            guard_reason=result.guard_reason,
            used_fallback=result.used_fallback,
            answer_mode=answer_mode,
            citations_count=len(result.citations),
        )
        result = _apply_profile_validation(
            question,
            result,
            trace,
            state,
            rag_profile_id=rag_profile_id,
            started_at=started_at,
        )
        return result, trace

    messages = build_messages(q, build_evidence_blocks(grounded))
    generation_started = time.perf_counter()
    try:
        if client is None:
            client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        resp = _create_chat_completion(
            client,
            messages,
        )
    except Exception as exc:
        trace.setdefault("stage_latency_ms", {})["generation_ms"] = int(
            (time.perf_counter() - generation_started) * 1000
        )
        trace["chat_generation_mode"] = "llm"
        trace["generation_error_type"] = type(exc).__name__
        trace["generation_error"] = str(exc)[:500]
        result = _llm_unavailable_result(q, state, retrieved)
        _set_final_trace(
            trace,
            started_at,
            grounded,
            guard_reason=result.guard_reason,
            used_fallback=True,
            answer_mode="fallback",
            citations_count=len(result.citations),
        )
        result = _apply_profile_validation(
            question,
            result,
            trace,
            state,
            rag_profile_id=rag_profile_id,
            started_at=started_at,
        )
        return result, trace
    trace.setdefault("stage_latency_ms", {})["generation_ms"] = int(
        (time.perf_counter() - generation_started) * 1000
    )
    trace["chat_generation_mode"] = "llm"
    raw, used_fallback = _finalize_generated_answer(
        resp.choices[0].message.content or "", q, grounded, intent,
        tenant_id=tenant_id, client=client if hasattr(client, "chat") else None,
    )
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
    result = _apply_profile_validation(
        question,
        result,
        trace,
        state,
        rag_profile_id=rag_profile_id,
        started_at=started_at,
    )
    return result, trace


def answer_query(question: str, client=None, top_k: int = 20, max_context_chars: int = 8000, intent_override: Optional[str] = None, tenant_id: str = "default", collection_name: Optional[str] = None, rag_profile_id: Optional[str] = None) -> AnswerResult:
    result, _ = _answer_query_impl(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
        tenant_id=tenant_id,
        collection_name=collection_name,
        rag_profile_id=rag_profile_id,
    )
    return result


def answer_query_with_trace(
    question: str,
    client=None,
    top_k: int = 20,
    max_context_chars: int = 8000,
    intent_override: Optional[str] = None,
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
    rag_profile_id: Optional[str] = None,
) -> Tuple[AnswerResult, Dict[str, object]]:
    return _answer_query_impl(
        question,
        client=client,
        top_k=top_k,
        max_context_chars=max_context_chars,
        intent_override=intent_override,
        tenant_id=tenant_id,
        collection_name=collection_name,
        rag_profile_id=rag_profile_id,
    )


def answer_query_stream(
    question: str,
    client=None,
    top_k: int = 20,
    max_context_chars: int = 8000,
    intent_override: Optional[str] = None,
    tenant_id: str = "default",
    collection_name: Optional[str] = None,
    rag_profile_id: Optional[str] = None,
):
    """Streaming variant of _answer_query_impl.

    Yields ("meta", dict), then zero or more ("delta", {"text": ...}), then
    ("final", (AnswerResult, trace)). Deltas are provisional; the final event
    is authoritative (validation/extractive fallback may correct the text).
    Guard/no-answer paths emit meta then final with no deltas.
    """
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
        tenant_id=tenant_id,
        collection_name=collection_name,
        rag_profile_id=rag_profile_id,
    )
    yield "meta", {
        "request_id": request_id,
        "intent": state.intent,
        "query_type": state.query_type,
        "guard_reason": state.guard_reason,
        "used_fallback": state.used_fallback,
    }

    if state.guard_reason:
        raw = _guard_fallback_raw(state.guard_reason)
        answer_chunks = state.selected_context
        result = _build_answer_result(
            raw,
            answer_chunks,
            intent=state.intent,
            guard_reason=state.guard_reason,
            used_fallback=True,
            retrieved=state.retrieved,
            rewritten_query=state.rewritten_query,
            augmented_query=state.augmented_query,
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
        result = _apply_profile_validation(
            question, result, trace, state, rag_profile_id=rag_profile_id, started_at=started_at
        )
        yield "final", (result, trace)
        return

    q = state.normalized_query
    grounded = state.selected_context
    if llm.generation_mode(tenant_id) == "extractive":
        if _has_sufficient_extractive_evidence(q, grounded):
            result = _build_extractive_answer_result(
                q,
                state,
                guard_reason=None,
                used_fallback=False,
                retrieved=state.retrieved,
            )
            result = _apply_answer_rewrite(q, state, result, trace, tenant_id=tenant_id, client=client)
            answer_mode = "grounded_extractive"
        else:
            result = _build_answer_result(
                _guard_fallback_raw("insufficient_evidence"),
                grounded,
                intent=state.intent,
                guard_reason="insufficient_evidence",
                used_fallback=True,
                retrieved=state.retrieved,
                rewritten_query=state.rewritten_query,
                augmented_query=state.augmented_query,
            )
            answer_mode = "fallback"
        trace.setdefault("stage_latency_ms", {})["generation_ms"] = 0
        trace["chat_generation_mode"] = "extractive"
        _set_final_trace(
            trace,
            started_at,
            grounded,
            guard_reason=result.guard_reason,
            used_fallback=result.used_fallback,
            answer_mode=answer_mode,
            citations_count=len(result.citations),
        )
        result = _apply_profile_validation(
            question, result, trace, state, rag_profile_id=rag_profile_id, started_at=started_at
        )
        yield "final", (result, trace)
        return

    messages = build_messages(q, build_evidence_blocks(grounded))
    pieces: List[str] = []
    generation_started = time.perf_counter()
    try:
        if client is None:
            client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        for delta_text in _stream_chat_completion(
            client,
            messages,
        ):
            pieces.append(delta_text)
            yield "delta", {"text": delta_text}
    except Exception as exc:
        trace.setdefault("stage_latency_ms", {})["generation_ms"] = int(
            (time.perf_counter() - generation_started) * 1000
        )
        trace["chat_generation_mode"] = "llm"
        trace["generation_error_type"] = type(exc).__name__
        trace["generation_error"] = str(exc)[:500]

        # Once any streamed text has been emitted, returning a different
        # extractive fallback as the final answer would create an inconsistent
        # client-visible stream. Do not retry or replace partial output.
        if pieces:
            raise

        result = _llm_unavailable_result(q, state, state.retrieved)
        _set_final_trace(
            trace,
            started_at,
            grounded,
            guard_reason=result.guard_reason,
            used_fallback=True,
            answer_mode="fallback",
            citations_count=len(result.citations),
        )
        result = _apply_profile_validation(
            question, result, trace, state, rag_profile_id=rag_profile_id, started_at=started_at
        )
        yield "final", (result, trace)
        return
    trace.setdefault("stage_latency_ms", {})["generation_ms"] = int(
        (time.perf_counter() - generation_started) * 1000
    )
    trace["chat_generation_mode"] = "llm"

    raw, used_fallback = _finalize_generated_answer(
        "".join(pieces), q, grounded, state.intent,
        tenant_id=tenant_id, client=client if hasattr(client, "chat") else None,
    )
    result = _build_answer_result(
        raw,
        grounded,
        intent=state.intent,
        guard_reason=None,
        used_fallback=used_fallback,
        retrieved=state.retrieved,
        rewritten_query=state.rewritten_query,
        augmented_query=state.augmented_query,
    )
    _set_final_trace(
        trace,
        started_at,
        grounded,
        guard_reason=None,
        used_fallback=used_fallback,
        answer_mode="fallback" if used_fallback else "grounded",
        citations_count=len(result.citations),
    )
    result = _apply_profile_validation(
        question, result, trace, state, rag_profile_id=rag_profile_id, started_at=started_at
    )
    yield "final", (result, trace)
