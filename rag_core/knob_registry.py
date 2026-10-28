"""The settings a proposed change may touch.

Only a registered setting can be proposed, with a value of its type inside its bounds. `stage` says where it acts,
`risk` is "high" for settings that decide whether the bot answers at all (the distance guards): those need a stricter
approval than the rest. The bounds are initial values; tune them on real data. snapshot() reads the current values
from config (so from the environment), which is what the evaluation ledger records.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import config


class KnobError(ValueError):
    pass


@dataclass(frozen=True)
class Knob:
    name: str
    kind: str  # "bool" | "int" | "float"
    stage: str
    risk: str = "low"
    lo: Optional[float] = None
    hi: Optional[float] = None
    description: str = ""


REGISTRY: Tuple[Knob, ...] = (
    Knob("PROFILE_BOOST_ENABLED", "bool", "boost", description="run the profile's retrieval_boost_rules"),
    Knob("PROFILE_BOOST_MAX_DELTA", "float", "boost", lo=0.0, hi=0.2, description="largest profile-rule score change"),
    Knob("QUERY_PREPROCESS_ENABLED", "bool", "query", description="query cleanup, synonyms and typo candidates"),
    Knob("MULTI_QUERY_ENABLED", "bool", "query", description="search rule-based variants of the question too"),
    Knob("MULTI_QUERY_MAX_VARIANTS", "int", "query", lo=1, hi=5, description="queries searched, question included"),
    Knob("MULTI_QUERY_RRF_K", "int", "query", lo=1, hi=200, description="rank constant of the variant fusion"),
    Knob("MULTI_QUERY_VARIANT_PENALTY", "float", "query", lo=0.0, hi=0.3, description="distance added to variant-only chunks"),
    Knob("QUERY_ROUTING_ENABLED", "bool", "query", description="lane weights of the fusion by query type"),
    Knob("GROUNDING_VERIFY_ENABLED", "bool", "answer", description="drop generated sentences the evidence does not support"),
    Knob("GROUNDING_MIN_COVERAGE", "float", "answer", lo=0.0, hi=1.0, description="share of a sentence's terms the evidence must hold"),
    Knob("KEYWORD_BOOST_ENABLED", "bool", "boost", description="keyword boost for exact lookups"),
    Knob("KEYWORD_BOOST_MAX_DELTA", "float", "boost", lo=0.0, hi=0.2, description="largest keyword boost"),
    Knob("HYBRID_DENSE_WEIGHT", "float", "fusion", lo=0.0, hi=5.0, description="weight of the vector lane"),
    Knob("HYBRID_KEYWORD_WEIGHT", "float", "fusion", lo=0.0, hi=5.0, description="weight of the keyword lane"),
    Knob("HYBRID_EXACT_WEIGHT", "float", "fusion", lo=0.0, hi=5.0, description="weight of the exact-match lane"),
    Knob("HYBRID_RRF_K", "int", "fusion", lo=1, hi=200, description="rank constant of the fusion"),
    Knob("VECTOR_TOP_K", "int", "retrieval", lo=1, hi=200, description="candidates from the vector lane"),
    Knob("BM25_TOP_K", "int", "retrieval", lo=1, hi=200, description="candidates from the keyword lane"),
    Knob("CROSS_ENCODER_RERANK_ENABLED", "bool", "rerank", description="semantic reranking stage"),
    Knob("CROSS_ENCODER_TOP_N", "int", "rerank", lo=1, hi=100, description="candidates the cross-encoder reorders"),
    Knob("MAX_PARENT_EXPANDED_CHUNKS", "int", "context", lo=1, hi=50, description="parent chunks in the context"),
    Knob("MAX_PARENT_CONTEXT_CHARS", "int", "context", lo=200, hi=20000, description="characters of parent context"),
    Knob("RAG_HARD_MAX_DIST", "float", "guard", "high", 0.3, 1.5, "distance above which the bot does not answer"),
    Knob("RAG_SOFT_DIST_OTHER", "float", "guard", "high", 0.3, 1.5, "soft distance guard, general questions"),
    Knob("RAG_SOFT_DIST_RESET", "float", "guard", "high", 0.3, 1.5, "soft distance guard, reset questions"),
    Knob("RAG_SOFT_DIST_CHANGE", "float", "guard", "high", 0.3, 1.5, "soft distance guard, change questions"),
)

_BY_NAME: Dict[str, Knob] = {knob.name: knob for knob in REGISTRY}


def names() -> Tuple[str, ...]:
    return tuple(_BY_NAME)


def knob(name: str) -> Knob:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KnobError(f"'{name}' is not a registered knob") from None


def check_value(name: str, value: Any) -> Any:
    """The value in its type, or KnobError. A bool is only a bool, an int has no fraction, a float is finite."""
    spec = knob(name)
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise KnobError(f"{name}: expected true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KnobError(f"{name}: expected a number")
    if spec.kind == "int":
        if isinstance(value, float) and not value.is_integer():
            raise KnobError(f"{name}: expected a whole number")
        value = int(value)
    else:
        value = float(value)
        if not math.isfinite(value):
            raise KnobError(f"{name}: expected a finite number")
    if spec.lo is not None and value < spec.lo or spec.hi is not None and value > spec.hi:
        raise KnobError(f"{name}: {value} is outside [{spec.lo}, {spec.hi}]")
    return value


def check_change(changes: Mapping[str, Any]) -> Dict[str, Any]:
    """Every name registered and every value valid, or KnobError (nothing is returned for a partly valid change)."""
    if not changes:
        raise KnobError("a change needs at least one knob")
    return {name: check_value(name, value) for name, value in changes.items()}


# Settings that qa.py reads from the config attribute when one is set and from the environment otherwise.
ENV_KNOBS = frozenset({"QUERY_PREPROCESS_ENABLED"})
_TRUE_VALUES = {"1", "true", "yes", "on"}


def read(name: str) -> Any:
    """The value in force: the config attribute, or for an env knob without one the environment (as a bool)."""
    if hasattr(config, name):
        return getattr(config, name)
    if name in ENV_KNOBS:
        return str(os.getenv(name, "")).strip().lower() in _TRUE_VALUES
    return None


def current(name: str) -> Any:
    knob(name)
    return read(name)


def snapshot() -> Dict[str, Any]:
    return {name: read(name) for name in _BY_NAME}
