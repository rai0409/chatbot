"""Lane weights of the hybrid fusion by query type (step 3-3).

classify_query_type (rag_core/keyword_scorer.py) names the kind of question. For some kinds one lane is the better
guide: a quoted phrase, an identifier or a short lookup is found by the keyword and exact lanes; a long or FAQ-like
question by the dense lane. ROUTES holds multipliers on the configured lane weights (HYBRID_*_WEIGHT). The numbers are
initial values to be tuned with the bench / tenant_eval; the switch is QUERY_ROUTING_ENABLED (default off).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import config

ROUTES: Dict[str, Dict[str, float]] = {
    "exact_lookup": {"dense": 0.6, "keyword": 1.5, "exact": 1.5},
    "identifier": {"dense": 0.5, "keyword": 1.6, "exact": 1.8},
    "faq": {"dense": 1.2, "keyword": 0.9, "exact": 1.0},
    "broad": {"dense": 1.3, "keyword": 0.8, "exact": 1.0},
}


def enabled() -> bool:
    return bool(getattr(config, "QUERY_ROUTING_ENABLED", False))


def route_for(query_type: str) -> Optional[Dict[str, Any]]:
    """{"name", "scale"} for a routed query type while routing is on, else None (the configured weights apply)."""
    if not enabled() or query_type not in ROUTES:
        return None
    return {"name": query_type, "scale": dict(ROUTES[query_type])}
