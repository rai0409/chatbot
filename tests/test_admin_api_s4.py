"""S4: the administration API that the console's screens use: records, golden questions, usage, audit, proposals, profiles,
evaluations, settings, monitoring, access. Every route is checked for who may use it and what it leaves in the audit log."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import golden_qa, llm_usage, records

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setenv("TENANT_EVAL_PROPOSALS_DIR", str(tmp_path / "proposals"))
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "RECORDS_DIR", "")
    monkeypatch.setattr(config, "GOLDEN_QA_DIR", "")
    monkeypatch.setattr(config, "RECORDS_HASH_SALT", "salt-not-a-secret")
    monkeypatch.setattr(config, "RECORD_LEVEL_DEFAULT", "off")
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "detailed")
    monkeypatch.setattr(config, "LLM_USAGE_ENABLED", True)
    from webapi import main

    return TestClient(main.app), tmp_path


def _actions(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    return [json.loads(line).get("action") for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _record(tenant="tenant_a", **kw):
    base = dict(tenant_id=tenant, request_id=f"r-{tenant}", trace_id=f"t-{tenant}", user="fp-1", question="休暇の申請期限は", answer_text="3営業日前です",
                answer_mode="grounded", citations=[{"source_doc": "rules.pdf", "page": 1}], question_type="procedure", now=datetime.now(timezone.utc))
    base.update(kw)
    return records.record_interaction(**base)


# --- every route needs an administrator ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("get", "/admin/records"), ("get", "/admin/records/level"), ("get", "/admin/records/stats"), ("get", "/admin/records/rec-x"),
    ("post", "/admin/records/purge"), ("get", "/admin/golden/tenant_a"), ("get", "/admin/usage"), ("get", "/admin/usage/daily"),
    ("get", "/admin/usage/downtime"), ("get", "/admin/audit"), ("get", "/admin/audit/verify"), ("get", "/admin/proposals"),
    ("get", "/admin/profiles"), ("get", "/admin/evaluations"), ("get", "/admin/knobs"), ("get", "/admin/monitoring"), ("get", "/admin/access"),
])
def test_no_route_answers_without_the_administrators_credentials(api, method, path):
    client, _tmp = api
    response = getattr(client, method)(path)
    assert response.status_code in (401, 403)


# --- records ----------------------------------------------------------------------------------------------------------------------------------------


def test_the_level_is_changed_with_a_reason_inside_the_ceiling_and_is_audited(api, monkeypatch):
    client, tmp_path = api
    assert client.get("/admin/records/level", headers=ADMIN).json()["effective"] == "off"
    assert client.post("/admin/records/level", headers=ADMIN, json={"level": "standard", "reason": ""}).status_code == 422
    done = client.post("/admin/records/level", headers=ADMIN, json={"level": "standard", "reason": "pilot with the legal team"})
    assert done.status_code == 200 and done.json()["effective"] == "standard"
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "standard")
    over = client.post("/admin/records/level", headers=ADMIN, json={"level": "detailed", "reason": "more"})
    assert over.status_code == 400 and "RECORD_LEVEL_MAX" in over.json()["detail"]
    assert client.post("/admin/records/level", headers=ADMIN, json={"level": "nonsense", "reason": "x"}).status_code == 400
    assert "records_level" in _actions(tmp_path)


def test_a_tenant_administrator_sets_only_their_own_tenant_and_sees_only_their_records(api):
    client, _tmp = api
    assert client.post("/admin/records/level", headers=TENANT_A, json={"level": "standard", "reason": "x"}).status_code == 403  # installation-wide
    assert client.post("/admin/records/level", headers=TENANT_A, json={"level": "standard", "reason": "x", "tenant_id": "tenant_b"}).status_code == 403
    assert client.post("/admin/records/level", headers=TENANT_A, json={"level": "standard", "reason": "x", "tenant_id": "tenant_a"}).status_code == 200
    client.post("/admin/records/level", headers=ADMIN, json={"level": "standard", "reason": "x", "tenant_id": "tenant_b"})
    a, b = _record("tenant_a"), _record("tenant_b")
    seen = client.get("/admin/records", headers=TENANT_A).json()
    assert [r["tenant_id"] for r in seen["records"]] == ["tenant_a"]
    assert client.get(f"/admin/records/{a['id']}", headers=TENANT_A).status_code == 200
    assert client.get(f"/admin/records/{b['id']}", headers=TENANT_A).status_code == 404  # another tenant's record does not exist for them
    assert client.get("/admin/records?tenant_id=tenant_b", headers=TENANT_A).status_code == 403
    assert client.get("/admin/records/level", headers=TENANT_A).json()["tenants"].keys() == {"tenant_a"}
    assert client.get("/admin/records/stats", headers=TENANT_A).json()["total"] == 1
    assert client.get("/admin/records/stats", headers=ADMIN).json()["total"] == 2


def test_reading_what_people_asked_is_itself_audited_and_a_judgement_is_kept(api):
    client, tmp_path = api
    client.post("/admin/records/level", headers=ADMIN, json={"level": "standard", "reason": "x"})
    rec = _record()
    client.get("/admin/records", headers=ADMIN)
    client.get(f"/admin/records/{rec['id']}", headers=ADMIN)
    assert {"records_listed", "record_viewed"} <= set(_actions(tmp_path))
    assert client.post(f"/admin/records/{rec['id']}/review", headers=ADMIN, json={"verdict": "incorrect", "reviewer": ""}).status_code == 400
    assert client.post(f"/admin/records/{rec['id']}/review", headers=ADMIN, json={"verdict": "incorrect", "reviewer": "佐藤次郎"}).status_code == 400  # no reason
    ok = client.post(f"/admin/records/{rec['id']}/review", headers=ADMIN,
                     json={"verdict": "incorrect", "reviewer": "佐藤次郎", "reasons": ["outdated"], "expected_answer": "5営業日前です"})
    assert ok.status_code == 200 and client.get(f"/admin/records/{rec['id']}", headers=ADMIN).json()["review"]["verdict"] == "incorrect"
    assert client.get("/admin/records?reviewed=true&verdict=incorrect", headers=ADMIN).json()["total"] == 1
    assert "record_reviewed" in _actions(tmp_path)


def test_purge_is_the_installation_administrators_and_a_dry_run_unless_told_otherwise(api, monkeypatch):
    client, tmp_path = api
    assert client.post("/admin/records/purge", headers=TENANT_A, json={}).status_code == 403
    first = client.post("/admin/records/purge", headers=ADMIN, json={})
    assert first.status_code == 200 and first.json()["records"]["dry_run"] is True and first.json()["llm_usage"]["dry_run"] is True
    assert client.post("/admin/records/purge", headers=ADMIN, json={"dry_run": False, "action": "shred"}).status_code == 400
    assert "records_purge" in _actions(tmp_path)


# --- golden questions ---------------------------------------------------------------------------------------------------------------------------------


def test_golden_questions_are_made_changed_and_removed_and_always_parse(api):
    client, tmp_path = api
    assert client.post("/admin/golden/tenant_a", headers=ADMIN, json={"question": "何も確かめない"}).status_code == 400  # checks nothing
    made = client.post("/admin/golden/tenant_a", headers=ADMIN,
                       json={"case_id": "q1", "question": "休暇の申請期限は", "expected_any": ["3営業日", "三営業日"], "expected_citation": True, "priority": 5})
    assert made.status_code == 200 and made.json()["created_by"] == "installation"
    assert client.post("/admin/golden/tenant_a", headers=ADMIN, json={"case_id": "q1", "question": "x", "reference": "y"}).status_code == 409
    changed = client.put("/admin/golden/tenant_a/q1", headers=ADMIN,
                         json={"forbidden_any": ["5営業日"], "expected_updated_at": made.json()["updated_at"]})
    assert changed.status_code == 200 and changed.json()["forbidden_any"] == ["5営業日"] and changed.json()["expected_any"] == ["3営業日", "三営業日"]
    stale = client.put("/admin/golden/tenant_a/q1", headers=ADMIN, json={"priority": 1, "expected_updated_at": made.json()["updated_at"]})
    assert stale.status_code == 409
    listed = client.get("/admin/golden/tenant_a", headers=ADMIN).json()
    assert listed["total"] == 1 and listed["enabled"] == 1
    exported = client.get("/admin/golden/tenant_a/export", headers=ADMIN).json()["golden_qa"]
    case, errors = golden_qa.parse_case(exported[0])
    assert errors == [] and case["case_id"] == "q1" and "created_by" not in exported[0]  # an evaluation takes the case without the bookkeeping
    client.put("/admin/golden/tenant_a/q1", headers=ADMIN, json={"enabled": False})
    assert client.get("/admin/golden/tenant_a/export", headers=ADMIN).json()["golden_qa"] == []
    assert client.delete("/admin/golden/tenant_a/q1", headers=ADMIN).status_code == 200
    assert client.get("/admin/golden/tenant_a/q1", headers=ADMIN).status_code == 404
    assert (tmp_path / "data" / "golden_qa" / "tenant_a.deleted.jsonl").exists()  # recoverable by hand
    assert {"golden_created", "golden_updated", "golden_deleted"} <= set(_actions(tmp_path))


def test_a_golden_question_is_made_from_a_judged_record_and_only_for_the_records_own_tenant(api):
    client, _tmp = api
    client.post("/admin/records/level", headers=ADMIN, json={"level": "standard", "reason": "x"})
    rec = _record()
    assert client.post("/admin/golden/tenant_a/from-record", headers=ADMIN, json={"record_id": rec["id"]}).status_code == 400  # not judged yet
    client.post(f"/admin/records/{rec['id']}/review", headers=ADMIN, json={"verdict": "correct", "reviewer": "佐藤次郎"})
    made = client.post("/admin/golden/tenant_a/from-record", headers=ADMIN, json={"record_id": rec["id"], "case_id": "from-1"})
    assert made.status_code == 200 and made.json()["reference"] == "3営業日前です" and made.json()["origin"] == f"record:{rec['id']}"
    assert made.json()["expected_citation"] is True and made.json()["tags"] == ["from-record"]
    assert client.post("/admin/golden/tenant_b/from-record", headers=ADMIN, json={"record_id": rec["id"]}).status_code == 404  # another tenant's record
    # the wrong answer with the reviewer's expected answer becomes the reference
    wrong = _record(request_id="r2", trace_id="t2", answer_text="7営業日前です")
    client.post(f"/admin/records/{wrong['id']}/review", headers=ADMIN,
                json={"verdict": "incorrect", "reviewer": "佐藤次郎", "reasons": ["outdated"], "expected_answer": "3営業日前です"})
    assert client.post("/admin/golden/tenant_a/from-record", headers=ADMIN, json={"record_id": wrong["id"]}).json()["reference"] == "3営業日前です"


def test_a_tenant_administrator_cannot_touch_another_tenants_golden_questions(api):
    client, _tmp = api
    assert client.get("/admin/golden/tenant_b", headers=TENANT_A).status_code == 403
    assert client.post("/admin/golden/tenant_b", headers=TENANT_A, json={"question": "q", "reference": "r"}).status_code == 403
    assert client.post("/admin/golden/tenant_a", headers=TENANT_A, json={"question": "q", "reference": "r"}).status_code == 200
    assert client.get("/admin/golden/../x", headers=ADMIN).status_code in (404, 400)


# --- usage and audit ---------------------------------------------------------------------------------------------------------------------------------


def test_usage_is_added_up_by_a_known_grouping_and_limited_to_the_tenants_of_the_administrator(api):
    client, _tmp = api
    now = datetime.now(timezone.utc)
    for tenant, tokens in (("tenant_a", 10), ("tenant_b", 99)):
        llm_usage.record_call(feature="generate", tenant_id=tenant, profile="local", model="m", host="localhost", location="local",
                              outcome="ok", input_tokens=tokens, output_tokens=1, now=now)
    everyone = {r["tenant_id"]: r for r in client.get("/admin/usage?days=1", headers=ADMIN).json()["rows"]}
    assert everyone["tenant_a"]["input_tokens"] == 10 and everyone["tenant_b"]["input_tokens"] == 99
    own = client.get("/admin/usage?days=1&by=feature", headers=TENANT_A).json()["rows"]
    assert own[0]["feature"] == "generate" and own[0]["input_tokens"] == 10 and own[0]["calls"] == 1
    assert client.get("/admin/usage?by=prompt", headers=ADMIN).status_code == 400
    assert client.get("/admin/usage/daily?days=2", headers=ADMIN).json()["rows"][0]["calls"] == 2
    assert client.get("/admin/usage/downtime", headers=TENANT_A).status_code == 403


def test_the_downtime_counts_only_the_business_hours(api, monkeypatch):
    client, _tmp = api
    monkeypatch.setattr(config, "BUSINESS_HOURS_UTC_OFFSET", 9.0)
    # Monday 2026-10-26: 06:00-14:00 UTC = 15:00-23:00 local; business hours are 08:00-21:00 local -> 15:00-21:00 = 6 hours
    start, end = datetime(2026, 10, 26, 6, tzinfo=timezone.utc), datetime(2026, 10, 26, 14, tzinfo=timezone.utc)
    assert llm_usage.business_seconds(start, end) == 6 * 3600
    # Saturday: nothing counts
    assert llm_usage.business_seconds(datetime(2026, 10, 31, 0, tzinfo=timezone.utc), datetime(2026, 10, 31, 12, tzinfo=timezone.utc)) == 0
    llm_usage.record_breaker("llm:local", "closed", "open", start.timestamp())
    llm_usage.record_breaker("llm:local", "open", "closed", end.timestamp())
    report = llm_usage.downtime_report(30, now=datetime(2026, 10, 27, tzinfo=timezone.utc), today=datetime(2026, 10, 27).date())
    assert report["by_breaker"]["llm:local"]["open_seconds"] == 8 * 3600
    assert report["by_breaker"]["llm:local"]["open_business_seconds"] == 6 * 3600 and report["by_breaker"]["llm:local"]["opened"] == 1
    served = client.get("/admin/usage/downtime", headers=ADMIN).json()
    assert served["business_hours"] == {"utc_offset_hours": 9.0, "start": 8, "end": 21, "weekdays_only": True, "holidays_known": False}


def test_the_audit_log_is_read_newest_first_filtered_and_checked(api):
    client, tmp_path = api
    client.post("/admin/records/level", headers=ADMIN, json={"level": "standard", "reason": "x"})
    client.post("/admin/records/level", headers=ADMIN, json={"level": "anonymous", "reason": "y", "tenant_id": "tenant_a"})
    events = client.get("/admin/audit?kind=admin", headers=ADMIN).json()
    assert events["total"] >= 2 and events["events"][0]["timestamp"] >= events["events"][-1]["timestamp"]
    assert client.get("/admin/audit?kind=admin&action=records_level", headers=ADMIN).json()["total"] == 2
    assert client.get("/admin/audit?kind=admin&text=tenant_a", headers=ADMIN).json()["total"] >= 1
    assert client.get("/admin/audit?kind=other", headers=ADMIN).status_code == 400
    own = client.get("/admin/audit?kind=admin", headers=TENANT_A).json()
    assert all(e.get("tenant_id") == "tenant_a" for e in own["events"])  # an event of no tenant is hidden from a tenant administrator
    assert client.get("/admin/audit/verify?kind=admin", headers=TENANT_A).status_code == 403
    assert client.get("/admin/audit/verify?kind=admin", headers=ADMIN).json()["ok"] is True
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["reason"] = "rewritten"
    path.write_text("\n".join([json.dumps(tampered, ensure_ascii=False)] + lines[1:]) + "\n", encoding="utf-8")
    assert client.get("/admin/audit/verify?kind=admin", headers=ADMIN).json()["ok"] is False  # a change of a past line is found
    assert client.get("/admin/audit/verify?kind=feedback", headers=ADMIN).json() == {"ok": True, "checked": 0, "kind": "feedback"}


# --- proposals, versions, evaluations, settings ---------------------------------------------------------------------------------------------------


def test_a_proposal_moves_with_a_named_person_and_the_proposer_cannot_approve(api):
    client, tmp_path = api
    new = {"id": "p1", "profile_id": "volcano_demo", "title": "表記ゆれの辞書を足す", "actor": "山田太郎"}
    assert client.post("/admin/proposals", headers=TENANT_A, json=new).status_code == 403  # the installation's business
    assert client.post("/admin/proposals", headers=ADMIN, json={**new, "actor": " "}).status_code == 400
    assert client.post("/admin/proposals", headers=ADMIN, json=new).status_code == 200
    assert client.post("/admin/proposals", headers=ADMIN, json=new).status_code == 409
    assert client.post("/admin/proposals/p1/classify", headers=ADMIN, json={"actor": "山田太郎", "category": "retrieval_setting"}).status_code == 200
    proposed = client.post("/admin/proposals/p1/propose", headers=ADMIN,
                           json={"actor": "山田太郎", "change": {"knobs": {"MULTI_QUERY_MAX_VARIANTS": 4}}})
    assert proposed.status_code == 200 and proposed.json()["state"] == "proposed" and proposed.json()["summary"]["approvals_required"] == 1
    assert client.post("/admin/proposals/p1/propose", headers=ADMIN, json={"actor": "山田太郎", "change": {"knobs": {"MULTI_QUERY_MAX_VARIANTS": 99}}}).status_code == 400
    assert client.post("/admin/proposals/p1/approve", headers=ADMIN, json={"actor": "佐藤次郎"}).status_code == 400  # not evaluated yet
    assert client.post("/admin/proposals/p1/attach-evaluation", headers=ADMIN, json={"actor": "山田太郎"}).status_code == 400  # needs a ledger entry
    assert client.post("/admin/proposals/p1/dance", headers=ADMIN, json={"actor": "山田太郎"}).status_code == 404
    listed = client.get("/admin/proposals?state=proposed", headers=ADMIN).json()
    assert [p["id"] for p in listed["proposals"]] == ["p1"] and client.get("/admin/proposals?state=nonsense", headers=ADMIN).status_code == 400
    rejected = client.post("/admin/proposals/p1/reject", headers=ADMIN, json={"actor": "佐藤次郎", "reason": "効果が見込めない"})
    assert rejected.status_code == 200 and rejected.json()["state"] == "rejected"
    assert [h["to"] for h in client.get("/admin/proposals/p1", headers=ADMIN).json()["history"]] == ["observed", "classified", "proposed", "rejected"]
    assert client.get("/admin/proposals/missing", headers=ADMIN).status_code == 404
    assert {"proposal_created", "proposal_classify", "proposal_propose", "proposal_reject"} <= set(_actions(tmp_path))


def test_profile_versions_evaluations_and_settings_can_be_read(api):
    client, _tmp = api
    assert client.get("/admin/profiles", headers=ADMIN).json() == {"profiles": []}
    assert client.get("/admin/profiles/volcano_demo/versions", headers=ADMIN).json()["versions"] == []
    assert client.get("/admin/profiles/not valid!/versions", headers=ADMIN).status_code == 400
    assert client.post("/admin/profiles/volcano_demo/activate", headers=TENANT_A, json={"version": "0" * 16, "actor": "a", "reason": "r"}).status_code == 403
    assert client.post("/admin/profiles/volcano_demo/activate", headers=ADMIN, json={"version": "0" * 16, "actor": "山田太郎", "reason": "r"}).status_code == 404
    assert client.post("/admin/profiles/volcano_demo/rollback", headers=ADMIN, json={"actor": "山田太郎", "reason": "r"}).status_code == 400  # nothing to go back to
    assert client.get("/admin/evaluations", headers=ADMIN).json() == {"chain_intact": True, "total": 0, "entries": []}
    knobs = client.get("/admin/knobs", headers=ADMIN).json()["knobs"]
    assert any(k["name"] == "RAG_HARD_MAX_DIST" and k["risk"] == "high" for k in knobs) and all("value" in k for k in knobs)


def test_monitoring_puts_feedback_and_records_side_by_side(api):
    client, _tmp = api
    from rag_core import audit_log

    audit_log.append_feedback_audit_event({"feedback_token": "t", "feedback_type": "bad", "tenant_id": "tenant_a"})
    audit_log.append_feedback_audit_event({"feedback_token": "u", "feedback_type": "good", "tenant_id": "tenant_b"})
    body = client.get("/admin/monitoring", headers=ADMIN).json()
    day = next(iter(body["feedback_by_day"].values()))
    assert day == {"bad": 1, "good": 1} and body["records"]["total"] == 0 and body["changes_in_rollout"] == []
    own = client.get("/admin/monitoring", headers=TENANT_A).json()
    assert next(iter(own["feedback_by_day"].values())) == {"bad": 1}


def test_the_access_overview_shows_the_set_up_without_a_key_or_a_group_name(api, monkeypatch):
    client, _tmp = api
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "secret-key-one,secret-key-two")
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "group-one=admin,group-two=viewer")
    overview = client.get("/admin/access", headers=ADMIN).json()
    assert overview["you"] == {"reach": "installation", "tenants": None}
    assert overview["admin_auth"]["tenant_credentials"] == 1 and overview["admin_auth"]["tenants_with_own_admin"] == ["tenant_a"]
    assert overview["api_auth"]["enabled"] is True and overview["api_auth"]["keys"] == 2
    assert overview["single_sign_on"]["group_role_map"] == {"entries": 2, "values": ["admin", "viewer"]}
    text = json.dumps(overview, ensure_ascii=False)
    for secret in ("secret-key-one", "group-one", ADMIN_TOKEN, TENANT_A_TOKEN):
        assert secret not in text
    assert client.get("/admin/access", headers=TENANT_A).json()["you"] == {"reach": "tenants", "tenants": ["tenant_a"]}
