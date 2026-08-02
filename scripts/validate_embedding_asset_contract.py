#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_CONTRACT = Path(
    "config/embedding_assets/retrieval_baseline.asset.json"
)
DEFAULT_SOURCE_CONTRACT = Path(
    "config/embedding_assets/retrieval_baseline.source.json"
)


class ContractError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContractError(f"file_missing:{path}")

    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"duplicate_json_key:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid_json:{path}:{exc}") from exc

    if not isinstance(value, dict):
        raise ContractError(f"json_root_not_object:{path}")

    return value


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    location: str,
) -> None:
    actual = set(value)

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    if missing or extra:
        raise ContractError(
            f"invalid_keys:{location}:"
            f"missing={missing}:extra={extra}"
        )


def _require_sha256(value: Any, location: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        value,
    ):
        raise ContractError(f"invalid_sha256:{location}")

    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def validate_contract(
    contract_path: Path,
    source_contract_path: Path,
    asset_dir: Path | None = None,
) -> dict[str, Any]:
    contract = _load_json(contract_path)
    source = _load_json(source_contract_path)

    _require_exact_keys(
        contract,
        {
            "schema_version",
            "profile",
            "provider",
            "model_id",
            "revision",
            "embedding_dimension",
            "source_contract",
            "trust_remote_code",
            "runtime_network_allowed",
            "storage_policy",
            "materialization",
            "runtime_asset",
            "verification",
        },
        "contract",
    )

    if contract["schema_version"] != (
        "retrieval_embedding_asset_contract.v1"
    ):
        raise ContractError("unsupported_schema_version")

    if contract["profile"] != "retrieval_baseline":
        raise ContractError("invalid_profile")

    if contract["provider"] != "huggingface":
        raise ContractError("invalid_provider")

    if contract["model_id"] != source.get("model_id"):
        raise ContractError("source_model_id_mismatch")

    if contract["revision"] != source.get("revision"):
        raise ContractError("source_revision_mismatch")

    if (
        contract["embedding_dimension"]
        != source.get("embedding_dimension")
    ):
        raise ContractError("source_dimension_mismatch")

    if contract["embedding_dimension"] != 384:
        raise ContractError("invalid_embedding_dimension")

    if contract["trust_remote_code"] is not False:
        raise ContractError("trust_remote_code_must_be_false")

    if source.get("trust_remote_code") is not False:
        raise ContractError(
            "source_trust_remote_code_must_be_false"
        )

    if contract["runtime_network_allowed"] is not False:
        raise ContractError(
            "runtime_network_allowed_must_be_false"
        )

    if source.get("runtime_network_allowed") is not False:
        raise ContractError(
            "source_runtime_network_allowed_must_be_false"
        )

    if contract["source_contract"] != (
        "config/embedding_assets/"
        "retrieval_baseline.source.json"
    ):
        raise ContractError("invalid_source_contract_path")

    storage = contract["storage_policy"]
    if not isinstance(storage, dict):
        raise ContractError("storage_policy_not_object")

    _require_exact_keys(
        storage,
        {
            "repository_weights_committed",
            "runtime_asset_location",
            "runtime_asset_path_required",
        },
        "storage_policy",
    )

    if storage["repository_weights_committed"] is not False:
        raise ContractError(
            "repository_weights_committed_must_be_false"
        )

    if storage["runtime_asset_location"] != "external":
        raise ContractError("invalid_runtime_asset_location")

    if storage["runtime_asset_path_required"] is not True:
        raise ContractError(
            "runtime_asset_path_required_must_be_true"
        )

    materialization = contract["materialization"]
    if not isinstance(materialization, dict):
        raise ContractError("materialization_not_object")

    _require_exact_keys(
        materialization,
        {
            "download_network_required",
            "source_file_count",
            "source_total_bytes",
            "source_manifest_sha256",
            "excluded_files",
            "selected_weight_file",
        },
        "materialization",
    )

    if materialization["download_network_required"] is not True:
        raise ContractError(
            "download_network_required_must_be_true"
        )

    if (
        not isinstance(materialization["source_file_count"], int)
        or materialization["source_file_count"] <= 0
    ):
        raise ContractError("invalid_source_file_count")

    if (
        not isinstance(materialization["source_total_bytes"], int)
        or materialization["source_total_bytes"] <= 0
    ):
        raise ContractError("invalid_source_total_bytes")

    _require_sha256(
        materialization["source_manifest_sha256"],
        "materialization.source_manifest_sha256",
    )

    excluded = materialization["excluded_files"]
    if (
        not isinstance(excluded, list)
        or not all(isinstance(item, str) for item in excluded)
        or excluded != sorted(set(excluded))
    ):
        raise ContractError("invalid_excluded_files")

    if excluded != ["pytorch_model.bin"]:
        raise ContractError("unexpected_excluded_files")

    if materialization["selected_weight_file"] != (
        "model.safetensors"
    ):
        raise ContractError("invalid_selected_weight_file")

    runtime = contract["runtime_asset"]
    if not isinstance(runtime, dict):
        raise ContractError("runtime_asset_not_object")

    _require_exact_keys(
        runtime,
        {
            "file_count",
            "total_bytes",
            "files_sha256",
            "files",
        },
        "runtime_asset",
    )

    if (
        not isinstance(runtime["file_count"], int)
        or runtime["file_count"] <= 0
    ):
        raise ContractError("invalid_runtime_file_count")

    if (
        not isinstance(runtime["total_bytes"], int)
        or runtime["total_bytes"] <= 0
    ):
        raise ContractError("invalid_runtime_total_bytes")

    files_sha256 = _require_sha256(
        runtime["files_sha256"],
        "runtime_asset.files_sha256",
    )

    files = runtime["files"]
    if not isinstance(files, list) or not files:
        raise ContractError("runtime_files_not_list")

    normalized_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    total_bytes = 0

    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ContractError(
                f"runtime_file_not_object:{index}"
            )

        _require_exact_keys(
            entry,
            {"path", "size", "sha256"},
            f"runtime_asset.files[{index}]",
        )

        relative_text = entry["path"]

        if not isinstance(relative_text, str):
            raise ContractError(
                f"runtime_file_path_not_string:{index}"
            )

        relative = PurePosixPath(relative_text)

        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in relative_text
            or not relative.parts
        ):
            raise ContractError(
                f"unsafe_runtime_file_path:{relative_text}"
            )

        if relative_text in seen_paths:
            raise ContractError(
                f"duplicate_runtime_file_path:{relative_text}"
            )

        if (
            not isinstance(entry["size"], int)
            or entry["size"] < 0
        ):
            raise ContractError(
                f"invalid_runtime_file_size:{relative_text}"
            )

        digest = _require_sha256(
            entry["sha256"],
            f"runtime_file_sha256:{relative_text}",
        )

        seen_paths.add(relative_text)
        total_bytes += entry["size"]

        normalized_files.append(
            {
                "path": relative_text,
                "size": entry["size"],
                "sha256": digest,
            }
        )

    paths = [entry["path"] for entry in normalized_files]

    if paths != sorted(paths):
        raise ContractError("runtime_files_not_sorted")

    if len(normalized_files) != runtime["file_count"]:
        raise ContractError("runtime_file_count_mismatch")

    if total_bytes != runtime["total_bytes"]:
        raise ContractError("runtime_total_bytes_mismatch")

    if "model.safetensors" not in seen_paths:
        raise ContractError("model_safetensors_missing")

    if "pytorch_model.bin" in seen_paths:
        raise ContractError("excluded_weight_file_present")

    canonical = json.dumps(
        normalized_files,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    calculated_files_sha = hashlib.sha256(
        canonical
    ).hexdigest()

    if calculated_files_sha != files_sha256:
        raise ContractError("runtime_files_sha256_mismatch")

    verification = contract["verification"]
    if not isinstance(verification, dict):
        raise ContractError("verification_not_object")

    _require_exact_keys(
        verification,
        {
            "runtime_manifest_sha256",
            "materialization_audit_sha256",
            "independent_runtime_manifest_match",
            "offline_load_verified",
            "network_blocked_during_runtime_verification",
            "independent_process_exact_vector_match",
            "maximum_absolute_difference",
            "minimum_cosine_similarity",
        },
        "verification",
    )

    _require_sha256(
        verification["runtime_manifest_sha256"],
        "verification.runtime_manifest_sha256",
    )
    _require_sha256(
        verification["materialization_audit_sha256"],
        "verification.materialization_audit_sha256",
    )

    for key in (
        "independent_runtime_manifest_match",
        "offline_load_verified",
        "network_blocked_during_runtime_verification",
        "independent_process_exact_vector_match",
    ):
        if verification[key] is not True:
            raise ContractError(
                f"verification_flag_not_true:{key}"
            )

    if verification["maximum_absolute_difference"] != 0.0:
        raise ContractError(
            "maximum_absolute_difference_not_zero"
        )

    if verification["minimum_cosine_similarity"] != 1.0:
        raise ContractError(
            "minimum_cosine_similarity_not_one"
        )

    if asset_dir is not None:
        asset_dir = asset_dir.resolve()

        if not asset_dir.is_dir():
            raise ContractError(
                f"asset_directory_missing:{asset_dir}"
            )

        actual_paths: list[str] = []

        for path in sorted(asset_dir.rglob("*")):
            if path.is_symlink():
                raise ContractError(
                    f"asset_symlink_forbidden:{path}"
                )

            if path.is_file():
                actual_paths.append(
                    path.relative_to(asset_dir).as_posix()
                )

        if actual_paths != paths:
            missing = sorted(set(paths) - set(actual_paths))
            extra = sorted(set(actual_paths) - set(paths))

            raise ContractError(
                f"asset_file_set_mismatch:"
                f"missing={missing}:extra={extra}"
            )

        for entry in normalized_files:
            path = asset_dir.joinpath(
                *PurePosixPath(entry["path"]).parts
            )

            if path.stat().st_size != entry["size"]:
                raise ContractError(
                    f"asset_size_mismatch:{entry['path']}"
                )

            if _file_sha256(path) != entry["sha256"]:
                raise ContractError(
                    f"asset_sha256_mismatch:{entry['path']}"
                )

    return {
        "schema_version": contract["schema_version"],
        "profile": contract["profile"],
        "model_id": contract["model_id"],
        "revision": contract["revision"],
        "embedding_dimension": contract[
            "embedding_dimension"
        ],
        "runtime_file_count": runtime["file_count"],
        "runtime_total_bytes": runtime["total_bytes"],
        "runtime_files_sha256": files_sha256,
        "asset_directory_verified": asset_dir is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--contract",
        type=Path,
        default=DEFAULT_CONTRACT,
    )
    parser.add_argument(
        "--source-contract",
        type=Path,
        default=DEFAULT_SOURCE_CONTRACT,
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
    )
    args = parser.parse_args()

    try:
        result = validate_contract(
            contract_path=args.contract,
            source_contract_path=args.source_contract,
            asset_dir=args.asset_dir,
        )
    except ContractError as exc:
        print(
            json.dumps(
                {
                    "valid": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "valid": True,
                **result,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
