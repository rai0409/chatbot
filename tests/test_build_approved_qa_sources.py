from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_approved_qa_sources import build_approved_qa_sources


ROOT = Path(__file__).resolve().parents[1]


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_builds_118_governed_records_deterministically():
    manifest = build_approved_qa_sources(root=ROOT)
    paths = [ROOT / source["output_path"] for source in manifest["sources"]]
    first = [path.read_bytes() for path in paths] + [(ROOT / "data/approved_qa/manifest.json").read_bytes()]
    second_manifest = build_approved_qa_sources(root=ROOT)
    second = [path.read_bytes() for path in paths] + [(ROOT / "data/approved_qa/manifest.json").read_bytes()]
    assert first == second
    assert manifest == second_manifest
    assert manifest["total_record_count"] == 118
    assert manifest["fully_governed_approved_count"] == 22
    assert manifest["review_required_count"] == 96
    assert {source["source_fingerprint_method"] for source in manifest["sources"]} == {"sha256_pdf_bytes"}
    assert sum(len(_rows(path)) for path in paths) == 118


def test_manifest_hashes_and_governance_fields_are_correct():
    manifest = build_approved_qa_sources(root=ROOT)
    for source in manifest["sources"]:
        payload = (ROOT / source["output_path"]).read_bytes()
        assert source["source_jsonl_sha256"] == hashlib.sha256(payload).hexdigest()
    tourism, legacy = (_rows(ROOT / source["output_path"]) for source in manifest["sources"])
    assert all(row["status"] == "approved" and not row["approval_review_required"] for row in tourism)
    assert all(row["approval_provenance"] == "legacy_import" and row["approval_review_required"] for row in legacy)
    assert len({row["qa_id"] for row in tourism + legacy}) == 118


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
