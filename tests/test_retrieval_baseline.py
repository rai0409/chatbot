from __future__ import annotations

import inspect
import json
import subprocess
import sys

import pytest

from scripts import generate_retrieval_baseline
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


def test_generator_uses_current_interpreter_and_normalizes_it_for_reports():
    source = inspect.getsource(generate_retrieval_baseline)

    assert '.venv/bin/python' not in source
    assert "sys.executable" in source
    assert generate_retrieval_baseline._display_command_arg(sys.executable) == "<current-python>"
    assert generate_retrieval_baseline._display_command_arg("bash") == "bash"


def test_product_readiness_command_uses_current_interpreter_and_sanitizes_it():
    command = generate_retrieval_baseline._product_readiness_command(sys.executable)

    assert command == [
        "bash",
        "scripts/product_readiness_smoke.sh",
        "--python",
        sys.executable,
    ]
    display = " ".join(generate_retrieval_baseline._display_command_arg(item) for item in command)
    assert display == "bash scripts/product_readiness_smoke.sh --python <current-python>"
    assert sys.executable not in display


def test_embedding_runtime_metadata_contains_identity_not_external_path():
    class Provider:
        name = "local"
        model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

        @staticmethod
        def embedding_asset_metadata():
            return {
                "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
                "runtime_files_sha256": "a" * 64,
                "runtime_file_count": 13,
            }

    metadata = generate_retrieval_baseline._embedding_runtime_metadata(Provider())

    assert metadata == {
        "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        "asset_files_sha256": "a" * 64,
        "asset_file_count": 13,
        "runtime_network_allowed": False,
        "trust_remote_code": False,
    }
    assert "/" not in json.dumps(metadata)


def test_runtime_gate_rejects_missing_external_model_path(monkeypatch):
    class Provider:
        name = "local"
        model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        model_path = None

    monkeypatch.setattr(
        generate_retrieval_baseline.embedding_provider,
        "get_embedding_provider",
        lambda: Provider(),
    )
    monkeypatch.setattr(
        generate_retrieval_baseline.validate_embedding_source_contract,
        "load_contract",
        lambda _path: {"model_id": Provider.model_name},
    )
    monkeypatch.setattr(
        generate_retrieval_baseline.validate_embedding_source_contract,
        "validate_contract",
        lambda _contract: None,
    )

    with pytest.raises(RuntimeError, match="requires an external embedding asset"):
        generate_retrieval_baseline.verify_baseline_runtime()


def test_runtime_gate_failure_occurs_before_any_evaluation_work(monkeypatch, tmp_path):
    monkeypatch.setattr(
        generate_retrieval_baseline,
        "verify_baseline_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("runtime gate failed")),
    )
    monkeypatch.setattr(
        generate_retrieval_baseline.tempfile,
        "mkdtemp",
        lambda **_kwargs: pytest.fail("evaluation work must not start"),
    )

    with pytest.raises(RuntimeError, match="runtime gate failed"):
        generate_retrieval_baseline.generate(tmp_path / "baseline.json")


def test_git_status_short_lines_preserve_porcelain_columns_and_order(monkeypatch):
    stdout = (
        " M first.txt\n"
        "M  second.txt\n"
        "MM third.txt\n"
        "?? fourth.txt\n"
        " M path with spaces.txt\n"
        "?? untracked file.txt\n"
    )
    monkeypatch.setattr(
        generate_retrieval_baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""),
    )

    assert generate_retrieval_baseline._git_status_short_lines() == [
        " M first.txt",
        "M  second.txt",
        "MM third.txt",
        "?? fourth.txt",
        " M path with spaces.txt",
        "?? untracked file.txt",
    ]


def test_git_status_short_lines_empty_output_is_clean(monkeypatch):
    monkeypatch.setattr(
        generate_retrieval_baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )

    assert generate_retrieval_baseline._git_status_short_lines() == []


def test_dirty_working_tree_state_uses_status_lines_without_normalizing_them():
    status_lines = [" M first.txt", "M  second.txt", "MM third.txt", "?? untracked file.txt"]

    assert generate_retrieval_baseline._dirty_working_tree_state(status_lines) == {
        "dirty_working_tree": True,
        "dirty_paths": status_lines,
    }


def test_dirty_working_tree_state_is_clean_when_status_has_no_lines():
    assert generate_retrieval_baseline._dirty_working_tree_state([]) == {
        "dirty_working_tree": False,
        "dirty_paths": [],
    }


def test_generic_git_helper_keeps_single_value_behavior(monkeypatch):
    monkeypatch.setattr(
        generate_retrieval_baseline.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=" main\n", stderr=""),
    )

    assert generate_retrieval_baseline._git("branch", "--show-current") == "main"
    assert generate_retrieval_baseline.SCHEMA_VERSION == "current_retrieval_baseline.v2"
