import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import contract_notify, contracts
from rag_core.connectors import outlook
from rag_core.connectors import sharepoint as sp

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}
STATIC = Path(__file__).resolve().parents[1] / "webapi" / "static"


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path / "contracts"))
    monkeypatch.delenv("CONTRACT_REMINDER_DAYS", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    from webapi import main

    return TestClient(main.app), tmp_path


def _audit(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _extraction(**fields):
    base = {"counterparty": {"value": "株式会社サンプル", "confidence": 0.9, "page": 1, "text": "甲 株式会社サンプル"},
            "title": {"value": "業務委託契約"}, "end_date": {"value": "2027-03-31", "page": 3, "text": "令和9年3月31日まで"}}
    base.update({k: {"value": v} for k, v in fields.items()})
    return {"schema": contracts.EXTRACT_SCHEMA, "fields": base}


def _import(client, contract_id="c1", tenant="tenant_a", headers=ADMIN, **fields):
    return client.post("/admin/contracts/import", headers=headers, json={
        "tenant_id": tenant, "contract_id": contract_id, "source_doc": "業務委託契約.pdf", "extracted": _extraction(**fields)})


def _confirm_all(client, contract_id="c1", tenant="tenant_a", names=("counterparty", "end_date", "auto_renewal", "renewal_notice_days", "contact_email")):
    for name in names:
        assert client.post(f"/admin/contracts/{contract_id}/confirm", headers=ADMIN, json={"tenant_id": tenant, "name": name}).status_code == 200


def test_every_route_needs_the_admin_authentication(api):
    client, _tmp = api
    for method, url in (("get", "/admin/contracts"), ("get", "/admin/contracts/list"), ("get", "/admin/contracts/upcoming"), ("get", "/admin/contracts/c1")):
        assert getattr(client, method)(url).status_code in {401, 403}, url
    for url, body in (("/admin/contracts/import", {"contract_id": "c1", "extracted": {}}), ("/admin/contracts/notify", {}),
                      ("/admin/contracts/c1/field", {"name": "title", "value": "x"}), ("/admin/contracts/c1/confirm", {"name": "title"}),
                      ("/admin/contracts/c1/draft", {"kind": "end", "mailbox": "a@example.co.jp"})):
        assert client.post(url, json=body).status_code in {401, 403}, url


def test_an_import_is_stored_unconfirmed_and_bad_values_are_reported(api):
    client, tmp_path = api
    out = _import(client, auto_renewal=True, renewal_notice_days=-5, amount="abc")
    assert out.status_code == 200
    body = out.json()
    assert body["contract"]["status"] == "needs_review" and body["contract"]["unconfirmed"] == 4  # counterparty, title, end_date, auto_renewal
    assert body["rejected"] == {"renewal_notice_days": "not stored: outside 0..3650", "amount": "not stored: not a number"}
    detail = client.get("/admin/contracts/c1", params={"tenant_id": "tenant_a"}, headers=ADMIN).json()
    assert detail["fields"]["end_date"] == {**detail["fields"]["end_date"], "state": "extracted", "value": "2027-03-31",
                                            "evidence": {"page": 3, "text": "令和9年3月31日まで"}}
    assert detail["deadlines"][0]["kind"] == "end" and detail["deadlines"][0]["verified"] is False and detail["required"] == ["counterparty", "end_date"]
    logged = [e for e in _audit(tmp_path) if e["action"] == "contract_imported"]
    assert logged and "株式会社サンプル" not in json.dumps(logged, ensure_ascii=False) and logged[0]["rejected"] == ["amount", "renewal_notice_days"]
    bad = client.post("/admin/contracts/import", headers=ADMIN, json={"tenant_id": "tenant_a", "contract_id": "c2", "extracted": {"schema": "x"}})
    assert bad.status_code == 400 and client.post("/admin/contracts/import", headers=ADMIN, json={"tenant_id": "tenant_a", "contract_id": "Bad Id", "extracted": _extraction()}).status_code == 400


def test_a_person_confirms_and_corrects_and_the_status_and_deadlines_follow(api):
    client, tmp_path = api
    _import(client, auto_renewal=True, renewal_notice_days=90)
    done = client.post("/admin/contracts/c1/confirm", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "counterparty"})
    assert done.status_code == 200 and done.json()["status"] == "needs_review"
    fixed = client.post("/admin/contracts/c1/field", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "end_date", "value": "令和15年4月30日", "note": "原本"})  # far in the future: the status does not depend on the day the test runs
    assert fixed.status_code == 200 and fixed.json()["status"] == "active"
    detail = client.get("/admin/contracts/c1", params={"tenant_id": "tenant_a"}, headers=ADMIN).json()
    assert detail["fields"]["end_date"]["value"] == "2033-04-30" and detail["fields"]["end_date"]["state"] == "corrected"
    assert detail["fields"]["end_date"]["updated_by"] == "installation" and [h["action"] for h in detail["history"]][-2:] == ["confirmed", "corrected"]
    assert {"contract_field_confirmed", "contract_field_set"} <= {e["action"] for e in _audit(tmp_path)}
    assert client.post("/admin/contracts/c1/field", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "end_date", "value": "2027-02-30"}).status_code == 400
    assert client.post("/admin/contracts/c1/confirm", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "amount"}).status_code == 400  # nothing to confirm
    assert client.post("/admin/contracts/c1/confirm", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "nope"}).status_code == 400
    assert client.post("/admin/contracts/zzz/confirm", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "title"}).status_code == 404
    assert client.get("/admin/contracts/zzz", params={"tenant_id": "tenant_a"}, headers=ADMIN).status_code == 404


def test_the_list_and_the_upcoming_deadlines_show_what_is_still_unconfirmed(api):
    client, _tmp = api
    _import(client)
    rows = client.get("/admin/contracts/list", params={"tenant_id": "tenant_a"}, headers=ADMIN).json()["contracts"]
    assert len(rows) == 1 and rows[0]["contract_id"] == "c1" and rows[0]["counterparty"] == "株式会社サンプル" and rows[0]["unconfirmed"] == 3
    assert rows[0]["nearest_deadline"]["kind"] == "end" and rows[0]["nearest_deadline"]["verified"] is False
    assert client.get("/admin/contracts/list", params={"tenant_id": "tenant_b"}, headers=ADMIN).json() == {"contracts": []}
    far = client.get("/admin/contracts/upcoming", params={"tenant_id": "tenant_a", "within_days": 0}, headers=ADMIN).json()["deadlines"]
    assert isinstance(far, list)
    assert client.get("/admin/contracts/upcoming", params={"tenant_id": "tenant_a", "within_days": 9999}, headers=ADMIN).status_code == 422
    assert client.get("/admin/contracts/list", params={"tenant_id": "../x"}, headers=ADMIN).status_code == 400


def test_a_tenant_admin_reaches_only_its_own_contracts(api):
    client, _tmp = api
    _import(client, tenant="tenant_a", headers=TENANT_A)
    _import(client, "c9", tenant="tenant_b", headers=ADMIN)
    assert [c["contract_id"] for c in client.get("/admin/contracts/list", params={"tenant_id": "tenant_a"}, headers=TENANT_A).json()["contracts"]] == ["c1"]
    for method, url, body in (("get", "/admin/contracts/list?tenant_id=tenant_b", None), ("get", "/admin/contracts/upcoming?tenant_id=tenant_b", None),
                              ("get", "/admin/contracts/c9?tenant_id=tenant_b", None),
                              ("post", "/admin/contracts/c9/confirm", {"tenant_id": "tenant_b", "name": "title"}),
                              ("post", "/admin/contracts/c9/field", {"tenant_id": "tenant_b", "name": "title", "value": "x"}),
                              ("post", "/admin/contracts/notify", {"tenant_id": "tenant_b"}),
                              ("post", "/admin/contracts/c9/draft", {"tenant_id": "tenant_b", "kind": "end", "mailbox": "a@example.co.jp"}),
                              ("post", "/admin/contracts/import", {"tenant_id": "tenant_b", "contract_id": "c8", "extracted": _extraction()})):
        resp = getattr(client, method)(url, headers=TENANT_A) if body is None else client.post(url, json=body, headers=TENANT_A)
        assert resp.status_code == 403, url
    assert client.post("/admin/contracts/c1/confirm", headers=TENANT_A, json={"tenant_id": "tenant_a", "name": "title"}).status_code == 200


def test_notify_lists_by_default_and_sends_once_when_asked(api, monkeypatch):
    client, tmp_path = api
    _import(client)
    _confirm_all(client, names=("counterparty", "end_date"))
    client.post("/admin/contracts/c1/field", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "end_date", "value": "2000-01-05"})
    sent = []
    monkeypatch.setattr(contract_notify, "default_send", lambda subject, body: sent.append((subject, body)) or {"ok": True})
    # a deadline in the past is outside the 30-day overdue window: nothing is due
    assert client.post("/admin/contracts/notify", headers=ADMIN, json={"tenant_id": "tenant_a"}).json()["due"] == 0
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=5)).isoformat()
    client.post("/admin/contracts/c1/field", headers=ADMIN, json={"tenant_id": "tenant_a", "name": "end_date", "value": soon})
    dry = client.post("/admin/contracts/notify", headers=ADMIN, json={"tenant_id": "tenant_a"}).json()
    assert dry["dry_run"] is True and dry["due"] == 1 and dry["results"][0]["status"] == "due" and sent == []
    real = client.post("/admin/contracts/notify", headers=ADMIN, json={"tenant_id": "tenant_a", "dry_run": False}).json()
    assert real["sent"] == 1 and len(sent) == 1 and "株式会社サンプル" in sent[0][1] and "株式会社サンプル" not in json.dumps(real, ensure_ascii=False)
    assert client.post("/admin/contracts/notify", headers=ADMIN, json={"tenant_id": "tenant_a", "dry_run": False}).json()["due"] == 0
    runs = [e for e in _audit(tmp_path) if e["action"] == "contract_notify_run"]
    assert [r["sent"] for r in runs] == [0, 0, 1, 0] and "株式会社サンプル" not in json.dumps(runs, ensure_ascii=False)


class Graph:
    def __init__(self, status=201, payload=None):
        self.status, self.payload, self.calls = status, payload or {"id": "AAMk-1", "webLink": "https://outlook.example/d/1"}, []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, body))
        return self.status, json.dumps(self.payload).encode("utf-8"), {}


def _ready(client):
    _import(client, auto_renewal=True, renewal_notice_days=90, contact_email="taro@example.co.jp")
    _confirm_all(client)


def test_a_draft_is_created_only_for_a_confirmed_address_and_deadline(api, monkeypatch):
    client, tmp_path = api
    graph = Graph()
    monkeypatch.setattr(outlook, "default_client", lambda: sp.GraphClient(lambda: "tok-123", graph))
    _import(client, auto_renewal=True, renewal_notice_days=90, contact_email="taro@example.co.jp")
    body = {"tenant_id": "tenant_a", "kind": "non_renewal_notice", "mailbox": "legal@example.co.jp"}
    refused = client.post("/admin/contracts/c1/draft", headers=ADMIN, json=body)
    assert refused.status_code == 400 and "not been confirmed" in refused.json()["detail"] and graph.calls == []
    _confirm_all(client)
    made = client.post("/admin/contracts/c1/draft", headers=ADMIN, json=body)
    assert made.status_code == 200 and made.json()["draft_id"] == "AAMk-1" and made.json()["date"] == "2026-12-31"
    assert len(graph.calls) == 1 and graph.calls[0][0] == "POST" and "send" not in graph.calls[0][1].lower()
    assert "contract_draft_created" in [e["action"] for e in _audit(tmp_path)]
    assert client.post("/admin/contracts/c1/draft", headers=ADMIN, json={**body, "kind": "bogus"}).status_code == 400
    assert client.post("/admin/contracts/c1/draft", headers=ADMIN, json={**body, "mailbox": "not a mailbox"}).status_code == 400


def test_draft_failures_are_reported_without_details(api, monkeypatch):
    client, _tmp = api
    _ready(client)
    body = {"tenant_id": "tenant_a", "kind": "end", "mailbox": "legal@example.co.jp"}
    for name in ("ACCESS_TOKEN", "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.delenv("OUTLOOK_" + name, raising=False)
    unconfigured = client.post("/admin/contracts/c1/draft", headers=ADMIN, json=body)
    assert unconfigured.status_code == 409 and "mail access is not configured" in unconfigured.json()["detail"]
    monkeypatch.setattr(outlook, "default_client", lambda: sp.GraphClient(lambda: "tok-123", Graph(status=500, payload={"error": "boom tok-123"})))
    failed = client.post("/admin/contracts/c1/draft", headers=ADMIN, json=body)
    assert failed.status_code == 502 and "tok-123" not in failed.text and "boom" not in failed.text


def test_the_page_is_served_with_the_menu_and_the_menu_links_to_it(api):
    client, _tmp = api
    page = client.get("/admin/contracts", headers=ADMIN)
    assert page.status_code == 200 and "契約管理" in page.text and 'aria-label="管理メニュー"' in page.text
    assert '<a href="/admin/contracts">契約管理</a>' in (STATIC / "admin_nav.html").read_text(encoding="utf-8")


def test_the_page_inserts_text_only_with_textcontent_and_asks_before_sending():
    html = (STATIC / "contracts.html").read_text(encoding="utf-8")
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html and "textContent" in html
    for needle in ("/admin/contracts/list", "/admin/contracts/notify", "confirm(", "未確認", "契約書の文言が優先", "送信はしていません"):
        assert needle in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_inline_script_of_the_page_is_valid_javascript():
    html = (STATIC / "contracts.html").read_text(encoding="utf-8")
    scripts = [s for s in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S) if s.strip()]
    assert len(scripts) == 1
    done = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=scripts[0],
                          text=True, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
