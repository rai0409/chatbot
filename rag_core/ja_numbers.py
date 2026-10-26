"""Exact parsing of Japanese number expressions into Decimal.

Handles arabic digits (thousands separators, decimals), kanji numerals both
positional (二〇二四) and unit-based (二千二十四), daiji (壱弐参拾), and mixed
forms with 万/億/兆 (1万5000, 1万5千, 3.5億). Malformed unit order (十百,
万億) returns None instead of a guessed value.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

KANJI_DIGITS = {
    "〇": 0, "零": 0, "一": 1, "壱": 1, "二": 2, "弐": 2, "三": 3, "参": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
SMALL_UNITS = {"十": 10, "拾": 10, "百": 100, "千": 1000}
LARGE_UNITS = {"万": 10**4, "億": 10**8, "兆": 10**12}
# Safe inside a regex character class (no metacharacters).
KANJI_NUMERAL_CLASS = "".join(KANJI_DIGITS) + "".join(SMALL_UNITS) + "".join(LARGE_UNITS)

_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_TOKEN_RE = re.compile(rf"\d+(?:\.\d+)?|[{KANJI_NUMERAL_CLASS}]")


def decimal_to_str(value: Decimal) -> str:
    """Plain notation without exponent or trailing zeros: 1E+4 -> 10000, 2.50 -> 2.5."""
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def parse_japanese_number(text: str) -> Optional[Decimal]:
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = _THOUSANDS_RE.sub("", s)
    if not s:
        return None
    tokens = _TOKEN_RE.findall(s)
    if "".join(tokens) != s:
        return None

    total = Decimal(0)
    section = Decimal(0)  # value accumulated below the current large unit
    current: Optional[Decimal] = None  # digits waiting for a unit
    current_kind: Optional[str] = None  # "arabic" or "kanji"
    last_small: Optional[int] = None
    last_large: Optional[int] = None

    try:
        for tok in tokens:
            if tok[0].isdigit():
                if current is not None:
                    return None
                current = Decimal(tok)
                current_kind = "arabic"
            elif tok in KANJI_DIGITS:
                if current_kind == "arabic":
                    return None
                current = (current or Decimal(0)) * 10 + KANJI_DIGITS[tok]
                current_kind = "kanji"
            elif tok in SMALL_UNITS:
                unit = SMALL_UNITS[tok]
                if last_small is not None and unit >= last_small:
                    return None
                section += (current if current is not None else Decimal(1)) * unit
                current, current_kind, last_small = None, None, unit
            else:
                unit = LARGE_UNITS[tok]
                if last_large is not None and unit >= last_large:
                    return None
                value = section + (current if current is not None else Decimal(0))
                if value == 0 and current is None:
                    value = Decimal(1)  # bare 万 means 1万
                total += value * unit
                section, current, current_kind, last_small = Decimal(0), None, None, None
                last_large = unit
    except InvalidOperation:
        return None
    return total + section + (current if current is not None else Decimal(0))


def parse_japanese_int(text: str) -> Optional[int]:
    """Integer form for years, months, days and article numbers; "元" means 1."""
    if (text or "").strip() == "元":
        return 1
    value = parse_japanese_number(text)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)
