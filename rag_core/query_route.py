"""Lane weights of the hybrid fusion by query type (step 3-3).

classify_query_type (rag_core/keyword_scorer.py) names the kind of question. For some kinds one lane is the better
guide: a quoted phrase, an identifier or a short lookup is found by the keyword and exact lanes; a long or FAQ-like
question by the dense lane. ROUTES holds multipliers on the configured lane weights (HYBRID_*_WEIGHT). The numbers are
initial values to be tuned with the bench / tenant_eval; the switch is QUERY_ROUTING_ENABLED (default off).

2026-11-27: the style of the question is a second signal (detect_style). A colloquial question ("経費精算ってどうやるの",
"打刻忘れちゃったんだけど") or a plain-language paraphrase ("家で仕事をするための事前の届出") shares few surface terms with
the document, so the dense lane gets more weight there. Identifier and exact lookups keep their own route: the exact
term still decides those. The style is only used when the caller passes the question to route_for.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import config

ROUTES: Dict[str, Dict[str, float]] = {
    "exact_lookup": {"dense": 0.6, "keyword": 1.5, "exact": 1.5},
    "identifier": {"dense": 0.5, "keyword": 1.6, "exact": 1.8},
    "faq": {"dense": 1.2, "keyword": 0.9, "exact": 1.0},
    "broad": {"dense": 1.3, "keyword": 0.8, "exact": 1.0},
    # Style routes (detect_style). Initial values, to be tuned with tenant_eval --perturb.
    "colloquial": {"dense": 1.3, "keyword": 0.8, "exact": 0.9},
    "paraphrase": {"dense": 1.25, "keyword": 0.85, "exact": 1.0},
}

# Query types whose exact term matters more than the style of the question.
_TERM_ROUTES = frozenset({"exact_lookup", "identifier"})

# Spoken-style markers: casual question endings, sentence-final particles, contracted verb forms and fillers.
_COLLOQUIAL_RE = re.compile(
    r"って(?:なに|何|なん|どう|どこ|いつ|誰|だれ|いくら)"
    r"|(?:んだけど|んですけど|なんだけど|だけど)"
    r"|(?:ちゃった|ちゃう|じゃった|ちゃって)"
    r"|(?:どうやるの|どうやれば|どうしたら|どうすれば)"
    r"|(?:かな|のかな|だっけ|っけ|よね|なの|るの|たの)[?？!！。\s]*$"
    r"|^(?:あの|えっと|えーと|ええと|ちょっと)[、,\s]"
)
# Plain-language paraphrase: a longer question written mostly in hiragana around a few short words, with no katakana
# term, identifier or quoted phrase the keyword lane could anchor on.
_PARAPHRASE_LINKS_RE = re.compile(r"(?:とき|ための|ために|には|ように|ような|みたいな|場合|代わり|なくなった|入れ方|出し方|やり方)")
_ANCHOR_RE = re.compile(r"[A-Za-z0-9]|[ァ-ヴー]{3,}|[「」\"]")
_HIRAGANA_RE = re.compile(r"[ぁ-ん]")
_MIN_PARAPHRASE_CHARS = 10
_MIN_HIRAGANA_SHARE = 0.4


def enabled() -> bool:
    return bool(getattr(config, "QUERY_ROUTING_ENABLED", False))


def detect_style(question: str) -> Optional[str]:
    """"colloquial", "paraphrase" or None for the question text (a heuristic, not a gold label)."""
    text = re.sub(r"\s+", " ", str(question or "")).strip()
    if not text:
        return None
    if _COLLOQUIAL_RE.search(text):
        return "colloquial"
    compact = text.replace(" ", "")
    if len(compact) < _MIN_PARAPHRASE_CHARS or _ANCHOR_RE.search(compact):
        return None
    share = len(_HIRAGANA_RE.findall(compact)) / len(compact)
    if share >= _MIN_HIRAGANA_SHARE and _PARAPHRASE_LINKS_RE.search(compact):
        return "paraphrase"
    return None


def route_for(query_type: str, question: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """{"name", "scale"} for a routed query while routing is on, else None (the configured weights apply).

    With the question, a colloquial / paraphrase style picks its route unless the query type is an exact lookup or
    an identifier."""
    if not enabled():
        return None
    name: Optional[str] = query_type if query_type in ROUTES else None
    if question is not None and query_type not in _TERM_ROUTES:
        style = detect_style(question)
        if style is not None:
            name = style
    if name is None:
        return None
    return {"name": name, "scale": dict(ROUTES[name])}
