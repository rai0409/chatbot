"""Line joining and NFKC in the extraction/preprocessing path. Synthetic data only."""
from __future__ import annotations

import pytest

from rag_core import ja_text
from rag_core.ingest_preprocess import PreprocessOptions, preprocess_rows
from scripts import pdf_to_canonical_jsonl as pdfconv
from webapi import ingestion_jobs


def _pre(text, **kw):
    rows = [{"doc_id": "d", "source_doc": "s.pdf", "text": text, "source_pages": [1], "source_type": "pdf"}]
    return preprocess_rows(rows, PreprocessOptions(**kw))[0][0]["text"]


@pytest.mark.parametrize("text", [
    "経費精算規程\n第1条（目的）\nこの規程は、従業員の経費精算に関する手続きを定める。",      # title and article headings
    "対象部署\n営業部\n総務部\n経理部\n開発部",                                                # a list of items
    "担当者\n山田太郎\n佐藤次郎",                                                              # names
    "営業部の目標\n売上を伸ばす\n新規顧客を増やす",                                            # slide lines
    "金額\n12,000円\n30,000円",                                                                  # numbers
    "① 電源を入れる\n② 設定を開く",                                                            # circled steps
])
def test_structural_lines_are_not_glued(text):
    assert _pre(text) == text


def test_a_wrapped_sentence_is_still_joined():
    wrapped = "この規程は、従業員の経費精算に関する手続きを定めることを目的として制\n定したものであり、全従業員に適用する。"
    assert _pre(wrapped) == wrapped.replace("\n", "")


def test_a_long_line_that_ends_a_sentence_is_not_joined_to_the_next():
    text = "この規程は、従業員の経費精算に関する手続きを定めることを目的とする。\nこの規程は、全従業員に適用する。"
    assert _pre(text) == text


def test_office_rows_are_not_joined_at_all(monkeypatch):
    # Line breaks in a slide, a cell or a paragraph are structure, not PDF layout wrapping.
    rows = [{"doc_id": "d", "source_doc": "a.pptx", "text": "営業部の目標について説明する長い一行の文章です\nそして次の段落もまた長い一行の文章です",
             "source_pages": [1], "source_type": "pptx"}]
    out, _ = ingestion_jobs._preprocess_converted(rows)
    assert out[0]["text"] == rows[0]["text"]


def test_join_rule_is_shared_with_the_pdf_converter():
    from rag_core import line_joining

    assert pdfconv._join_separator("この規程は、従業員の経費精算に関する手", "続きを定める。") == line_joining.join_separator(
        "この規程は、従業員の経費精算に関する手", "続きを定める。") == ""


# --- NFKC that keeps circled numbers (PDF path) ------------------------------------------------------------------------------


def test_the_pdf_converter_keeps_circled_numbers_and_still_folds_the_rest():
    text = pdfconv._normalize_text("手順\n① 電源を入れる\n② 設定を開く\nＡＢＣ１２３")
    assert "①" in text and "②" in text and "ABC123" in text


def test_circled_steps_stay_separate_lines_in_the_pdf_converter():
    text = pdfconv._normalize_text("① 電源を入れる操作を行ってから設定の画面を開く\n② 設定を開く")
    assert text.splitlines() == ["① 電源を入れる操作を行ってから設定の画面を開く", "② 設定を開く"]


def test_ja_text_helpers():
    assert ja_text.nfkc_keep_circled("ｶﾞｲﾄﾞ① ﾎﾟｲﾝﾄ") == "ガイド① ポイント"
    assert ja_text.fold_circled("手順① と ⑩") == "手順1 と 10"


def test_a_short_line_ending_in_a_particle_is_unfinished():
    assert _pre("申請の流れを\n説明します。") == "申請の流れを説明します。"
    assert _pre("営業部の目標\n売上を伸ばす") == "営業部の目標\n売上を伸ばす"      # a heading
    assert _pre("以下の場合は、\n申請が必要です。") == "以下の場合は、申請が必要です。"


# --- day counts in deadlines (quantities) ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, canonical", [
    ("10日以内に提出する", "10day"),
    ("30日前までに申請する", "30day"),
    ("申請から14日経過後", "14day"),
    ("５営業日以内に回答する", "5business_day"),
    ("3営業日", "3business_day"),
])
def test_day_counts_in_deadlines_are_durations(text, canonical):
    from rag_core.quantities import extract_quantities

    found = [(q.kind, q.canonical) for q in extract_quantities(text)]
    assert ("duration", canonical) in found


@pytest.mark.parametrize("text", ["4月1日から施行する", "毎月25日とする", "令和6年4月10日以内"])
def test_calendar_days_are_not_durations(text):
    from rag_core.quantities import extract_quantities

    assert not any(q.kind == "duration" and q.unit in {"day", "business_day"} for q in extract_quantities(text))
