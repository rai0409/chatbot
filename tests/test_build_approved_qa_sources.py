from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.build_approved_qa_sources import build_approved_qa_sources


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/approved_qa_sources"
FIXTURE_SPECS = (
    {
        "name": "fixture-approved",
        "input": "ingest/default.jsonl",
        "output": "generated/sources/fixture-approved.jsonl",
        "source_document": "fixture-approved.pdf",
        "approval_provenance": "existing_governed_source",
        "legacy": False,
    },
    {
        "name": "fixture-legacy",
        "input": "ingest/legacy.jsonl",
        "output": "generated/sources/fixture-legacy.jsonl",
        "source_document": "fixture-legacy.pdf",
        "approval_provenance": "legacy_import",
        "legacy": True,
    },
)


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _fixture_root(tmp_path):
    shutil.copytree(FIXTURE_ROOT, tmp_path / "fixture")
    return tmp_path / "fixture"


def test_builds_fixture_governed_records_deterministically(tmp_path):
    root = _fixture_root(tmp_path)
    manifest = build_approved_qa_sources(root=root, source_specs=FIXTURE_SPECS)
    paths = [root / source["output_path"] for source in manifest["sources"]]
    first = [path.read_bytes() for path in paths] + [(root / "data/approved_qa/manifest.json").read_bytes()]
    second_manifest = build_approved_qa_sources(root=root, source_specs=FIXTURE_SPECS)
    second = [path.read_bytes() for path in paths] + [(root / "data/approved_qa/manifest.json").read_bytes()]
    assert first == second
    assert manifest == second_manifest
    assert manifest["total_record_count"] == 5
    assert manifest["fully_governed_approved_count"] == 2
    assert manifest["review_required_count"] == 3
    assert {source["source_fingerprint_method"] for source in manifest["sources"]} == {"sha256_source_document_identity"}
    assert sum(len(_rows(path)) for path in paths) == 5
    assert all(not source["input_path"].startswith(("data/", "artifacts/")) for source in manifest["sources"])


def test_manifest_hashes_and_governance_fields_are_correct(tmp_path):
    root = _fixture_root(tmp_path)
    manifest = build_approved_qa_sources(root=root, source_specs=FIXTURE_SPECS)
    for source in manifest["sources"]:
        payload = (root / source["output_path"]).read_bytes()
        assert source["source_jsonl_sha256"] == hashlib.sha256(payload).hexdigest()
        assert source["input_sha256"] == hashlib.sha256((root / source["input_path"]).read_bytes()).hexdigest()
    tourism, legacy = (_rows(root / source["output_path"]) for source in manifest["sources"])
    assert all(row["status"] == "approved" and not row["approval_review_required"] for row in tourism)
    assert all(row["approval_provenance"] == "legacy_import" and row["approval_review_required"] for row in legacy)
    assert {row["tenant_id"] for row in tourism + legacy} == {"fixture-tenant"}
    assert all(row["source_pages"] and row["source_record_fingerprint"] for row in tourism + legacy)
    assert len({row["qa_id"] for row in tourism + legacy}) == 5


def test_builder_fails_closed_on_missing_explicit_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_approved_qa_sources(root=tmp_path, source_specs=FIXTURE_SPECS)


def test_builder_rejects_source_specification_path_traversal(tmp_path):
    spec = ({**FIXTURE_SPECS[0], "input": "../outside.jsonl"},)
    with pytest.raises(ValueError, match="escapes root"):
        build_approved_qa_sources(root=tmp_path, source_specs=spec)


@pytest.mark.parametrize("bad_line, message", [("{bad}\n", "malformed JSON"), ("[]\n", "must be an object")])
def test_builder_fails_closed_on_invalid_jsonl(tmp_path, monkeypatch, bad_line, message):
    import scripts.build_approved_qa_sources as builder

    source = tmp_path / "input.jsonl"
    source.write_text(bad_line, encoding="utf-8")
    monkeypatch.setattr(builder, "SOURCE_SPECS", ({"name":"x","input":"input.jsonl","output":"out.jsonl","source_document":"missing.pdf","approval_provenance":"legacy_import","legacy":True},))
    with pytest.raises(ValueError, match=message):
        builder.build_approved_qa_sources(root=tmp_path)


def test_builder_fails_closed_on_duplicate_id_and_conflicting_answer(tmp_path, monkeypatch):
    import scripts.build_approved_qa_sources as builder

    rows = [
        {"qa_id":"same","question":"question","approved_answer":"one","source_doc":"x.pdf","source_pages":[1]},
        {"qa_id":"same","question":"question two","approved_answer":"two","source_doc":"x.pdf","source_pages":[1]},
    ]
    (tmp_path / "input.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(builder, "SOURCE_SPECS", ({"name":"x","input":"input.jsonl","output":"out.jsonl","source_document":"x.pdf","approval_provenance":"legacy_import","legacy":True},))
    with pytest.raises(ValueError, match="duplicate qa_id"):
        builder.build_approved_qa_sources(root=tmp_path)


def test_builder_fails_closed_on_conflicting_normalized_question(tmp_path, monkeypatch):
    import scripts.build_approved_qa_sources as builder

    rows = [
        {"qa_id":"one","question":"Same   question","approved_answer":"one","source_doc":"x.pdf","source_pages":[1]},
        {"qa_id":"two","question":"same question","approved_answer":"two","source_doc":"x.pdf","source_pages":[1]},
    ]
    (tmp_path / "input.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(builder, "SOURCE_SPECS", ({"name":"x","input":"input.jsonl","output":"out.jsonl","source_document":"x.pdf","approval_provenance":"legacy_import","legacy":True},))
    with pytest.raises(ValueError, match="conflicting answer for normalized question"):
        builder.build_approved_qa_sources(root=tmp_path)
