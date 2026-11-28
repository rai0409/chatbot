"""Query preprocessing pipeline (N5). Fixed order, every step is traced.

1. normalization (ja_text.normalize_for_index, the same as the index)
2. colloquial cleanup: rewrite rules first ("rewrite" key, re.sub pairs, and
   a built-in definition rewrite "Xってなに" -> "Xとは"), then fillers at the
   start and request/question endings ("〜なんだけど", "教えてください",
   "〜ですか") are removed. Rules live in configs/query_rules/colloquial_ja.json;
   a tenant may add rules in configs/query_rules/tenants/<tenant_id>.json.
   Every rule written with 〜 also matches ~ (NORMALIZATION_PROFILE=index)
3. numeric tokens: handled by the keyword lane (KEYWORD_NUMERIC_TOKENS)
3b. abbreviations (QUERY_ABBREVIATIONS, default off): generic pairs in
   configs/query_rules/abbreviations_ja.json plus the tenant's "abbreviations"
   key in configs/query_rules/tenants/<tenant_id>.json (育休 -> 育児休業, and the
   reverse for bidirectional entries). Short abbreviations must be a whole
   query term and are ignored inside a longer dictionary term in the query
4. synonym expansion from the tenant's approved RAG profile dictionary
   (config/rag_profiles/<id>/exported/synonyms.json, enabled entries); with
   QUERY_BUSINESS_SYNONYMS (default off) the generic groups of
   configs/japanese_business_synonyms.json are merged in (a profile group owns
   its terms) and its negative_mismatch_pairs suppress an expansion that
   would cross to the opposite intent. Terms of up to 3 characters must be a
   whole query term, not a substring. Abbreviations and synonyms share one
   cap of expansions
5. dictionary typo correction: a query token that is not in the keyword
   index vocabulary OF THE TENANT gets that tenant's index terms at edit
   distance 1 (2 for long tokens)
   as expansions. Identifiers (letters+digits), quoted text and short tokens
   are never corrected
6. reading lane: not implemented (needs a separate reading index)

The cleaned query replaces the question for retrieval; synonyms and typo
candidates are appended once as expansions (the original terms stay, so
expansions weigh less in BM25 and in the embedding).

On by default; config.query_preprocess_enabled() is the single switch
(QUERY_PREPROCESS_ENABLED=false restores the raw query). Measure on the bench
slices colloquial / abbreviation / typo / short when changing the rules, and
with scripts/tenant_eval.py --perturb (docs/operations/query_normalization_ja.md).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import config
from rag_core.ja_text import normalize_for_index

logger = logging.getLogger(__name__)
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


def _tilde_variants(pattern: str) -> List[str]:
    """The pattern, plus a copy that matches "~" where it matches "〜".

    NORMALIZATION_PROFILE=index turns 〜 into ~ before the rules run, so a rule
    written with 〜 would otherwise never fire there."""
    out = [pattern]
    if "〜" in pattern:
        out.append(pattern.replace("〜", "~"))
    return out


def _compile_all(patterns) -> Tuple[re.Pattern, ...]:
    compiled: List[re.Pattern] = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        for variant in _tilde_variants(pattern):
            try:
                compiled.append(re.compile(variant))
            except re.error as exc:  # a broken tenant rule must not break every query; the rule files are cached, so this logs once
                logger.warning("query rule skipped (invalid regex %r): %s", variant, exc)
                continue
    return tuple(compiled)


def _rewrite_rules(raw) -> Tuple[Tuple[re.Pattern, str], ...]:
    """"rewrite": [{"pattern": regex, "replace": template}] or [[regex, template]] (re.sub syntax, \\1 / \\g<name>)."""
    out: List[Tuple[re.Pattern, str]] = []
    for rule in raw or []:
        if isinstance(rule, dict):
            pattern = rule.get("pattern")
            replace = rule.get("replace", rule.get("replacement", rule.get("repl")))
        elif isinstance(rule, (list, tuple)) and len(rule) == 2:
            pattern, replace = rule
        else:
            continue
        if not isinstance(pattern, str) or not isinstance(replace, str):
            continue
        for variant in _tilde_variants(pattern):
            try:
                out.append((re.compile(variant), replace))
            except re.error as exc:
                logger.warning("query rewrite rule skipped (invalid regex %r): %s", variant, exc)
                continue
    return tuple(out)


@lru_cache(maxsize=32)
def _load_rules(path: str) -> Tuple[Tuple[re.Pattern, ...], Tuple[re.Pattern, ...], Tuple[Tuple[re.Pattern, str], ...]]:
    """(strip_prefix, strip_suffix, rewrite) rules of one file. Rule types:
    strip_prefix / strip_suffix: patterns whose match is removed;
    rewrite: {"pattern", "replace"} pairs applied with re.sub (before stripping)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), (), ()
    prefixes = _compile_all(data.get("strip_prefix", []))
    suffixes = _compile_all(data.get("strip_suffix", []))
    rewrites = _rewrite_rules(data.get("rewrite", []))
    return prefixes, suffixes, rewrites


def _rule_files(tenant_id: str) -> List[str]:
    files = [str(RULES_DIR / "colloquial_ja.json")]
    tenant = str(tenant_id or "").strip()
    if tenant and tenant != "default" and _TENANT_RE.fullmatch(tenant):
        files.append(str(RULES_DIR / "tenants" / f"{tenant}.json"))
    return files


# Built-in definition rewrite (works without any rule file): "XってなにX" style
# questions become "Xとは", so the colloquial cleanup no longer leaves "vpnなに"
# and the question keeps its definition form for retrieval.
_TAIL = r"(?:ですか|でしょうか|だっけ|でしたっけ|なの|なのか|なんですか|なんでしょう|か)?[?？。!！~〜\s]*$"
_BUILTIN_REWRITES: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"^(?P<x>\S.*?)\s*って(?:なに|何|なん|どういう(?:意味|こと)|どんな(?:もの|意味))" + _TAIL), r"\g<x>とは"),
    (re.compile(r"^(?P<x>\S.*?)\s*とは(?:なに|何|なん)" + _TAIL), r"\g<x>とは"),
)


def rewrite_definition(text: str) -> Tuple[str, List[str]]:
    """The built-in definition rewrite; (text, applied rule names)."""
    for pattern, replace in _BUILTIN_REWRITES:
        new = pattern.sub(replace, text, count=1)
        if new != text and new.strip() and new.strip() != "とは":
            return new.strip(), [f"rewrite:builtin:{pattern.pattern[:40]}"]
    return text, []


def apply_rewrites(text: str, *, tenant_id: str = "default") -> Tuple[str, List[str]]:
    """Rule-file rewrites (global, then tenant), then the built-in definition rewrite."""
    applied: List[str] = []
    out = text
    for path in _rule_files(tenant_id):
        _prefixes, _suffixes, rewrites = _load_rules(path)
        for pattern, replace in rewrites:
            try:
                new = pattern.sub(replace, out, count=1)
            except (re.error, IndexError):
                continue
            if new != out and new.strip():
                applied.append(f"rewrite:{pattern.pattern}")
                out = new
    out, builtin = rewrite_definition(out)
    return out, applied + builtin


def strip_colloquial(text: str, *, tenant_id: str = "default", rewrite: bool = False) -> Tuple[str, List[str]]:
    """Remove filler prefixes and request / question endings.

    rewrite=True first applies the rewrite rules (rule files and the built-in
    definition rewrite: "vpnってなに" -> "vpnとは"). Callers that need the
    result to be a substring of the input (approved QA matching) keep the
    default False."""
    applied: List[str] = []
    out = text
    if rewrite:
        out, applied = apply_rewrites(out, tenant_id=tenant_id)
    for path in _rule_files(tenant_id):
        prefixes, suffixes, _rewrites = _load_rules(path)
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


# --- cached rule files -----------------------------------------------------------------


_FILE_CACHE_LOCK = Lock()
_FILE_CACHE: Dict[Tuple[str, str], Tuple[Tuple, Any]] = {}


def _file_state(path: str) -> Tuple:
    try:
        st = os.stat(path)
    except OSError:
        return ("missing",)
    return (st.st_mtime_ns, st.st_size)


def _cached_file(path: str, parse) -> Any:
    """parse(path), reused until the file's mtime or size changes (an edited dictionary applies without a restart)."""
    key = (path, getattr(parse, "__name__", repr(parse)))
    state = _file_state(path)
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(key)
        if hit is not None and hit[0] == state:
            return hit[1]
    value = parse(path)
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[key] = (state, value)
    return value


def _read_json(path: str) -> Optional[Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:  # a broken dictionary must not break every query; logged once per file state
        logger.warning("query dictionary skipped (%s): %s", path, exc)
        return None


# --- 3b. abbreviations -------------------------------------------------------------------


@dataclass(frozen=True)
class Abbreviation:
    abbr: str
    expansion: str
    bidirectional: bool = True


def _parse_abbreviation_entries(raw: Any) -> List[Tuple[str, Optional[Abbreviation]]]:
    """[(abbr, entry or None for a disabled one)] of an "entries" / "abbreviations" list."""
    out: List[Tuple[str, Optional[Abbreviation]]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        abbr = normalize_for_index(str(item.get("abbr") or "")).strip()
        expansion = normalize_for_index(str(item.get("expansion") or "")).strip()
        if not abbr or len(abbr) < 2:  # 1-character abbreviations are never safe
            continue
        if item.get("enabled") is False:
            out.append((abbr, None))
            continue
        if not expansion or expansion == abbr:
            continue
        out.append((abbr, Abbreviation(abbr, expansion, bool(item.get("bidirectional", True)))))
    return out


def _parse_abbreviation_file(path: str) -> Tuple[Tuple[str, Optional[Abbreviation]], ...]:
    data = _read_json(path)
    if not isinstance(data, dict):
        return ()
    # The global file uses "entries"; a tenant's query-rule file uses "abbreviations" next to its colloquial rules.
    return tuple(_parse_abbreviation_entries(data.get("entries", data.get("abbreviations"))))


def abbreviation_files(tenant_id: str = "default") -> List[str]:
    """The global dictionary, then the tenant's query-rule file (same validation as the colloquial rules)."""
    return [str(RULES_DIR / "abbreviations_ja.json"), *_rule_files(tenant_id)[1:]]


def abbreviation_entries(tenant_id: str = "default") -> Tuple[Abbreviation, ...]:
    """The effective entries: a tenant entry replaces the global one with the same abbr ({"enabled": false} drops it)."""
    merged: Dict[Tuple[str, str], Optional[Abbreviation]] = {}
    for path in abbreviation_files(tenant_id):
        tenant_file = path != str(RULES_DIR / "abbreviations_ja.json")
        if tenant_file:
            overridden = {abbr for abbr, _entry in _cached_file(path, _parse_abbreviation_file)}
            merged = {k: v for k, v in merged.items() if k[0] not in overridden}
        for abbr, entry in _cached_file(path, _parse_abbreviation_file):
            merged[(abbr, entry.expansion if entry else "")] = entry
    return tuple(entry for entry in merged.values() if entry is not None)


def _present_surfaces(query: str, surfaces: Sequence[str], tokens: Optional[Set[str]]) -> Set[str]:
    """Dictionary surfaces that occur in the query on their own.

    Longer surfaces claim their characters first, so 在宅 inside 在宅勤務 or 時短 inside 時短勤務 is not a separate
    hit; surfaces of up to 3 characters must also be a whole query term (マイナ is not in マイナス)."""
    claimed = [False] * len(query)
    found: Set[str] = set()
    for surface in sorted(set(s for s in surfaces if s), key=lambda s: (-len(s), s)):
        start = 0
        while True:
            at = query.find(surface, start)
            if at < 0:
                break
            start = at + 1
            if any(claimed[at : at + len(surface)]):
                continue
            if not _surface_at(surface, query, at):
                continue
            for i in range(at, at + len(surface)):
                claimed[i] = True
            found.add(surface)
    return found


def expand_abbreviations(
    query: str, *, tenant_id: str = "default", entries: Optional[Sequence[Abbreviation]] = None,
    limit: Optional[int] = None,
) -> List[str]:
    """Expansions for the abbreviations in the query (and abbreviations for expansions, when bidirectional)."""
    query = normalize_for_index(query or "")
    entries = abbreviation_entries(tenant_id) if entries is None else entries
    cap = _MAX_SYNONYM_EXPANSIONS if limit is None else max(0, int(limit))
    if not entries or not query or cap == 0:
        return []
    surfaces = [s for e in entries for s in (e.abbr, e.expansion)]
    tokens = _query_term_set(query) if any(len(s) <= _SHORT_TERM_MAX_CHARS for s in surfaces) else None
    present = _present_surfaces(query, surfaces, tokens)
    out: List[str] = []
    for entry in entries:
        if entry.abbr in present and entry.expansion not in query:
            term = entry.expansion
        elif entry.bidirectional and entry.expansion in present and entry.abbr not in query:
            term = entry.abbr
        else:
            continue
        if term not in out:
            out.append(term)
        if len(out) >= cap:
            break
    return out


# --- 4. synonyms ---------------------------------------------------------------------


# None: rag_core.japanese_normalizer.DEFAULT_SYNONYM_CONFIG (tests may point this at a fixture).
BUSINESS_SYNONYMS_PATH: Optional[str] = None


def _parse_business_synonyms(path: str) -> Tuple[Tuple[Tuple[str, ...], ...], Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...]]:
    from rag_core.japanese_normalizer import load_japanese_business_synonyms

    try:
        cfg = load_japanese_business_synonyms(path)
    except ValueError as exc:
        logger.warning("business synonyms skipped: %s", exc)
        return (), ()
    # The loader returns only the groups and the pairs; the expansion-only exclusions are read from the file itself.
    raw = _read_json(path)
    options = raw.get("query_expansion") if isinstance(raw, dict) else None
    excluded = {
        normalize_for_index(str(t)).strip()
        for t in ((options or {}).get("exclude_terms") or [] if isinstance(options, dict) else [])
    }
    groups: List[Tuple[str, ...]] = []
    for entry in cfg.get("synonym_groups") or []:
        if not isinstance(entry, dict):
            continue
        terms = [entry.get("canonical"), *(entry.get("terms") or [])]
        normalized = list(dict.fromkeys(normalize_for_index(str(t)).strip() for t in terms if str(t or "").strip()))
        normalized = [t for t in normalized if t and t not in excluded]
        if len(normalized) >= 2:
            groups.append(tuple(normalized))
    pairs: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = []
    for pair in cfg.get("negative_mismatch_pairs") or []:
        if not isinstance(pair, dict):
            continue
        sides = []
        for key in ("left", "right"):
            raw = pair.get(key) if isinstance(pair.get(key), list) else []
            sides.append(tuple(t for t in (normalize_for_index(str(x or "")).strip() for x in raw) if t))
        if sides[0] and sides[1]:
            pairs.append((sides[0], sides[1]))
    return tuple(groups), tuple(pairs)


def business_synonyms() -> Tuple[Tuple[Tuple[str, ...], ...], Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...]]:
    """(groups, negative_mismatch_pairs) of the generic business dictionary, index-normalized, cached by mtime."""
    from rag_core.japanese_normalizer import DEFAULT_SYNONYM_CONFIG

    return _cached_file(str(BUSINESS_SYNONYMS_PATH or DEFAULT_SYNONYM_CONFIG), _parse_business_synonyms)


def merge_synonym_groups(profile_groups: Sequence[Sequence[str]], generic_groups: Sequence[Sequence[str]]) -> List[List[str]]:
    """Profile groups, then the generic groups that share no term with any profile group (the profile owns its
    terms: a tenant that grouped 退職 differently is not overridden). Identical groups are kept once."""
    out = [list(dict.fromkeys(g)) for g in profile_groups]
    owned = {t for g in out for t in g}
    seen = {frozenset(g) for g in out}
    for group in generic_groups:
        terms = list(dict.fromkeys(t for t in group if t))
        key = frozenset(terms)
        if len(terms) < 2 or key in seen or owned & key:
            continue
        seen.add(key)
        out.append(terms)
    return out


def crosses_mismatch(term: str, query: str, pairs) -> bool:
    """Whether adding term would cross a negative mismatch pair: the term is on one side, the query hits the other
    side and nothing of the term's side (扶養に入る must not be expanded with 扶養から外れる)."""
    for left, right in pairs or ():
        for side, other in ((left, right), (right, left)):
            if any(s in term for s in side) and any(s in query for s in other) and not any(s in query for s in side):
                return True
    return False


def _profile_synonym_groups(rag_profile_id: Optional[str]) -> List[List[str]]:
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


def _synonym_groups(rag_profile_id: Optional[str]) -> List[List[str]]:
    groups = _profile_synonym_groups(rag_profile_id)
    if not _flag("QUERY_BUSINESS_SYNONYMS", False):
        return groups
    return merge_synonym_groups(groups, business_synonyms()[0])


_SHORT_TERM_MAX_CHARS = 3


# ASCII identifiers include punctuation used in product names and versions.
_ASCII_ID = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._:/-")
_JAPANESE_WORD = re.compile(r"[一-龥々〆〤ぁ-んァ-ヴー]")
_LEFT_PARTICLES = frozenset("のをがはにでとへもや")
_RIGHT_PARTICLES = ("について", "に関して", "から", "まで", "より", "とは", "って", "の", "を", "が", "は", "に", "で", "と", "へ", "も", "や")


def _query_term_set(query: str) -> Set[str]:
    """ASCII tokens for legacy callers; dictionary matching uses explicit boundaries."""
    norm = normalize_for_index(query or "")
    return set(re.findall(r"[a-z0-9][a-z0-9._:/-]*", norm))


def _surface_at(term: str, query: str, at: int) -> bool:
    end = at + len(term)
    before = query[at - 1] if at else ""
    after = query[end] if end < len(query) else ""
    if term[0].isascii() and term[0].isalnum() and before in _ASCII_ID:
        return False
    if term[-1].isascii() and term[-1].isalnum() and after in _ASCII_ID:
        return False
    if len(term) <= _SHORT_TERM_MAX_CHARS and not term.isascii():
        if before and _JAPANESE_WORD.fullmatch(before) and before not in _LEFT_PARTICLES:
            return False
        if after and _JAPANESE_WORD.fullmatch(after) and not query[end:].startswith(_RIGHT_PARTICLES):
            if not ("ぁ" <= term[-1] <= "ん" and re.fullmatch(r"[一-龥々〆〤]", after)):
                return False
    return True


def term_in_query(term: str, query: str, tokens: Optional[Set[str]] = None) -> bool:
    """Match a normalized dictionary surface without invoking a tokenizer.

    Japanese short words require conservative particle or punctuation boundaries;
    this does not attempt general Japanese word segmentation.
    """
    term, query = normalize_for_index(term or ""), normalize_for_index(query or "")
    if not term:
        return False
    start = 0
    while (at := query.find(term, start)) >= 0:
        if _surface_at(term, query, at):
            return True
        start = at + 1
    return False


def expand_synonyms(
    query: str, *, rag_profile_id: Optional[str] = None, limit: Optional[int] = None, exclude: Sequence[str] = (),
) -> List[str]:
    """Synonym expansions; limit is the room left in the shared cap, exclude the expansions already made."""
    cap = _MAX_SYNONYM_EXPANSIONS if limit is None else max(0, min(int(limit), _MAX_SYNONYM_EXPANSIONS))
    if cap == 0:
        return []
    query = normalize_for_index(query or "")
    business = _flag("QUERY_BUSINESS_SYNONYMS", False)
    generic, pairs = business_synonyms() if business else ((), ())
    generic_keys = {frozenset(g) for g in generic}
    taken = set(exclude)
    expansions: List[str] = []
    groups = _synonym_groups(rag_profile_id)
    present = _present_surfaces(query, [term for group in groups for term in group], None)
    for group in groups:
        matched = [term for term in group if term in present]
        if not matched:
            continue
        is_generic = frozenset(group) in generic_keys
        for term in group:
            if term in query or term in expansions or term in taken:
                continue
            if pairs and crosses_mismatch(term, query, pairs):
                continue
            # A generic group mixes synonyms with narrower compounds (パスワード / パスワード再設定, 精算 / 経費精算);
            # a compound of the matched term would narrow the question to one of its sub-topics, so it is not added.
            if is_generic and any(m in term for m in matched):
                continue
            expansions.append(term)
        if len(expansions) >= cap:
            break
    return expansions[:cap]


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
_VOCAB_CACHE: Dict[Tuple[int, str], Tuple[Any, _Vocabulary]] = {}
_VOCAB_CACHE_MAX = 16


def _tenant_words(index, tenant: str):
    # 2026-11-22: only the tenant's own rows. The whole collection's vocabulary
    # let another tenant's terms become "corrections" (query and trace leak).
    getter = getattr(index, "tenant_vocabulary", None)
    if callable(getter):
        return getter(tenant)
    return index.vocabulary  # an index object without tenant data (test doubles)


def _vocabulary(collection_name: Optional[str], tenant_id: str = "default") -> Tuple[Any, _Vocabulary]:
    from rag_core import retrieval

    index = retrieval._index_for_collection(collection_name)
    tenant = retrieval.normalize_tenant_id(tenant_id)
    key = (id(index), tenant)
    with _VOCAB_LOCK:
        cached = _VOCAB_CACHE.get(key)
        if cached is not None and cached[0] is index:
            return cached
    vocab = _Vocabulary.from_words(_tenant_words(index, tenant))
    with _VOCAB_LOCK:
        if key not in _VOCAB_CACHE and len(_VOCAB_CACHE) >= _VOCAB_CACHE_MAX:
            _VOCAB_CACHE.pop(next(iter(_VOCAB_CACHE)))
        _VOCAB_CACHE[key] = (index, vocab)
    return index, vocab


def correct_typos(
    query: str, *, collection_name: Optional[str] = None, tenant_id: str = "default"
) -> List[Dict[str, Any]]:
    from rag_core import retrieval

    index, vocab = _vocabulary(collection_name, tenant_id)
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

    cleaned, applied = strip_colloquial(normalized, tenant_id=tenant_id, rewrite=True)
    result.query = cleaned or normalized
    result.steps.append({"step": "colloquial", "rules": applied[:10]})

    result.steps.append(
        {"step": "numeric_tokens", "enabled": bool(getattr(config, "KEYWORD_NUMERIC_TOKENS", False))}
    )

    # Abbreviations and synonyms share one cap (_MAX_SYNONYM_EXPANSIONS); the abbreviation step is only traced
    # while QUERY_ABBREVIATIONS is on.
    if _flag("QUERY_ABBREVIATIONS", False):
        abbreviations = expand_abbreviations(result.query, tenant_id=tenant_id)
        result.expansions.extend(a for a in abbreviations if a not in result.expansions)
        result.steps.append({"step": "abbreviations", "added": abbreviations})

    if _flag("QUERY_SYNONYM_EXPANSION", True):
        synonyms = expand_synonyms(
            result.query,
            rag_profile_id=rag_profile_id,
            limit=_MAX_SYNONYM_EXPANSIONS - len(result.expansions),
            exclude=result.expansions,
        )
        result.expansions.extend(s for s in synonyms if s not in result.expansions)
        result.steps.append({"step": "synonyms", "added": synonyms})

    if _flag("QUERY_TYPO_CORRECTION", True):
        try:
            corrections = correct_typos(result.query, collection_name=collection_name, tenant_id=tenant_id)
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
