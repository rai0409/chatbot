#!/usr/bin/env python3
"""Validate the fixed source metadata for the retrieval-baseline embedding model."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_CONTRACT = {
    "schema_version": "embedding_source_contract.v1",
    "profile": "retrieval_baseline",
    "provider": "huggingface",
    "model_id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
    "model_type": "sentence_transformer",
    "embedding_dimension": 384,
    "trust_remote_code": False,
    "license_identifier": "apache-2.0",
    "license_review_status": "metadata_reviewed_legal_approval_pending",
    "gated": False,
    "materialization_network_allowed": True,
    "runtime_network_allowed": False,
    "contract_status": "source_metadata_fixed",
}

_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_ABSOLUTE_PATH_RE = re.compile(r"(?:^/|^[A-Za-z]:[\\/])")
_URL_RE = re.compile(r"://|\bhttps?\b", re.IGNORECASE)
_CREDENTIAL_RE = re.compile(r"(?:api[_-]?key|token|password|secret|credential)", re.IGNORECASE)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_contract(path: Path) -> dict:
    """Load one contract JSON object while rejecting duplicate keys."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("contract JSON could not be loaded") from exc
    if not isinstance(parsed, dict):
        raise ValueError("contract JSON root must be an object")
    return parsed


def _validate_safe_strings(contract: dict) -> None:
    for value in contract.values():
        if not isinstance(value, str):
            continue
        if _ABSOLUTE_PATH_RE.search(value):
            raise ValueError("absolute paths are not allowed")
        if _URL_RE.search(value):
            raise ValueError("URLs are not allowed")
        if _CREDENTIAL_RE.search(value):
            raise ValueError("credential-like strings are not allowed")


def validate_contract(contract: dict) -> None:
    """Validate the exact, non-secret source metadata contract."""
    if not isinstance(contract, dict):
        raise ValueError("contract must be an object")
    if set(contract) != set(EXPECTED_CONTRACT):
        raise ValueError("contract fields must exactly match the schema")
    _validate_safe_strings(contract)
    for field, expected in EXPECTED_CONTRACT.items():
        value = contract[field]
        if field == "embedding_dimension" and isinstance(value, bool):
            raise ValueError("embedding_dimension must be an integer")
        if value != expected:
            raise ValueError(f"invalid {field}")
    revision = contract["revision"]
    if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
        raise ValueError("revision must be a 40-character lowercase hexadecimal commit")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        validate_contract(contract)
    except ValueError as exc:
        print(f"contract validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "schema_version": contract["schema_version"],
                "profile": contract["profile"],
                "model_id": contract["model_id"],
                "revision": contract["revision"],
                "contract_status": contract["contract_status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
