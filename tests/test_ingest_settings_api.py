"""The console's ingestion settings route. Synthetic data only."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import ingest_settings as ist

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT = {"x-admin-token": TENANT_TOKEN}


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    present = {s.name: os.environ[s.name] for s in ist.SETTINGS if s.name in os.environ}
    for s in ist.SETTINGS:
        monkeypatch.delenv(s.name, raising=False)
    ist._ORIGINAL.clear(); ist._APPLIED.clear(); ist._SEEN["rev"] = None
    from webapi import main

    yield TestClient(main.app)
    for s in ist.SETTINGS:
        os.environ.pop(s.name, None)
    os.environ.update(present)
    ist._ORIGINAL.clear(); ist._APPLIED.clear(); ist._SEEN["rev"] = None


def test_the_route_needs_authentication(api):
    assert api.get("/admin/ingestion/settings").status_code in (401, 403)
    assert api.post("/admin/ingestion/settings", json={}).status_code in (401, 403, 422)


def test_the_list_has_every_setting_with_its_source_and_the_revision(api):
    body = api.get("/admin/ingestion/settings", headers=ADMIN).json()
    assert {s["name"] for s in body["settings"]} == {s.name for s in ist.SETTINGS}
    assert body["can_edit"] is True and isinstance(body["rev"], int) and body["groups"] == list(ist.GROUPS)
    row = next(s for s in body["settings"] if s["name"] == "INGEST_UPLOAD_MAX_CONCURRENT")
    assert row["source"] == "default" and row["value"] == 4 and row["minimum"] == 0


def test_a_tenant_administrator_can_read_but_not_change(api):
    body = api.get("/admin/ingestion/settings", headers=TENANT).json()
    assert body["can_edit"] is False
    refused = api.post("/admin/ingestion/settings", headers=TENANT,
                       json={"name": "INGEST_UPLOAD_MAX_CONCURRENT", "value": 8, "reason": "試験", "actor": "山田太郎"})
    assert refused.status_code == 403


def test_a_change_is_applied_listed_and_audited(api):
    done = api.post("/admin/ingestion/settings", headers=ADMIN,
                    json={"name": "INGEST_UPLOAD_MAX_CONCURRENT", "value": 8, "reason": "同時利用が増えた", "actor": "山田太郎"})
    assert done.status_code == 200
    assert os.environ["INGEST_UPLOAD_MAX_CONCURRENT"] == "8"
    body = api.get("/admin/ingestion/settings", headers=ADMIN).json()
    row = next(s for s in body["settings"] if s["name"] == "INGEST_UPLOAD_MAX_CONCURRENT")
    assert row["value"] == 8 and row["source"] == "override" and row["by"] == "山田太郎" and row["reason"] == "同時利用が増えた"
    assert body["history"][0]["name"] == "INGEST_UPLOAD_MAX_CONCURRENT" and body["history"][0]["after"] == 8


def test_a_reset_removes_the_override(api):
    api.post("/admin/ingestion/settings", headers=ADMIN, json={"name": "PDF_TABLES", "value": False, "reason": "誤検出", "actor": "山田太郎"})
    assert os.environ["PDF_TABLES"] == "false"
    done = api.post("/admin/ingestion/settings", headers=ADMIN, json={"name": "PDF_TABLES", "reset": True, "reason": "戻す", "actor": "山田太郎"})
    assert done.status_code == 200 and "PDF_TABLES" not in os.environ


@pytest.mark.parametrize("payload, status", [
    ({"name": "INGEST_UPLOAD_MAX_CONCURRENT", "value": -3, "reason": "x", "actor": "山田太郎"}, 400),
    ({"name": "NOT_A_SETTING", "value": 1, "reason": "x", "actor": "山田太郎"}, 400),
    ({"name": "INGEST_UPLOAD_MAX_CONCURRENT", "value": 3, "reason": "", "actor": "山田太郎"}, 400),
    ({"name": "INGEST_UPLOAD_MAX_CONCURRENT", "value": 3, "reason": "x", "actor": ""}, 400),
    ({"name": "INGEST_UPLOAD_MAX_CONCURRENT", "reason": "x", "actor": "山田太郎"}, 400),  # no value and no reset
    ({"name": "JA_CHUNK_TARGET_FAQ_MIN_CHARS", "value": 4000, "reason": "x", "actor": "山田太郎"}, 400),  # above the maximum
])
def test_invalid_changes_are_refused_with_400(api, payload, status):
    response = api.post("/admin/ingestion/settings", headers=ADMIN, json=payload)
    assert response.status_code == status
    assert ist.overrides() == {}


def test_a_stale_revision_is_a_409(api):
    revision = api.get("/admin/ingestion/settings", headers=ADMIN).json()["rev"]
    api.post("/admin/ingestion/settings", headers=ADMIN, json={"name": "PDF_TABLES", "value": False, "reason": "a", "actor": "山田太郎"})
    stale = api.post("/admin/ingestion/settings", headers=ADMIN,
                     json={"name": "PDF_TABLES", "value": True, "reason": "b", "actor": "佐藤次郎", "expected_rev": revision})
    assert stale.status_code == 409


def test_changing_a_worker_setting_restarts_the_conversion_workers(api, monkeypatch):
    from rag_core.document_converters import isolated

    stopped = []
    monkeypatch.setattr(isolated, "shutdown", lambda: stopped.append(True))
    api.post("/admin/ingestion/settings", headers=ADMIN,
             json={"name": "INGEST_CONVERT_MAX_MEMORY_MB", "value": 4096, "reason": "メモリ制限", "actor": "山田太郎"})
    assert stopped == [True]
    stopped.clear()
    api.post("/admin/ingestion/settings", headers=ADMIN, json={"name": "PDF_TABLES", "value": False, "reason": "x", "actor": "山田太郎"})
    assert stopped == []


def test_the_upload_limit_follows_a_console_change_in_the_same_request_cycle(api):
    api.post("/admin/ingestion/settings", headers=ADMIN, json={"name": "INGEST_UPLOAD_MAX_BYTES", "value": 2 * 1024 * 1024, "reason": "小さくする", "actor": "山田太郎"})
    from webapi import security_middleware

    assert security_middleware.max_upload_bytes() == 2 * 1024 * 1024


def test_the_raw_document_route_accepts_and_validates_job_options(api, monkeypatch):
    from webapi import ingestion_jobs

    seen = {}

    def fake(inputs, **kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(ingestion_jobs, "run_raw_document_job", fake)
    monkeypatch.setattr(ingestion_jobs, "is_production_collection", lambda c: False)
    body = {"inputs": ["x.pdf"], "expected_tenant": "default", "collection": "staging_x", "execute": False}
    assert api.post("/admin/ingestion/raw-documents", headers=ADMIN, json={**body, "doc_type": "faq", "ocr": True}).status_code == 200
    assert seen["doc_type"] == "faq" and seen["ocr"] is True
    bad = api.post("/admin/ingestion/raw-documents", headers=ADMIN, json={**body, "doc_type": "table"})
    assert bad.status_code == 400
