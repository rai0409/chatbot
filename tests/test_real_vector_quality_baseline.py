from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_real_vector_quality_baseline as baseline


ROOT = Path(__file__).resolve().parents[1]


def test_contract_and_committed_report_validate_current_real_vector_fixture():
    contract = baseline.load(ROOT / "config/evaluation/real_vector_quality_baseline.contract.json")
    baseline.validate_contract(contract)
    report = baseline.load(ROOT / "reports/current_real_vector_quality_baseline.json")
    assert report["schema_version"] == baseline.SCHEMA_VERSION
    assert report["evaluation_semantics"] == {"real_vector": True, "real_generation": False}
    assert report["external_network_attempt_count"] == 0
    assert report["promotion"]["product_promotion_eligible"] is False
    assert report["comparison"]["semantic_gain_case_count"] == 0


def test_command_is_real_vector_isolated_and_uses_current_interpreter(tmp_path):
    contract = baseline.load(ROOT / "config/evaluation/real_vector_quality_baseline.contract.json")
    command = baseline.build_command(contract, tmp_path / "rows", tmp_path / "summary", "isolated_eval")
    assert command[0] == baseline.sys.executable
    assert "--retrieval-aware" in command and "--real-vector" in command
    assert "--real-generation" not in command
    assert command[command.index("--eval-collection") + 1] == "isolated_eval"
    assert command[command.index("--modes") + 1] == "bm25_only,dense_only,hybrid,hybrid_rerank"


def test_contract_rejects_input_hash_change(monkeypatch):
    contract = baseline.load(ROOT / "config/evaluation/real_vector_quality_baseline.contract.json")
    contract["inputs"]["cases_sha256"] = "0" * 64
    with pytest.raises(baseline.ContractError, match="input hash mismatch"):
        baseline.validate_contract(contract)


def test_zero_semantic_gain_is_valid_but_not_promotable():
    report = baseline.load(ROOT / "reports/current_real_vector_quality_baseline.json")
    assert report["comparison"]["semantic_contribution_demonstrated"] is False
    assert report["promotion"]["product_promotion_eligible"] is False
