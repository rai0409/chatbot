from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.knowledge_manifest_builder import build_knowledge_manifest, scan_knowledge_sources
from rag_core.knowledge_manifest import (
    checksum_mismatches,
    compute_file_checksum,
    duplicate_source_ids,
    filter_records,
    find_record_by_source_id,
    load_manifest,
    missing_local_files,
    normalize_source_record,
    save_manifest,
    validate_source_record,
)


def test_checksum_is_stable_for_same_file_content(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_text("same\n", encoding="utf-8")

    assert compute_file_checksum(path) == compute_file_checksum(path)


def test_checksum_changes_when_file_content_changes(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_text("before\n", encoding="utf-8")
    before = compute_file_checksum(path)
    path.write_text("after\n", encoding="utf-8")

    assert compute_file_checksum(path) != before


def test_normalize_record_fills_defaults():
    record = normalize_source_record({"source_id": "qa", "source_path": "data/approved_qa/default.jsonl"})

    assert record["tenant_id"] == "default"
    assert record["source_type"] == "other"
    assert record["source_title"] == "default.jsonl"
    assert record["version"] == "1"
    assert record["checksum_algorithm"] == "sha256"
    assert record["status"] == "active"
    assert record["metadata"] == {}


def test_validate_record_accepts_valid_record():
    record = normalize_source_record(
        {
            "source_id": "qa",
            "source_path": "data/approved_qa/default.jsonl",
            "source_type": "approved_qa",
            "status": "active",
        }
    )

    assert validate_source_record(record) == []


@pytest.mark.parametrize("field", ["source_id", "source_path", "source_type", "status"])
def test_validate_record_rejects_required_missing_fields(field):
    record = {
        "source_id": "qa",
        "source_path": "file.jsonl",
        "source_type": "approved_qa",
        "status": "active",
    }
    record[field] = ""

    assert f"missing_{field}" in validate_source_record(record)


def test_duplicate_source_id_detection_works():
    records = [{"source_id": "a"}, {"source_id": "b"}, {"source_id": "a"}]

    assert duplicate_source_ids(records) == ["a"]


def test_filtering_by_tenant_status_source_type_and_category_works():
    records = [
        {"source_id": "a", "tenant_id": "t1", "status": "active", "source_type": "pdf", "category": "pdf"},
        {"source_id": "b", "tenant_id": "t2", "status": "archived", "source_type": "approved_qa", "category": "qa"},
    ]

    assert [r["source_id"] for r in filter_records(records, tenant_id="t1")] == ["a"]
    assert [r["source_id"] for r in filter_records(records, status="archived")] == ["b"]
    assert [r["source_id"] for r in filter_records(records, source_type="pdf", category="pdf")] == ["a"]


def test_missing_local_file_detection_works(tmp_path):
    records = [{"source_id": "missing", "source_path": "missing.pdf"}]

    assert missing_local_files(records, root_dir=tmp_path) == ["missing"]


def test_checksum_mismatch_detection_works(tmp_path):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"before")
    record = {"source_id": "pdf", "source_path": "source.pdf", "checksum": compute_file_checksum(path)}
    path.write_bytes(b"after")

    assert checksum_mismatches([record], root_dir=tmp_path) == ["pdf"]


def test_manifest_save_load_round_trip_and_find_record(tmp_path):
    manifest = {
        "manifest_version": "1",
        "generated_at": "2026-06-07T00:00:00+00:00",
        "records": [
            {
                "source_id": "qa",
                "source_path": "data/approved_qa/default.jsonl",
                "source_type": "approved_qa",
                "status": "active",
            }
        ],
        "warnings": ["warning"],
    }
    path = tmp_path / "manifest.json"

    save_manifest(manifest, path)
    loaded = load_manifest(path)

    assert loaded["warnings"] == ["warning"]
    assert find_record_by_source_id(loaded, "qa")["source_type"] == "approved_qa"


def _write(path: Path, content: bytes | str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_builder_scans_known_source_locations_in_tmp_directory(tmp_path):
    _write(tmp_path / "data/approved_qa/default.jsonl", '{"q":"secret qa content"}\n')
    _write(tmp_path / "data/source_pdfs/source.pdf", b"%PDF-source")
    _write(tmp_path / "pdfs/public.pdf", b"%PDF-public")
    _write(tmp_path / "index/chunks.jsonl", '{"text":"secret chunk"}\n')
    _write(tmp_path / "eval/cases/smoke.jsonl", '{"query":"secret query"}\n')

    manifest = build_knowledge_manifest(root_dir=tmp_path, output_path="data/knowledge/manifest.json")
    records = manifest["records"]
    source_types = {record["source_type"] for record in records}

    assert (tmp_path / "data/knowledge/manifest.json").exists()
    assert {"approved_qa", "pdf", "index_jsonl", "eval_case"}.issubset(source_types)
    assert [record["source_path"] for record in records] == [
        "data/approved_qa/default.jsonl",
        "data/source_pdfs/source.pdf",
        "pdfs/public.pdf",
        "index/chunks.jsonl",
        "eval/cases/smoke.jsonl",
    ]


def test_builder_excludes_pycache_and_runtime_log_files(tmp_path):
    _write(tmp_path / "index/active.jsonl", "{}\n")
    _write(tmp_path / "eval/__pycache__/bad.jsonl", "{}\n")
    _write(tmp_path / "runs/audit/chat_events.jsonl", "{}\n")
    _write(tmp_path / "artifacts/eval/report.jsonl", "{}\n")

    records, _warnings = scan_knowledge_sources(root_dir=tmp_path)
    paths = {record["source_path"] for record in records}

    assert "index/active.jsonl" in paths
    assert all("__pycache__" not in path for path in paths)
    assert all(not path.startswith("runs/") for path in paths)
    assert all(not path.startswith("artifacts/eval/") for path in paths)


def test_builder_writes_manifest_json_with_records_and_warnings(tmp_path):
    _write(tmp_path / "index/active.jsonl", "{}\n")

    manifest = build_knowledge_manifest(root_dir=tmp_path, output_path="data/knowledge/manifest.json")
    loaded = json.loads((tmp_path / "data/knowledge/manifest.json").read_text(encoding="utf-8"))

    assert loaded["records"]
    assert "warnings" in loaded
    assert manifest["records"][0]["checksum"]


def test_builder_does_not_include_file_contents(tmp_path):
    secret = "秘密の本文"
    _write(tmp_path / "data/approved_qa/default.jsonl", secret)

    build_knowledge_manifest(root_dir=tmp_path, output_path="data/knowledge/manifest.json")
    raw = (tmp_path / "data/knowledge/manifest.json").read_text(encoding="utf-8")

    assert secret not in raw
    assert "default.jsonl" in raw


def test_builder_output_is_deterministic_enough_for_tests(tmp_path):
    _write(tmp_path / "index/b.jsonl", "{}\n")
    _write(tmp_path / "index/a.jsonl", "{}\n")

    first = build_knowledge_manifest(root_dir=tmp_path, output_path="data/knowledge/manifest.json")
    second = build_knowledge_manifest(root_dir=tmp_path, output_path="data/knowledge/manifest.json")

    assert [record["source_id"] for record in first["records"]] == [record["source_id"] for record in second["records"]]
    assert [record["source_path"] for record in first["records"]] == ["index/a.jsonl", "index/b.jsonl"]


def test_no_runtime_main_import_needed():
    import sys

    sys.modules.pop("webapi.main", None)
    __import__("rag_core.knowledge_manifest")
    __import__("eval.knowledge_manifest_builder")

    assert "webapi.main" not in sys.modules
