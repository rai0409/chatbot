import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import source_apply, source_scan

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}
STATIC = Path(__file__).resolve().parents[1] / "webapi" / "static"


class Fake:
    def __init__(self):
        self.ingests, self.deletes = [], []

    def ingest(self, paths, tenant, collection):
        self.ingests.append(([p.name for p in paths], tenant, collection))
        return {"ok": True, "status": "ok", "warnings": []}

    def delete(self, collection, tenant, documents):
        self.deletes.append((collection, tenant, list(documents)))
        return {"chunks": len(documents)}


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    ingest = tmp_path / "ingest"
    (ingest / "docs-a").mkdir(parents=True)
    (ingest / "docs-b").mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(ingest))
    monkeypatch.setenv("SOURCES_DIR", str(ingest / "state"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"pdf"}))
    fake = Fake()
    monkeypatch.setattr(source_apply, "default_ingest", fake.ingest)
    monkeypatch.setattr(source_apply, "default_delete", fake.delete)
    from webapi import main

    return TestClient(main.app), ingest, fake, tmp_path


def _audit(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _register(client, ingest, source_id="docs-a", tenant="tenant_a", headers=ADMIN, folder=None, **extra):
    body = {"source_id": source_id, "tenant_id": tenant, "path": str(ingest / (folder or source_id)), **extra}
    return client.post("/admin/sources", json=body, headers=headers)


def test_every_route_needs_the_admin_authentication(api):
    client, ingest, _fake, _tmp = api
    for method, url in (("get", "/admin/sources"), ("get", "/admin/sources/list"), ("post", "/admin/sources/x/scan"),
                        ("get", "/admin/sources/x/plan"), ("post", "/admin/sources/x/enable"), ("get", "/admin/sources/x/changes")):
        assert getattr(client, method)(url).status_code in {401, 403}, url
    assert client.post("/admin/sources", json={"source_id": "x", "tenant_id": "t"}).status_code in {401, 403}
    assert client.get("/admin/sources/list", headers={"x-admin-token": "wrong"}).status_code in {401, 403}


def test_register_list_and_the_response_never_shows_a_server_path(api):
    client, ingest, _fake, tmp_path = api
    created = _register(client, ingest, schedule_minutes=30)
    assert created.status_code == 200 and created.json()["collection"] == "src-docs-a" and created.json()["pending"] is None
    listed = client.get("/admin/sources/list", headers=ADMIN)
    assert [s["source_id"] for s in listed.json()["sources"]] == ["docs-a"]
    assert str(tmp_path) not in created.text and str(tmp_path) not in listed.text and "path" not in listed.json()["sources"][0]
    assert _register(client, ingest).status_code == 409
    assert _register(client, ingest, source_id="Bad Id").status_code == 400
    assert _register(client, ingest, source_id="out", folder="../outside").status_code == 400
    assert "source_created" in [e["action"] for e in _audit(tmp_path)]


def test_a_tenant_admin_sees_and_changes_only_its_own_sources(api):
    client, ingest, _fake, _tmp = api
    assert _register(client, ingest, "docs-a", "tenant_a", headers=TENANT_A).status_code == 200
    assert _register(client, ingest, "docs-b", "tenant_b", headers=ADMIN).status_code == 200
    assert _register(client, ingest, "docs-x", "tenant_b", headers=TENANT_A, folder="docs-b").status_code == 403
    assert [s["source_id"] for s in client.get("/admin/sources/list", headers=TENANT_A).json()["sources"]] == ["docs-a"]
    assert len(client.get("/admin/sources/list", headers=ADMIN).json()["sources"]) == 2
    for method, url in (("post", "/admin/sources/docs-b/scan"), ("get", "/admin/sources/docs-b/plan"), ("get", "/admin/sources/docs-b/changes"),
                        ("post", "/admin/sources/docs-b/disable"), ("post", "/admin/sources/docs-b/remove")):
        assert getattr(client, method)(url, headers=TENANT_A).status_code == 404, url  # the same as a source that does not exist
    assert client.post("/admin/sources/docs-b/apply", json={}, headers=TENANT_A).status_code == 404
    assert client.post("/admin/sources/docs-b/approve", json={"plan_sha256": "0" * 64}, headers=TENANT_A).status_code == 404
    assert client.post("/admin/sources/nope/scan", headers=ADMIN).status_code == 404


def test_scan_plan_apply_and_changes_through_the_api(api):
    client, ingest, fake, tmp_path = api
    for name in ("a.pdf", "b.pdf"):
        (ingest / "docs-a" / name).write_text("x", encoding="utf-8")
    _register(client, ingest)
    scan = client.post("/admin/sources/docs-a/scan", headers=ADMIN).json()
    assert scan["counts"] == {"added": 2, "modified": 0, "deleted": 0} and scan["total_files"] == 2 and scan["name_conflicts"] == 0
    assert "changes" not in scan and str(tmp_path) not in json.dumps(scan)
    plan = client.get("/admin/sources/docs-a/plan", headers=ADMIN).json()
    assert plan["added"] == ["a.pdf", "b.pdf"] and plan["guard"] == {"hold": False, "reasons": []} and len(plan["plan_sha256"]) == 64
    applied = client.post("/admin/sources/docs-a/apply", json={}, headers=ADMIN).json()
    assert applied["status"] == "applied" and applied["ingested"] == 2 and fake.ingests == [(["a.pdf", "b.pdf"], "tenant_a", "src-docs-a")]
    row = client.get("/admin/sources/list", headers=ADMIN).json()["sources"][0]
    assert row["pending"] == {"added": 0, "modified": 0, "deleted": 0} and row["state"]["last_apply_at"]
    entries = client.get("/admin/sources/docs-a/changes?limit=5", headers=ADMIN).json()["entries"]
    assert [e["kind"] for e in entries] == ["scan", "apply"]
    assert client.get("/admin/sources/docs-a/changes?limit=0", headers=ADMIN).status_code == 422
    actions = [e["action"] for e in _audit(tmp_path)]
    assert {"source_created", "source_scanned", "source_applied"} <= set(actions)


def test_a_mass_deletion_is_held_until_that_exact_plan_is_approved(api):
    client, ingest, fake, tmp_path = api
    for i in range(10):
        (ingest / "docs-a" / f"f{i}.pdf").write_text("x", encoding="utf-8")
    _register(client, ingest)
    client.post("/admin/sources/docs-a/scan", headers=ADMIN)
    client.post("/admin/sources/docs-a/apply", json={}, headers=ADMIN)
    for i in range(6):
        (ingest / "docs-a" / f"f{i}.pdf").unlink()
    client.post("/admin/sources/docs-a/scan", headers=ADMIN)
    row = client.get("/admin/sources/list", headers=ADMIN).json()["sources"][0]
    assert row["pending"]["deleted"] == 6 and row["guard"] == {"hold": True, "reasons": ["mass_delete"]}
    held = client.post("/admin/sources/docs-a/apply", json={}, headers=ADMIN).json()
    assert held["status"] == "held" and fake.deletes == []
    assert client.post("/admin/sources/docs-a/approve", json={"plan_sha256": "0" * 64}, headers=ADMIN).status_code == 400
    approved = client.post("/admin/sources/docs-a/approve", json={"plan_sha256": held["plan_sha256"]}, headers=ADMIN)
    assert approved.status_code == 200 and approved.json()["by"] == "installation"
    done = client.post("/admin/sources/docs-a/apply", json={"approval": held["plan_sha256"]}, headers=ADMIN).json()
    assert done["status"] == "applied" and done["approved_by"] == "installation" and len(fake.deletes[-1][2]) == 6
    assert "source_approved" in [e["action"] for e in _audit(tmp_path)]


def test_a_tenant_admin_is_recorded_by_its_reach(api):
    client, ingest, _fake, _tmp = api
    for i in range(10):
        (ingest / "docs-a" / f"f{i}.pdf").write_text("x", encoding="utf-8")
    _register(client, ingest, headers=TENANT_A)
    client.post("/admin/sources/docs-a/scan", headers=TENANT_A)
    client.post("/admin/sources/docs-a/apply", json={}, headers=TENANT_A)
    for i in range(6):
        (ingest / "docs-a" / f"f{i}.pdf").unlink()
    client.post("/admin/sources/docs-a/scan", headers=TENANT_A)
    sha = client.post("/admin/sources/docs-a/apply", json={}, headers=TENANT_A).json()["plan_sha256"]
    assert client.post("/admin/sources/docs-a/approve", json={"plan_sha256": sha}, headers=TENANT_A).json()["by"] == "tenant_a"


def test_an_unavailable_folder_is_reported_not_treated_as_empty(api):
    client, ingest, _fake, _tmp = api
    (ingest / "docs-a" / "a.pdf").write_text("x", encoding="utf-8")
    _register(client, ingest)
    client.post("/admin/sources/docs-a/scan", headers=ADMIN)
    (ingest / "docs-a").rename(ingest / "docs-a-gone")
    out = client.post("/admin/sources/docs-a/scan", headers=ADMIN)
    assert out.status_code == 200 and out.json()["error"] == "source_unavailable"
    row = client.get("/admin/sources/list", headers=ADMIN).json()["sources"][0]
    assert row["state"]["last_error"] == "source_unavailable" and row["pending"] == {"added": 1, "modified": 0, "deleted": 0}


def test_enable_disable_and_remove_keep_the_data(api):
    client, ingest, _fake, tmp_path = api
    _register(client, ingest)
    assert client.post("/admin/sources/docs-a/disable", headers=ADMIN).json()["enabled"] is False
    assert client.post("/admin/sources/docs-a/enable", headers=ADMIN).json()["enabled"] is True
    assert client.post("/admin/sources/docs-a/explode", headers=ADMIN).status_code == 404
    assert client.post("/admin/sources/docs-a/remove", headers=ADMIN).json() == {"removed": "docs-a"}
    assert client.get("/admin/sources/list", headers=ADMIN).json() == {"sources": []}
    assert client.post("/admin/sources/docs-a/remove", headers=ADMIN).status_code == 404
    assert {"source_enabled", "source_disabled", "source_removed"} <= {e["action"] for e in _audit(tmp_path)}


def test_a_connector_failure_is_a_502_without_its_details(api, monkeypatch):
    client, ingest, _fake, _tmp = api
    _register(client, ingest)
    from rag_core import source_sync

    def boom(source_id, now=None):
        raise RuntimeError("secret detail /srv/x")

    monkeypatch.setattr(source_sync, "scan_source", boom)
    out = client.post("/admin/sources/docs-a/scan", headers=ADMIN)
    assert out.status_code == 502 and "secret" not in out.text and "/srv" not in out.text


# --- the page ---------------------------------------------------------------------------------------------------


def test_the_page_is_served_with_the_admin_menu_and_the_menu_links_to_it(api):
    client, _ingest, _fake, _tmp = api
    page = client.get("/admin/sources", headers=ADMIN)
    assert page.status_code == 200 and "文書ソース" in page.text and 'aria-label="管理メニュー"' in page.text
    assert '<!--admin-nav-->' not in page.text
    assert '<a href="/admin/sources">文書ソース</a>' in (STATIC / "admin_nav.html").read_text(encoding="utf-8")


def test_the_page_inserts_text_only_with_textcontent_and_asks_before_a_held_apply():
    html = (STATIC / "sources.html").read_text(encoding="utf-8")
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html
    assert "textContent" in html and "/admin/sources/list" in html and "approve" in html and "confirm(" in html
    for label in ("走査", "計画", "反映", "履歴", "登録解除"):
        assert label in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_inline_script_of_the_page_is_valid_javascript():
    html = (STATIC / "sources.html").read_text(encoding="utf-8")
    scripts = [s for s in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S) if s.strip()]
    assert len(scripts) == 1
    done = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=scripts[0],
                          text=True, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
