"""Backups from the console: list, run now, status. The script is replaced by a fake. Synthetic data only."""
from __future__ import annotations

import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

import config

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT = {"x-admin-token": TENANT_TOKEN}


def _script(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    from webapi import backups, main

    backups._JOBS.clear()
    monkeypatch.setattr(backups, "script_path", lambda: _script(tmp_path / "fake_backup.sh", 'out=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; *) shift;; esac; done; mkdir -p "$out"; echo data > "$out/chatbot_backup_20261101T090000Z.tar.gz"; echo "done"'))
    return TestClient(main.app), backups


def _wait(client, job_id, headers=ADMIN):
    for _ in range(100):
        job = client.get(f"/admin/backups/jobs/{job_id}", headers=headers).json()
        if job["status"] in ("ok", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("the backup job did not finish")


def test_the_routes_need_the_installation_administrator(api):
    client, _ = api
    assert client.get("/admin/backups").status_code in (401, 403)
    assert client.get("/admin/backups", headers=TENANT).status_code == 403
    assert client.post("/admin/backups", headers=TENANT, json={"actor": "山田太郎"}).status_code == 403


def test_an_empty_directory_lists_nothing(api):
    client, _ = api
    body = client.get("/admin/backups", headers=ADMIN).json()
    assert body["archives"] == [] and body["running"] is None and body["available"] is True


def test_a_backup_runs_in_the_background_and_shows_up_in_the_list(api):
    client, _ = api
    started = client.post("/admin/backups", headers=ADMIN, json={"actor": "山田太郎"})
    assert started.status_code == 202
    job = _wait(client, started.json()["job_id"])
    assert job["status"] == "ok" and "done" in job["output"]
    archives = client.get("/admin/backups", headers=ADMIN).json()["archives"]
    assert [a["name"] for a in archives] == ["chatbot_backup_20261101T090000Z.tar.gz"] and archives[0]["bytes"] > 0


def test_only_one_backup_runs_at_a_time(api, tmp_path, monkeypatch):
    client, backups = api
    monkeypatch.setattr(backups, "script_path", lambda: _script(tmp_path / "slow.sh", "sleep 1; echo done"))
    first = client.post("/admin/backups", headers=ADMIN, json={"actor": "山田太郎"})
    assert first.status_code == 202
    second = client.post("/admin/backups", headers=ADMIN, json={"actor": "佐藤次郎"})
    assert second.status_code == 409
    assert client.get("/admin/backups", headers=ADMIN).json()["running"] == first.json()["job_id"]
    _wait(client, first.json()["job_id"])


def test_a_failing_script_is_an_error_with_its_output(api, tmp_path, monkeypatch):
    client, backups = api
    monkeypatch.setattr(backups, "script_path", lambda: _script(tmp_path / "bad.sh", 'echo "ERROR: disk full" >&2; exit 3'))
    job = _wait(client, client.post("/admin/backups", headers=ADMIN, json={"actor": "山田太郎"}).json()["job_id"])
    assert job["status"] == "error" and "disk full" in job["output"] and job["exit_code"] == 3


def test_a_name_is_required_and_the_start_is_audited(api):
    client, _ = api
    assert client.post("/admin/backups", headers=ADMIN, json={"actor": ""}).status_code == 400
    job_id = client.post("/admin/backups", headers=ADMIN, json={"actor": "山田太郎"}).json()["job_id"]
    _wait(client, job_id)
    audit = (os.path.join(config.RUNS_DIR, "audit", "events.jsonl"))
    assert os.path.exists(audit) and "backup_started" in open(audit, encoding="utf-8").read()


def test_a_missing_script_says_so(api, tmp_path, monkeypatch):
    client, backups = api
    monkeypatch.setattr(backups, "script_path", lambda: tmp_path / "nope.sh")
    body = client.get("/admin/backups", headers=ADMIN).json()
    assert body["available"] is False
    assert client.post("/admin/backups", headers=ADMIN, json={"actor": "山田太郎"}).status_code == 501


def test_the_output_is_bounded_and_secrets_in_it_are_masked(api, tmp_path, monkeypatch):
    client, backups = api
    monkeypatch.setattr(backups, "script_path", lambda: _script(tmp_path / "noisy.sh", 'for i in $(seq 1 3000); do echo "line $i"; done; echo "OPENAI_API_KEY=sk-secret-value"'))
    job = _wait(client, client.post("/admin/backups", headers=ADMIN, json={"actor": "山田太郎"}).json()["job_id"])
    assert len(job["output"]) <= backups.MAX_OUTPUT_CHARS and "sk-secret-value" not in job["output"]


def test_an_unknown_job_is_404(api):
    client, _ = api
    assert client.get("/admin/backups/jobs/nope", headers=ADMIN).status_code == 404
