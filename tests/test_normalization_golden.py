from __future__ import annotations

import pytest

from rag_core.ja_numbers import decimal_to_str, parse_japanese_int, parse_japanese_number
from rag_core.quantities import kanji_numeral_spans, quantity_tokens
from rag_core.text_normalize import normalize
from scripts import check_normalization

_CASES = check_normalization._read_jsonl(check_normalization.DEFAULT_GOLDEN)


@pytest.mark.parametrize("case", _CASES, ids=[case["id"] for case in _CASES])
def test_golden_case(case):
    ok, actual = check_normalization.check_case(case)

    assert ok, f"{case['id']}: expected {case['expected']!r}, got {actual!r}"


def test_golden_inputs_are_idempotent_in_every_profile():
    assert check_normalization.idempotence_failures([case["input"] for case in _CASES]) == []


def test_trace_lists_only_rules_that_changed_the_text():
    out, changed = normalize("第三条　１，２００円", "index", trace=True)

    assert out == "第3条 1200円"
    assert changed == ["nfkc", "thousands", "kanji_numerals"]


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown normalization profile"):
        normalize("x", "aggressive")


def test_equivalent_amounts_share_quantity_tokens():
    assert quantity_tokens("1万5千円") == quantity_tokens("15,000円") == ["q:money:15000JPY"]


def test_kanji_numeral_spans_skip_compounds_and_common_words():
    assert kanji_numeral_spans("第三条") == [(1, 2, "3")]
    assert kanji_numeral_spans("統一された") == []
    assert kanji_numeral_spans("十分な説明") == []


def test_decimal_formatting_has_no_exponent_or_trailing_zeros():
    assert decimal_to_str(parse_japanese_number("1万")) == "10000"
    assert decimal_to_str(parse_japanese_number("2.50")) == "2.5"


def test_gannen_is_one_and_fractions_are_not_integers():
    assert parse_japanese_int("元") == 1
    assert parse_japanese_int("1.5") is None
