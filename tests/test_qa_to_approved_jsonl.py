from __future__ import annotations

import csv
import json

import pytest

from eval.approved_qa_runner import run_approved_qa_eval
from rag_core.approved_qa import load_approved_qa
from scripts.qa_to_approved_jsonl import convert_file, parse_source_pages


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_csv_conversion_defaults_to_draft(tmp_path):
    src = tmp_path / "input.csv"
    out = tmp_path / "approved.jsonl"
    _write_csv(
        src,
        [
            {
                "question": "パスワード再設定の方法は？",
                "approved_answer": "回答です。",
                "source_doc": "faq.pdf",
                "source_pages": "1,2",
                "status": "",
            }
        ],
    )

    summary = convert_file(input_path=src, output_path=out, fmt="csv")
    records = _read_jsonl(out)

    assert summary["written_count"] == 1
    assert records[0]["status"] == "draft"
    assert records[0]["normalized_question"] == "パスワード再設定の方法は?"
    assert records[0]["approved_citations"][0]["source_pages"] == [1, 2]


def test_jsonl_conversion_with_approved_status_can_load(tmp_path):
    src = tmp_path / "input.jsonl"
    out = tmp_path / "approved.jsonl"
    src.write_text(
        json.dumps(
            {
                "question": "企業IDとは？",
                "approved_answer": "企業IDの回答です。",
                "approved_citations": [{"source_doc": "faq.pdf", "source_pages": "[1,2]"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    convert_file(input_path=src, output_path=out, fmt="jsonl", status="approved")
    records = _read_jsonl(out)
    index = load_approved_qa(out)

    assert records[0]["status"] == "approved"
    assert records[0]["approved_citations"][0]["source_pages"] == [1, 2]
    assert len(index.records) == 1


def test_source_pages_parsing():
    assert parse_source_pages("1") == [1]
    assert parse_source_pages("1,2") == [1, 2]
    assert parse_source_pages("[1,2]") == [1, 2]
    assert parse_source_pages("") == []


def test_deterministic_qa_id(tmp_path):
    src = tmp_path / "input.csv"
    out1 = tmp_path / "one.jsonl"
    out2 = tmp_path / "two.jsonl"
    rows = [
        {
            "question": "PR2 の仕様",
            "approved_answer": "PR2 answer",
            "source_doc": "codes.pdf",
            "source_pages": "3",
        }
    ]
    _write_csv(src, rows)

    convert_file(input_path=src, output_path=out1, fmt="csv", status="approved")
    convert_file(input_path=src, output_path=out2, fmt="csv", status="approved")

    assert _read_jsonl(out1)[0]["qa_id"] == _read_jsonl(out2)[0]["qa_id"]


def test_duplicate_detection_fails_without_allow_errors(tmp_path):
    src = tmp_path / "input.json"
    out = tmp_path / "approved.jsonl"
    src.write_text(
        json.dumps(
            [
                {
                    "question": "利用者登録とは？",
                    "approved_answer": "A",
                    "source_doc": "faq.pdf",
                },
                {
                    "question": "利用者登録とは?",
                    "approved_answer": "B",
                    "source_doc": "faq.pdf",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        convert_file(input_path=src, output_path=out, fmt="json", status="draft")


def test_invalid_status_failure(tmp_path):
    src = tmp_path / "input.json"
    out = tmp_path / "approved.jsonl"
    src.write_text(
        json.dumps({"question": "Q", "approved_answer": "A", "status": "pending"}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        convert_file(input_path=src, output_path=out, fmt="json")


def test_approved_status_output(tmp_path):
    src = tmp_path / "input.json"
    out = tmp_path / "approved.jsonl"
    src.write_text(
        json.dumps({"question": "Q", "approved_answer": "A", "source_doc": "faq.pdf"}, ensure_ascii=False),
        encoding="utf-8",
    )

    convert_file(input_path=src, output_path=out, fmt="json", status="approved")

    assert _read_jsonl(out)[0]["status"] == "approved"


def test_generated_approved_sample_passes_runner(tmp_path):
    src = tmp_path / "input.csv"
    out = tmp_path / "approved.jsonl"
    result = tmp_path / "result.json"
    _write_csv(
        src,
        [
            {
                "question": "パスワード再設定の方法は？",
                "approved_answer": "承認済み回答サンプルです。",
                "source_doc": "040219e-biscfaq.pdf",
                "source_pages": "3",
            },
            {
                "question": "企業IDとは？",
                "approved_answer": "企業IDに関する承認済み回答サンプルです。",
                "source_doc": "040219e-biscfaq.pdf",
                "source_pages": "1",
            },
        ],
    )

    convert_file(input_path=src, output_path=out, fmt="csv", status="approved")
    payload = run_approved_qa_eval(out, result)

    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["pass_rate"] == 1.0
