"""Query preprocessing pipeline (N5). Fixed order, every step is traced.

1. normalization (ja_text.normalize_for_index, the same as the index)
2. colloquial cleanup: fillers at the start and request/question endings
   ("〜なんだけど", "教えてください", "〜ですか") are removed. Rules live in
   configs/query_rules/colloquial_ja.json; a tenant may add rules in
   configs/query_rules/tenants/<tenant_id>.json
3. numeric tokens: handled by the keyword lane (KEYWORD_NUMERIC_TOKENS)
4. synonym expansion from the tenant's approved RAG profile dictionary only
   (config/rag_profiles/<id>/exported/synonyms.json, enabled entries)
5. dictionary typo correction: a query token that is not in the keyword
   index vocabulary gets index terms at edit distance 1 (2 for long tokens)
   as expansions. Identifiers (letters+digits), quoted text and short tokens
   are never corrected
6. reading lane: not implemented (needs a separate reading index)

The cleaned query replaces the question for retrieval; synonyms and typo
candidates are appended once as expansions (the original terms stay, so
expansions weigh less in BM25 and in the embedding).

Enable with QUERY_PREPROCESS_ENABLED=true (qa.py). Measure on the bench
slices colloquial / typo / short before enabling in production.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import config
from rag_core.ja_text import normalize_for_index

RULES_DIR = Path(config.BASE_DIR) / "configs" / "query_rules"
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_QUOTED_RE = re.compile(r"「([^」]+)」|\"([^\"]+)\"")
_ID_LIKE_RE = re.compile(r"(?=.*[a-z])(?=.*\d)[a-z0-9._:/-]+")
_MAX_SYNONYM_EXPANSIONS = 8
_MAX_TYPO_EXPANSIONS = 4
_MIN_TYPO_TOKEN_CHARS = 3
_LONG_TOKEN_CHARS = 7
_ENABLED_VALUES = {"1", "true", "yes", "on"}


@dataclass
class QueryPreprocessResult:
    original: str
    query: str
    expansions: List[str] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def retrieval_query(self) -> str:
        return " ".join([self.query, *self.expansions]).strip()

    def to_trace(self) -> Dict[str, Any]:
        return {"original": self.original, "query": self.query, "expansions": list(self.expansions), "steps": list(self.steps)}


def _flag(name: str, default: bool) -> bool:
    value = getattr(config, name, None)
    if value is None:
        raw = str(os.getenv(name, "")).strip().lower()
        return default if raw == "" else raw in _ENABLED_VALUES
    return bool(value)


# --- 2. colloquial cleanup ---------------------------------------------------------


@lru_cache(maxsize=32)
def _load_rules(path: str) -> Tuple[Tuple[re.Pattern, ...], Tuple[re.Pattern, ...]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), ()
    prefixes = tuple(re.compile(p) for p in data.get("strip_prefix", []) if isinstance(p, str))
    suffixes = tuple(re.compile(p) for p in data.get("strip_suffix", []) if isinstance(p, str))
    return prefixes, suffixes


def _rule_files(tenant_id: str) -> List[str]:
    files = [str(RULES_DIR / "colloquial_ja.json")]
    tenant = str(tenant_id or "").strip()
    if tenant and tenant != "default" and _TENANT_RE.fullmatch(tenant):
        files.append(str(RULES_DIR / "tenants" / f"{tenant}.json"))
    return files


def strip_colloquial(text: str, *, tenant_id: str = "default") -> Tuple[str, List[str]]:
    applied: List[str] = []
    out = text
    for path in _rule_files(tenant_id):
        prefixes, suffixes = _load_rules(path)
        for _ in range(3):  # fillers can repeat: "あの、えっと、"
            changed = False
            for pattern in prefixes:
                new = pattern.sub("", out, count=1)
                if new != out and new.strip():
                    applied.append(f"prefix:{pattern.pattern}")
                    out, changed = new, True
            for pattern in suffixes:
                new = pattern.sub("", out, count=1)
                if new != out and new.strip():
                    applied.append(f"suffix:{pattern.pattern}")
                    out, changed = new, True
            if not changed:
                break
    return out.strip(), applied


# --- 4. synonyms ---------------------------------------------------------------------


def _synonym_groups(rag_profile_id: Optional[str]) -> List[List[str]]:
    from rag_core.profile_loader import is_valid_rag_profile_id, load_rag_profile

    profile_id = rag_profile_id or "default"
    if not is_valid_rag_profile_id(profile_id):
        return []
    groups: List[List[str]] = []
    for entry in load_rag_profile(profile_id).get("synonyms") or []:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        terms = [entry.get("canonical"), *(entry.get("synonyms") or [])]
        normalized = [normalize_for_index(str(t)) for t in terms if str(t or "").strip()]
        if len(normalized) >= 2:
            groups.append(list(dict.fromkeys(normalized)))
    return groups


def expand_synonyms(query: str, *, rag_profile_id: Optional[str] = None) -> List[str]:
    expansions: List[str] = []
    for group in _synonym_groups(rag_profile_id):
        if not any(term and term in query for term in group):
            continue
        for term in group:
            if term not in query and term not in expansions:
                expansions.append(term)
        if len(expansions) >= _MAX_SYNONYM_EXPANSIONS:
            break
    return expansions[:_MAX_SYNONYM_EXPANSIONS]


# --- 5. typo correction ------------------------------------------------------------------


def _deletes(word: str, distance: int) -> Set[str]:
    out = {word}
    frontier = {word}
    for _ in range(distance):
        nxt: Set[str] = set()
        for w in frontier:
            for i in range(len(w)):
                nxt.add(w[:i] + w[i + 1 :])
        out |= nxt
        frontier = nxt
    return out


def _edit_distance(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


class _Vocabulary:
    """Keyword-index vocabulary with a deletion index (SymSpell style)."""

    def __init__(self, tokens: Sequence[Sequence[str]]):
        self._build({t for doc in tokens for t in doc})

    @classmethod
    def from_words(cls, words) -> "_Vocabulary":
        """From the keyword index's vocabulary (it keeps no token lists, stage 14)."""
        vocab = cls.__new__(cls)
        vocab._build(words)
        return vocab

    def _build(self, words) -> None:
        self.words: Set[str] = {t for t in words if t and ":" not in t and len(t) >= _MIN_TYPO_TOKEN_CHARS - 1}
        self.by_delete: Dict[str, Set[str]] = {}
        for word in self.words:
            for key in _deletes(word, 2 if len(word) >= _LONG_TOKEN_CHARS else 1):
                self.by_delete.setdefault(key, set()).add(word)

    def candidates(self, token: str) -> List[str]:
        limit = 2 if len(token) >= _LONG_TOKEN_CHARS else 1
        found: Set[str] = set()
        for key in _deletes(token, limit):
            found |= self.by_delete.get(key, set())
        scored = sorted(
            (d, w) for w in found if w != token and (d := _edit_distance(token, w, limit)) <= limit
        )
        return [w for _, w in scored]


_VOCAB_LOCK = Lock()
_VOCAB_CACHE: Dict[int, Tuple[Any, _Vocabulary]] = {}


def _vocabulary(collection_name: Optional[str]) -> Tuple[Any, _Vocabulary]:
    from rag_core import retrieval

    index = retrieval._index_for_collection(collection_name)
    key = id(index)
    with _VOCAB_LOCK:
        cached = _VOCAB_CACHE.get(key)
        if cached is not None and cached[0] is index:
            return cached
        vocab = _Vocabulary.from_words(index.vocabulary)
        if len(_VOCAB_CACHE) >= 4:
            _VOCAB_CACHE.pop(next(iter(_VOCAB_CACHE)))
        _VOCAB_CACHE[key] = (index, vocab)
        return index, vocab


def correct_typos(query: str, *, collection_name: Optional[str] = None) -> List[Dict[str, Any]]:
    from rag_core import retrieval

    index, vocab = _vocabulary(collection_name)
    if not vocab.words:
        return []
    quoted = " ".join(a or b for a, b in _QUOTED_RE.findall(query))
    corrections: List[Dict[str, Any]] = []
    for token in dict.fromkeys(retrieval._query_tokens(query, index.tokenizer)):
        # Namespaced tokens (2g: bigrams, numeric tokens) are not words.
        if ":" in token or len(token) < _MIN_TYPO_TOKEN_CHARS:
            continue
        if token in vocab.words or _ID_LIKE_RE.fullmatch(token) or token.isdigit() or (quoted and token in quoted):
            continue
        candidates = vocab.candidates(token)
        if candidates:
            corrections.append({"token": token, "candidates": candidates[:2]})
        if len(corrections) >= _MAX_TYPO_EXPANSIONS:
            break
    return corrections


# --- pipeline ------------------------------------------------------------------------------


def preprocess_query(
    question: str,
    *,
    tenant_id: str = "default",
    rag_profile_id: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> QueryPreprocessResult:
    normalized = normalize_for_index(question or "")
    result = QueryPreprocessResult(original=question or "", query=normalized)
    result.steps.append({"step": "normalize", "changed": normalized != (question or "")})

    cleaned, applied = strip_colloquial(normalized, tenant_id=tenant_id)
    result.query = cleaned or normalized
    result.steps.append({"step": "colloquial", "rules": applied[:10]})

    result.steps.append(
        {"step": "numeric_tokens", "enabled": bool(getattr(config, "KEYWORD_NUMERIC_TOKENS", False))}
    )

    if _flag("QUERY_SYNONYM_EXPANSION", True):
        synonyms = expand_synonyms(result.query, rag_profile_id=rag_profile_id)
        result.expansions.extend(s for s in synonyms if s not in result.expansions)
        result.steps.append({"step": "synonyms", "added": synonyms})

    if _flag("QUERY_TYPO_CORRECTION", True):
        try:
            corrections = correct_typos(result.query, collection_name=collection_name)
        except Exception as exc:  # noqa: BLE001 - preprocessing must never fail a query
            corrections = []
            result.steps.append({"step": "typo", "error": type(exc).__name__})
        else:
            for item in corrections:
                for candidate in item["candidates"]:
                    if candidate not in result.expansions:
                        result.expansions.append(candidate)
            result.steps.append({"step": "typo", "corrections": corrections})

    result.steps.append({"step": "reading_lane", "status": "not_implemented"})
    return result
