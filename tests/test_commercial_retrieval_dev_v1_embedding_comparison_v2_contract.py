from __future__ import annotations

import json

import pytest

from scripts import validate_commercial_retrieval_dev_v1_embedding_comparison_v2_contract as validator


def _contract() -> dict:
    return validator.load_json(validator.CONTRACT)


def _write_contract(path, contract: dict) -> None:
    path.write_bytes(validator.canonical_json(contract))


def test_v2_contract_is_canonical_and_preserves_all_frozen_inputs_and_v1_history():
    result = validator.validate_contract()

    assert result["validation_status"] == "PASS"
    assert result["schema_version"] == "commercial_retrieval_dev_v1.embedding_comparison.contract.v2"
    assert result["candidate_count"] == 6
    contract = _contract()
    assert validator.CONTRACT.read_bytes() == validator.canonical_json(contract)
    assert contract["frozen_inputs"] == {
        name: {"path": path, "sha256": digest}
        for name, (path, digest) in validator.FROZEN_INPUTS.items()
    }
    assert contract["v1_immutable_artifacts"] == {
        name: digest for name, (_, digest) in validator.V1_ARTIFACTS.items()
    }


def test_exact_candidate_set_has_immutable_revisions_commercial_license_and_formatting():
    contract = _contract()
    candidates = {candidate["id"]: candidate for candidate in contract["candidate_embeddings"]}

    assert set(candidates) == set(validator.EXPECTED_CANDIDATES)
    for candidate_id, (model_id, revision, license_id, dimension, max_length, trust_remote_code) in validator.EXPECTED_CANDIDATES.items():
        candidate = candidates[candidate_id]
        assert candidate["model_id"] == model_id
        assert candidate["revision"] == revision
        assert candidate["commercial_use"]["eligible"] is True
        assert candidate["commercial_use"]["license_identifier"] == license_id
        assert candidate["upstream_source"].startswith("https://huggingface.co/")
        assert candidate["embedding_dimension"] == dimension
        assert candidate["max_sequence_length"] == max_length
        assert candidate["runtime_assumptions"]["trust_remote_code"] is trust_remote_code
        assert candidate["local_loading"]["required_snapshot_revision"] == revision
        assert candidate["local_loading"]["local_files_only"] is True
        assert candidate["local_loading"]["network_allowed_at_runtime"] is False
        assert candidate["encoding"]["query_format"]
        assert candidate["encoding"]["document_format"]
        assert candidate["encoding"]["normalization"] == "l2"
        assert isinstance(candidate["encoding"]["matryoshka_truncation_supported"], bool)
    assert candidates["qwen3_embedding_0_6b_global_challenger"]["encoding"]["query_format"].startswith("Instruct:")
    assert candidates["ruri_v3_310m_japanese_specialist"]["encoding"]["query_format"].startswith("検索クエリ: ")
    assert candidates["plamo_embedding_1b_japanese_higher_capacity"]["runtime_assumptions"]["trust_remote_code"] is True
    assert candidates["paraphrase_multilingual_minilm_l12_v2_historical_control"]["model_id"] == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_validator_rejects_floating_revision_and_unverified_commercial_license(tmp_path):
    contract = _contract()
    contract["candidate_embeddings"][0]["revision"] = "main"
    path = tmp_path / "floating.json"
    _write_contract(path, contract)
    with pytest.raises(validator.ContractError, match="floating_model_revision:bge_m3_incumbent"):
        validator.validate_contract(path)

    contract = _contract()
    contract["candidate_embeddings"][0]["commercial_use"]["eligible"] = False
    path = tmp_path / "license.json"
    _write_contract(path, contract)
    with pytest.raises(validator.ContractError, match="candidate_commercial_license:bge_m3_incumbent"):
        validator.validate_contract(path)


def test_metrics_slices_multidocument_operational_and_dense_only_protocol_are_exact():
    contract = _contract()

    assert {item["name"] for item in contract["quality_metrics"]} == validator.EXPECTED_METRICS
    assert {item["name"] for item in contract["slice_metrics"]} == set(validator.EXPECTED_SLICES)
    assert {item["name"] for item in contract["multi_document_completeness_metrics"]} == {
        "at_least_one_required_support_at_20",
        "all_required_supports_at_20",
        "at_least_one_required_support_at_50",
        "all_required_supports_at_50",
    }
    assert set(contract["operational_measurements"]["required"]) == validator.EXPECTED_OPERATIONAL
    assert contract["operational_measurements"]["informative_only"] is True
    assert contract["operational_measurements"]["hard_eligibility_gates"] == []
    assert contract["retrieval_protocol"]["dense_only"] is True
    assert contract["retrieval_protocol"]["ann_approximation_allowed"] is False
    assert contract["retrieval_protocol"]["cross_encoder_reranking_allowed"] is False
    assert contract["retrieval_protocol"]["hybrid_fusion_allowed"] is False


def test_selection_holdout_and_execution_boundary_remain_unconsumed_and_unexecuted():
    contract = _contract()

    assert contract["execution_state"] == {
        "embedding_model_comparison_count": 1,
        "network_attempts": 0,
        "retrieval_execution_count": 1,
        "selection_holdout_execution_count": 0,
        "vector_backend_comparison_count": 0,
    }
    assert contract["selection_policy"]["final_embedding_winner_allowed_from_dev_alone"] is False
    assert contract["selection_policy"]["selection_holdout_unconsumed"] is True
    assert contract["selection_policy"]["selection_holdout_gold_labels_inspected"] is False
    assert contract["selection_policy"]["next_stage_after_v2_execution"] == "Hybrid-aware Embedding Dev Comparison"
    assert all(not (validator.ROOT / path).exists() for path in contract["pre_execution_artifact_policy"]["execution_result_paths"])


def test_contract_validator_is_metadata_only_and_never_imports_model_runtime():
    source = validator.Path(validator.__file__).read_text(encoding="utf-8")

    assert "sentence_transformers" not in source
    assert "from transformers" not in source
    assert "torch" not in source
    assert "embedding_inference_in_contract_freeze" in json.dumps(_contract(), ensure_ascii=False)
