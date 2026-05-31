from __future__ import annotations

import json

from rag_core.approved_qa import validate_approved_qa_records
from scripts.canonical_jsonl_to_approved_qa_candidates import (
    convert_canonical_jsonl,
    parse_source_pages,
)


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_explicit_qa_extraction_defaults_to_draft(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out = tmp_path / "candidates.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "chunk-1",
                "source_doc": "faq.pdf",
                "source_pages": [1],
                "title": "FAQ",
                "display_text": "Q: パスワード再設定の方法は？ A: 管理画面からパスワード再設定を行ってください。",
                "language": "ja",
                "tenant_id": "default",
                "doc_version": "v1",
            }
        ],
    )

    summary = convert_canonical_jsonl(input_path=src, output_path=out)
    records = _read_jsonl(out)

    assert summary["written_count"] == 1
    assert records[0]["status"] == "draft"
    assert records[0]["question"] == "パスワード再設定の方法は？"
    assert records[0]["normalized_question"] == "パスワード再設定の方法は?"
    assert records[0]["approved_answer"] == "管理画面からパスワード再設定を行ってください。"
    assert records[0]["approved_citations"][0]["chunk_id"] == "chunk-1"
    assert records[0]["tags"] == ["candidate", "faq"]


def test_japanese_qa_marker_extraction(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out = tmp_path / "candidates.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "chunk-2",
                "source_doc": "faq.pdf",
                "source_pages": "2",
                "display_text": "質問: 企業IDとは？ 回答: 企業IDは利用者登録時に発行される番号です。",
            }
        ],
    )

    convert_canonical_jsonl(input_path=src, output_path=out)
    record = _read_jsonl(out)[0]

    assert record["question"] == "企業IDとは？"
    assert record["approved_answer"] == "企業IDは利用者登録時に発行される番号です。"
    assert record["approved_citations"][0]["source_pages"] == [2]


def test_section_path_question_extraction_and_duplicate_question_removal(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out = tmp_path / "candidates.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "chunk-3",
                "source_doc": "faq.pdf",
                "source_pages": "[3]",
                "section_path": ["FAQ", "企業IDとは？"],
                "searchable_text": "企業IDとは？ 企業IDは利用者登録時に発行される番号です。",
            }
        ],
    )

    convert_canonical_jsonl(input_path=src, output_path=out)
    record = _read_jsonl(out)[0]

    assert record["question"] == "企業IDとは？"
    assert record["approved_answer"] == "企業IDは利用者登録時に発行される番号です。"
    assert record["tags"] == ["candidate", "section_path"]


def test_source_pages_parsing_variants():
    assert parse_source_pages([1, "2"]) == [1, 2]
    assert parse_source_pages(3) == [3]
    assert parse_source_pages("4,5") == [4, 5]
    assert parse_source_pages("[6,7]") == [6, 7]
    assert parse_source_pages("") == []


def test_duplicate_handling_prefers_explicit_qa_over_section_path(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out = tmp_path / "candidates.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "section",
                "source_doc": "faq.pdf",
                "source_pages": [1],
                "section_path": ["FAQ", "利用者登録とは？"],
                "display_text": "利用者登録とは？ 利用者登録の説明です。",
                "chunk_role": "parent",
            },
            {
                "id": "explicit",
                "source_doc": "faq.pdf",
                "source_pages": [2],
                "section_path": ["FAQ", "利用者登録とは？"],
                "display_text": "Q: 利用者登録とは？ A: 明示的な回答を採用します。",
                "chunk_role": "child",
            },
        ],
    )

    summary = convert_canonical_jsonl(input_path=src, output_path=out)
    records = _read_jsonl(out)

    assert summary["duplicate_count"] >= 1
    assert len(records) == 1
    assert records[0]["approved_answer"] == "明示的な回答を採用します。"
    assert records[0]["tags"] == ["candidate", "faq"]
    assert records[0]["approved_citations"][0]["chunk_id"] == "explicit"


def test_stable_qa_id(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out1 = tmp_path / "one.jsonl"
    out2 = tmp_path / "two.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "stable",
                "source_doc": "faq.pdf",
                "source_pages": [4],
                "display_text": "Q: PR2とは？ A: PR2に関する固定の回答です。",
            }
        ],
    )

    convert_canonical_jsonl(input_path=src, output_path=out1)
    convert_canonical_jsonl(input_path=src, output_path=out2)

    assert _read_jsonl(out1)[0]["qa_id"] == _read_jsonl(out2)[0]["qa_id"]


def test_generated_records_validate_with_approved_qa_validator(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out = tmp_path / "candidates.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "valid",
                "source_doc": "faq.pdf",
                "source_pages": [5],
                "display_text": "Q: ログインできない場合は？ A: 管理者に連絡してログイン状態を確認してください。",
            }
        ],
    )

    convert_canonical_jsonl(input_path=src, output_path=out)
    records = _read_jsonl(out)

    assert validate_approved_qa_records(records) == []


def test_procedure_candidates_are_default_off(tmp_path):
    src = tmp_path / "canonical.jsonl"
    out = tmp_path / "candidates.jsonl"
    _write_jsonl(
        src,
        [
            {
                "id": "procedure",
                "source_doc": "manual.pdf",
                "source_pages": [6],
                "section_path": ["操作", "利用者登録"],
                "display_text": "利用者登録画面で必要事項を入力し、登録ボタンを押してください。",
            }
        ],
    )

    summary = convert_canonical_jsonl(input_path=src, output_path=out)
    assert summary["written_count"] == 0

    out_with_procedure = tmp_path / "procedure_candidates.jsonl"
    convert_canonical_jsonl(
        input_path=src,
        output_path=out_with_procedure,
        include_procedure_candidates=True,
    )
    record = _read_jsonl(out_with_procedure)[0]
    assert record["question"] == "利用者登録の手順を教えてください"
    assert record["tags"] == ["candidate", "procedure"]
