from __future__ import annotations

import json

from rag_core.approved_qa import validate_approved_qa_records
from scripts.qanda_table_pdf_to_approved_qa import (
    QARow,
    Word,
    cleanup_text,
    dedupe_records,
    format_debug_words,
    row_to_record,
    validate_records,
    words_to_rows,
)


def _w(text: str, x0: float, y0: float, x1: float | None = None, y1: float | None = None, page: int = 1) -> Word:
    return Word(x0=x0, y0=y0, x1=x1 or x0 + 20, y1=y1 or y0 + 10, text=text, page=page)


def test_japanese_text_cleanup_preserves_identifiers():
    assert cleanup_text(" パスワード  再設定 の 方法 は？ ") == "パスワード再設定の方法は?"
    assert cleanup_text("PR2 と PR20 の違い") == "PR2 と PR20 の違い"
    assert cleanup_text("「 佐渡市 」 とは") == "「佐渡市」とは"


def test_row_grouping_by_no_and_column_split():
    words = [
        _w("№", 56, 60),
        _w("質問項目", 93, 60),
        _w("質問内容", 293, 60),
        _w("回答", 617, 60),
        _w("1", 62, 100),
        _w("仕様書", 72, 88),
        _w("4.内容", 103, 88),
        _w("(2)調査手法", 72, 104, x1=140),
        _w("質問本文ですか。", 157, 88, x1=300),
        _w("回答本文です。", 471, 88, x1=620),
        _w("2", 62, 155),
        _w("仕様書", 72, 145),
        _w("次の質問ですか。", 157, 145, x1=300),
        _w("次の回答です。", 471, 145, x1=620),
    ]

    rows = words_to_rows(words)

    assert len(rows) == 2
    assert rows[0].question_no == 1
    assert rows[0].question_item == "仕様書 4.内容 (2)調査手法"
    assert rows[0].question == "質問本文ですか。"
    assert rows[0].answer == "回答本文です。"
    assert rows[1].question_no == 2


def test_row_grouping_handles_vertically_centered_no_cell():
    words = [
        _w("№", 56, 60),
        _w("質問項目", 93, 60),
        _w("質問内容", 293, 60),
        _w("回答", 617, 60),
        _w("7", 62, 230),
        _w("仕様書", 72, 225),
        _w("アンケートシステムについて教えてください。", 157, 80, x1=460),
        _w("ご提示の表示はできません。", 471, 230, x1=700),
        _w("8", 62, 420),
        _w("仕様書", 72, 412),
        _w("利用料は含まれますか。", 157, 412, x1=360),
        _w("含みません。", 471, 420, x1=540),
    ]

    rows = words_to_rows(words)

    assert len(rows) == 2
    assert rows[0].question_no == 7
    assert rows[0].question == "アンケートシステムについて教えてください。"
    assert rows[0].answer == "ご提示の表示はできません。"


def test_deterministic_qa_id_and_default_status():
    row = QARow(
        page=1,
        question_no=6,
        question_item="仕様書 4.内容 (2)調査手法",
        question="「佐渡市が指定するアンケートシステム」はどのようなシステムでしょうか。",
        answer="カスタマーリングスのアンケートフォームになります。",
    )

    one = row_to_record(row, source_doc="58887_95105_misc.pdf", tenant_id="default", doc_version="v1")
    two = row_to_record(row, source_doc="58887_95105_misc.pdf", tenant_id="default", doc_version="v1")

    assert one["qa_id"] == two["qa_id"]
    assert one["status"] == "draft"
    assert one["normalized_question"] == "「佐渡市が指定するアンケートシステム」はどのようなシステムでしょうか。"
    assert one["approved_citations"][0]["chunk_id"] == "tourism_q006"
    assert one["source_question_no"] == 6
    assert one["question_item"] == "仕様書 4.内容 (2)調査手法"


def test_output_schema_compatible_with_approved_qa_validator():
    row = QARow(
        page=2,
        question_no=10,
        question_item="仕様書 4.内容",
        question="入力作業は受託者が行う認識でお間違いないでしょうか。",
        answer="入力作業はこちらで実施いたします。",
    )
    draft = row_to_record(row, source_doc="sample.pdf", tenant_id="default", doc_version="v1")
    approved = dict(draft, status="approved")

    assert validate_records([draft]) == []
    assert validate_approved_qa_records([approved]) == []


def test_duplicate_normalized_question_handling_keeps_first():
    row_one = QARow(page=1, question_no=1, question_item="項目", question="企業IDとは？", answer="最初の回答です。")
    row_two = QARow(page=2, question_no=2, question_item="項目", question="企業IDとは?", answer="別の回答です。")
    records = [
        row_to_record(row_one, source_doc="sample.pdf", tenant_id="default", doc_version="v1"),
        row_to_record(row_two, source_doc="sample.pdf", tenant_id="default", doc_version="v1"),
    ]

    deduped, duplicate_count = dedupe_records(records)

    assert duplicate_count == 1
    assert len(deduped) == 1
    assert deduped[0]["approved_answer"] == "最初の回答です。"


def test_debug_word_formatting_is_bounded():
    words = [_w(f"word{i}", 10 + i, 20 + i) for i in range(5)]

    formatted = format_debug_words(words, limit=2)

    assert "word0" in formatted
    assert "word1" in formatted
    assert "word2" not in formatted
    assert "truncated 3 more words" in formatted


def test_record_can_roundtrip_as_jsonl(tmp_path):
    row = QARow(page=1, question_no=3, question_item="項目", question="質問ですか。", answer="回答です。")
    record = row_to_record(row, source_doc="sample.pdf", tenant_id="default", doc_version="v1")
    out = tmp_path / "qa.jsonl"
    out.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    loaded = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    assert loaded[0]["question"] == "質問ですか。"
    assert loaded[0]["approved_answer"] == "回答です。"
