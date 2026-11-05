"""The approved-QA console routes. Synthetic data only."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import approved_qa_store as store

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT = {"x-admin-token": TENANT_TOKEN}
CITE = [{"source_doc": "manual.pdf", "source_pages": [2]}]
HEADER = ",".join(store.TEMPLATE_HEADERS)


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.delenv("APPROVED_QA_REQUIRE_APPROVER", raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(tmp_path / "live.jsonl"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    from webapi import main

    return TestClient(main.app)


def _save(api, headers=ADMIN, tenant="tenant_a", question="申請期限はいつですか？", **kw):
    body = {"tenant_id": tenant, "question": question, "approved_answer": "毎月末日です。", "citations": CITE, "reason": "新規", "actor": "山田太郎", **kw}
    return api.post("/admin/approved-qa/save", headers=headers, json=body)


def test_every_route_needs_the_admin_authentication(api):
    for method, path in (("get", "/admin/approved-qa"), ("get", "/admin/approved-qa/health"), ("get", "/admin/approved-qa/export"),
                         ("get", "/admin/approved-qa/template"), ("post", "/admin/approved-qa/save"), ("post", "/admin/approved-qa/status"),
                         ("post", "/admin/approved-qa/delete"), ("post", "/admin/approved-qa/import")):
        assert getattr(api, method)(path).status_code in (401, 403, 422), path


def test_create_list_approve_and_chat_reads_it(api):
    made = _save(api)
    assert made.status_code == 200 and made.json()["status"] == "draft"
    qa_id = made.json()["qa_id"]
    listing = api.get("/admin/approved-qa", headers=ADMIN).json()
    assert listing["total"] == 1 and listing["counts"]["draft"] == 1 and listing["rows"][0]["qa_id"] == qa_id
    done = api.post("/admin/approved-qa/status", headers=ADMIN, json={"tenant_id": "tenant_a", "qa_id": qa_id, "status": "approved", "reason": "確認した", "actor": "佐藤次郎"})
    assert done.status_code == 200 and done.json()["approved_by"] == "佐藤次郎"
    from rag_core import approved_qa

    index = approved_qa.load_approved_qa(config.APPROVED_QA_PATH, tenant_id="tenant_a")
    assert approved_qa.lookup_approved_answer(index, "申請期限はいつですか？", tenant_id="tenant_a").approved_answer == "毎月末日です。"


def test_validation_errors_are_400_and_a_missing_record_is_404(api):
    assert api.post("/admin/approved-qa/save", headers=ADMIN, json={"tenant_id": "tenant_a", "question": "", "approved_answer": "a", "reason": "x", "actor": "山田太郎"}).status_code == 400
    assert _save(api, reason="").status_code == 400
    assert _save(api, actor="").status_code == 400
    assert api.post("/admin/approved-qa/status", headers=ADMIN, json={"tenant_id": "tenant_a", "qa_id": "nope", "status": "approved", "reason": "x", "actor": "山田太郎"}).status_code == 404
    assert api.get("/admin/approved-qa/record?tenant_id=tenant_a&qa_id=nope", headers=ADMIN).status_code == 404
    assert api.get("/admin/approved-qa?status=weird", headers=ADMIN).status_code == 400


def test_approving_without_a_citation_is_refused(api):
    made = api.post("/admin/approved-qa/save", headers=ADMIN, json={"tenant_id": "tenant_a", "question": "質問です", "approved_answer": "回答です", "reason": "x", "actor": "山田太郎"}).json()
    refused = api.post("/admin/approved-qa/status", headers=ADMIN, json={"tenant_id": "tenant_a", "qa_id": made["qa_id"], "status": "approved", "reason": "x", "actor": "佐藤次郎"})
    assert refused.status_code == 400 and "citation" in refused.json()["detail"]


def test_a_stale_edit_is_409(api):
    made = _save(api).json()
    ok = _save(api, qa_id=made["qa_id"], question=made["question"], expected_updated_at=made["updated_at"])
    assert ok.status_code == 200
    stale = _save(api, qa_id=made["qa_id"], question=made["question"], expected_updated_at=made["updated_at"])
    assert stale.status_code == 409


def test_a_tenant_administrator_sees_and_changes_only_their_own_tenant(api):
    own = _save(api, TENANT, tenant="tenant_a").json()
    other = _save(api, ADMIN, tenant="tenant_b", question="他社の質問").json()
    listing = api.get("/admin/approved-qa", headers=TENANT).json()
    assert [r["qa_id"] for r in listing["rows"]] == [own["qa_id"]] and listing["can_see_all"] is False
    assert api.get("/admin/approved-qa?tenant_id=tenant_b", headers=TENANT).status_code == 403
    assert _save(api, TENANT, tenant="tenant_b", question="書き込み").status_code == 403
    assert api.post("/admin/approved-qa/status", headers=TENANT, json={"tenant_id": "tenant_b", "qa_id": other["qa_id"], "status": "approved", "reason": "x", "actor": "山田太郎"}).status_code == 403
    assert api.get(f"/admin/approved-qa/record?tenant_id=tenant_b&qa_id={other['qa_id']}", headers=TENANT).status_code == 403
    assert api.get("/admin/approved-qa/export", headers=TENANT).text.count("\n") == 1  # only their own record
    assert api.get("/admin/approved-qa/export", headers=ADMIN).text.count("\n") == 2


def test_the_health_detail_is_for_the_installation_administrator(api):
    _save(api)
    assert api.get("/admin/approved-qa/health", headers=ADMIN).json()["detail_hidden"] is False
    own = api.get("/admin/approved-qa/health", headers=TENANT).json()
    assert own["detail_hidden"] is True and own["errors"] == [] and own["total"] == 1


def test_delete_and_history(api):
    made = _save(api).json()
    api.post("/admin/approved-qa/status", headers=ADMIN, json={"tenant_id": "tenant_a", "qa_id": made["qa_id"], "status": "rejected", "reason": "不要", "actor": "佐藤次郎"})
    record = api.get(f"/admin/approved-qa/record?tenant_id=tenant_a&qa_id={made['qa_id']}", headers=ADMIN).json()
    assert [h["action"] for h in record["history"]] == ["status", "create"]
    assert record["history"][0]["status_before"] == "draft" and record["history"][0]["status_after"] == "rejected"
    assert "before" not in record["history"][0]
    assert api.post("/admin/approved-qa/delete", headers=ADMIN, json={"tenant_id": "tenant_a", "qa_id": made["qa_id"], "reason": "整理", "actor": "山田太郎"}).status_code == 200
    assert api.get("/admin/approved-qa", headers=ADMIN).json()["total"] == 0


def test_import_preview_then_apply_with_text_and_with_shift_jis_bytes(api):
    csv_text = HEADER + "\n" + "申請期限は？,毎月末日です。,manual.pdf,2,,手続き,tenant_a,,有効,期限は？,,,\n"
    preview = api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a", "content": csv_text})
    assert preview.status_code == 200 and preview.json()["counts"]["create"] == 1 and preview.json()["applied"] is False
    assert api.get("/admin/approved-qa", headers=ADMIN).json()["total"] == 0
    assert api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a", "content": csv_text, "apply": True}).status_code == 400  # no reason / name
    encoded = base64.b64encode(csv_text.encode("cp932")).decode()
    done = api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a", "content_base64": encoded, "apply": True, "reason": "Excel から", "actor": "山田太郎"})
    assert done.status_code == 200 and done.json()["counts"]["create"] == 1
    assert api.get("/admin/approved-qa", headers=ADMIN).json()["rows"][0]["question"] == "申請期限は？"


def test_import_input_checks(api):
    assert api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a"}).status_code == 400
    assert api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a", "content": "x", "content_base64": "eA=="}).status_code == 400
    assert api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a", "content_base64": "!!!"}).status_code == 400
    assert api.post("/admin/approved-qa/import", headers=ADMIN, json={"tenant_id": "tenant_a", "content": "a,b\n1,2"}).status_code == 400
    assert api.post("/admin/approved-qa/import", headers=TENANT, json={"tenant_id": "tenant_b", "content": HEADER}).status_code == 403


def test_a_tenant_administrator_cannot_import_into_another_tenant_through_the_tenant_column(api):
    csv_text = HEADER + "\n" + "質問です,回答です,a.pdf,1,,,tenant_b,,有効,,,,\n"
    result = api.post("/admin/approved-qa/import", headers=TENANT, json={"tenant_id": "tenant_a", "content": csv_text, "apply": True, "reason": "x", "actor": "山田太郎"}).json()
    assert result["counts"]["error"] == 1 and api.get("/admin/approved-qa", headers=ADMIN).json()["total"] == 0


def test_template_and_export_download(api):
    template = api.get("/admin/approved-qa/template", headers=ADMIN)
    assert template.status_code == 200 and template.text.splitlines()[0] == HEADER and "attachment" in template.headers["content-disposition"]
    _save(api)
    export = api.get("/admin/approved-qa/export", headers=ADMIN)
    assert export.status_code == 200 and export.text.count("\n") == 1


def test_the_enabled_flag_tells_whether_chat_uses_the_file(api, monkeypatch):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    assert api.get("/admin/approved-qa", headers=ADMIN).json()["enabled"] is False
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    assert api.get("/admin/approved-qa", headers=ADMIN).json()["enabled"] is True


def test_the_base64_import_route_accepts_a_body_larger_than_the_default_request_limit(api):
    filler = "あ" * 400
    rows = "\n".join(f"質問{i}です,{filler},a.pdf,1,,,tenant_a,,有効,,," for i in range(1500))
    body = {"tenant_id": "tenant_a", "content_base64": base64.b64encode((HEADER + "\n" + rows).encode()).decode()}
    from webapi import security_middleware

    assert len(body["content_base64"]) > security_middleware.max_request_bytes()  # over the default limit of other routes
    assert api.post("/admin/approved-qa/import", headers=ADMIN, json=body).status_code == 200
