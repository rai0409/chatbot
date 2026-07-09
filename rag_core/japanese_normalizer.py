from __future__ import annotations

import json
import re
import string
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

import config


DEFAULT_SYNONYM_CONFIG = Path(config.BASE_DIR) / "configs" / "japanese_business_synonyms.json"
_MAX_TERMS = 20
_PUNCT_TABLE = str.maketrans({ch: " " for ch in string.punctuation})
_EXTRA_PUNCT_RE = re.compile(r"[、。・「」『』【】（）［］｛｝〈〉《》〔〕！？…￥〜〜ー—–－※★☆◎○●◇◆■□▲△▼▽|｜]+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*|[ぁ-んァ-ヴー一-龥々〆〤]{2,}")


def _bounded_unique(values: Sequence[Any], limit: int = _MAX_TERMS) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def normalize_japanese_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u3000", " ")
    normalized = normalized.lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def normalize_for_match(text: str) -> str:
    normalized = normalize_japanese_text(text)
    normalized = normalized.translate(_PUNCT_TABLE)
    normalized = _EXTRA_PUNCT_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def tokenize_lightweight(text: str) -> List[str]:
    normalized = normalize_for_match(text)
    tokens = []
    for match in _TOKEN_RE.finditer(normalized):
        token = match.group(0).strip("のをはがにへと")
        if token:
            tokens.append(token)
    return _bounded_unique(tokens)


def _empty_config(path: str | Path | None = None, *, loaded: bool = False, reason: str | None = None) -> Dict[str, Any]:
    return {
        "synonym_groups": [],
        "negative_mismatch_pairs": [],
        "_metadata": {
            "loaded": loaded,
            "path": str(path) if path is not None else None,
            "reason": reason,
        },
    }


def load_japanese_business_synonyms(path: str | Path | None = None) -> Dict[str, Any]:
    config_path = Path(path) if path is not None else DEFAULT_SYNONYM_CONFIG
    if not config_path.exists():
        return _empty_config(config_path, loaded=False, reason="missing")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Japanese business synonym config: {config_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Japanese business synonym config must be a JSON object: {config_path}")

    groups = payload.get("synonym_groups")
    pairs = payload.get("negative_mismatch_pairs")
    out = {
        "synonym_groups": groups if isinstance(groups, list) else [],
        "negative_mismatch_pairs": pairs if isinstance(pairs, list) else [],
        "_metadata": {
            "loaded": True,
            "path": str(config_path),
            "reason": None,
        },
    }
    return out


def _term_variants(canonical: Any, terms: Any) -> List[str]:
    raw_terms = list(terms) if isinstance(terms, list) else []
    raw_terms.append(canonical)
    variants: List[str] = []
    for term in raw_terms:
        normalized = normalize_for_match(str(term or ""))
        if normalized:
            variants.append(normalized)
    return _bounded_unique(variants, limit=50)


def _text_contains_term(normalized_text: str, term: str) -> bool:
    compact_text = normalized_text.replace(" ", "")
    compact_term = term.replace(" ", "")
    return bool(compact_term) and compact_term in compact_text


def _matched_terms(text: str, terms: Sequence[str]) -> List[str]:
    normalized_text = normalize_for_match(text)
    return _bounded_unique(term for term in terms if _text_contains_term(normalized_text, term))


def _canonical_hits(text: str, cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    hits: Dict[str, List[str]] = {}
    for group in cfg.get("synonym_groups") or []:
        if not isinstance(group, dict):
            continue
        canonical = normalize_for_match(str(group.get("canonical") or ""))
        if not canonical:
            continue
        terms = _term_variants(canonical, group.get("terms"))
        matched = _matched_terms(text, terms)
        if matched:
            hits[canonical] = matched
    return hits


def find_synonym_hits(query: str, candidate_text: str, config: Dict[str, Any]) -> Dict[str, Any]:
    query_hits = _canonical_hits(query, config or {})
    candidate_hits = _canonical_hits(candidate_text, config or {})
    shared = _bounded_unique(
        canonical for canonical in query_hits.keys() if canonical in candidate_hits
    )
    query_terms: List[str] = []
    candidate_terms: List[str] = []
    for canonical in shared:
        query_terms.extend(query_hits.get(canonical, []))
        candidate_terms.extend(candidate_hits.get(canonical, []))
    score = 0.0
    if query_hits:
        score = round(len(shared) / max(1, len(query_hits)), 6)
    return {
        "query_terms": _bounded_unique(query_terms),
        "candidate_terms": _bounded_unique(candidate_terms),
        "shared_canonicals": shared,
        "score": score,
    }


def compute_synonym_overlap_score(query: str, candidate_text: str, config: Dict[str, Any]) -> float:
    return float(find_synonym_hits(query, candidate_text, config or {}).get("score") or 0.0)


def compute_business_term_overlap_score(query: str, candidate_text: str, config: Dict[str, Any]) -> float:
    synonym_score = compute_synonym_overlap_score(query, candidate_text, config or {})
    query_tokens: Set[str] = set(tokenize_lightweight(query))
    candidate_tokens: Set[str] = set(tokenize_lightweight(candidate_text))
    token_score = 0.0
    if query_tokens:
        token_score = len(query_tokens & candidate_tokens) / max(1, len(query_tokens))
    return round(max(synonym_score, token_score), 6)


def _pair_side_hits(text: str, terms: Any) -> List[str]:
    if not isinstance(terms, list):
        return []
    normalized_terms = [
        normalized
        for term in terms
        if (normalized := normalize_for_match(str(term or "")))
    ]
    return _matched_terms(text, normalized_terms)


def detect_negative_mismatch(query: str, candidate_text: str, config: Dict[str, Any]) -> Dict[str, Any]:
    for pair in (config or {}).get("negative_mismatch_pairs") or []:
        if not isinstance(pair, dict):
            continue
        left_query = _pair_side_hits(query, pair.get("left"))
        right_query = _pair_side_hits(query, pair.get("right"))
        left_candidate = _pair_side_hits(candidate_text, pair.get("left"))
        right_candidate = _pair_side_hits(candidate_text, pair.get("right"))
        if left_query and right_candidate:
            return {
                "matched": True,
                "query_side_terms": _bounded_unique(left_query),
                "candidate_side_terms": _bounded_unique(right_candidate),
                "reason": "opposite_intent_terms",
            }
        if right_query and left_candidate:
            return {
                "matched": True,
                "query_side_terms": _bounded_unique(right_query),
                "candidate_side_terms": _bounded_unique(left_candidate),
                "reason": "opposite_intent_terms",
            }
    return {
        "matched": False,
        "query_side_terms": [],
        "candidate_side_terms": [],
        "reason": None,
    }
