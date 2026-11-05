"""Rule-based variants of a question and the reciprocal rank fusion of their results (step 3-2)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import config
from rag_core import query_preprocess, search_mode
from rag_core.ja_text import extract_salient_terms_ja, normalize_for_index
from rag_core.retrieval import RetrievedChunk

DEEP_MIN_QUERIES = 3
MAX_QUERIES = 5


def enabled() -> bool:
    return bool(getattr(config, "MULTI_QUERY_ENABLED", False)) or search_mode.is_deep()


def max_queries() -> int:
    """Queries to search in all (the question included): the setting, at least 3 for a deep request, at most 5."""
    count = int(getattr(config, "MULTI_QUERY_MAX_VARIANTS", 3))
    if search_mode.is_deep():
        count = max(count, DEEP_MIN_QUERIES)
    return max(1, min(count, MAX_QUERIES))


def build_variants(
    question: str, *, profile_id: Optional[str] = None, tenant_id: str = "default", limit: int = 3
) -> List[Dict[str, str]]:
    """[{"query", "kind"}]: the question first, then cleaned, core_terms and synonym variants, at most `limit` in all."""
    base = str(question or "").strip()
    out: List[Dict[str, str]] = [{"query": base, "kind": "original"}]
    seen = {normalize_for_index(base)}

    def add(query: str, kind: str) -> None:
        key = normalize_for_index(query)
        if len(out) < limit and query.strip() and key not in seen:
            seen.add(key)
            out.append({"query": query.strip(), "kind": kind})

    normalized = normalize_for_index(base)
    cleaned, _applied = query_preprocess.strip_colloquial(normalized, tenant_id=tenant_id)
    add(cleaned, "cleaned")
    terms = extract_salient_terms_ja(base)
    if terms:
        add(" ".join(terms), "core_terms")
    for group in query_preprocess._synonym_groups(profile_id):
        present = next((term for term in group if term and term in normalized), None)
        if present is None:
            continue
        for other in group:
            if other != present:
                add(normalized.replace(present, other, 1), "synonym")
    return out[: max(1, limit)]


def _key(chunk: RetrievedChunk) -> str:
    return chunk.metadata.get("id") or (chunk.text[:120] + str(chunk.metadata.get("doc_id")))


def fuse_rrf(
    trusted: Sequence[Sequence[RetrievedChunk]],
    variant_lists: Sequence[Sequence[RetrievedChunk]],
    *,
    k: int = 60,
    variant_penalty: float = 0.05,
) -> List[RetrievedChunk]:
    """Union of the lists, ordered by reciprocal rank fusion (ties: lower distance, then first seen).

    `trusted` are the passes of the question itself, `variant_lists` those of its variants. A chunk of a trusted list
    keeps its (first) trusted score; a chunk only a variant found gets that variant's best score plus the penalty."""
    entries: Dict[str, Dict[str, Any]] = {}
    for variant, lists in ((False, trusted), (True, variant_lists)):
        for index, ranked in enumerate(lists):
            for rank, chunk in enumerate(ranked, start=1):
                entry = entries.setdefault(
                    _key(chunk), {"order": len(entries), "rrf": 0.0, "trusted": None, "variant": None, "from": []}
                )
                entry["rrf"] += 1.0 / (k + rank)
                label = f"{'variant' if variant else 'question'}:{index}"
                entry["from"].append(label)
                slot = "variant" if variant else "trusted"
                if entry[slot] is None or (variant and chunk.score < entry[slot].score):
                    entry[slot] = chunk
    fused: List[tuple] = []
    for entry in entries.values():
        source = entry["trusted"] or entry["variant"]
        score = source.score if entry["trusted"] is not None else source.score + float(variant_penalty)
        meta = dict(source.metadata or {})
        details = dict(meta.get("score_details") or {})
        details["multi_query"] = {
            "rrf": round(entry["rrf"], 6), "found_by": entry["from"], "variant_only": entry["trusted"] is None,
        }
        meta["score_details"] = details
        fused.append((-entry["rrf"], score, entry["order"], RetrievedChunk(text=source.text, metadata=meta, score=score)))
    fused.sort(key=lambda item: item[:3])
    return [item[3] for item in fused]
