"""S2: the breaker, the model profiles, the usage log, and the gateway that joins them."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import circuit_breaker, integrations, llm, llm_gateway, llm_profiles, llm_usage, qa
from rag_core.circuit_breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker, MemoryStore

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
ADMIN = {"x-admin-token": ADMIN_TOKEN}


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _breaker(clock, **kw):
    return CircuitBreaker("t", threshold=kw.get("threshold", 3), open_seconds=kw.get("open_seconds", 60),
                          probes=kw.get("probes", 1), store=MemoryStore(), clock=clock)


# --- the breaker ------------------------------------------------------------------------------------------------------------------


def test_the_breaker_opens_after_failures_in_a_row_and_a_success_resets_the_count():
    clock = Clock()
    b = _breaker(clock)
    b.record_failure(); b.record_failure(); b.record_success(); b.record_failure(); b.record_failure()
    assert b.state() == CLOSED and b.allow()
    b.record_failure()
    assert b.state() == OPEN and not b.allow()


def test_an_open_breaker_lets_one_trial_through_after_the_period_and_closes_on_success():
    clock = Clock()
    b = _breaker(clock, threshold=1)
    b.record_failure()
    clock.now += 59
    assert not b.allow()
    clock.now += 2
    assert b.state() == HALF_OPEN
    assert b.allow() and not b.allow()  # one trial, the second caller waits
    b.record_success()
    assert b.state() == CLOSED and b.allow()


def test_a_failed_trial_opens_the_breaker_for_another_period():
    clock = Clock()
    b = _breaker(clock, threshold=1)
    b.record_failure()
    clock.now += 61
    assert b.allow()
    b.record_failure()
    assert b.state() == OPEN
    clock.now += 30
    assert not b.allow()
    clock.now += 31
    assert b.allow()


def test_changes_of_state_reach_the_listeners_and_a_listener_cannot_break_a_call():
    seen = []
    circuit_breaker.add_listener(lambda key, old, new, at: seen.append((key, old, new)))
    circuit_breaker.add_listener(lambda *a: 1 / 0)
    clock = Clock()
    b = _breaker(clock, threshold=1)
    b.record_failure()
    clock.now += 61
    b.allow()
    b.record_success()
    assert seen == [("t", CLOSED, OPEN), ("t", OPEN, HALF_OPEN), ("t", HALF_OPEN, CLOSED)]


def test_breakers_can_share_a_store():
    store, clock = MemoryStore(), Clock()
    a = CircuitBreaker("k", threshold=1, store=store, clock=clock)
    b = CircuitBreaker("k", threshold=1, store=store, clock=clock)  # another worker, the same store
    a.record_failure()
    assert b.state() == OPEN and not b.allow()


# --- model profiles ---------------------------------------------------------------------------------------------------------------


def _write_profiles(tmp_path, monkeypatch, data=None):
    data = data if data is not None else {
        "profiles": {
            "local": {"base_url": "http://localhost:11434/v1", "model": "local-model", "timeout_seconds": 12, "max_tokens": 300},
            "cloud": {"base_url": "https://llm.example.co.jp/v1", "api_key_env": "CLOUD_KEY_FOR_TEST", "model": "cloud-model",
                      "cost": {"input_per_million": 1.0, "output_per_million": 2.0}},
        },
        "routes": {"generate": ["local", "cloud"], "judge": ["cloud"]},
        "tenants": {"tenant_a": {"generate": ["cloud"]}},
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "LLM_PROFILES_FILE", str(path))
    return path


def test_without_a_file_there_is_one_implicit_profile_of_the_old_settings(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROFILES_FILE", "")
    monkeypatch.setattr(config, "CHAT_MODEL", "old-model")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
    only = llm_profiles.chain("generate", "tenant_a")
    assert [p.name for p in only] == ["default"] and only[0].model == "old-model" and only[0].implicit
    assert llm_profiles.fingerprint() == "" and not llm_profiles.configured()


def test_routes_tenants_locations_and_costs(tmp_path, monkeypatch):
    _write_profiles(tmp_path, monkeypatch)
    names = lambda f, t=None: [p.name for p in llm_profiles.chain(f, t)]  # noqa: E731
    assert names("generate") == ["local", "cloud"] and names("generate", "tenant_a") == ["cloud"]
    assert names("judge") == ["cloud"] and names("rewrite") == ["local"]  # a feature without a route: the first profile
    local, cloud = llm_profiles.all_profiles()["local"], llm_profiles.all_profiles()["cloud"]
    assert local.location == "local" and cloud.location == "external" and cloud.host == "llm.example.co.jp"
    assert local.timeout_seconds == 12.0 and local.max_tokens == 300
    assert cloud.cost(1_000_000, 500_000) == pytest.approx(2.0)
    assert llm_profiles.fingerprint() != ""


def test_a_bad_file_is_named_without_a_key(tmp_path, monkeypatch):
    for bad in ({}, {"profiles": {}}, {"profiles": {"a": {"base_url": "x"}}},
                {"profiles": {"a": {"model": "m"}}, "routes": {"generate": ["zzz"]}},
                {"profiles": {"a": {"model": "m"}}, "routes": {"nonsense": ["a"]}},
                {"profiles": {"a": {"model": "m", "timeout_seconds": "slow"}}}):
        _write_profiles(tmp_path, monkeypatch, bad)
        llm_profiles._CACHE.update(key=None, value=None)
        assert llm_profiles.validate(), bad
    monkeypatch.setattr(config, "LLM_PROFILES_FILE", str(tmp_path / "missing.json"))
    assert llm_profiles.validate() == ["LLM_PROFILES_FILE does not name a readable file"]


def test_a_changed_file_is_read_again(tmp_path, monkeypatch):
    path = _write_profiles(tmp_path, monkeypatch)
    before = llm_profiles.fingerprint()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["profiles"]["local"]["model"] = "another-model"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert llm_profiles.all_profiles()["local"].model == "another-model" and llm_profiles.fingerprint() != before


def test_production_does_not_use_an_external_profile_unless_allowed(tmp_path, monkeypatch):
    _write_profiles(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(config, "LLM_EXTERNAL_ALLOWED", False)
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    assert [p.name for p in llm_gateway._usable("generate", None)] == ["local"]
    assert llm.check("generate", "default").allowed  # the local profile is enough
    assert not llm.check("generate", "tenant_a").allowed  # this tenant's route is the external profile only
    assert llm.check("generate", "tenant_a").layer == "endpoint"
    assert llm.production_problem() is None
    monkeypatch.setattr(config, "LLM_EXTERNAL_ALLOWED", True)
    assert [p.name for p in llm_gateway._usable("generate", None)] == ["local", "cloud"]


# --- the gateway -------------------------------------------------------------------------------------------------------------------


class Provider:
    """A fake provider: a client per host that answers, or fails, as told."""

    def __init__(self):
        self.fail = set()
        self.calls = []

    def client(self, host):
        provider = self

        def create(**kwargs):
            provider.calls.append((host, kwargs["model"]))
            if host in provider.fail:
                raise ConnectionError("down")
            usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=f"answer from {host}"))], usage=usage)

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture
def provider(tmp_path, monkeypatch):
    _write_profiles(tmp_path, monkeypatch)
    prov = Provider()
    monkeypatch.setattr(llm_profiles, "make_client", lambda profile: prov.client(profile.host))
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_FEATURES", ["generate", "rewrite", "verify", "judge"])
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 0)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "LLM_USAGE_ENABLED", True)
    return prov


def _complete(feature="generate", tenant="default"):
    return llm.complete([{"role": "user", "content": "質問"}], feature=feature, tenant_id=tenant)


def test_the_first_profile_answers_and_the_usage_line_holds_no_text(provider, tmp_path):
    assert _complete() == "answer from localhost"
    [row] = [r for r in llm_usage.read(1) if r["event"] == "call"]
    assert row["profile"] == "local" and row["model"] == "local-model" and row["outcome"] == "ok" and row["location"] == "local"
    assert row["input_tokens"] == 11 and row["output_tokens"] == 7 and row["estimated"] is False and row["cost"] == 0
    assert "質問" not in json.dumps(row, ensure_ascii=False) and "answer" not in json.dumps(row)


def test_the_next_profile_answers_when_the_first_fails(provider):
    provider.fail.add("localhost")
    assert _complete() == "answer from llm.example.co.jp"
    outcomes = [(r["profile"], r["outcome"]) for r in llm_usage.read(1) if r["event"] == "call"]
    assert outcomes == [("local", "error"), ("cloud", "ok")]
    cloud = [r for r in llm_usage.read(1) if r.get("profile") == "cloud"][0]
    assert cloud["cost"] == pytest.approx((11 * 1.0 + 7 * 2.0) / 1_000_000)


def test_an_open_breaker_is_skipped_without_a_call(provider):
    provider.fail.add("localhost")
    for _ in range(3):
        _complete()
    provider.calls.clear()
    assert _complete() == "answer from llm.example.co.jp"
    assert [h for h, _m in provider.calls] == ["llm.example.co.jp"]  # the local profile was not even tried
    assert llm_gateway.status()["local"]["breaker"] == OPEN
    skipped = [r for r in llm_usage.read(1) if r.get("outcome") == "breaker_open"]
    assert skipped and skipped[-1]["profile"] == "local"


def test_when_every_profile_fails_the_error_reaches_the_caller_and_when_every_breaker_is_open_it_is_unavailable(provider):
    provider.fail.update({"localhost", "llm.example.co.jp"})
    for _ in range(3):  # three failures in a row open both breakers
        with pytest.raises(ConnectionError):
            _complete()
    provider.calls.clear()
    with pytest.raises(llm_gateway.LLMUnavailable):  # both breakers are open now: nothing is called, nothing is waited for
        _complete()
    assert provider.calls == []
    assert llm.stats()["generate"]["errors"] >= 4


def test_a_malformed_request_does_not_count_against_the_endpoint(provider, monkeypatch):
    class Refused(Exception):
        status_code = 400

    def create(**kwargs):
        raise Refused("bad request")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_profiles, "make_client", lambda profile: client)  # every profile of the route refuses it
    for _ in range(5):
        with pytest.raises(Refused):
            llm_gateway.invoke("generate", "default", lambda c, p: c.chat.completions.create(model=p.model))
    assert all(llm_gateway.breaker_for(p).state() == CLOSED for p in llm_profiles.all_profiles().values())


def test_the_breaker_can_be_turned_off(provider, monkeypatch):
    monkeypatch.setattr(config, "LLM_BREAKER_ENABLED", False)
    provider.fail.update({"localhost", "llm.example.co.jp"})
    for _ in range(5):
        with pytest.raises(ConnectionError):
            _complete()
    assert len([c for c in provider.calls if c[0] == "localhost"]) == 5


def test_tokens_are_estimated_when_the_provider_reports_none(provider, monkeypatch):
    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="回答です" * 10))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_profiles, "make_client", lambda profile: client)
    _complete()
    row = [r for r in llm_usage.read(1) if r["event"] == "call"][0]
    assert row["estimated"] is True and row["input_tokens"] > 0 and row["output_tokens"] > 0


def test_a_stream_tries_the_next_profile_only_before_the_first_piece(provider, monkeypatch):
    def make(client_host_fails, pieces):
        def stream(client, profile):
            if profile.host in client_host_fails:
                raise ConnectionError("down")
            yield from pieces

        return stream

    out = list(llm_gateway.invoke_stream("generate", "default", make({"localhost"}, ["a", "b"])))
    assert out == ["a", "b"]  # the local profile failed before anything was sent; the cloud one streamed

    def broken(client, profile):
        yield "a"
        raise ConnectionError("cut")

    got = []
    with pytest.raises(ConnectionError):
        for piece in llm_gateway.invoke_stream("generate", "default", broken):
            got.append(piece)
    assert got == ["a"]  # after the first piece a failure ends the stream; there is no second answer in the middle


def test_the_generation_path_uses_the_profile_and_the_gateway(provider):
    messages = [{"role": "user", "content": "質問"}]
    provider.fail.add("localhost")
    resp = llm_gateway.invoke("generate", "default", lambda c, p: qa._create_chat_completion(c, messages, p))
    assert resp.choices[0].message.content == "answer from llm.example.co.jp"
    assert provider.calls == [("localhost", "local-model"), ("llm.example.co.jp", "cloud-model")]


def test_the_counters_go_to_the_hook(provider):
    counted = []
    llm_gateway.set_metrics_hook(lambda name, label=None: counted.append((name, label)))
    _complete()
    assert ("llm_calls_total", "generate:ok") in counted


# --- usage: reading it back ------------------------------------------------------------------------------------------------------------


def test_usage_is_added_up_by_tenant_and_by_feature(provider):
    _complete("generate", "tenant_b")
    _complete("judge", "tenant_b")
    _complete("generate", "default")
    by_tenant = {r["tenant_id"]: r for r in llm_usage.summarize(1)}
    assert by_tenant["tenant_b"]["calls"] == 2 and by_tenant["default"]["calls"] == 1 and by_tenant["tenant_b"]["input_tokens"] == 22
    assert {r["feature"]: r["calls"] for r in llm_usage.summarize(1, by="feature")} == {"generate": 2, "judge": 1}


def test_the_time_in_each_state_is_added_up_from_the_changes(provider):
    llm_gateway.install()
    clock = Clock(1_000_000.0)
    b = CircuitBreaker("llm:local", threshold=1, open_seconds=60, store=MemoryStore(), clock=clock)
    b.record_failure()
    clock.now += 100
    b.allow()
    b.record_success()
    now = datetime.fromtimestamp(clock.now + 40, tz=timezone.utc)
    seconds = llm_usage.state_seconds(1, now=now, today=now.date())["llm:local"]
    assert seconds[OPEN] == pytest.approx(60.0, abs=1) or seconds[OPEN] == pytest.approx(100.0, abs=1)
    assert seconds[CLOSED] == pytest.approx(40.0, abs=1)


def test_the_usage_log_never_breaks_a_call(provider, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", "/proc/not-writable")
    assert _complete() == "answer from localhost"


# --- what the screens read ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    from webapi import main

    return TestClient(main.app)


def test_a_user_sees_a_degraded_service_only_as_a_code(provider, api, monkeypatch):
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "llm")
    assert api.get("/system/status").json() == {"service": "normal", "answers": "generated", "reason": ""}
    provider.fail.update({"localhost", "llm.example.co.jp"})
    for _ in range(3):
        with pytest.raises(ConnectionError):
            _complete()
    seen = api.get("/system/status").json()
    assert seen == {"service": "degraded", "answers": "extractive", "reason": "llm_unavailable"}
    text = json.dumps(seen)
    assert "example.co.jp" not in text and "model" not in text  # no host, no model
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "extractive")
    assert api.get("/system/status").json()["answers"] == "extractive"


def test_the_administrator_sees_every_profile_and_its_breaker(provider, api):
    provider.fail.add("localhost")
    for _ in range(3):
        _complete()
    body = api.get("/admin/system/status", headers=ADMIN).json()
    assert body["llm"]["profiles"]["local"]["breaker"] == OPEN and body["llm"]["profiles"]["cloud"]["breaker"] == CLOSED
    assert body["llm"]["profiles_file_problems"] == []
    llm_state = next(i for i in body["integrations"] if i["name"] == "llm")
    assert llm_state["state"] == integrations.OK  # the cloud profile still answers


def test_the_answer_cache_key_follows_the_profiles_file(tmp_path, monkeypatch):
    from rag_core import answer_cache

    monkeypatch.setattr(config, "LLM_PROFILES_FILE", "")
    without = answer_cache._pipeline_state()
    path = _write_profiles(tmp_path, monkeypatch)
    with_file = answer_cache._pipeline_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["profiles"]["local"]["model"] = "changed"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert len({without, with_file, answer_cache._pipeline_state()}) == 3


def test_the_example_profiles_file_is_valid(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(config, "LLM_PROFILES_FILE", str(Path(config.BASE_DIR) / "config" / "deploy" / "llm_profiles.example.json"))
    assert llm_profiles.validate() == []
    assert [p.name for p in llm_profiles.chain("generate")] == ["local", "cloud_low_cost"]
    assert llm_profiles.all_profiles()["local"].location == "local"


def test_a_day_of_usage_is_kept_in_its_own_file(provider, tmp_path):
    _complete()
    day = datetime.now(timezone.utc).date()
    assert (tmp_path / "runs" / "usage" / f"llm_usage-{day.isoformat()}.jsonl").exists()
    assert list(llm_usage.read(2, today=day + timedelta(days=1))) != []  # yesterday and "today" of a later reader
    assert list(llm_usage.read(2, today=day + timedelta(days=5))) == []
