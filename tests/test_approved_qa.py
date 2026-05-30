from __future__ import annotations

import json
from pathlib import Path

import config
from eval.approved_qa_runner import run_approved_qa_eval
from rag_core.approved_qa import (
    load_approved_qa,
    lookup_approved_answer,
    validate_approved_qa_records,
)
from rag_core.question_normalization import normalize_question_for_exact_match


def _record(**overrides):
    base = {
        "qa_id": "qa-1",
        "question": "パスワード再設定の方法は？",
        "approved_answer": "承認済み回答です。",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1], "chunk_id": "c1"}],
        "language": "ja",
        "tenant_id": "default",
        "status": "approved",
    }
    base.update(overrides)
    return base


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_normalize_question_for_exact_match_surface_forms():
    assert normalize_question_for_exact_match(" パスワード再設定の方法は？ ") == "パスワード再設定の方法は?"
    assert normalize_question_for_exact_match("PR2 と PR20 の違い") == "PR2 と PR20 の違い"
    assert normalize_question_for_exact_match("「 利用者登録 」") == "「利用者登録」"


def test_loader_computes_normalized_question_and_lookup_returns_answer(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write_jsonl(path, [_record()])

    index = load_approved_qa(path)
    answer = lookup_approved_answer(index, " パスワード再設定の方法は? ")

    assert answer is not None
    assert answer.qa_id == "qa-1"
    assert answer.normalized_question == "パスワード再設定の方法は?"
    assert answer.approved_answer == "承認済み回答です。"
    assert answer.approved_citations[0].source_doc == "faq.pdf"


def test_non_approved_status_is_ignored(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write_jsonl(path, [_record(status="draft")])

    index = load_approved_qa(path)

    assert lookup_approved_answer(index, "パスワード再設定の方法は？") is None


def test_duplicate_normalized_question_reports_validation_error():
    records = [
        _record(qa_id="qa-1", question="利用者登録とは？"),
        _record(qa_id="qa-2", question="利用者登録とは?"),
    ]

    errors = validate_approved_qa_records(records)

    assert any("duplicate normalized_question" in error for error in errors)


def test_tenant_isolation(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write_jsonl(
        path,
        [
            _record(qa_id="default-qa", tenant_id="default", approved_answer="default answer"),
            _record(qa_id="tenant-qa", tenant_id="tenant-a", approved_answer="tenant answer"),
        ],
    )

    default_index = load_approved_qa(path, tenant_id="default")
    tenant_index = load_approved_qa(path, tenant_id="tenant-a")

    assert lookup_approved_answer(default_index, "パスワード再設定の方法は？").qa_id == "default-qa"
    assert lookup_approved_answer(default_index, "パスワード再設定の方法は？", tenant_id="tenant-a") is None
    assert lookup_approved_answer(tenant_index, "パスワード再設定の方法は？", tenant_id="tenant-a").qa_id == "tenant-qa"


def test_pr2_and_pr20_remain_distinguishable(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write_jsonl(
        path,
        [
            _record(qa_id="pr2", question="PR2 の仕様", approved_answer="PR2 answer"),
            _record(qa_id="pr20", question="PR20 の仕様", approved_answer="PR20 answer"),
        ],
    )

    index = load_approved_qa(path)

    assert lookup_approved_answer(index, "PR2 の仕様").qa_id == "pr2"
    assert lookup_approved_answer(index, "PR20 の仕様").qa_id == "pr20"


def test_approved_chat_lookup_does_not_call_llm(monkeypatch, tmp_path):
    from webapi import main

    path = tmp_path / "approved.jsonl"
    _write_jsonl(path, [_record()])
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(main, "_approved_qa_index", None)
    monkeypatch.setattr(main, "_approved_qa_index_path", None)

    def _fail_client(*args, **kwargs):
        raise AssertionError("LLM client must not be created for approved exact match")

    monkeypatch.setattr(main, "ensure_openai_client", _fail_client)
    response = main.chat(main.ChatRequest(question="パスワード再設定の方法は？"))

    assert response["answer_mode"] == "approved_exact_match"
    assert response["approved_qa_id"] == "qa-1"
    assert response["answer_text"] == "承認済み回答です。"


def test_approved_qa_runner_passes_sample_file(tmp_path):
    output = tmp_path / "approved_results.json"
    payload = run_approved_qa_eval("eval/cases/approved_qa_sample.jsonl", output)

    assert output.exists()
    assert payload["summary"]["total"] > 0
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["pass_rate"] == 1.0
