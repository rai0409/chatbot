from __future__ import annotations

import copy
import errno
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_real_vector_quality_baseline as baseline

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/evaluation/real_vector_quality_baseline.contract.json"


def contract(): return baseline.load(CONTRACT_PATH)


def rows():
    output = []
    for mode in baseline.MODES:
        for number in range(25):
            supported = number < 20
            output.append({"case_id": f"case-{number}", "mode": mode, "gold_chunk_ids": ["gold"] if supported else [], "gold_doc_ids": [], "gold_chunk_hit_at_k": supported, "gold_doc_hit_at_k": False, "before_rerank_ids": ["gold"] if mode == "dense_only" else ["bm25"], "query_error": False})
    return output


def summary(collection="isolated", count=27):
    return {"status": "ok", "evaluation_collection": {"collection_name": collection, "inserted_record_count": count, "corpus_fingerprint": "x"}, "by_mode": {mode: {"mean_mrr_at_k": 1.0, "mean_ndcg_at_k": 1.0} for mode in baseline.MODES}}


def test_current_contract_and_report_validate():
    baseline.validate_contract(contract())
    baseline.validate_report(baseline.load(ROOT / "reports/current_real_vector_quality_baseline.json"))


@pytest.mark.parametrize(("path", "value"), [
    (("profile", "name"), "wrong"), (("profile", "real_vector"), False), (("profile", "real_generation"), True),
    (("inputs", "cases_sha256"), "0" * 64), (("inputs", "chunks_sha256"), "0" * 64),
    (("inputs", "modes"), list(reversed(baseline.MODES))), (("inputs", "top_k"), 19), (("inputs", "eval_k"), 4),
    (("inputs", "expected_row_count"), 99), (("embedding", "model"), "wrong"), (("embedding", "revision"), "0" * 40),
    (("embedding", "dimension"), 385), (("embedding", "asset_files_sha256"), "0" * 64),
])
def test_contract_rejects_exact_value_changes(path, value):
    value_contract = contract(); value_contract[path[0]][path[1]] = value
    with pytest.raises(baseline.ContractError): baseline.validate_contract(value_contract)


@pytest.mark.parametrize("bad", [True, None, "20", float("nan")])
def test_contract_rejects_malformed_release_numeric_values(bad):
    value_contract = contract(); value_contract["release_invariants"]["dense_gold_hit_min_at_5"] = bad
    with pytest.raises(baseline.ContractError): baseline.validate_contract(value_contract)


@pytest.mark.parametrize("key", ["model_id", "revision", "embedding_dimension", "runtime_files_sha256"])
def test_asset_metadata_mismatch_fails_before_evaluation(monkeypatch, tmp_path, key):
    metadata = {"model_id": contract()["embedding"]["model"], "revision": contract()["embedding"]["revision"], "embedding_dimension": 384, "runtime_files_sha256": contract()["embedding"]["asset_files_sha256"]}; metadata[key] = "wrong" if key != "embedding_dimension" else 1
    monkeypatch.setattr(baseline.validate_embedding_source_contract, "validate_contract", lambda *_: None)
    monkeypatch.setattr(baseline.validate_embedding_source_contract, "load_contract", lambda *_: {})
    monkeypatch.setattr(baseline.validate_embedding_asset_contract, "validate_contract", lambda *_: metadata)
    monkeypatch.setattr(baseline.subprocess, "run", lambda *a, **k: pytest.fail("evaluation must not start"))
    with pytest.raises(baseline.AssetError): baseline.generate(CONTRACT_PATH, tmp_path / "out.json", tmp_path, tmp_path)


@pytest.mark.parametrize("mutation", ["empty_collection", "wrong_collection", "dense_empty", "dense_error", "dense_miss", "hybrid_regression", "network"])
def test_analyse_quality_violations(mutation):
    data, result = rows(), summary()
    if mutation == "empty_collection": result["evaluation_collection"]["inserted_record_count"] = 0
    if mutation == "wrong_collection": result["evaluation_collection"]["collection_name"] = "wrong"
    if mutation == "dense_empty":
        for row in data:
            if row["mode"] == "dense_only": row["before_rerank_ids"] = []
    if mutation == "dense_error": data[25]["query_error"] = True
    if mutation == "dense_miss": data[25]["gold_chunk_hit_at_k"] = False
    if mutation == "hybrid_regression": data[50]["gold_chunk_hit_at_k"] = False
    facts = baseline.analyse(contract(), result, data, "isolated", 1 if mutation == "network" else 0)
    assert facts["validation_errors"]


def test_zero_semantic_gain_is_valid_and_non_promotable():
    facts = baseline.analyse(contract(), summary(), rows(), "isolated", 0)
    assert facts["validation_errors"] == []
    assert facts["comparison"]["semantic_gain_case_count"] == 0
    assert facts["comparison"]["semantic_contribution_demonstrated"] is False


def test_bm25_miss_dense_and_hybrid_hits_are_detected():
    data = rows(); data[0]["gold_chunk_hit_at_k"] = False
    facts = baseline.analyse(contract(), summary(), data, "isolated", 0)
    assert facts["comparison"]["dense_unique_gain_case_ids"] == ["case-0"]
    assert facts["comparison"]["semantic_gain_case_ids"] == ["case-0"]


def test_abstain_rows_do_not_create_gain_or_regression():
    data = rows(); data[20]["gold_chunk_hit_at_k"] = False; data[70]["gold_chunk_hit_at_k"] = True
    facts = baseline.analyse(contract(), summary(), data, "isolated", 0)
    assert facts["comparison"]["semantic_gain_case_ids"] == []
    assert facts["comparison"]["hybrid_regression_case_ids"] == []


def _guard_environment(tmp_path):
    guard, log = tmp_path / "guard", tmp_path / "network.jsonl"; guard.mkdir(); baseline._network_guard(guard / "sitecustomize.py")
    env = {"PYTHONPATH": str(guard), "REAL_VECTOR_NETWORK_LOG": str(log)}
    return env, log


def _loaded_guard(tmp_path, monkeypatch):
    import socket
    env, log = _guard_environment(tmp_path)
    monkeypatch.setenv("REAL_VECTOR_NETWORK_LOG", str(log))
    class FakeSocket:
        def __init__(self, family=socket.AF_INET, *args, **kwargs): self.family = family
        def connect(self, address): return "connected"
        def connect_ex(self, address): return 0
    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: ["allowed"])
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: "connected")
    spec = importlib.util.spec_from_file_location("test_guard", tmp_path / "guard" / "sitecustomize.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    spec.loader.exec_module(module)
    return module, log


@pytest.mark.parametrize("operation", ["getaddrinfo", "connect", "connect_ex", "create_connection"])
def test_external_network_operations_are_blocked_and_logged(tmp_path, monkeypatch, operation):
    guard, log = _loaded_guard(tmp_path, monkeypatch)
    if operation == "getaddrinfo":
        with pytest.raises(OSError): guard.getaddrinfo("example.invalid", 80)
    elif operation == "connect":
        with pytest.raises(OSError): guard.GuardedSocket().connect(("198.51.100.1", 80))
    elif operation == "connect_ex":
        assert guard.GuardedSocket().connect_ex(("198.51.100.1", 80)) == errno.EACCES
    else:
        with pytest.raises(OSError): guard.create_connection(("example.invalid", 80))
    record = json.loads(log.read_text().strip())
    assert record["operation"] == operation and record["pid"] > 0 and "timestamp" in record


def test_blocked_operations_append_jsonl_records(tmp_path):
    env, log = _guard_environment(tmp_path)
    subprocess.run([sys.executable, "-c", "import socket\nfor host in ('example.invalid','example.com'):\n\n try: socket.getaddrinfo(host, 80)\n except OSError: pass"], env={**os.environ, **env}, check=True)
    assert len(log.read_text().splitlines()) == 2


def test_loopback_and_unix_socket_are_permitted(tmp_path, monkeypatch):
    import socket
    guard, log = _loaded_guard(tmp_path, monkeypatch)
    assert guard.getaddrinfo("localhost.", 80) == ["allowed"]
    assert guard.GuardedSocket(socket.AF_UNIX).connect_ex("/tmp/no-such-real-vector.sock") == 0
    assert not log.exists()


def test_deliberate_child_environment_removes_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "top-secret"); monkeypatch.setenv("HF_TOKEN", "token")
    env = baseline.build_child_env(tmp_path, tmp_path, tmp_path, tmp_path / "n", contract())
    assert "OPENAI_API_KEY" not in env and "HF_TOKEN" not in env
    assert env["HF_HUB_OFFLINE"] == "1" and env["HTTP_PROXY"] == "http://127.0.0.1:9"


@pytest.mark.parametrize(("error", "expected"), [(baseline.PreconditionError("bad"), 2), (baseline.QualityViolationError("bad"), 3), (RuntimeError("bad"), 1)])
def test_exit_code_mapping(monkeypatch, error, expected):
    monkeypatch.setattr(baseline, "generate", lambda *args: (_ for _ in ()).throw(error))
    assert baseline.main([]) == expected


def test_implementation_source_fingerprints_are_correct():
    sources = baseline._implementation_sources()
    assert [entry["path"] for entry in sources] == baseline.IMPLEMENTATION_SOURCES
    assert all(entry["sha256"] == baseline.sha(ROOT / entry["path"]) for entry in sources)


def test_committed_report_has_no_machine_paths_or_secret_keys():
    serialized = json.dumps(baseline.load(ROOT / "reports/current_real_vector_quality_baseline.json"))
    assert "/home/" not in serialized and "/tmp/" not in serialized and "vectorstore" not in serialized.lower()
    assert "api_key" not in serialized.lower() and "token" not in serialized.lower()


def test_deterministic_baseline_is_separate_and_unchanged():
    assert baseline.sha(ROOT / "reports/current_retrieval_baseline.json") == "24d160979b05f2383db11033f1d830ca5e943bb75629a341fe9832ce2fc5672d"
