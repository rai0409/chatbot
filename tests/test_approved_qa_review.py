from __future__ import annotations

import json

import pytest

from rag_core.approved_qa import load_approved_qa
from scripts.approved_qa_review import (
    export_approved,
    list_records,
    main,
    promote_all_records,
    read_jsonl,
    review_validation_errors,
    update_record_status,
    write_jsonl,
)


def _record(
    qa_id: str,
    question: str,
    *,
    status: str = "draft",
    normalized_question: str | None = None,
    tenant_id: str = "default",
) -> dict:
    record = {
        "qa_id": qa_id,
        "question": question,
        "approved_answer": f"{question} の回答です。",
        "approved_citations": [
            {
                "source_doc": "sample.pdf",
                "source_pages": [1],
                "chunk_id": qa_id + "_chunk",
                "title": "FAQ",
            }
        ],
        "tags": ["candidate"],
        "language": "ja",
        "tenant_id": tenant_id,
        "doc_version": "v1",
        "status": status,
        "notes": "sample",
    }
    if normalized_question is not None:
        record["normalized_question"] = normalized_question
    return record


def _write(path, records):
    write_jsonl(path, records, overwrite=True)


def _read(path):
    return read_jsonl(path)


def test_list_filters_draft_records():
    records = [
        _record("qa_one", "パスワード再設定の方法は？", status="draft"),
        _record("qa_two", "企業IDとは？", status="approved"),
        _record("qa_three", "利用者登録とは？", status="draft", tenant_id="tenant-b"),
    ]

    draft = list_records(records, status="draft", limit=10)
    tenant_b = list_records(records, status="draft", tenant_id="tenant-b", limit=10)
    query = list_records(records, status="all", query="企業ID", limit=10)

    assert [record["qa_id"] for record in draft] == ["qa_one", "qa_three"]
    assert [record["qa_id"] for record in tenant_b] == ["qa_three"]
    assert [record["qa_id"] for record in query] == ["qa_two"]


def test_promote_one_record_adds_review_metadata_and_normalized_question():
    records = [_record("qa_one", "パスワード再設定の方法は？", normalized_question=None)]

    updated = update_record_status(
        records,
        qa_id="qa_one",
        status="approved",
        reviewer="rai",
        notes="reviewed",
        reviewed_at="2026-05-31T00:00:00+00:00",
    )

    assert updated[0]["status"] == "approved"
    assert updated[0]["reviewed_by"] == "rai"
    assert updated[0]["review_notes"] == "reviewed"
    assert updated[0]["reviewed_at"] == "2026-05-31T00:00:00+00:00"
    assert updated[0]["normalized_question"] == "パスワード再設定の方法は?"


def test_reject_one_record_adds_reason():
    records = [_record("qa_two", "企業IDとは？")]

    updated = update_record_status(
        records,
        qa_id="qa_two",
        status="rejected",
        reviewer="rai",
        reason="bad answer",
        reviewed_at="2026-05-31T00:00:00+00:00",
    )

    assert updated[0]["status"] == "rejected"
    assert updated[0]["reviewed_by"] == "rai"
    assert updated[0]["rejection_reason"] == "bad answer"


def test_validate_catches_duplicate_normalized_question():
    records = [
        _record("qa_one", "企業IDとは？"),
        _record("qa_two", "企業IDとは?"),
    ]

    errors = review_validation_errors(records)

    assert any("duplicate normalized_question" in error for error in errors)


def test_export_approved_writes_only_approved_and_loads(tmp_path):
    out = tmp_path / "approved_only.jsonl"
    records = [
        _record("qa_one", "パスワード再設定の方法は？", status="approved"),
        _record("qa_two", "企業IDとは？", status="draft"),
        _record("qa_three", "利用者登録とは？", status="rejected"),
    ]

    approved = export_approved(records)
    write_jsonl(out, approved)
    loaded = load_approved_qa(out)

    assert [record["qa_id"] for record in approved] == ["qa_one"]
    assert len(loaded.records) == 1
    assert loaded.records[0].qa_id == "qa_one"


def test_no_in_place_modification_by_default(tmp_path):
    src = tmp_path / "candidates.jsonl"
    _write(src, [_record("qa_one", "パスワード再設定の方法は？")])
    before = src.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        main(
            [
                "promote",
                "--in",
                str(src),
                "--qa-id",
                "qa_one",
                "--reviewer",
                "rai",
            ]
        )

    assert src.read_text(encoding="utf-8") == before


def test_overwrite_protection(tmp_path):
    src = tmp_path / "candidates.jsonl"
    out = tmp_path / "reviewed.jsonl"
    _write(src, [_record("qa_one", "パスワード再設定の方法は？")])
    out.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        main(
            [
                "promote",
                "--in",
                str(src),
                "--out",
                str(out),
                "--qa-id",
                "qa_one",
                "--reviewer",
                "rai",
            ]
        )

    assert out.read_text(encoding="utf-8") == "existing\n"


def test_cli_promote_reject_and_export_workflow(tmp_path):
    src = tmp_path / "candidates.jsonl"
    reviewed = tmp_path / "reviewed.jsonl"
    reviewed2 = tmp_path / "reviewed2.jsonl"
    approved_only = tmp_path / "approved_only.jsonl"
    _write(
        src,
        [
            _record("qa_one", "パスワード再設定の方法は？"),
            _record("qa_two", "企業IDとは？"),
        ],
    )

    assert (
        main(
            [
                "promote",
                "--in",
                str(src),
                "--out",
                str(reviewed),
                "--qa-id",
                "qa_one",
                "--reviewer",
                "rai",
                "--notes",
                "approved sample",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "reject",
                "--in",
                str(reviewed),
                "--out",
                str(reviewed2),
                "--qa-id",
                "qa_two",
                "--reviewer",
                "rai",
                "--reason",
                "sample rejection",
            ]
        )
        == 0
    )
    assert main(["export-approved", "--in", str(reviewed2), "--out", str(approved_only)]) == 0

    exported = _read(approved_only)
    assert [record["qa_id"] for record in exported] == ["qa_one"]
    assert load_approved_qa(approved_only).records[0].qa_id == "qa_one"


def test_promote_all_requires_yes():
    records = [_record("qa_one", "パスワード再設定の方法は？")]

    with pytest.raises(ValueError):
        promote_all_records(records, reviewer="rai")

    updated = promote_all_records(records, reviewer="rai", yes=True)
    assert updated[0]["status"] == "approved"
