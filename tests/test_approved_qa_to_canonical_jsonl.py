from __future__ import annotations

import json

from eval.approved_qa_runner import run_approved_qa_eval
from scripts.approved_qa_to_canonical_jsonl import (
    approved_qa_record_to_canonical_row,
    convert_approved_qa_to_canonical,
)


def _approved_record(**overrides):
    record = {
        "qa_id": "qa_one",
        "question": "パスワード再設定の方法は？",
        "normalized_question": "パスワード再設定の方法は?",
        "approved_answer": "管理画面からパスワード再設定を行ってください。",
        "approved_citations": [
            {
                "source_doc": "sample.pdf",
                "source_pages": [1],
                "chunk_id": "tourism_q001",
                "title": "FAQ",
            }
        ],
        "tags": ["qanda_table", "candidate"],
        "language": "ja",
        "tenant_id": "default",
        "doc_version": "v1",
        "status": "approved",
        "question_item": "仕様書 4.内容",
        "source_question_no": 1,
    }
    record.update(overrides)
    return record


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_one_approved_record_produces_one_canonical_row(tmp_path):
    src = tmp_path / "approved.jsonl"
    out = tmp_path / "canonical.jsonl"
    src.write_text(json.dumps(_approved_record(), ensure_ascii=False) + "\n", encoding="utf-8")

    summary = convert_approved_qa_to_canonical(input_path=src, output_path=out)
    rows = _read_jsonl(out)

    assert summary["written_records"] == 1
    assert rows[0]["id"] == "approved_qa_pair:qa_one"
    assert rows[0]["doc_type"] == "approved_qa_pair"
    assert rows[0]["chunk_type"] == "qa_pair"
    assert rows[0]["qa_id"] == "qa_one"


def test_q_and_a_are_both_present_in_searchable_text():
    row = approved_qa_record_to_canonical_row(_approved_record())

    assert "Q: パスワード再設定の方法は？" in row["text"]
    assert "A: 管理画面からパスワード再設定を行ってください。" in row["text"]
    assert row["searchable_text"] == row["text"]
    assert row["display_text"] == row["text"]
    assert row["question_text"] == "パスワード再設定の方法は？"
    assert row["answer_text"] == "管理画面からパスワード再設定を行ってください。"


def test_source_and_future_debug_metadata_are_preserved():
    row = approved_qa_record_to_canonical_row(_approved_record(reviewed_by="rai", reviewed_at="now"))

    assert row["source_doc"] == "sample.pdf"
    assert row["source_pages"] == [1]
    assert row["source_citation_chunk_id"] == "tourism_q001"
    assert row["title"] == "FAQ"
    assert row["section_path"] == ["FAQ", "仕様書 4.内容"]
    assert row["tenant_id"] == "default"
    assert row["doc_version"] == "v1"
    assert row["normalized_question"] == "パスワード再設定の方法は?"
    assert row["approved_answer"] == "管理画面からパスワード再設定を行ってください。"
    assert row["reviewed_by"] == "rai"
    assert row["reviewed_at"] == "now"


def test_output_lines_are_valid_json_and_draft_is_skipped(tmp_path):
    src = tmp_path / "approved.jsonl"
    out = tmp_path / "canonical.jsonl"
    records = [
        _approved_record(qa_id="qa_one", question="質問1?", normalized_question="質問1?"),
        _approved_record(qa_id="qa_draft", question="質問2?", normalized_question="質問2?", status="draft"),
    ]
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")

    summary = convert_approved_qa_to_canonical(input_path=src, output_path=out)
    rows = _read_jsonl(out)

    assert summary["input_records"] == 2
    assert summary["written_records"] == 1
    assert summary["skipped_records"] == 1
    assert rows[0]["qa_id"] == "qa_one"


def test_existing_exact_match_eval_still_uses_original_approved_qa_file(tmp_path):
    src = tmp_path / "approved.jsonl"
    out = tmp_path / "eval.json"
    src.write_text(json.dumps(_approved_record(), ensure_ascii=False) + "\n", encoding="utf-8")

    payload = run_approved_qa_eval(src, out)

    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["pass_rate"] == 1.0
