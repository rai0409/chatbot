from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import config
from rag_core import embedder, store
from rag_core.utils import ensure_openai_client
from rag_grounded import Chunk, build_evidence_blocks, build_prompt, extractive_fallback, merge_by_page, rewrite_query, strip_reference_block, to_footnotes, validate_output


@dataclass
class RetrievedChunk:
    text: str
    metadata: Dict
    score: float


def _build_base_where(allowed_types=None, allowed_qualities=None) -> Dict:
    where = {}
    if allowed_types:
        where["type"] = {"$in": list(allowed_types)}
    if allowed_qualities:
        where["quality"] = {"$in": list(allowed_qualities)}
    if not config.IGNORE_SEARCHABLE:
        where["searchable"] = 1
    if config.LOG_WHERE:
        print("where:", json.dumps(where, ensure_ascii=False))
    return where


def retrieve_chunks(question: str, client, top_k: int, allowed_types=None, allowed_qualities=None) -> List[RetrievedChunk]:
    collection = store.get_vectorstore()
    where = _build_base_where(allowed_types, allowed_qualities)
    embedding = embedder.embed_queries([question], client=client)[0]
    res = collection.query(query_embeddings=[embedding], n_results=top_k, where=where)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    out = []
    for text, meta, dist in zip(docs, metas, dists):
        out.append(RetrievedChunk(text=text or "", metadata=meta or {}, score=float(dist)))
    return out


def _add_neighbor_chunks(seeds: Sequence[RetrievedChunk], collection, window: int = 1) -> List[RetrievedChunk]:
    out = list(seeds)
    for seed in seeds:
        doc_id = seed.metadata.get("doc_id")
        idx = seed.metadata.get("chunk_index")
        if doc_id is None or idx is None:
            continue
        for delta in range(-window, window + 1):
            if delta == 0:
                continue
            try:
                res = collection.get(where={"doc_id": doc_id, "chunk_index": idx + delta})
                for text, meta in zip(res.get("documents", []), res.get("metadatas", [])):
                    out.append(
                        RetrievedChunk(
                            text=text or "",
                            metadata=meta or {},
                            score=seed.score + 0.01,
                        )
                    )
            except Exception:
                continue
    return out


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


def guard_merged_top(question: str, intent: str, chunks: Sequence[Chunk], retrieved: Sequence[RetrievedChunk]) -> Optional[str]:
    if config.DISABLE_GUARD:
        return None
    if not retrieved:
        return "no_results"
    best = min(r.score for r in retrieved)
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
    salient = _salient_terms(question)
    if salient:
        evidence = " ".join(ch.text for ch in chunks)
        if not any(t in evidence for t in salient):
            return "salient_mismatch"
    if intent == "other":
        if len(re.findall(r"[A-Za-z0-9ぁ-んァ-ン一-龥]+", question)) <= 2:
            req = ["ID", "番号", "許容値", "フラグ"]
            if not any(r in question for r in req):
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
        out.append(
            Chunk(
                id=str(meta.get("id") or idx),
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


def answer_query(question: str, client=None, top_k: int = 20, max_context_chars: int = 8000, intent_override: Optional[str] = None) -> str:
    q = question.strip()
    q = re.sub(r"^質問\s*:\s*", "", q)
    q = q.strip().strip("「」\"'")
    q = re.sub(r"\s+", " ", q)
    intent = intent_override or infer_intent(q)
    rewritten = rewrite_query(q)
    augmented = _compose_query(rewritten, intent)

    if client is None:
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
    base = retrieve_chunks(q, client, top_k=top_k)
    aug = retrieve_chunks(augmented, client, top_k=top_k)
    retrieved = _unique_chunks(base + aug)

    collection = store.get_vectorstore()
    if intent == "procedure":
        retrieved = _unique_chunks(_add_neighbor_chunks(retrieved, collection))

    grounded = _to_grounded(retrieved)
    grounded = merge_by_page(grounded)
    if intent == "procedure":
        max_per_page = config.PROCEDURE_MAX_PER_PAGE
    elif intent in {"change", "reset"}:
        max_per_page = config.CHANGE_RESET_MAX_PER_PAGE
    else:
        max_per_page = config.OTHER_MAX_PER_PAGE
    grounded = _page_diversity(grounded, max_per_page=max_per_page)

    if intent in {"change", "reset"}:
        if not any(term in " ".join(ch.text for ch in grounded) for term in config.PROCEDURE_STRONG_TERMS):
            fallback = "- 手順の記載が見つかりません。OCR版を確認してください。 [S1]"
            return to_footnotes(fallback + "\n不明: 手順不明 [S1]\n不足: OCR版確認 [S1]", grounded[:1] or [Chunk("1", "OCR", "unknown", tuple(), None)])

    guard_reason = guard_merged_top(q, intent, grounded, retrieved)
    if guard_reason:
        body = f"- 関連情報が見つかりませんでした。理由: {guard_reason} [S1]"
        answer = body + "\n不明: 根拠不足 [S1]\n不足: 関連記載なし [S1]"
        return to_footnotes(answer, grounded[:1] or [Chunk("1", "該当なし", "unknown", tuple(), None)])

    grounded = _prefer_sort(grounded, intent)
    grounded = _cut_context(grounded, max_context_chars)

    evidence_blocks = build_evidence_blocks(grounded)
    prompt = build_prompt(q, evidence_blocks)
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
    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):
        raw = extractive_fallback(q, grounded)

    if "不足:" not in raw and "不明:" not in raw:
        raw = raw.strip() + "\n不足: なし [S1]"
    return to_footnotes(raw, grounded)
