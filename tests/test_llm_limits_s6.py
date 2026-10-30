"""S6: daily limits, model tiers assigned to people, batch calls, the evaluation's scoring through batches, the alerts, and the API."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from eval.tenant_eval import llm_judge
from rag_core import audit_log, llm, llm_assignments, llm_batch, llm_gateway, llm_profiles, llm_quota, llm_usage, runtime_settings

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}


class Provider:
    def __init__(self):
        self.calls = []

    def client(self, host):
        provider = self

        def create(**kwargs):
            provider.calls.append((host, kwargs["model"]))
            usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage)

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _profiles(tmp_path, monkeypatch):
    data = {
        "profiles": {
            "small": {"base_url": "http://localhost:11434/v1", "model": "small-model"},
            "large": {"base_url": "https://llm.example.co.jp/v1", "api_key_env": "K", "model": "large-model", "batch": True,
                      "cost": {"input_per_million": 10.0, "output_per_million": 20.0}},
        },
        "routes": {"generate": ["small"], "rewrite": ["small"], "verify": ["small"], "judge": ["large"]},
        "tiers": {"premium": {"generate": ["large"]}, "economy": {"generate": ["small"]}},
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "LLM_PROFILES_FILE", str(path))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "LLM_USAGE_ENABLED", True)
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_FEATURES", ["generate", "rewrite", "verify", "judge"])
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 0)
    monkeypatch.setattr(config, "BUSINESS_HOURS_UTC_OFFSET", 0.0)
    for name in ("USER_DAILY_CALLS", "USER_DAILY_TOKENS", "TENANT_DAILY_TOKENS", "TENANT_DAILY_COST", "GLOBAL_DAILY_COST"):
        monkeypatch.setattr(config, "LLM_QUOTA_" + name, 0)
    _profiles(tmp_path, monkeypatch)
    provider = Provider()
    monkeypatch.setattr(llm_profiles, "make_client", lambda profile: provider.client(profile.host))
    llm_quota.reset()
    token = audit_log._ACTOR.set({"authenticated": True, "key_fingerprint": "aaaaaaaaaaaa", "role": "user"})
    yield provider
    audit_log._ACTOR.reset(token)


def _ask(feature="generate", tenant="tenant_a"):
    return llm.complete([{"role": "user", "content": "質問"}], feature=feature, tenant_id=tenant)


def _as(fingerprint, role="user"):
    return audit_log._ACTOR.set({"authenticated": True, "key_fingerprint": fingerprint, "role": role})


# --- daily limits ------------------------------------------------------------------------------------------------------------------------------


def test_nothing_is_limited_by_default(env):
    for _ in range(5):
        _ask()
    assert len(env.calls) == 5


def test_a_caller_is_stopped_at_the_daily_calls_before_the_call_is_made(env):
    llm_quota.set_limit("user_daily_calls", 2, by="山田太郎", reason="pilot")
    _ask()
    _ask()
    env.calls.clear()
    with pytest.raises(llm_quota.QuotaExceeded) as caught:
        _ask()
    assert caught.value.limit == "user_daily_calls" and caught.value.scope == "user" and env.calls == []  # nothing was sent
    other = _as("bbbbbbbbbbbb")
    _ask()  # another person has their own count
    audit_log._ACTOR.reset(other)
    refused = [r for r in llm_usage.read(1) if r.get("outcome") == "refused"]
    assert refused and refused[0]["error_type"] == "QuotaExceeded:user_daily_calls"
    assert llm_gateway.breaker_for(llm_profiles.all_profiles()["small"]).state() == "closed"  # a limit is not an endpoint failure


def test_a_limit_on_callers_does_not_apply_without_an_identified_caller_but_the_tenant_limit_does(env):
    audit_log._ACTOR.set(None)
    llm_quota.set_limit("user_daily_calls", 1, by="a", reason="r")
    _ask()
    _ask()  # no identified caller: nothing to count against
    llm_quota.set_limit("tenant_daily_tokens", 40, by="a", reason="r")  # 15 tokens a call, 30 used so far
    _ask()
    with pytest.raises(llm_quota.QuotaExceeded) as caught:
        _ask()
    assert caught.value.limit == "tenant_daily_tokens" and caught.value.scope == "tenant"
    _ask(tenant="tenant_b")  # another tenant has its own count


def test_cost_limits_and_the_global_one(env):
    llm_quota.set_limit("global_daily_cost", 0.0006, by="a", reason="r")  # one call of the large profile costs 0.0002
    llm_quota.set_limit("tenant_daily_cost", 0.0005, by="a", reason="r")
    _ask("judge")  # exempt from the limits (but what it costs counts: 0.0002)
    audit_log._ACTOR.set({"authenticated": True, "key_fingerprint": "cccccccccccc", "role": "user"})
    llm_assignments.set_assignment("global", None, "premium", by="a", reason="r")  # generate goes to the large profile
    _ask()
    _ask()
    with pytest.raises(llm_quota.QuotaExceeded) as caught:
        _ask()
    assert caught.value.limit in ("tenant_daily_cost", "global_daily_cost")


def test_a_tenants_own_limit_wins_over_the_global_one_which_wins_over_the_environment(env, monkeypatch):
    monkeypatch.setattr(config, "LLM_QUOTA_USER_DAILY_CALLS", 7)
    assert llm_quota.limit_for("user_daily_calls", "tenant_a") == {"value": 7.0, "source": "environment"}
    llm_quota.set_limit("user_daily_calls", 5, by="a", reason="r")
    assert llm_quota.limit_for("user_daily_calls", "tenant_a") == {"value": 5.0, "source": "global"}
    llm_quota.set_limit("user_daily_calls", 2, by="a", reason="r", tenant_id="tenant_a")
    assert llm_quota.limit_for("user_daily_calls", "tenant_a") == {"value": 2.0, "source": "tenant"}
    assert llm_quota.limit_for("user_daily_calls", "tenant_b")["value"] == 5.0
    llm_quota.set_limit("user_daily_calls", None, by="a", reason="back", tenant_id="tenant_a")
    assert llm_quota.limit_for("user_daily_calls", "tenant_a")["source"] == "global"


def test_a_limit_is_a_number_of_zero_or_more_and_the_global_cost_has_no_tenant_value(env):
    for bad in (-1, True, "10"):
        with pytest.raises(runtime_settings.SettingsError):
            llm_quota.set_limit("user_daily_calls", bad, by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        llm_quota.set_limit("global_daily_cost", 1, by="a", reason="r", tenant_id="tenant_a")
    with pytest.raises(runtime_settings.SettingsError):
        llm_quota.set_limit("made_up", 1, by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        llm_quota.set_limit("user_daily_calls", 1, by="a", reason=" ")


def test_yesterdays_use_does_not_count_and_the_day_is_the_local_one(env, monkeypatch):
    monkeypatch.setattr(config, "BUSINESS_HOURS_UTC_OFFSET", 9.0)
    now = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)  # 12:00 local; the local day began 2026-10-28 15:00 UTC
    llm_quota.set_limit("user_daily_calls", 2, by="a", reason="r")
    for when in (datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc), datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)):
        llm_usage.record_call(feature="generate", tenant_id="tenant_a", profile="p", model="m", host="h", location="local", outcome="ok",
                              input_tokens=1, output_tokens=1, now=when)
    llm_quota.check("generate", "tenant_a", "aaaaaaaaaaaa", now=now)  # one call counts (16:00 UTC is already today, locally)
    llm_usage.record_call(feature="generate", tenant_id="tenant_a", profile="p", model="m", host="h", location="local", outcome="ok",
                          input_tokens=1, output_tokens=1, now=datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc))
    llm_quota.reset()
    with pytest.raises(llm_quota.QuotaExceeded):
        llm_quota.check("generate", "tenant_a", "aaaaaaaaaaaa", now=now)


def test_the_snapshot_shows_use_against_each_limit_and_warns_from_the_ratio(env):
    llm_quota.set_limit("user_daily_calls", 5, by="a", reason="r")
    for _ in range(4):
        _ask()
    snapshot = llm_quota.snapshot()
    assert snapshot["tenants"]["tenant_a"]["calls"] == 4 and snapshot["users"][0]["user"] == "aaaaaaaaaaaa"
    warning = snapshot["warnings"][0]
    assert warning["limit"] == "user_daily_calls" and warning["scope"] == "user" and warning["ratio"] == 0.8
    assert {l["name"] for l in snapshot["limits"]} == set(llm_quota.LIMITS)
    assert llm_quota.snapshot(tenants=["tenant_x"])["tenants"] == {}


# --- model tiers ---------------------------------------------------------------------------------------------------------------------------------


def test_the_most_specific_assignment_wins(env):
    names = lambda user=None, role=None, tenant="tenant_a": [p.name for p in llm_profiles.chain("generate", tenant, user, role)]  # noqa: E731
    assert names() == ["small"]  # no assignment: the routes of the file
    llm_assignments.set_assignment("global", None, "premium", by="a", reason="everyone")
    assert names() == ["large"]
    llm_assignments.set_assignment("tenant", "tenant_a", "economy", by="a", reason="this tenant")
    assert names() == ["small"] and names(tenant="tenant_b") == ["large"]
    llm_assignments.set_assignment("role", "admin", "premium", by="a", reason="admins")
    assert names(role="admin") == ["large"] and names(role="user") == ["small"]
    llm_assignments.set_assignment("user", "abcdef123456", "economy", by="a", reason="one person")
    assert names(user="abcdef123456", role="admin") == ["small"]  # the person beats the role
    llm_assignments.set_assignment("user", "abcdef123456", None, by="a", reason="removed")
    assert names(user="abcdef123456", role="admin") == ["large"]


def test_the_gateway_uses_the_tier_of_the_caller(env):
    llm_assignments.set_assignment("user", "aaaaaaaaaaaa", "premium", by="a", reason="evaluation")
    _ask()
    assert env.calls[-1] == ("llm.example.co.jp", "large-model")
    audit_log._ACTOR.set({"authenticated": True, "key_fingerprint": "dddddddddddd", "role": "user"})
    _ask()
    assert env.calls[-1] == ("localhost", "small-model")


def test_an_assignment_is_checked_and_listed_and_changes_the_cache_key(env):
    from rag_core import answer_cache

    before = answer_cache._pipeline_state()
    with pytest.raises(runtime_settings.SettingsError, match="not defined"):
        llm_assignments.set_assignment("global", None, "gold", by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        llm_assignments.set_assignment("role", "boss", "premium", by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        llm_assignments.set_assignment("user", "NOT A FINGERPRINT", "premium", by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        llm_assignments.set_assignment("tenant", None, "premium", by="a", reason="r")
    llm_assignments.set_assignment("tenant", "tenant_a", "premium", by="山田太郎", reason="cost review")
    listed = llm_assignments.list_assignments()
    assert listed["tiers"] == ["premium", "economy"] and listed["profiles"] == ["large", "small"]
    assert listed["assignments"] == [{"kind": "tenant", "id": "tenant_a", "tier": "premium", "by": "山田太郎", "reason": "cost review",
                                      "at": listed["assignments"][0]["at"], "defined": True}]
    assert answer_cache._pipeline_state() != before


# --- batches ---------------------------------------------------------------------------------------------------------------------------------------


def _requests(n=3):
    return [llm_batch.BatchRequest(f"q{i}", [{"role": "user", "content": f"質問{i}"}], max_tokens=50) for i in range(n)]


def test_run_asks_one_after_the_other_through_the_gateway_and_a_failure_is_a_result(env, monkeypatch):
    llm_quota.set_limit("tenant_daily_tokens", 1, by="a", reason="r")  # tiny limit: counts only features that are not exempt
    out = llm_batch.run(_requests(2), feature="judge", tenant_id="tenant_a")
    assert {k: v.text for k, v in out.items()} == {"q0": "ok", "q1": "ok"} and all(not v.error for v in out.values())
    calls = [r for r in llm_usage.read(1) if r.get("event") == "call" and r["feature"] == "judge"]
    assert len(calls) == 2 and calls[0]["batch"] is False  # each went through the gateway
    refused = llm_batch.run(_requests(1), feature="generate", tenant_id="tenant_a")["q0"]  # the scoring used the tenant's tokens up
    assert refused.error == "quota:tenant_daily_tokens"
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    assert llm_batch.run(_requests(1), feature="judge")["q0"].error.startswith("not_allowed")
    with pytest.raises(llm_batch.BatchError):
        llm_batch.run([])
    with pytest.raises(llm_batch.BatchError):
        llm_batch.run([llm_batch.BatchRequest("same", []), llm_batch.BatchRequest("same", [])])


class FakeProvider:
    """The provider's batch API: files and batches."""

    def __init__(self, status="completed", answer_status=200):
        self.status, self.answer_status = status, answer_status
        self.uploaded, self.created = [], []
        self.files = SimpleNamespace(create=self._create_file, content=self._content)
        self.batches = SimpleNamespace(create=self._create_batch, retrieve=self._retrieve)

    def _create_file(self, file, purpose):
        self.uploaded.append((file, purpose))
        return SimpleNamespace(id="file-in")

    def _create_batch(self, input_file_id, endpoint, completion_window):
        self.created.append((input_file_id, endpoint, completion_window))
        return SimpleNamespace(id="batch-1", status="validating")

    def _retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, status=self.status, output_file_id="file-out" if self.status == "completed" else None,
                               request_counts=SimpleNamespace(total=3, completed=3, failed=0))

    def _content(self, file_id):
        rows = []
        for i in range(2):  # q2 gets no answer at all
            body = {"choices": [{"message": {"content": f"答え{i}"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
            rows.append({"custom_id": f"q{i}", "response": {"status_code": 200 if i == 0 else self.answer_status, "body": body if i == 0 else {}}, "error": None})
        return SimpleNamespace(text="\n".join(json.dumps(r) for r in rows))


def test_a_native_batch_is_submitted_polled_and_collected_with_the_usage_written_once(env):
    provider = FakeProvider(answer_status=500)
    job = llm_batch.submit(_requests(3), feature="judge", tenant_id="tenant_a", client=provider)
    assert job["status"] == "validating" and job["profile"] == "large" and job["request_ids"] == ["q0", "q1", "q2"]
    (file, purpose), = provider.uploaded
    lines = [json.loads(l) for l in file[1].decode("utf-8").splitlines()]
    assert purpose == "batch" and provider.created == [("file-in", "/v1/chat/completions", "24h")]
    assert lines[0]["custom_id"] == "q0" and lines[0]["body"]["model"] == "large-model" and lines[0]["body"]["max_tokens"] == 50
    assert "質問0" not in json.dumps(llm_batch.get_job(job["job_id"]), ensure_ascii=False)  # the job file keeps ids, not the texts
    polled = llm_batch.poll(job["job_id"], client=provider)
    assert polled["status"] == "completed" and polled["request_counts"]["total"] == 3
    results = llm_batch.collect(job["job_id"], client=provider)
    assert results["q0"].text == "答え0" and results["q0"].input_tokens == 100
    assert results["q1"].error == "provider_error" and results["q2"].error == "no_answer"
    usage = [r for r in llm_usage.read(1) if r.get("batch")]
    assert len(usage) == 3 and sorted(r["outcome"] for r in usage) == ["error", "error", "ok"]
    ok = next(r for r in usage if r["outcome"] == "ok")
    assert ok["cost"] == pytest.approx((100 * 10 + 50 * 20) / 1_000_000 * 0.5)  # the batch price factor
    llm_batch.collect(job["job_id"], client=provider)  # asking again does not write the usage again
    assert len([r for r in llm_usage.read(1) if r.get("batch")]) == 3
    assert llm_batch.list_jobs()[0]["collected"] is True


def test_a_batch_is_refused_when_it_cannot_be_made_or_is_not_finished(env, monkeypatch):
    with pytest.raises(llm_batch.BatchError, match="no profile"):
        llm_batch.submit(_requests(1), feature="generate", tenant_id="tenant_a", client=FakeProvider())  # the route's profile offers no batch
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    with pytest.raises(llm_batch.BatchError, match="not allowed"):
        llm_batch.submit(_requests(1), feature="judge", client=FakeProvider())
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    running = FakeProvider(status="in_progress")
    job = llm_batch.submit(_requests(1), feature="judge", client=running)
    with pytest.raises(llm_batch.BatchError, match="not finished"):
        llm_batch.collect(job["job_id"], client=running)
    failed = FakeProvider(status="expired")
    job2 = llm_batch.submit(_requests(1), feature="judge", client=failed)
    with pytest.raises(llm_batch.BatchError, match="expired"):
        llm_batch.collect(job2["job_id"], client=failed)
    with pytest.raises(llm_batch.BatchError, match="not found"):
        llm_batch.get_job("job-nope")
    with pytest.raises(llm_batch.BatchError, match="not valid"):
        llm_batch.get_job("../x")

    class Refusing(FakeProvider):
        def _create_file(self, file, purpose):
            raise ConnectionError("down")

    with pytest.raises(llm_batch.BatchError, match="provider refused"):
        llm_batch.submit(_requests(1), feature="judge", client=Refusing())


def test_the_scoring_of_an_evaluation_can_go_through_a_batch(env):
    cases = [{"case_id": "c1", "question": "期限は", "reference": "3営業日"}, {"case_id": "c2", "question": "場所は", "reference": "本社"}]
    rows = [{"case_id": "c1", "answered": True, "answer_text": "3営業日です"}, {"case_id": "c2", "answered": False}]
    requests = llm_judge.build_batch_requests(cases, rows)
    assert [r.custom_id for r in requests] == ["c1"] and "3営業日" in requests[0].messages[1]["content"]
    results = {"c1": llm_batch.BatchResult("c1", text='{"verdict": "pass", "reason": "同じ"}'), "c9": llm_batch.BatchResult("c9", error="no_answer")}
    assert llm_judge.judgements_from_batch(results) == {"c1": {"verdict": "pass", "reason": "同じ"}, "c9": {"verdict": "error", "reason": "no_answer"}}
    assert llm_judge.parse_judgement("not json")["verdict"] == "error"


# --- the alerts --------------------------------------------------------------------------------------------------------------------------------------


def test_the_alerts_see_a_failing_llm_a_stopped_endpoint_and_a_reached_limit():
    from webapi import alerting

    quiet = alerting.evaluate_alerts({"counters": {}})
    assert not [s for s in quiet["signals"] if s["name"].startswith("llm_")]  # no counters, no signal
    payload = {"counters": {"llm_calls_total": {"generate:ok": 5, "generate:error": 15, "generate:refused": 3},
                            "llm_breaker_open_total": {"local": 2}, "llm_quota_exceeded_total": {"user_daily_calls": 150}}}
    signals = {s["name"]: s for s in alerting.evaluate_alerts(payload)["signals"]}
    assert signals["llm_error_rate"]["status"] == "WARN" or signals["llm_error_rate"]["status"] == "CRITICAL"
    assert signals["llm_error_rate"]["detail"] == "15/20" and signals["llm_error_rate"]["status"] == "CRITICAL"
    assert signals["llm_breaker_open"]["status"] == "WARN" and signals["llm_quota_exceeded"]["status"] == "CRITICAL"
    few = alerting.evaluate_alerts({"counters": {"llm_calls_total": {"generate:error": 3}}})
    assert "llm_error_rate" not in {s["name"] for s in few["signals"]}  # too few calls to judge a rate


def test_the_gateways_counters_reach_the_metrics_hook(env):
    counted = []
    llm_gateway.set_metrics_hook(lambda name, label=None: counted.append((name, label)))
    llm_quota.set_limit("user_daily_calls", 1, by="a", reason="r")
    _ask()
    with pytest.raises(llm_quota.QuotaExceeded):
        _ask()
    assert ("llm_calls_total", "generate:ok") in counted and ("llm_quota_exceeded_total", "user_daily_calls") in counted


# --- the API ---------------------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def api(env, monkeypatch):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    from webapi import main

    return TestClient(main.app)


def _actions(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    return [json.loads(line).get("action") for line in path.read_text(encoding="utf-8").splitlines()]


def test_limits_are_read_and_set_with_a_reason_and_audited(api, tmp_path):
    for path in ("/admin/limits", "/admin/llm-assignments"):
        assert api.get(path).status_code in (401, 403)
    assert api.post("/admin/limits", headers=ADMIN, json={"name": "user_daily_calls", "value": 3, "reason": ""}).status_code == 422
    done = api.post("/admin/limits", headers=ADMIN, json={"name": "user_daily_calls", "value": 3, "reason": "pilot"})
    assert done.status_code == 200 and next(l for l in done.json()["limits"] if l["name"] == "user_daily_calls")["value"] == 3
    assert api.post("/admin/limits", headers=ADMIN, json={"name": "user_daily_calls", "value": -1, "reason": "x"}).status_code == 400
    assert api.post("/admin/limits", headers=ADMIN, json={"name": "nonsense", "value": 1, "reason": "x"}).status_code == 400
    assert "llm_limit_set" in _actions(tmp_path)
    _ask()
    assert api.get("/admin/limits", headers=ADMIN).json()["tenants"]["tenant_a"]["calls"] == 1


def test_a_tenant_administrator_sets_limits_for_their_tenant_only(api):
    assert api.post("/admin/limits", headers=TENANT_A, json={"name": "tenant_daily_tokens", "value": 100, "reason": "x"}).status_code == 403
    assert api.post("/admin/limits", headers=TENANT_A, json={"name": "tenant_daily_tokens", "value": 100, "reason": "x", "tenant_id": "tenant_b"}).status_code == 403
    assert api.post("/admin/limits", headers=TENANT_A, json={"name": "tenant_daily_tokens", "value": 100, "reason": "x", "tenant_id": "tenant_a"}).status_code == 200
    assert api.post("/admin/limits", headers=TENANT_A, json={"name": "global_daily_cost", "value": 1, "reason": "x", "tenant_id": "tenant_a"}).status_code == 400
    _ask(tenant="tenant_a")
    _ask(tenant="tenant_b")
    seen = api.get("/admin/limits", headers=TENANT_A).json()
    assert list(seen["tenants"]) == ["tenant_a"] and all(u["tenant_id"] == "tenant_a" for u in seen["users"])
    assert api.get("/admin/limits?tenant_id=tenant_b", headers=TENANT_A).status_code == 403


def test_assignments_are_the_installation_administrators_and_audited(api, tmp_path):
    assert api.post("/admin/llm-assignments", headers=TENANT_A, json={"kind": "global", "tier": "premium", "reason": "x"}).status_code == 403
    listed = api.get("/admin/llm-assignments", headers=TENANT_A).json()  # anyone may read
    assert listed["tiers"] == ["premium", "economy"] and listed["assignments"] == [] and listed["profiles_file_configured"] is True
    done = api.post("/admin/llm-assignments", headers=ADMIN, json={"kind": "role", "id": "admin", "tier": "premium", "reason": "admins review"})
    assert done.status_code == 200 and done.json()["assignments"][0]["kind"] == "role"
    assert api.post("/admin/llm-assignments", headers=ADMIN, json={"kind": "role", "id": "admin", "tier": "gold", "reason": "x"}).status_code == 400
    assert api.post("/admin/llm-assignments", headers=ADMIN, json={"kind": "role", "id": "admin", "tier": None, "reason": "undo"}).json()["assignments"] == []
    assert "llm_assignment_set" in _actions(tmp_path)
