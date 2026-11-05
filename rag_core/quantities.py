"""Deterministic extraction of numbers, money, percents, dates and counts.

Rules favour precision over recall and every rule is explicit:
- arabic numbers are always candidates; kanji numerals only in a numeric
  context (followed by a unit/counter, or after 第 / an era name), so 一般 or
  統一 never yield numbers;
- identifiers (ZZ-10, A100, 03-1234-5678, 100-0001) are skipped;
- common words that start with a numeral (十分, 一時, 一部, ...) are skipped;
- 10〜20万円 is read as 100,000-200,000 JPY (shared unit and multiplier).

Input is NFKC-normalized first; spans and raw strings refer to that text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from rag_core import ja_dates
from rag_core.ja_numbers import KANJI_NUMERAL_CLASS, LARGE_UNITS, decimal_to_str, parse_japanese_number

EXTRACTOR_VERSION = "quantities-v1"


@dataclass(frozen=True)
class Quantity:
    kind: str  # number | money | percent | measure | duration | count | ratio | article | date
    canonical: str
    value: Optional[Decimal]
    unit: str
    raw: str
    start: int
    end: int
    valid: bool = True
    note: str = ""

    def token(self) -> str:
        return f"q:{self.kind}:{self.canonical}"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["value"] = None if self.value is None else decimal_to_str(self.value)
        return payload


# (surface, kind, unit). Single-letter ASCII units (g, m, l, t) are left out
# on purpose: they collide with identifiers and English text.
_UNIT_TABLE: Tuple[Tuple[str, str, str], ...] = (
    ("パーセント", "percent", "%"), ("%", "percent", "%"),
    ("キログラム", "measure", "kg"), ("ミリグラム", "measure", "mg"), ("グラム", "measure", "g"),
    ("kg", "measure", "kg"), ("mg", "measure", "mg"),
    ("キロメートル", "measure", "km"), ("センチメートル", "measure", "cm"),
    ("ミリメートル", "measure", "mm"), ("メートル", "measure", "m"),
    ("km", "measure", "km"), ("cm", "measure", "cm"), ("mm", "measure", "mm"),
    ("ミリリットル", "measure", "ml"), ("リットル", "measure", "l"), ("ml", "measure", "ml"),
    ("トン", "measure", "t"),
    ("時間", "duration", "hour"), ("分間", "duration", "minute"), ("秒間", "duration", "second"),
    ("日間", "duration", "day"), ("週間", "duration", "week"),
    ("か月", "duration", "month"), ("ヶ月", "duration", "month"), ("カ月", "duration", "month"),
    ("ケ月", "duration", "month"), ("箇月", "duration", "month"), ("年間", "duration", "year"),
    ("分", "duration", "minute"), ("秒", "duration", "second"),
    ("円", "money", "JPY"), ("倍", "ratio", "times"),
    ("箇所", "count", "箇所"), ("か所", "count", "箇所"), ("カ所", "count", "箇所"),
    ("件", "count", "件"), ("人", "count", "人"), ("名", "count", "名"), ("回", "count", "回"),
    ("個", "count", "個"), ("枚", "count", "枚"), ("台", "count", "台"), ("本", "count", "本"),
    ("冊", "count", "冊"), ("社", "count", "社"), ("歳", "count", "歳"), ("才", "count", "歳"),
    ("部", "count", "部"), ("点", "count", "点"),
)
_UNIT_BY_SURFACE = {surface: (kind, unit) for surface, kind, unit in _UNIT_TABLE}


def _unit_pattern(surface: str) -> str:
    escaped = re.escape(surface)
    if surface.isascii() and surface.isalpha():
        return escaped + r"(?![A-Za-z])"
    if surface == "分":
        return escaped + r"(?!の)"  # 3分の1 is a fraction, not minutes
    return escaped


UNIT_ALTERNATION = "|".join(
    _unit_pattern(surface) for surface in sorted(_UNIT_BY_SURFACE, key=len, reverse=True)
)

# Words that begin with a numeral but are not quantities.
AMBIGUOUS_KANJI_WORDS = ("十分", "一時", "一部", "一方", "一面", "一本", "一番", "一応", "一緒", "一層", "一旦")

_ARABIC = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_ARABIC_FORM = rf"{_ARABIC}(?:[千百十万億兆](?:{_ARABIC})?)*"
_KANJI_FORM = rf"[{KANJI_NUMERAL_CLASS}]+"
_QUANTITY_RE = re.compile(
    rf"(?P<sign>[△▲−]|マイナス|(?<![\w.])-)?(?<![\d,.])"
    rf"(?P<num>{_ARABIC_FORM}|{_KANJI_FORM})(?P<unit>{UNIT_ALTERNATION})?"
)
_YEN_PREFIX_RE = re.compile(rf"[¥\\]\s*(?P<num>{_ARABIC_FORM})")
_ARTICLE_RE = re.compile(rf"第\s*(?P<num>\d+|[{KANJI_NUMERAL_CLASS}]+)\s*(?P<unit>条|項|号|章|節|款|編)")
# ASCII-only classes: Python's \w also matches Japanese, which would swallow
# the rest of a sentence (A1の手続きで3件) into the identifier.
_CODE_RE = re.compile(
    r"[A-Za-z]+[-_]?\d[A-Za-z0-9_-]*"  # ZZ-10, A100
    r"|\d+[A-Za-z]+\d[A-Za-z0-9_-]*"  # 3B2
    r"|\d+(?:-\d+){2,}"  # 03-1234-5678
    r"|\d{3}-\d{4}(?!\d)"  # 100-0001
)
_RANGE_SEPARATORS = ("〜", "~", "-", "から")
_KANJI_CHAR_RE = re.compile(r"[一-龥々〆〤]")
_NUMERIC_CONTEXT_SUFFIX_RE = re.compile(rf"(?:{UNIT_ALTERNATION}|[年月日時])")


def _kanji_allowed(text: str, start: int, end: int) -> bool:
    """A kanji numeral counts only outside compounds and common words."""
    if any(text.startswith(word, start) for word in AMBIGUOUS_KANJI_WORDS):
        return False
    before = text[:start]
    if before.endswith("第") or before.endswith(ja_dates.ERA_NAMES):
        return True
    return not (before and _KANJI_CHAR_RE.fullmatch(before[-1]))


def _canonical(kind: str, value: Decimal, unit: str) -> str:
    number = decimal_to_str(value)
    if kind == "number":
        return number
    return f"{number}{unit}"


def kanji_numeral_spans(text: str) -> List[Tuple[int, int, str]]:
    """Kanji numerals in a numeric context as (start, end, arabic string).

    Used by text_normalize to rewrite 第三条 -> 第3条 and 三件 -> 3件 while
    leaving 一般, 統一 and 十分 untouched. Input must be NFKC-normalized.
    """
    spans: List[Tuple[int, int, str]] = []
    for match in re.finditer(_KANJI_FORM, text):
        start, end = match.span()
        after_dai = text[:start].endswith("第")
        if not (after_dai or _NUMERIC_CONTEXT_SUFFIX_RE.match(text, end)):
            continue
        if not _kanji_allowed(text, start, end):
            continue
        value = parse_japanese_number(match.group(0))
        if value is None:
            continue
        spans.append((start, end, decimal_to_str(value)))
    return spans


def _overlaps(start: int, end: int, taken: List[Tuple[int, int]]) -> bool:
    return any(not (end <= s or start >= e) for s, e in taken)


def extract_quantities(text: str) -> List[Quantity]:
    norm = unicodedata.normalize("NFKC", text or "")
    found: List[Quantity] = []
    taken: List[Tuple[int, int]] = []
    nums: Dict[int, str] = {}  # start -> numeral part, for range handling

    for parsed in ja_dates.find_dates(norm):
        found.append(
            Quantity("date", parsed.iso, None, "", parsed.raw, parsed.start, parsed.end, parsed.valid, parsed.reason)
        )
        taken.append((parsed.start, parsed.end))
    for match in _CODE_RE.finditer(norm):
        if not _overlaps(match.start(), match.end(), taken):
            taken.append(match.span())
    for match in _ARTICLE_RE.finditer(norm):
        value = parse_japanese_number(match.group("num"))
        if value is None or _overlaps(match.start(), match.end(), taken):
            continue
        unit = match.group("unit")
        found.append(Quantity("article", f"{decimal_to_str(value)}{unit}", value, unit, match.group(0), *match.span()))
        taken.append(match.span())
    for match in _YEN_PREFIX_RE.finditer(norm):
        value = parse_japanese_number(match.group("num"))
        if value is None or _overlaps(match.start(), match.end(), taken):
            continue
        found.append(Quantity("money", _canonical("money", value, "JPY"), value, "JPY", match.group(0), *match.span()))
        taken.append(match.span())

    for match in _QUANTITY_RE.finditer(norm):
        start, end = match.span()
        num_start = match.start("num")
        if _overlaps(start, end, taken):
            continue
        num, unit_surface = match.group("num"), match.group("unit")
        is_kanji = not num[0].isdigit()
        if is_kanji and (not unit_surface or not _kanji_allowed(norm, num_start, match.end("num"))):
            continue
        if not unit_surface and end < len(norm) and norm[end].isascii() and norm[end].isalpha():
            continue  # 100A is an identifier, not a number
        value = parse_japanese_number(num)
        if value is None:
            continue
        if match.group("sign"):
            value = -value
        kind, unit = _UNIT_BY_SURFACE.get(unit_surface or "", ("number", ""))
        found.append(Quantity(kind, _canonical(kind, value, unit), value, unit, match.group(0), start, end))
        nums[start] = num
        taken.append((start, end))

    found.sort(key=lambda q: q.start)
    return _link_ranges(norm, found, nums)


def _link_ranges(norm: str, found: List[Quantity], nums: Dict[int, str]) -> List[Quantity]:
    out = list(found)
    for i in range(len(out) - 1):
        low, high = out[i], out[i + 1]
        if low.value is None or high.value is None or norm[low.end : high.start] not in _RANGE_SEPARATORS:
            continue
        if low.unit == "" and high.unit != "":
            value = low.value
            low_num, high_num = nums.get(low.start, ""), nums.get(high.start, "")
            if high_num and high_num[-1] in LARGE_UNITS and not any(c in LARGE_UNITS for c in low_num):
                value = value * LARGE_UNITS[high_num[-1]]
            low = replace(low, kind=high.kind, unit=high.unit, value=value, canonical=_canonical(high.kind, value, high.unit))
        out[i] = replace(low, note="range_low")
        out[i + 1] = replace(high, note="range_high")
    return out


def quantity_tokens(text: str) -> List[str]:
    """Index/query tokens so 1万5千円 and 15,000円 match: q:money:15000JPY."""
    return [q.token() for q in extract_quantities(text) if q.valid]
