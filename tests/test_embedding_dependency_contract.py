from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements-embedding.txt"
CONSTRAINTS = ROOT / "constraints" / "linux-py312-embedding.txt"
BASE_CONSTRAINTS = ROOT / "constraints" / "linux-py312.txt"
SOURCE_CONTRACT = (
    ROOT / "config" / "embedding_assets" / "retrieval_baseline.source.json"
)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        assert not line.startswith(("-e", "--editable"))
        assert "://" not in line
        assert not line.startswith(("git+", "file:"))
        assert ";" not in line
        assert line.count("==") == 1

        name, version = line.split("==", 1)
        normalized = _normalize(name)

        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized)
        assert version
        assert normalized not in pins

        pins[normalized] = version

    return pins


def test_embedding_requirement_is_single_exact_pin() -> None:
    lines = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines == ["sentence-transformers==5.2.2"]


def test_embedding_constraints_are_sorted_and_unique() -> None:
    pins = _parse_pins(CONSTRAINTS)
    assert list(pins) == sorted(pins)
    assert len(pins) == len(set(pins))


def test_embedding_constraints_exclude_bootstrap_packages() -> None:
    pins = _parse_pins(CONSTRAINTS)
    assert {"pip", "setuptools", "wheel"}.isdisjoint(pins)


def test_required_embedding_packages_are_pinned() -> None:
    pins = _parse_pins(CONSTRAINTS)

    assert pins["sentence-transformers"] == "5.2.2"
    assert pins["torch"] == "2.10.0+cpu"

    required = {
        "transformers",
        "tokenizers",
        "huggingface-hub",
        "safetensors",
        "numpy",
        "scipy",
        "scikit-learn",
        "pytest",
    }
    assert required.issubset(pins)


def test_base_constraint_overlap_has_identical_versions() -> None:
    base = _parse_pins(BASE_CONSTRAINTS)
    embedding = _parse_pins(CONSTRAINTS)

    overlap = set(base) & set(embedding)
    assert overlap

    mismatches = {
        name: (base[name], embedding[name])
        for name in sorted(overlap)
        if base[name] != embedding[name]
    }
    assert mismatches == {}


def test_embedding_constraints_have_no_external_sources() -> None:
    text = CONSTRAINTS.read_text(encoding="utf-8")

    assert "://" not in text
    assert "git+" not in text
    assert "file:" not in text
    assert "--editable" not in text
    assert "/home/" not in text
    assert "/tmp/" not in text


def test_embedding_source_contract_remains_fixed() -> None:
    contract = json.loads(SOURCE_CONTRACT.read_text(encoding="utf-8"))

    assert contract["profile"] == "retrieval_baseline"
    assert (
        contract["model_id"]
        == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert (
        contract["revision"]
        == "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    )
    assert contract["embedding_dimension"] == 384
    assert contract["trust_remote_code"] is False
    assert contract["runtime_network_allowed"] is False
