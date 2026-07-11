from __future__ import annotations

import json

import pytest

import config
from eval.approved_qa_alias_runner import run_alias_eval
from rag_core.approved_qa import (
    ApprovedAnswer,
    ApprovedQAIndex,
    load_approved_qa,
    lookup_approved_answer,
    validate_approved_qa_records,
)
from scripts.approved_qa_review import export_approved, update_record_status


def _record(qa_id="qa-1", question="正規質問？", answer="承認回答", *, tenant="default",
            status="approved", aliases=None):
    record = {
        "qa_id": qa_id,
        "question": question,
        "approved_answer": answer,
        "approved_citations": [{"source_doc": "fictional.pdf", "source_pages": [1], "title": "架空資料"}],
        "tenant_id": tenant,
        "language": "ja",
        "status": status,
    }
    if aliases is not None:
        record["approved_aliases"] = aliases
    return record


def _write(path, records):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")


def test_aliasless_record_is_backward_compatible(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write(path, [_record(aliases=None)])
    index = load_approved_qa(path)
    answer = lookup_approved_answer(index, "正規質問?")
    assert answer.qa_id == "qa-1"
    assert answer.approved_aliases == ()
    assert answer.match_type == "canonical"


def test_formal_alias_loads_and_matches_exact_answer_and_citations(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write(path, [_record(aliases=["明示承認された別表現？"])])
    answer = lookup_approved_answer(load_approved_qa(path), " 明示承認された別表現? ")
    assert answer.qa_id == "qa-1"
    assert answer.approved_answer == "承認回答"
    assert answer.approved_citations[0].source_doc == "fictional.pdf"
    assert answer.match_type == "alias"
    assert answer.matched_alias == "明示承認された別表現？"


def test_canonical_question_has_lookup_priority_even_if_index_is_malformed():
    answer = ApprovedAnswer("canonical", "正規質問？", "正規質問?", "A", (), "default", "ja")
    other = ApprovedAnswer("alias-owner", "別質問？", "別質問?", "B", (), "default", "ja",
                           approved_aliases=("正規質問？",))
    index = ApprovedQAIndex(records=(answer, other), by_tenant_question={("default", "正規質問?"): answer},
                            by_tenant_alias={("default", "正規質問?"): (other, "正規質問？")})
    assert lookup_approved_answer(index, "正規質問？").qa_id == "canonical"


@pytest.mark.parametrize(
    "aliases,needle",
    [
        ("not-a-list", "must be a list"),
        ([1], "must be a string"),
        ([""], "must not be empty"),
        (["正規質問?"], "duplicates the canonical question"),
        (["重複？", " 重複? "], "duplicate approved_aliases"),
        (["bad\x00alias"], "control character"),
        (["x" * 501], "exceeds 500"),
        ([f"alias-{i}" for i in range(21)], "exceeds maximum count"),
    ],
)
def test_alias_field_validation(aliases, needle):
    errors = validate_approved_qa_records([_record(aliases=aliases)])
    assert any(needle in error for error in errors)


def test_alias_canonical_and_alias_alias_collisions_are_errors():
    canonical_collision = [
        _record("one", "質問一？", "A", aliases=["質問二？"]),
        _record("two", "質問二？", "B"),
    ]
    alias_collision = [
        _record("one", "質問一？", "A", aliases=["共通？"]),
        _record("two", "質問二？", "B", aliases=[" 共通? "]),
    ]
    assert any("conflicts with canonical question" in error for error in validate_approved_qa_records(canonical_collision))
    assert any("approved alias conflicts" in error for error in validate_approved_qa_records(alias_collision))


def test_same_alias_is_allowed_across_tenants_and_lookup_is_isolated(tmp_path):
    path = tmp_path / "approved.jsonl"
    _write(path, [
        _record("default", "既定質問？", "default answer", aliases=["共通alias"]),
        _record("tenant-b", "B質問？", "tenant b answer", tenant="tenant-b", aliases=["共通alias"]),
    ])
    default = lookup_approved_answer(load_approved_qa(path, tenant_id="default"), "共通alias")
    tenant_b = lookup_approved_answer(load_approved_qa(path, tenant_id="tenant-b"), "共通alias", tenant_id="tenant-b")
    assert default.qa_id == "default" and default.approved_answer == "default answer"
    assert tenant_b.qa_id == "tenant-b" and tenant_b.approved_answer == "tenant b answer"
    assert lookup_approved_answer(load_approved_qa(path), "共通alias", tenant_id="tenant-b") is None


def test_draft_and_rejected_candidate_aliases_are_not_runtime_aliases(tmp_path):
    path = tmp_path / "candidates.jsonl"
    records = []
    for status in ("draft", "rejected"):
        row = _record(status, f"{status}質問？", status, status=status, aliases=None)
        row["candidate_metadata"] = {"aliases": [f"{status} alias"]}
        records.append(row)
    _write(path, records)
    index = load_approved_qa(path)
    assert lookup_approved_answer(index, "draft alias") is None
    assert lookup_approved_answer(index, "rejected alias") is None


def test_formal_alias_is_forbidden_on_nonapproved_record():
    errors = validate_approved_qa_records([_record(status="draft", aliases=["alias"]), _record("r", "R?", status="rejected", aliases=["r alias"])])
    assert sum("only allowed on approved" in error for error in errors) == 2


def test_review_requires_explicit_alias_approval_and_export_strips_candidate_metadata():
    candidate = _record(status="draft", aliases=None)
    candidate["candidate_metadata"] = {"aliases": ["確認済み候補alias"]}
    without = update_record_status([candidate], qa_id="qa-1", status="approved", reviewer="operator")
    assert "approved_aliases" not in without[0]

    with_alias = update_record_status([candidate], qa_id="qa-1", status="approved", reviewer="operator",
                                      approve_aliases=True)
    assert with_alias[0]["approved_aliases"] == ["確認済み候補alias"]
    exported = export_approved(with_alias)
    assert exported[0]["approved_aliases"] == ["確認済み候補alias"]
    assert "candidate_metadata" not in exported[0]


def test_alias_chat_mode_audit_and_no_llm_or_retrieval(monkeypatch, tmp_path):
    from webapi import main

    path = tmp_path / "approved.jsonl"
    _write(path, [_record(aliases=["承認alias？"])])
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(main, "_approved_qa_index", None)
    monkeypatch.setattr(main, "_approved_qa_index_path", None)
    monkeypatch.setattr(main, "ensure_openai_client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM used")))
    monkeypatch.setattr(main, "answer_query_with_trace", lambda *a, **k: (_ for _ in ()).throw(AssertionError("retrieval used")))
    events = []
    monkeypatch.setattr(main, "append_audit_event", lambda kind, event: events.append((kind, event)))

    response = main.chat(main.ChatRequest(question=" 承認alias? ", trace_id="trace-alias"))
    assert response["answer_mode"] == "approved_alias_match"
    assert response["approved_qa_id"] == "qa-1"
    assert response["answer_text"] == "承認回答"
    assert response["canonical_approved_question"] == "正規質問？"
    assert response["matched_alias"] == "承認alias？"
    assert response["citations"][0]["source_doc"] == "fictional.pdf"
    assert response["retrieved"] == [] and response["retrieval_required"] is False
    assert response["llm_used"] is False
    event = events[0][1]
    assert event["request_id"] == event["trace_id"] == "trace-alias"
    assert event["answer_mode"] == "approved_alias_match"
    assert event["normalized_input_question"] == "承認alias?"
    assert event["matched_alias"] == "承認alias？"
    assert event["canonical_question"] == "正規質問？"
    assert event["retrieval_required"] is False and event["llm_used"] is False


def test_alias_runner_covers_dangerous_differences_and_passes(tmp_path):
    summary = run_alias_eval("eval/cases/approved_qa_alias_fixture.json", tmp_path)
    results = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    dangerous = {row["case_id"]: row for row in results if row["case_type"] == "false_positive"}
    assert summary["status"] == "passed"
    assert summary["alias_qa"]["pass_rate"] == 1.0
    assert summary["false_positive_total"] == 0
    assert summary["tenant_isolation_failures"] == 0
    assert all(dangerous[name]["passed"] for name in ("numeric_difference", "year_difference", "negation_difference"))
