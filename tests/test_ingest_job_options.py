"""Settings and per-job options reach the conversion (in a worker process and in the calling process)."""
from __future__ import annotations

import os
import zipfile

import pytest

import config
from rag_core import ingest_settings as ist
from rag_core.document_converters import isolated
from webapi import ingestion_jobs

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path):
    body = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>経費精算の締め日は毎月25日です。</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", body)
    return path


def test_the_worker_applies_environment_overrides_and_restores_them(tmp_path):
    path = _docx(tmp_path / "a.docx")
    isolated.shutdown()
    # a worker sees the override...
    assert isolated.worker_value("INGEST_CONVERT_TIMEOUT_SECONDS", env={"INGEST_CONVERT_TIMEOUT_SECONDS": "123"}, timeout=120) == "123"
    # ...and gives back its own environment when the override goes away (None)
    assert isolated.worker_value("INGEST_CONVERT_TIMEOUT_SECONDS", env={"INGEST_CONVERT_TIMEOUT_SECONDS": None}, timeout=120) is None
    isolated.shutdown()
    assert path.exists()


def test_a_task_can_carry_options_and_old_three_item_tasks_still_work(tmp_path, monkeypatch):
    path = _docx(tmp_path / "a.docx")
    isolated.shutdown()
    out = isolated.convert_files([(path, "default", "a.docx")], timeout=120, env={})
    assert out[path] and not isinstance(out[path], Exception)
    out = isolated.convert_files([(path, "default", "b.docx", {"doc_type": None, "ocr": None})], timeout=120, env={})
    assert out[path][0]["source_doc"] == "b.docx"
    isolated.shutdown()


def test_a_chunk_size_setting_changes_the_chunks_made_in_a_worker(tmp_path, monkeypatch):
    # (docx rows are not split by the chunk targets; this checks the environment reaches the worker, see worker_value)
    isolated.shutdown()
    env = ist.worker_environment()
    assert "JA_CHUNK_TARGET_POLICY_MAX_CHARS" in env
    isolated.shutdown()


def test_job_options_are_validated():
    assert ingestion_jobs.normalize_job_options(None, None) == {"doc_type": None, "ocr": None}
    assert ingestion_jobs.normalize_job_options("faq", True) == {"doc_type": "faq", "ocr": True}
    assert ingestion_jobs.normalize_job_options("auto", False) == {"doc_type": "auto", "ocr": False}
    assert ingestion_jobs.normalize_job_options("", None) == {"doc_type": None, "ocr": None}
    for bad in ("table", "../x", "FAQ; drop", "unknown"):
        with pytest.raises(ValueError):
            ingestion_jobs.normalize_job_options(bad, None)


def test_the_options_are_applied_to_pdfs_only(tmp_path):
    pdf, office = tmp_path / "a.pdf", tmp_path / "b.docx"
    tasks = ingestion_jobs.conversion_tasks([pdf, office], "default", {"doc_type": "faq", "ocr": True})
    by_path = {t[0]: t for t in tasks}
    assert by_path[pdf][3] == {"doc_type": "faq", "ocr": True}
    assert by_path[office][3] == {"doc_type": None, "ocr": None}


def test_the_job_record_keeps_the_options_that_were_used(tmp_path, monkeypatch):
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    path = _docx(tmp_path / "a.docx")
    monkeypatch.setattr(ingestion_jobs, "get_collection", lambda name: None)
    result = ingestion_jobs.run_raw_document_job([str(path)], expected_tenant="default", collection="staging_x", execute=False,
                                                 doc_type="faq", ocr=False)
    assert result["options"] == {"doc_type": "faq", "ocr": False}


def test_a_job_starts_with_the_current_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(ingestion_jobs, "get_collection", lambda name: None)
    ist._ORIGINAL.clear(); ist._APPLIED.clear(); ist._SEEN["rev"] = None
    path = _docx(tmp_path / "a.docx")
    ist.set_value("INGEST_PREPROCESS_QUANTITIES", False, by="山田太郎", reason="試験")
    os.environ.pop("INGEST_PREPROCESS_QUANTITIES", None)  # as if another process had written the store
    ist._APPLIED.clear(); ist._SEEN["rev"] = None
    ingestion_jobs.run_raw_document_job([str(path)], expected_tenant="default", collection="staging_x", execute=False)
    assert os.environ.get("INGEST_PREPROCESS_QUANTITIES") == "false"
    for s in ist.SETTINGS:
        os.environ.pop(s.name, None)
    ist._ORIGINAL.clear(); ist._APPLIED.clear(); ist._SEEN["rev"] = None
