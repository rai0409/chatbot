"""How the question was understood, for the trace, the API response and the chat UI (step 3-3)."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

TYPE_LABELS = {
    "exact_lookup": "語句・名称の照会",
    "identifier": "識別子・番号の照会",
    "procedure": "手順の質問",
    "faq": "よくある質問",
    "broad": "広い質問",
    "ambiguous": "短く曖昧な質問",
    "other": "一般的な質問",
}
SUMMARY_LIMIT = 300


def build(
    *,
    question: str,
    retrieval_query: str,
    query_type: str,
    intent: str,
    expansions: Sequence[str],
    stats: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    stats = stats or {}
    multi = stats.get("multi_query") or {}
    variants = [str(v.get("query")) for v in multi.get("variants", []) if v.get("query")]
    route = (stats.get("route") or {}).get("name")
    cleaned = retrieval_query if retrieval_query and retrieval_query != question else None
    label = TYPE_LABELS.get(query_type, TYPE_LABELS["other"])
    parts = [f"{label}として解釈しました"]
    if cleaned:
        parts.append(f"検索語: {cleaned}")
    if expansions:
        parts.append("言い換えも検索: " + "、".join(list(expansions)[:5]))
    if variants:
        parts.append("別の言い方でも検索: " + "、".join(variants[:4]))
    return {
        "query_type": query_type,
        "type_label": label,
        "intent": intent,
        "cleaned_query": cleaned,
        "expansions": list(expansions)[:10],
        "variants": variants[:5],
        "route": route,
        "deep": bool(multi.get("deep")),
        "summary": "。".join(parts)[:SUMMARY_LIMIT],
    }
