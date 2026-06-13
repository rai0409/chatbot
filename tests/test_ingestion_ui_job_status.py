from __future__ import annotations

import json

from fastapi.testclient import TestClient

import config
from webapi import ingestion_jobs, main


SYNTH = "eval/cases/qa_pair_chunks.jsonl"  # existing synthetic, non-customer data


def _admin(monkeypatch, *, enabled=True):
    for var in ("ADMIN_AUTH_ENABLED", "ADMIN_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    if enabled:
        monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
        monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    ingestion_jobs.reset()


def _h(tok="admin-secret"):
    return {"X-Admin-Token": tok}


# --- module: dry-run + production refusal ----------------------------------


def test_module_dry_run_no_issues_on_synthetic(monkeypatch):
    ingestion_jobs.reset()
    rec = ingestion_jobs.run_dry_run([SYNTH], collection="pilot_v1")
    assert rec["ok"] is True
    assert rec["status"] == "ok"
    assert rec["mode"] == "dry_run"


def test_module_refuses_production_collection(monkeypatch):
    ingestion_jobs.reset()
    for name in (None, "", "default", config.VECTORSTORE_COLLECTION_NAME):
        assert ingestion_jobs.is_production_collection(name) is True
    import pytest
    with pytest.raises(ValueError):
        ingestion_jobs.run_dry_run([SYNTH], collection="default")


# --- endpoints: admin-gated ------------------------------------------------


def test_endpoints_require_admin(monkeypatch):
    _admin(monkeypatch, enabled=True)
    client = TestClient(main.app)
    assert client.post("/admin/ingestion/dry-run", json={"inputs": [SYNTH]}).status_code == 401
    assert client.post("/admin/ingestion/dry-run", json={"inputs": [SYNTH]}, headers=_h("nope")).status_code == 403
    assert client.get("/admin/ingestion/jobs").status_code == 401
    assert client.get("/admin/ingestion", headers=_h()).status_code == 200  # page serves with admin


def test_dry_run_endpoint_and_job_status(monkeypatch):
    _admin(monkeypatch, enabled=True)
    client = TestClient(main.app)
    res = client.post("/admin/ingestion/dry-run", json={"inputs": [SYNTH], "collection": "pilot_v1"}, headers=_h())
    assert res.status_code == 200
    job = res.json()
    assert job["ok"] is True and job["mode"] == "dry_run"
    jid = job["job_id"]
    status = client.get(f"/admin/ingestion/jobs/{jid}", headers=_h())
    assert status.status_code == 200 and status.json()["job_id"] == jid
    listing = client.get("/admin/ingestion/jobs", headers=_h()).json()["jobs"]
    assert any(j["job_id"] == jid for j in listing)


def test_dry_run_endpoint_refuses_production_collection(monkeypatch):
    _admin(monkeypatch, enabled=True)
    client = TestClient(main.app)
    r = client.post("/admin/ingestion/dry-run", json={"inputs": [SYNTH], "collection": "default"}, headers=_h())
    assert r.status_code == 400


def test_dry_run_requires_inputs(monkeypatch):
    _admin(monkeypatch, enabled=True)
    client = TestClient(main.app)
    assert client.post("/admin/ingestion/dry-run", json={"inputs": []}, headers=_h()).status_code == 400


def test_no_secret_or_raw_doc_in_job_output(monkeypatch):
    _admin(monkeypatch, enabled=True)
    client = TestClient(main.app)
    job = client.post("/admin/ingestion/dry-run", json={"inputs": [SYNTH], "collection": "pilot_v1"}, headers=_h()).json()
    blob = json.dumps(job, ensure_ascii=False)
    # job carries counts + safe metadata, not raw document text or secrets
    assert "admin-secret" not in blob
    for forbidden in ("sk-", "Bearer ", "X-Api-Key"):
        assert forbidden not in blob
    assert "issue_counts" in blob
