"""Japanese date expressions (wareki and western) to ISO 8601 strings.

Input must already be NFKC-normalized; spans refer to that text. Dates are
validated against the calendar and against era boundaries, and an invalid
date is still returned with valid=False so callers can report it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from rag_core.ja_numbers import KANJI_NUMERAL_CLASS, parse_japanese_int


@dataclass(frozen=True)
class Era:
    name: str
    abbr: str
    start: date
    end: Optional[date]


ERAS = (
    Era("令和", "R", date(2019, 5, 1), None),
    Era("平成", "H", date(1989, 1, 8), date(2019, 4, 30)),
    Era("昭和", "S", date(1926, 12, 25), date(1989, 1, 7)),
    Era("大正", "T", date(1912, 7, 30), date(1926, 12, 24)),
    # 明治 dates before 1873 used the lunisolar calendar; only the end is checked.
    Era("明治", "M", date(1868, 1, 1), date(1912, 7, 29)),
)
_ERA_BY_NAME = {era.name: era for era in ERAS}
_ERA_BY_ABBR = {era.abbr: era for era in ERAS}
ERA_NAMES = tuple(_ERA_BY_NAME)


@dataclass(frozen=True)
class ParsedDate:
    year: int
    month: Optional[int]
    day: Optional[int]
    start: int
    end: int
    raw: str
    valid: bool
    reason: str = ""

    @property
    def iso(self) -> str:
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


_NUM = rf"(?:\d{{1,4}}|[{KANJI_NUMERAL_CLASS}]+)"
_ERA_RE = re.compile(
    rf"(?P<era>令和|平成|昭和|大正|明治)\s*(?P<y>元|{_NUM})\s*年"
    rf"(?:\s*(?P<m>{_NUM})\s*月(?:\s*(?P<d>{_NUM})\s*日)?)?"
)
_ABBR_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<era>[RHSTM])\.?\s*(?P<y>\d{1,2})[./](?P<m>\d{1,2})[./](?P<d>\d{1,2})(?![\d./])"
)
_WESTERN_KANJI_RE = re.compile(
    rf"(?<!\d)(?P<y>\d{{4}})\s*年(?:\s*(?P<m>{_NUM})\s*月(?:\s*(?P<d>{_NUM})\s*日)?)?"
)
_WESTERN_SEP_RE = re.compile(
    r"(?<![\d./-])(?P<y>\d{4})(?P<sep>[/.-])(?P<m>\d{1,2})(?P=sep)(?P<d>\d{1,2})(?![\d./-])"
)
_WESTERN_YM_RE = re.compile(r"(?<![\d./-])(?P<y>\d{4})[/-](?P<m>\d{1,2})(?![\d./-])")


def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date.fromordinal(date(year, month + 1, 1).toordinal() - 1)


def _validate(year: int, month: Optional[int], day: Optional[int], era: Optional[Era]) -> Tuple[bool, str]:
    if not 1 <= year <= 9999:
        return False, "year_out_of_range"
    if month is not None and not 1 <= month <= 12:
        return False, "month_out_of_range"
    if day is not None:
        try:
            date(year, month or 1, day)
        except ValueError:
            return False, "invalid_day"
    if era is None:
        return True, ""
    if day is not None:
        lo = hi = date(year, month or 1, day)
    elif month is not None:
        lo, hi = date(year, month, 1), _last_day(year, month)
    else:
        lo, hi = date(year, 1, 1), date(year, 12, 31)
    if hi < era.start or (era.end is not None and lo > era.end):
        return False, "outside_era"
    return True, ""


def _int_or_none(text: Optional[str]) -> Optional[int]:
    return None if text is None else parse_japanese_int(text)


def find_dates(text: str) -> List[ParsedDate]:
    found: List[ParsedDate] = []
    taken: List[Tuple[int, int]] = []

    def _free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    def _add(match, era: Optional[Era], year: Optional[int]) -> None:
        month, day = _int_or_none(match.group("m")), _int_or_none(match.group("d"))
        if year is None or (match.group("m") and month is None) or (match.group("d") and day is None):
            return
        if era is not None:
            if year < 1:
                return
            year = era.start.year + year - 1
        valid, reason = _validate(year, month, day, era)
        found.append(ParsedDate(year, month, day, match.start(), match.end(), match.group(0), valid, reason))
        taken.append((match.start(), match.end()))

    for match in _ERA_RE.finditer(text):
        if _free(match.start(), match.end()):
            _add(match, _ERA_BY_NAME[match.group("era")], parse_japanese_int(match.group("y")))
    for match in _ABBR_RE.finditer(text):
        if _free(match.start(), match.end()):
            _add(match, _ERA_BY_ABBR[match.group("era")], int(match.group("y")))
    for pattern in (_WESTERN_KANJI_RE, _WESTERN_SEP_RE):
        for match in pattern.finditer(text):
            if _free(match.start(), match.end()):
                _add(match, None, int(match.group("y")))
    for match in _WESTERN_YM_RE.finditer(text):
        if _free(match.start(), match.end()):
            month = int(match.group("m"))
            valid, reason = _validate(int(match.group("y")), month, None, None)
            found.append(
                ParsedDate(int(match.group("y")), month, None, match.start(), match.end(), match.group(0), valid, reason)
            )
            taken.append((match.start(), match.end()))
    found.sort(key=lambda item: item.start)
    return found
