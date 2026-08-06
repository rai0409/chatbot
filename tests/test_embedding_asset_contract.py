from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "config"
    / "embedding_assets"
    / "retrieval_baseline.asset.json"
)
SOURCE_CONTRACT_PATH = (
    ROOT
    / "config"
    / "embedding_assets"
    / "retrieval_baseline.source.json"
)
VALIDATOR_PATH = (
    ROOT
    / "scripts"
    / "validate_embedding_asset_contract.py"
)

SPEC = importlib.util.spec_from_file_location(
    "embedding_asset_validator",
    VALIDATOR_PATH,
)
assert SPEC is not None
assert SPEC.loader is not None

VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_tracked_asset_contract_validates() -> None:
    result = VALIDATOR.validate_contract(
        CONTRACT_PATH,
        SOURCE_CONTRACT_PATH,
    )

    assert result["profile"] == "retrieval_baseline"
    assert result["runtime_file_count"] == 13
    assert result["runtime_total_bytes"] == 499562317
    assert (
        result["runtime_files_sha256"]
        == "cede7177dd492d9d7776484dce8d030f0"
        "cd127eae3297305991de91386394d5a"
    )


def test_asset_contract_identity_is_fixed() -> None:
    contract = _contract()

    assert contract["provider"] == "huggingface"
    assert (
        contract["model_id"]
        == "sentence-transformers/"
        "paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert (
        contract["revision"]
        == "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    )
    assert contract["embedding_dimension"] == 384
    assert contract["trust_remote_code"] is False
    assert contract["runtime_network_allowed"] is False


def test_runtime_file_manifest_is_sorted_and_unique() -> None:
    files = _contract()["runtime_asset"]["files"]
    paths = [entry["path"] for entry in files]

    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))

    for path in paths:
        relative = PurePosixPath(path)
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        assert "\\" not in path


def test_runtime_manifest_count_and_size_match() -> None:
    runtime = _contract()["runtime_asset"]
    files = runtime["files"]

    assert runtime["file_count"] == len(files)
    assert runtime["total_bytes"] == sum(
        entry["size"] for entry in files
    )


def test_runtime_manifest_aggregate_hash_matches() -> None:
    runtime = _contract()["runtime_asset"]

    canonical = json.dumps(
        runtime["files"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert hashlib.sha256(canonical).hexdigest() == (
        runtime["files_sha256"]
    )


def test_runtime_weight_selection_is_unambiguous() -> None:
    contract = _contract()
    paths = {
        entry["path"]
        for entry in contract["runtime_asset"]["files"]
    }

    assert contract["materialization"]["excluded_files"] == [
        "pytorch_model.bin"
    ]
    assert (
        contract["materialization"]["selected_weight_file"]
        == "model.safetensors"
    )
    assert "model.safetensors" in paths
    assert "pytorch_model.bin" not in paths


def test_model_weights_are_not_committed_to_repository() -> None:
    contract = _contract()

    assert (
        contract["storage_policy"][
            "repository_weights_committed"
        ]
        is False
    )
    assert (
        contract["storage_policy"]["runtime_asset_location"]
        == "external"
    )

    forbidden_suffixes = {
        ".safetensors",
        ".bin",
        ".onnx",
    }

    tracked_config_weights = [
        path
        for path in (
            ROOT / "config" / "embedding_assets"
        ).rglob("*")
        if path.is_file()
        and path.suffix.lower() in forbidden_suffixes
    ]

    assert tracked_config_weights == []


def test_tampered_aggregate_hash_is_rejected(
    tmp_path: Path,
) -> None:
    contract = _contract()
    contract["runtime_asset"]["files_sha256"] = "0" * 64

    tampered = tmp_path / "tampered.json"
    tampered.write_text(
        json.dumps(contract),
        encoding="utf-8",
    )

    with pytest.raises(
        VALIDATOR.ContractError,
        match="runtime_files_sha256_mismatch",
    ):
        VALIDATOR.validate_contract(
            tampered,
            SOURCE_CONTRACT_PATH,
        )


def test_unsafe_runtime_path_is_rejected(
    tmp_path: Path,
) -> None:
    contract = _contract()
    contract["runtime_asset"]["files"][0]["path"] = (
        "../outside"
    )

    tampered = tmp_path / "unsafe.json"
    tampered.write_text(
        json.dumps(contract),
        encoding="utf-8",
    )

    with pytest.raises(
        VALIDATOR.ContractError,
        match="unsafe_runtime_file_path",
    ):
        VALIDATOR.validate_contract(
            tampered,
            SOURCE_CONTRACT_PATH,
        )
