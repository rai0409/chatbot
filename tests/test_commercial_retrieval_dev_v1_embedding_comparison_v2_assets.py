from __future__ import annotations

import shutil

import pytest

from scripts import validate_commercial_retrieval_dev_v1_embedding_comparison_v2_assets as validator


def _receipt() -> dict:
    return validator.load_json(validator.RECEIPT)


def _copy_evidence(tmp_path):
    copied = tmp_path / "v2"
    shutil.copytree(validator.ASSET_DIR, copied)
    return copied / "asset_acquisition_receipt.json"


def _write(path, value: dict) -> None:
    path.write_bytes(validator.canonical_json(value))


def test_six_candidates_and_revisions_match_the_frozen_contract():
    result = validator.validate_assets()
    receipt = _receipt()

    assert result["validation_status"] == "PASS"
    assert receipt["candidate_count"] == 6
    assert {item["candidate_id"] for item in receipt["candidate_assets"]} == set(validator.EXPECTED)
    for item in receipt["candidate_assets"]:
        assert (item["model_id"], item["revision"]) == validator.EXPECTED[item["candidate_id"]][:2]


def test_receipt_binds_the_frozen_parent_sha():
    assert _receipt()["parent_frozen_contract"]["sha256"] == validator.CONTRACT_SHA256
    assert validator.validate_assets()["parent_contract_sha256"] == validator.CONTRACT_SHA256


def test_manifest_aggregate_sha_mismatch_fails(tmp_path):
    receipt_path = _copy_evidence(tmp_path)
    receipt = validator.load_json(receipt_path)
    asset = receipt["candidate_assets"][0]
    manifest_path = receipt_path.parent / asset["manifest_path"].split("/")[-1]
    manifest = validator.load_json(manifest_path)
    manifest["aggregate_sha256"] = "0" * 64
    _write(manifest_path, manifest)

    with pytest.raises(validator.AssetEvidenceError, match="manifest_receipt_mismatch"):
        validator.validate_assets(receipt_path)


def test_missing_candidate_fails(tmp_path):
    receipt_path = _copy_evidence(tmp_path)
    receipt = validator.load_json(receipt_path)
    receipt["candidate_assets"].pop()
    receipt["candidate_count"] = 5
    _write(receipt_path, receipt)

    with pytest.raises(validator.AssetEvidenceError, match="receipt_candidate_count"):
        validator.validate_assets(receipt_path)


def test_plamo_custom_code_files_are_hashed_and_protected(tmp_path):
    receipt_path = _copy_evidence(tmp_path)
    receipt = validator.load_json(receipt_path)
    asset = next(item for item in receipt["candidate_assets"] if item["candidate_id"] == "plamo_embedding_1b_japanese_higher_capacity")
    manifest_path = receipt_path.parent / asset["manifest_path"].split("/")[-1]
    manifest = validator.load_json(manifest_path)
    manifest["files"] = [entry for entry in manifest["files"] if entry["relative_path"] != "modeling_plamo.py"]
    manifest["file_count"] -= 1
    manifest["total_asset_bytes"] -= 43942
    _write(manifest_path, manifest)
    asset.update(manifest)
    _write(receipt_path, receipt)

    with pytest.raises(validator.AssetEvidenceError, match="plamo_remote_code_files_missing"):
        validator.validate_assets(receipt_path)


def test_granite_default_tokenizer_policy_and_evidence_are_preserved():
    granite = _receipt()["granite_tokenizer_compatibility"]

    assert granite["default_auto_tokenizer_load"] == "PASS"
    assert granite["tokenizer_execution_policy"] == {
        "explicit_fix_mistral_regex_argument": False,
        "use_default_auto_tokenizer": True,
    }
    assert granite["canonical_tokenizer_json_case_count"] == 6
    assert granite["canonical_tokenizer_json_token_ids_match"] is True


def test_no_benchmark_result_or_winner_is_recorded():
    assert validator._forbidden_field_present(_receipt()) is False
    assert validator.validate_assets()["validation_status"] == "PASS"


def test_selection_holdout_is_protected_and_unconsumed():
    selection = validator.load_json(validator.CONTRACT)["selection_policy"]

    assert selection["selection_holdout_role"] == "PLANNED"
    assert selection["selection_holdout_unconsumed"] is True
    assert selection["selection_holdout_gold_labels_inspected"] is False
