"""Stage 12: citation excerpts for staging / approved QA, upload retention,
admin logout-all precision, request totals over workers, parallel conversion,
dependency pins. Synthetic data only.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def open_api(monkeypatch):
    for name in ("API_AUTH_ENABLED", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    from webapi import main as main_mod

    return main_mod


# --- 1. citation excerpts ------------------------------------------------------------------------


def test_chat_source_reads_the_staging_collection(open_api, monkeypatch):
    from rag_core import retrieval

    calls = []

    def fake_row(chunk_id, collection_name=None):
        calls.append((chunk_id, collection_name))
        if collection_name == "pilot_staging_v1":
            return {"tenant_id": "default", "text": "ステージングの本文", "source_doc": "s.pdf"}
        return None

    monkeypatch.setattr(retrieval, "chunk_row", fake_row)
    monkeypatch.setattr(open_api, "_resolve_staging_collection", lambda name, tenant_id: name or None)
    client = TestClient(open_api.app)
    ok = client.get("/chat/source?chunk_id=c9&collection=pilot_staging_v1")
    assert ok.status_code == 200 and ok.json()["text"] == "ステージングの本文"
    assert calls[-1] == ("c9", "pilot_staging_v1")
    assert client.get("/chat/source?chunk_id=c9").status_code == 404


def test_chat_source_staging_access_is_checked_like_chat(open_api):
    client = TestClient(open_api.app)
    # A production/default collection name is refused, as in /chat.
    assert client.get(f"/chat/source?chunk_id=c1&collection={config.VECTORSTORE_COLLECTION_NAME}").status_code == 400


def _approved(qa_id, question, answer, tenant="default"):
    return json.dumps({
        "qa_id": qa_id, "question": question, "approved_answer": answer, "status": "approved",
        "tenant_id": tenant, "updated_at": "2026-01-01",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [2]}],
    }, ensure_ascii=False)


def test_chat_source_falls_back_to_the_approved_answer(open_api, monkeypatch, tmp_path):
    from rag_core import retrieval

    path = tmp_path / "approved.jsonl"
    path.write_text("\n".join([
        _approved("q1", "営業時間は？", "9時から17時です。"),
        _approved("q2", "定休日は？", "土日です。", tenant="tenant_b"),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", path, raising=False)
    monkeypatch.setattr(retrieval, "chunk_row", lambda chunk_id, collection_name=None: None)
    client = TestClient(open_api.app)
    ok = client.get("/chat/source?chunk_id=approved_qa_pair:q1")
    assert ok.status_code == 200
    body = ok.json()
    assert body["text"] == "Q: 営業時間は？\nA: 9時から17時です。" and body["source_pages"] == [2]
    # Another tenant's approved answer is not served.
    assert client.get("/chat/source?chunk_id=approved_qa_pair:q2").status_code == 404


def test_chat_ui_passes_the_collection_for_excerpts():
    html = (ROOT / "webapi/static/chat.html").read_text(encoding="utf-8")
    assert '"&collection="' in html and "turn._collection = ctx.collection" in html


# --- 2. upload retention --------------------------------------------------------------------------


def test_old_uploads_are_removed(open_api, monkeypatch, tmp_path):
    old_dir = tmp_path / "tenant_a" / "20250101"
    new_dir = tmp_path / "tenant_a" / "20261020"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_file = old_dir / "0123456789ab_old.pdf"
    other = old_dir / "keep-me.txt"
    fresh = new_dir / "abcdef012345_new.pdf"
    for f in (old_file, other, fresh):
        f.write_bytes(b"%PDF")
    stale = time.time() - 40 * 86400
    os.utime(old_file, (stale, stale))
    os.utime(other, (stale, stale))
    monkeypatch.setenv("INGEST_UPLOAD_RETENTION_DAYS", "30")
    open_api._UPLOAD_PRUNE["last"] = 0.0
    assert open_api._prune_old_uploads(tmp_path) == 1
    assert not old_file.exists() and other.exists() and fresh.exists()
    # At most once an hour.
    assert open_api._prune_old_uploads(tmp_path) == 0
    monkeypatch.setenv("INGEST_UPLOAD_RETENTION_DAYS", "0")
    open_api._UPLOAD_PRUNE["last"] = 0.0
    assert open_api._prune_old_uploads(tmp_path) == 0


# --- 3. logout-all spares sessions issued right after it ------------------------------------------


def test_logout_all_spares_a_session_issued_in_the_same_second(monkeypatch, tmp_path):
    from webapi import admin_auth

    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "stage12-admin-token")
    monkeypatch.delenv("SESSION_REVOCATION_BACKEND", raising=False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    before, _ = admin_auth.issue_admin_session()
    admin_auth.revoke_all_admin_sessions()
    after, _ = admin_auth.issue_admin_session(now=time.time() + 0.01)
    assert not admin_auth._valid_admin_session(before, "stage12-admin-token")
    assert admin_auth._valid_admin_session(after, "stage12-admin-token")


# --- 4. request totals over workers ----------------------------------------------------------------


def test_request_totals_are_summed_over_worker_files(monkeypatch, tmp_path):
    from webapi import metrics_registry

    monkeypatch.setenv("METRICS_MULTIPROC_DIR", str(tmp_path))
    (tmp_path / "999-1.json").write_text(json.dumps({"counters": {}, "totals": {"requests": 5, "errors": 1}}), encoding="utf-8")
    base = metrics_registry.aggregate_totals()
    metrics_registry.note_request()
    metrics_registry.note_request()
    metrics_registry.note_error()
    totals = metrics_registry.aggregate_totals()
    assert totals == {"requests": base["requests"] + 2, "errors": base["errors"] + 1}
    assert totals["requests"] >= 7


def test_metrics_endpoint_reports_summed_totals(open_api, monkeypatch, tmp_path):
    monkeypatch.setenv("METRICS_MULTIPROC_DIR", str(tmp_path))
    (tmp_path / "999-1.json").write_text(json.dumps({"counters": {}, "totals": {"requests": 40, "errors": 3}}), encoding="utf-8")
    payload = TestClient(open_api.app).get("/metrics").json()
    assert payload["counters_scope"] == "all_workers"
    assert payload["total_requests"] >= 40 and payload["error_requests"] >= 3


# --- 5. parallel conversion -------------------------------------------------------------------------


def test_files_of_a_job_are_converted_in_parallel(monkeypatch, tmp_path):
    from webapi import ingestion_jobs

    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    broken = tmp_path / "broken.xlsx"
    a.write_text("項目,内容\n交通費,1200\n", encoding="utf-8")
    b.write_text("項目,内容\n宿泊費,8000\n", encoding="utf-8")
    broken.write_bytes(b"not a workbook")
    monkeypatch.setenv("INGEST_CONVERT_WORKERS", "2")
    if ingestion_jobs._convert_workers() < 2:
        pytest.skip("one CPU: parallel conversion is not used")
    out = ingestion_jobs._convert_files([a, b, broken], "default")
    assert out[a] and out[b] and isinstance(out[broken], Exception)
    assert "交通費" in out[a][0]["text"] and "宿泊費" in out[b][0]["text"]
    monkeypatch.setenv("INGEST_CONVERT_WORKERS", "1")
    sequential = ingestion_jobs._convert_files([a, b], "default")
    assert [r["text"] for r in sequential[a]] == [r["text"] for r in out[a]]


# --- 6. dependency pins ----------------------------------------------------------------------------------


def test_constraints_pin_pdfplumber_and_drop_pymupdf():
    lines = (ROOT / "constraints/linux-py312.txt").read_text(encoding="utf-8").splitlines()
    pins = {ln.split("==")[0].lower(): ln for ln in lines if "==" in ln and not ln.startswith("#")}
    assert "pymupdf" not in pins
    assert {"pdfplumber", "pdfminer.six", "pypdfium2"} <= set(pins)
