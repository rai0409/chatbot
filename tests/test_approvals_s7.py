"""S7: turning an external integration on needs a second, different person."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import approvals, integrations, runtime_settings

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["slack", "sharepoint"])
    monkeypatch.setattr(config, "INTEGRATION_APPROVALS_REQUIRED", 2)
    monkeypatch.setattr(config, "APPROVAL_REQUIRE_SSO", False)
    monkeypatch.setattr(config, "APPROVAL_EXPIRY_HOURS", 72.0)
    from webapi import main

    return TestClient(main.app), tmp_path


def _actions(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    return [json.loads(line).get("action") for line in path.read_text(encoding="utf-8").splitlines()]


def _ask(client, name="slack", actor="山田太郎", headers=ADMIN, **extra):
    return client.post("/admin/system/switches", headers=headers, json={"name": name, "enabled": True, "reason": "team channel", "actor": actor, **extra})


def _decide(client, item_id, decision, actor, headers=ADMIN, reason=""):
    return client.post(f"/admin/system/approvals/{item_id}/{decision}", headers=headers, json={"actor": actor, "reason": reason})


# --- who is the same person ------------------------------------------------------------------------------------------------------------------


def test_one_person_is_recognised_by_identifier_when_both_have_one_else_by_name():
    a, b = {"name": "山田太郎", "id": "aaaa", "verified": True}, {"name": "別の名前", "id": "aaaa", "verified": True}
    assert approvals.same_person(a, b)  # the same identifier: the same person, whatever name was typed
    assert not approvals.same_person(a, {"name": "山田太郎", "id": "bbbb", "verified": True})  # two identifiers: two people
    assert approvals.same_person({"name": "山田 太郎", "id": None}, {"name": "山田太郎", "id": None})  # spaces and case do not make a second person
    assert approvals.same_person({"name": "Taro_Yamada", "id": None}, {"name": " taro_yamada ", "id": None})
    assert not approvals.same_person({"name": "山田太郎", "id": None}, {"name": "田中太郎", "id": None})
    assert approvals.same_person({"name": "山田太郎", "id": "aaaa"}, {"name": "山田太郎", "id": None})  # one is not identified: the names decide


# --- the request and the decision ------------------------------------------------------------------------------------------------------------------------


def test_turning_on_is_a_request_that_changes_nothing_until_a_different_person_approves(api, monkeypatch):
    client, tmp_path = api
    assert client.get("/admin/system/status", headers=ADMIN).json()["approvals"] == {"required": 2, "pending": 0, "require_sso": False}
    assert client.post("/admin/system/switches", headers=ADMIN, json={"name": "slack", "enabled": True, "reason": "x"}).status_code == 400  # no name
    asked = _ask(client)
    assert asked.status_code == 202 and asked.json()["pending"]["status"] == "pending" and asked.json()["approvals"]["pending"] == 1
    assert integrations.switch_on("slack") is False  # nothing changed yet
    item_id = asked.json()["pending"]["id"]
    assert _ask(client, actor="田中太郎").status_code == 409  # the same change is already waiting
    same = _decide(client, item_id, "approve", "山田 太郎")
    assert same.status_code == 400 and "cannot approve or reject their own" in same.json()["detail"] and integrations.switch_on("slack") is False
    done = _decide(client, item_id, "approve", "田中太郎", reason="確認した")
    assert done.status_code == 200 and done.json()["approval"]["status"] == "approved" and integrations.switch_on("slack") is True
    change = runtime_settings.history(1)[0]
    assert change["by"] == "山田太郎 (asked) / 田中太郎 (approved)" and change["reason"] == "team channel"  # both names are on the change
    assert {"integration_enable_requested", "integration_enable_approved"} <= set(_actions(tmp_path))
    again = _decide(client, item_id, "approve", "佐藤次郎")
    assert again.status_code == 400 and "already approved" in again.json()["detail"]


def test_a_request_can_be_rejected_by_another_person_and_cancelled_only_by_who_asked(api):
    client, _tmp = api
    first = _ask(client).json()["pending"]["id"]
    assert _decide(client, first, "cancel", "田中太郎").status_code == 400  # not theirs to cancel
    assert _decide(client, first, "reject", "山田太郎").status_code == 400  # rejecting your own request is cancelling it
    assert _decide(client, first, "reject", "田中太郎", reason="理由が足りない").json()["approval"]["status"] == "rejected"
    second = _ask(client).json()["pending"]["id"]  # the rejected one no longer blocks a new request
    cancelled = _decide(client, second, "cancel", "山田太郎")
    assert cancelled.json()["approval"]["status"] == "cancelled" and integrations.switch_on("slack") is False
    assert _decide(client, "apr-missing", "approve", "田中太郎").status_code == 404
    assert _decide(client, second, "dance", "田中太郎").status_code == 404
    listed = client.get("/admin/system/approvals", headers=ADMIN).json()
    assert [a["status"] for a in listed["approvals"]] == ["cancelled", "rejected"] and client.get("/admin/system/approvals?status=pending", headers=ADMIN).json()["approvals"] == []
    assert client.get("/admin/system/approvals?status=nonsense", headers=ADMIN).status_code == 400


def test_a_request_expires_and_cannot_be_approved_afterwards(api, monkeypatch):
    client, _tmp = api
    item_id = _ask(client).json()["pending"]["id"]
    late = datetime.now(timezone.utc) + timedelta(hours=73)
    assert approvals.list_items(now=late)[0]["status"] == "expired" and approvals.pending_count(now=late) == 0
    with pytest.raises(approvals.ApprovalError, match="already expired"):
        approvals.decide(item_id, "approve", decided_by={"name": "田中太郎", "id": None}, now=late)
    assert integrations.switch_on("slack") is False
    assert _ask(client).status_code == 202  # an expired request does not block a new one


def test_turning_off_and_taking_a_tenant_out_never_wait_for_anyone(api):
    client, _tmp = api
    integrations.set_switch("slack", True, by="a", reason="on")
    off = client.post("/admin/system/switches", headers=ADMIN, json={"name": "slack", "enabled": False, "reason": "stop"})
    assert off.status_code == 200 and integrations.switch_on("slack") is False
    out = client.post("/admin/system/switches", headers=TENANT_A, json={"name": "slack", "enabled": False, "reason": "not us", "tenant_id": "tenant_a"})
    assert out.status_code == 200 and integrations.tenant_off("slack", "tenant_a") is True


def test_asking_for_what_is_already_on_changes_nothing_and_a_single_approver_setting_applies_at_once(api, monkeypatch):
    client, _tmp = api
    integrations.set_switch("slack", True, by="a", reason="on")
    unchanged = _ask(client)
    assert unchanged.status_code == 200 and unchanged.json()["unchanged"] is True and approvals.list_items() == []
    monkeypatch.setattr(config, "INTEGRATION_APPROVALS_REQUIRED", 1)
    immediate = _ask(client, name="sharepoint")
    assert immediate.status_code == 200 and immediate.json()["change"]["after"] is True and integrations.switch_on("sharepoint") is True


# --- scope --------------------------------------------------------------------------------------------------------------------------------------


def test_a_tenant_request_is_decided_by_someone_who_reaches_that_tenant_and_a_global_one_by_the_installation_administrator(api):
    client, _tmp = api
    integrations.set_switch("slack", True, by="a", reason="on")
    integrations.set_switch("slack", False, by="a", reason="not this tenant", tenant_id="tenant_a")
    tenant_request = _ask(client, headers=TENANT_A, actor="鈴木花子", tenant_id="tenant_a")
    assert tenant_request.status_code == 202 and tenant_request.json()["pending"]["params"] == {"name": "slack", "tenant_id": "tenant_a"}
    assert _ask(client, headers=TENANT_A, actor="鈴木花子", tenant_id="tenant_b").status_code == 403
    visible = client.get("/admin/system/approvals", headers=TENANT_A).json()["approvals"]
    assert [a["params"]["tenant_id"] for a in visible] == ["tenant_a"]
    approved = _decide(client, tenant_request.json()["pending"]["id"], "approve", "山田太郎", headers=ADMIN)  # the installation reaches every tenant
    assert approved.status_code == 200 and integrations.tenant_off("slack", "tenant_a") is False
    integrations.set_switch("slack", False, by="a", reason="off")
    global_request = _ask(client, actor="山田太郎").json()["pending"]["id"]
    waiting = client.get("/admin/system/approvals?status=pending", headers=TENANT_A).json()["approvals"]
    assert waiting == []  # the installation-wide request is not about their tenant
    assert _decide(client, global_request, "approve", "鈴木花子", headers=TENANT_A).status_code == 403
    assert _decide(client, global_request, "approve", "田中太郎", headers=ADMIN).status_code == 200


def test_no_route_answers_without_the_administrators_credentials(api):
    client, _tmp = api
    assert client.get("/admin/system/approvals").status_code in (401, 403)
    assert client.post("/admin/system/approvals/apr-x/approve", json={"actor": "a"}).status_code in (401, 403)


# --- single sign-on ----------------------------------------------------------------------------------------------------------------------------------


def _sessions(monkeypatch, by_actor):
    """Make the request carry a single sign-on session whose identifier depends on the header `x-test-person`."""
    from webapi import oidc_auth

    def resolve(headers):
        person = headers.get("x-test-person")
        return (SimpleNamespace(key_fingerprint=by_actor[person], role=None, tenant_authorization_enabled=False, allowed_tenants=frozenset())
                if person in by_actor else None)

    monkeypatch.setattr(oidc_auth, "resolve_oidc_session", resolve)


def test_identifiers_decide_when_there_are_sessions_and_a_name_cannot_hide_the_same_person(api, monkeypatch):
    client, _tmp = api
    _sessions(monkeypatch, {"one": "fp-aaaaaaaaaaaa", "two": "fp-bbbbbbbbbbbb"})
    item_id = _ask(client, actor="山田太郎", headers={**ADMIN, "x-test-person": "one"}).json()["pending"]["id"]
    assert client.get("/admin/system/approvals", headers=ADMIN).json()["approvals"][0]["requested_by"] == {"name": "山田太郎", "id": "fp-aaaaaaaaaaaa", "verified": True}
    disguised = _decide(client, item_id, "approve", "全く別の名前", headers={**ADMIN, "x-test-person": "one"})
    assert disguised.status_code == 400 and "their own" in disguised.json()["detail"]  # the same identifier under another name
    homonym = _decide(client, item_id, "approve", "山田太郎", headers={**ADMIN, "x-test-person": "two"})
    assert homonym.status_code == 200  # two identified people who typed the same name are two people


def test_with_single_sign_on_required_a_typed_name_is_not_enough(api, monkeypatch):
    client, _tmp = api
    monkeypatch.setattr(config, "APPROVAL_REQUIRE_SSO", True)
    _sessions(monkeypatch, {"one": "fp-aaaaaaaaaaaa", "two": "fp-bbbbbbbbbbbb"})
    assert client.get("/admin/system/status", headers=ADMIN).json()["approvals"]["require_sso"] is True
    item_id = _ask(client, actor="山田太郎", headers={**ADMIN, "x-test-person": "one"}).json()["pending"]["id"]
    refused = _decide(client, item_id, "approve", "田中太郎", headers=ADMIN)  # no session
    assert refused.status_code == 400 and "single sign-on" in refused.json()["detail"] and integrations.switch_on("slack") is False
    assert _decide(client, item_id, "approve", "田中太郎", headers={**ADMIN, "x-test-person": "two"}).status_code == 200


def test_the_audit_log_names_who_asked_and_who_decided(api):
    client, tmp_path = api
    item_id = _ask(client).json()["pending"]["id"]
    _decide(client, item_id, "approve", "田中太郎")
    lines = [json.loads(line) for line in (tmp_path / "runs" / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    decided = next(r for r in lines if r.get("action") == "approval_approved")
    assert decided["requested_by"] == "山田太郎" and decided["decided_by"] == "田中太郎" and decided["verified"] is False
