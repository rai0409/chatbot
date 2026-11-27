"""Deterministic perturbations of golden questions, to measure how well retrieval copes with how people really type.

Every kind is a rule, not a model: the same question and seed always give the same variant. A kind that cannot apply to
a question (no ASCII letters for "fullwidth", no katakana for "katakana_hiragana") gives no variant for it.

"abbreviate" and "synonym_swap" (2026-11-27) use the generic dictionaries of the query pipeline: abbreviate replaces a
dictionary expansion in the question with its abbreviation (configs/query_rules/abbreviations_ja.json read in reverse,
育児休業 -> 育休); synonym_swap replaces a term of a group in configs/japanese_business_synonyms.json with another member
of the group (never with a member on the other side of a negative_mismatch_pair, never with a member that contains
the term or is contained in it). They measure QUERY_ABBREVIATIONS / QUERY_BUSINESS_SYNONYMS.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from eval.tenant_eval.stats import clopper_pearson_upper
from rag_core.golden_qa import group_of
from rag_core.ja_text import extract_salient_terms_ja

KINDS = (
    "fullwidth", "spacing", "filler", "casual_ending", "typo_swap", "typo_drop", "katakana_hiragana", "punctuation",
    "keywords_only", "abbreviate", "synonym_swap",
)
_REPO = Path(__file__).resolve().parents[2]
ABBREVIATIONS_PATH = _REPO / "configs" / "query_rules" / "abbreviations_ja.json"
SYNONYMS_PATH = _REPO / "configs" / "japanese_business_synonyms.json"
FILLERS = ("えっと、", "すみません、", "ちょっと教えてほしいのですが、")
_RUN = re.compile(r"[一-龥ぁ-んァ-ヶ]+")
_KATAKANA_RUN = re.compile(r"[ァ-ヶー]{3,}")
_POLITE_END = re.compile(r"(ですか|ますか)[？?。]?$")


def _pick(text: str, kind: str, seed: str, n: int) -> int:
    return int(hashlib.sha256(f"{seed}\0{kind}\0{text}".encode("utf-8")).hexdigest()[:8], 16) % n


def _longest_run(text: str, minimum: int) -> Optional[re.Match]:
    runs = [m for m in _RUN.finditer(text) if len(m.group()) >= minimum]
    return max(runs, key=lambda m: len(m.group())) if runs else None


def _fullwidth(text: str, seed: str) -> Optional[str]:
    out = "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalnum() else c for c in text)
    return out if out != text else None


def _spacing(text: str, seed: str) -> Optional[str]:
    run = _longest_run(text, 6)
    if run is None:
        return None
    at = run.start() + 2 + _pick(text, "spacing", seed, len(run.group()) - 3)
    return text[:at] + " " + text[at:]


def _filler(text: str, seed: str) -> Optional[str]:
    return FILLERS[_pick(text, "filler", seed, len(FILLERS))] + text


def _casual_ending(text: str, seed: str) -> Optional[str]:
    if _POLITE_END.search(text):
        return _POLITE_END.sub(("なの？", "かな？", "のか教えて")[_pick(text, "casual_ending", seed, 3)], text)
    return text.rstrip("？?。 ") + "んだけど教えて"


def _typo_swap(text: str, seed: str) -> Optional[str]:
    run = _longest_run(text, 4)
    if run is None:
        return None
    chars = list(text)
    positions = [i for i in range(run.start() + 1, run.end() - 1) if "ー" not in (text[i], text[i + 1])]
    if not positions:
        return None
    i = positions[_pick(text, "typo_swap", seed, len(positions))]
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    out = "".join(chars)
    return out if out != text else None


def _typo_drop(text: str, seed: str) -> Optional[str]:
    run = _longest_run(text, 5)
    if run is None:
        return None
    positions = [i for i in range(run.start() + 1, run.end() - 1) if text[i] != "ー"]
    if not positions:
        return None
    i = positions[_pick(text, "typo_drop", seed, len(positions))]
    return text[:i] + text[i + 1:]


def _katakana_hiragana(text: str, seed: str) -> Optional[str]:
    out = _KATAKANA_RUN.sub(lambda m: "".join(chr(ord(c) - 0x60) if c != "ー" else c for c in m.group()), text)
    return out if out != text else None


def _punctuation(text: str, seed: str) -> Optional[str]:
    return text.rstrip("？?") if text.endswith(("？", "?")) else text + "？"


def _keywords_only(text: str, seed: str) -> Optional[str]:
    terms = extract_salient_terms_ja(text)
    return " ".join(terms) if terms else None


@lru_cache(maxsize=4)
def _abbreviation_pairs(path: str) -> tuple:
    """((expansion, abbr), ...) as written in the dictionary (not normalized: the question keeps its own form)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    pairs = []
    for entry in data.get("entries") or [] if isinstance(data, dict) else []:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        abbr, expansion = str(entry.get("abbr") or "").strip(), str(entry.get("expansion") or "").strip()
        if len(abbr) >= 2 and expansion and expansion != abbr:
            pairs.append((expansion, abbr))
    return tuple(pairs)


@lru_cache(maxsize=4)
def _synonym_groups(path: str) -> tuple:
    """(groups, mismatch sides) of the generic business dictionary."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (), ()
    groups = []
    for group in data.get("synonym_groups") or []:
        if isinstance(group, dict):
            terms = [str(t).strip() for t in [group.get("canonical"), *(group.get("terms") or [])] if str(t or "").strip()]
            if len(set(terms)) >= 2:
                groups.append(tuple(dict.fromkeys(terms)))
    sides = []
    for pair in data.get("negative_mismatch_pairs") or []:
        if isinstance(pair, dict):
            sides.append((tuple(pair.get("left") or ()), tuple(pair.get("right") or ())))
    return tuple(groups), tuple(sides)


def _occurrences(text: str, terms) -> List[str]:
    """Terms found in the text, longest first, skipping a term inside a longer one already found."""
    found: List[str] = []
    for term in sorted(set(terms), key=lambda t: (-len(t), t)):
        if term and term in text and not any(term in longer for longer in found):
            found.append(term)
    return found


def _abbreviate(text: str, seed: str) -> Optional[str]:
    pairs = _abbreviation_pairs(str(ABBREVIATIONS_PATH))
    by_expansion: Dict[str, List[str]] = {}
    for expansion, abbr in pairs:
        by_expansion.setdefault(expansion, []).append(abbr)
    hits = _occurrences(text, by_expansion)
    if not hits:
        return None
    expansion = sorted(hits)[_pick(text, "abbreviate", seed, len(hits))]
    abbrs = sorted(by_expansion[expansion])
    abbr = abbrs[_pick(text + expansion, "abbreviate", seed, len(abbrs))]
    return text.replace(expansion, abbr, 1)


def _crosses(term: str, replacement: str, sides) -> bool:
    for left, right in sides:
        for a, b in ((left, right), (right, left)):
            if any(s in term for s in a) and any(s in replacement for s in b):
                return True
    return False


def _synonym_swap(text: str, seed: str) -> Optional[str]:
    groups, sides = _synonym_groups(str(SYNONYMS_PATH))
    options = []
    for term in _occurrences(text, {t for g in groups for t in g if len(t) >= 2}):
        for group in groups:
            if term not in group:
                continue
            for other in group:
                if other == term or other in term or term in other or other in text or _crosses(term, other, sides):
                    continue
                options.append((term, other))
    if not options:
        return None
    term, other = sorted(set(options))[_pick(text, "synonym_swap", seed, len(set(options)))]
    return text.replace(term, other, 1)


_RULES = {
    "fullwidth": _fullwidth, "spacing": _spacing, "filler": _filler, "casual_ending": _casual_ending,
    "typo_swap": _typo_swap, "typo_drop": _typo_drop, "katakana_hiragana": _katakana_hiragana,
    "punctuation": _punctuation, "keywords_only": _keywords_only, "abbreviate": _abbreviate,
    "synonym_swap": _synonym_swap,
}


def perturb_question(text: str, *, kinds: Sequence[str] = KINDS, seed: str = "") -> List[Dict[str, str]]:
    """The variants of a question, one per applicable kind, never equal to the question or to each other."""
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown:
        raise ValueError(f"unknown perturbation kind(s): {', '.join(unknown)}")
    seen = {text}
    variants: List[Dict[str, str]] = []
    for kind in kinds:
        variant = _RULES[kind](text, seed)
        if variant and variant.strip() and variant not in seen:
            seen.add(variant)
            variants.append({"kind": kind, "text": variant})
    return variants


def perturb_cases(
    cases: Sequence[Mapping[str, Any]], *, kinds: Sequence[str] = KINDS, seed: str = ""
) -> List[Dict[str, Any]]:
    """Golden cases for the variants: same expectations, id "<case>~<kind>", the source case's group, priority 0."""
    out: List[Dict[str, Any]] = []
    for case in cases:
        for variant in perturb_question(case["question"], kinds=kinds, seed=seed):
            copy = dict(case)
            copy.update(
                case_id=f"{case['case_id']}~{variant['kind']}",
                question=variant["text"],
                group=group_of(case),
                priority=0,
                tags=[*case.get("tags", []), f"perturb:{variant['kind']}", f"from:{case['case_id']}"],
            )
            out.append(copy)
    return out


def robustness(rows: Sequence[Mapping[str, Any]], *, alpha: float = 0.05) -> Dict[str, Dict[str, Any]]:
    """Per kind: of the cases that pass as written, how many fail when perturbed (with an upper bound), and the rates."""
    by_id = {row["case_id"]: row for row in rows}
    kinds: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        source, sep, kind = row["case_id"].rpartition("~")
        if not sep or kind not in KINDS or source not in by_id:
            continue
        stats = kinds.setdefault(kind, {"pairs": 0, "orig_passed": 0, "lost": 0, "gained": 0, "perturbed_passed": 0})
        original = by_id[source]
        stats["pairs"] += 1
        stats["perturbed_passed"] += 1 if row["passed"] else 0
        if original["passed"]:
            stats["orig_passed"] += 1
            stats["lost"] += 0 if row["passed"] else 1
        elif row["passed"]:
            stats["gained"] += 1
    for stats in kinds.values():
        stats["lost_rate"] = stats["lost"] / stats["orig_passed"] if stats["orig_passed"] else None
        stats["lost_upper"] = clopper_pearson_upper(stats["lost"], stats["orig_passed"], alpha) if stats["orig_passed"] else None
        stats["perturbed_pass_rate"] = stats["perturbed_passed"] / stats["pairs"]
    return dict(sorted(kinds.items()))


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def robustness_markdown(baseline_rows: Sequence[Mapping[str, Any]], candidate_rows: Sequence[Mapping[str, Any]]) -> str:
    base, cand = robustness(baseline_rows), robustness(candidate_rows)
    lines = ["", "## 質問の揺れへの強さ（摂動）", "",
             "元の質問で合格しているケースが、揺らした質問で落ちた数。少ないほど強い。", "",
             "| 揺れの種類 | 対象 | 基準で落ちる | 候補で落ちる | 基準の合格率 | 候補の合格率 |", "|---|---|---|---|---|---|"]
    if not base:
        return "\n".join(lines[:4] + ["摂動した質問は評価に含まれていない。", ""])
    for kind, b in base.items():
        c = cand.get(kind, {"lost": 0, "lost_rate": None, "perturbed_pass_rate": None})  # nosec B105 - rates, not credentials
        lines.append(
            f"| {kind} | {b['orig_passed']} | {b['lost']}（{_pct(b['lost_rate'])}） | "
            f"{c['lost']}（{_pct(c['lost_rate'])}） | {_pct(b['perturbed_pass_rate'])} | {_pct(c['perturbed_pass_rate'])} |"
        )
    lines.append("")
    return "\n".join(lines)
