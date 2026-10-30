"""Question suggestions from the approved QA of a tenant (step 3-4)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from rag_core.approved_qa import ApprovedQAIndex
from rag_core.question_normalization import exact_match_key

MIN_CHARS = 2
MAX_LIMIT = 10


def suggest(
    text: str, *, index: Optional[ApprovedQAIndex], tenant_id: str = "default", limit: int = 5
) -> List[Dict[str, Any]]:
    """Approved questions that start with, or contain, the typed text; prefix matches first, then file order.

    Each suggestion is {"text": the canonical question, "qa_id", "match": "prefix" | "contains", "via": "question" |
    "alias"}. One entry per approved question, and only questions of `tenant_id`."""
    key = exact_match_key(text)
    if index is None or len(key) < MIN_CHARS:
        return []
    found = []
    for order, answer in enumerate(index.records):
        if answer.tenant_id != tenant_id:
            continue
        best = None
        for via, candidate in (("question", answer.question), *(("alias", alias) for alias in answer.approved_aliases)):
            normalized = exact_match_key(candidate)
            if normalized.startswith(key):
                rank = 0
            elif key in normalized:
                rank = 1
            else:
                continue
            item = (rank, 0 if via == "question" else 1, len(normalized), via)  # the question beats an alias
            if best is None or item[:3] < best[:3]:
                best = item
        if best is not None:
            found.append((best[0], order, answer, best))
    found.sort(key=lambda row: (row[0], row[1]))
    limit = max(1, min(int(limit), MAX_LIMIT))
    return [
        {"text": answer.question, "qa_id": answer.qa_id, "match": "prefix" if rank == 0 else "contains", "via": best[3]}
        for rank, _order, answer, best in found[:limit]
    ]
