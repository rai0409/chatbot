from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_embedding_source_contract as validator


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "embedding_assets" / "retrieval_baseline.source.json"
SCRIPT_PATH = ROOT / "scripts" / "validate_embedding_source_contract.py"


def _contract() -> dict:
    return validator.load_contract(CONTRACT_PATH)


def test_tracked_contract_is_valid():
    validator.validate_contract(_contract())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", "other/model"),
        ("revision", "f" * 40),
        ("revision", "main"),
        ("revision", "E8F8C211226B894FCB81ACC59F3B34BA3EFD5F42"),
        ("trust_remote_code", True),
        ("embedding_dimension", 385),
        ("embedding_dimension", True),
        ("gated", True),
        ("runtime_network_allowed", True),
        ("license_identifier", "unknown"),
        ("license_review_status", "legal_approved"),
        ("model_id", "/tmp/model"),
        ("model_id", "https://example.invalid/model"),
        ("model_id", "api_key=not-a-secret"),
    ],
)
def test_invalid_contract_values_are_rejected(field, value):
    contract = _contract()
    contract[field] = value
    with pytest.raises(ValueError):
        validator.validate_contract(contract)


def test_unknown_and_missing_fields_are_rejected():
    contract = _contract()
    contract["unexpected"] = "value"
    with pytest.raises(ValueError):
        validator.validate_contract(contract)

    contract = _contract()
    contract.pop("gated")
    with pytest.raises(ValueError):
        validator.validate_contract(contract)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"profile":"a","profile":"b"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="could not be loaded"):
        validator.load_contract(path)


def test_cli_outputs_only_contract_summary():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--contract", str(CONTRACT_PATH)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "contract_status": "source_metadata_fixed",
        "model_id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "profile": "retrieval_baseline",
        "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        "schema_version": "embedding_source_contract.v1",
        "valid": True,
    }


def test_cli_rejects_invalid_contract_without_echoing_contents(tmp_path):
    path = tmp_path / "invalid.json"
    contract = _contract()
    contract["model_id"] = "token=do-not-echo"
    path.write_text(json.dumps(contract), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--contract", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "do-not-echo" not in result.stderr
