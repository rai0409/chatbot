from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import validate_commercial_retrieval_dev_v1_embedding_comparison_v2_contract as contract_validator
from scripts import validate_commercial_retrieval_dev_v1_embedding_comparison_v2_execution as validator


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_valid_execution(output_dir: Path) -> None:
    contract = contract_validator.load_json(contract_validator.CONTRACT)
    candidates = contract["candidate_embeddings"]
    case_ids = [json.loads(line)["case_id"] for line in (ROOT / "eval/commercial_retrieval_dev_v1/candidates/cases.jsonl").read_text(encoding="utf-8").splitlines() if line]
    chunk_ids = [json.loads(line)["chunk_id"] for line in (ROOT / "eval/commercial_retrieval_dev_v1/source/chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rankings = []
    for candidate in candidates:
        rankings.extend({"candidate_id": candidate["id"], "case_id": case_id, "top_chunk_ids": chunk_ids[:50]} for case_id in case_ids)
    rankings_path = output_dir / "rankings.jsonl"
    rankings_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n" for row in rankings), encoding="utf-8")
    metric_names = [item["name"] for item in contract["quality_metrics"]]
    metrics = {name: {"denominator": 300, "numerator": 0, "value": 0.0} for name in metric_names}
    slices = {definition["name"]: {"case_count": definition["expected_case_count"], "metrics": {name: {"denominator": definition["expected_case_count"], "numerator": 0, "value": 0.0} for name in metric_names}} for definition in contract["slice_metrics"]}
    multi = {item["name"]: {"denominator": 35, "numerator": 0, "value": 0.0} for item in contract["multi_document_completeness_metrics"]}
    result_path = output_dir / "comparison_result.json"
    _write_json(result_path, {
        "schema_version": "commercial_retrieval_dev_v1.embedding_comparison.result.v2",
        "contract_sha256": _sha(contract_validator.CONTRACT),
        "dataset_identity": contract["dataset_identity"],
        "candidate_results": {candidate["id"]: {"model_id": candidate["model_id"], "revision": candidate["revision"], "float32_output": True, "metrics": metrics, "slice_metrics": slices, "multi_document_completeness_metrics": multi} for candidate in candidates},
        "rankings_sha256": _sha(rankings_path),
        "determinism": {"quality_output_identical_on_second_exact_evaluation": True, "ranking_tie_breaker": "ascending canonical chunk_id"},
        "execution_protocol": contract["retrieval_protocol"],
        "selection_holdout_consumed": False,
        "next_stage_after_execution": contract["selection_policy"]["next_stage_after_v2_execution"],
        "validation_status": "PASS",
    })
    operational_path = output_dir / "operational_measurements.json"
    _write_json(operational_path, {
        "schema_version": "commercial_retrieval_dev_v1.embedding_comparison.operational.v2",
        "measurement_conditions": contract["operational_measurements"]["measurement_conditions"],
        "per_candidate": {candidate["id"]: {
            "corpus_embedding_elapsed_seconds": 1.0,
            "query_embedding_latency_ms_p50": 1.0,
            "query_embedding_latency_ms_p95": 1.0,
            "retrieval_latency_ms_p50": 1.0,
            "retrieval_latency_ms_p95": 1.0,
            "peak_resident_memory_bytes": 1,
            "embedding_dimension": candidate["embedding_dimension"],
            "serialized_embedding_size_bytes": 1,
            "serialized_exact_search_index_size_bytes": 0,
            "model_asset_size_bytes": 1,
            "runtime_device_dtype_batch_size_worker_thread_settings": {"device": "cpu", "dtype": "float32", "batch_size": 1, "worker_threads": 1, "comparison_truncation_model_tokens": 1024 if candidate["id"] == "plamo_embedding_1b_japanese_higher_capacity" else 128 if candidate["id"] == "paraphrase_multilingual_minilm_l12_v2_historical_control" else 8192, "pooling": candidate["encoding"]["pooling"], "normalization": "l2"},
            "latency_sample_count": 400,
        } for candidate in candidates},
    })
    _write_json(output_dir / "execution_receipt.json", {
        "schema_version": "commercial_retrieval_dev_v1.embedding_comparison.execution_receipt.v2",
        "executed_at_utc": "2026-10-03T00:00:00+00:00",
        "contract_sha256": _sha(contract_validator.CONTRACT),
        "contract_validation_status": "PASS",
        "asset_validation_status": "PASS",
        "comparison_result_sha256": _sha(result_path),
        "rankings_sha256": _sha(rankings_path),
        "operational_measurements_sha256": _sha(operational_path),
        "external_network_attempt_count": 0,
        "selection_holdout_access_count": 0,
        "runtime": {"device": "cpu", "dtype": "float32", "batch_size": 1, "worker_threads": 1},
    })


def test_execution_validator_accepts_complete_synthetic_artifacts_and_no_winner(tmp_path):
    _write_valid_execution(tmp_path)
    result = validator.validate(tmp_path)

    assert result["validation_status"] == "PASS"
    assert "winner" not in (tmp_path / "comparison_result.json").read_text(encoding="utf-8").lower()


def test_execution_validator_rejects_incomplete_artifacts_and_final_winner(tmp_path):
    _write_valid_execution(tmp_path)
    (tmp_path / "operational_measurements.json").unlink()
    with pytest.raises(validator.ExecutionArtifactError, match="artifact files missing"):
        validator.validate(tmp_path)

    _write_valid_execution(tmp_path)
    result_path = tmp_path / "comparison_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["final_embedding_winner"] = "bge_m3_incumbent"
    _write_json(result_path, result)
    with pytest.raises(validator.ExecutionArtifactError, match="final winner present"):
        validator.validate(tmp_path)
