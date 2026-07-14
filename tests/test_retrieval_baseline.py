from __future__ import annotations

import json

from scripts.generate_retrieval_baseline import (
    approved_qa_fingerprint,
    compatibility_fingerprint,
    corpus_fingerprint,
    deterministic_hash,
    sanitize_report,
)


def _approved(status: str = "approved", **overrides):
    record = {
        "qa_id": "qa-1",
        "tenant_id": "default",
        "question": "申請方法は？",
        "approved_answer": "窓口で申請します。",
        "approved_aliases": ["申請はどうする？"],
        "approved_citations": [{"source_doc": "manual.pdf", "source_pages": [1]}],
        "status": status,
        "doc_version": "v1",
    }
    record.update(overrides)
    return record


def test_same_input_produces_same_fingerprint():
    value = {"embedding_model": "model", "corpus": [1, 2, 3]}
    assert deterministic_hash(value) == deterministic_hash(value)


def test_json_key_order_does_not_change_fingerprint():
    assert deterministic_hash({"a": 1, "b": 2}) == deterministic_hash({"b": 2, "a": 1})


def test_search_affecting_corpus_change_changes_fingerprint():
    before = [{"id": "c1", "tenant_id": "default", "doc_id": "d1", "text": "申請手順", "searchable": 1}]
    after = [{"id": "c1", "tenant_id": "default", "doc_id": "d1", "text": "変更手順", "searchable": 1}]
    assert corpus_fingerprint(before) != corpus_fingerprint(after)


def test_approved_answer_change_changes_fingerprint():
    assert approved_qa_fingerprint([_approved()]) != approved_qa_fingerprint(
        [_approved(approved_answer="オンラインで申請します。")]
    )


def test_non_active_qa_changes_do_not_change_fingerprint():
    baseline = approved_qa_fingerprint([_approved()])
    for inactive in (
        _approved("candidate", qa_id="qa-candidate", approved_answer="candidate one"),
        _approved("rejected", qa_id="qa-rejected", approved_answer="rejected one"),
        _approved("approved", qa_id="qa-disabled", approved_answer="disabled one", disabled=True),
    ):
        changed = dict(inactive, approved_answer=str(inactive["approved_answer"]) + " changed")
        assert approved_qa_fingerprint([_approved(), inactive]) == baseline
        assert approved_qa_fingerprint([_approved(), changed]) == baseline


def test_secret_value_is_not_serialized_in_report():
    secret = "unit-test-super-secret"
    sanitized = sanitize_report(
        {
            "runtime": {"model": "safe", "api_key": secret},
            "tokenizer_version": "heuristic-v1",
            "warning": f"value={secret}",
        },
        secret_values=[secret],
    )
    serialized = json.dumps(sanitized)
    assert secret not in serialized
    assert "api_key" not in serialized
    assert sanitized["tokenizer_version"] == "heuristic-v1"


def test_commit_sha_change_does_not_change_compatibility_fingerprint():
    fields = {"embedding_provider": "local", "corpus_fingerprint": "abc"}
    first = compatibility_fingerprint(fields)
    report_one = {"commit_sha": "1", "compatibility": fields}
    report_two = {"commit_sha": "2", "compatibility": fields}
    assert report_one["commit_sha"] != report_two["commit_sha"]
    assert first == compatibility_fingerprint(report_two["compatibility"])
