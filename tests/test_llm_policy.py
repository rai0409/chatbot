import sys
import types

import pytest

import config
from rag_core import llm, utils


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name, value in {"LLM_ENABLED": None, "LLM_TENANT_ALLOWLIST": [], "LLM_TENANT_DENYLIST": [],
                        "LLM_FEATURES": ["generate"], "LLM_EXTERNAL_ALLOWED": False, "LLM_INTERNAL_HOSTS": ["localhost"],
                        "OPENAI_BASE_URL": None, "CHAT_GENERATION_MODE": "extractive",
                        "CHAT_COMPLETION_MAX_RETRIES": 1, "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS": 0.0}.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    llm.reset_stats()


def _reason(decision):
    return (decision.allowed, decision.layer)


def test_the_historical_switch_decides_when_llm_enabled_is_not_set(monkeypatch):
    assert _reason(llm.check("generate")) == (False, "global")
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "llm")
    assert _reason(llm.check("generate")) == (True, "")
    monkeypatch.setattr(config, "LLM_ENABLED", False)  # the kill switch wins
    assert _reason(llm.check("generate")) == (False, "global")
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "extractive")
    assert _reason(llm.check("generate")) == (True, "")  # on explicitly, answers stay extractive until the mode says llm
    assert llm.generation_mode() == "extractive"


def test_the_tenant_layer_denies_first_and_an_allowlist_limits(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_TENANT_DENYLIST", ["Tenant_B"])
    assert _reason(llm.check("generate", "tenant_b")) == (False, "tenant")
    assert _reason(llm.check("generate", "tenant_a")) == (True, "")
    monkeypatch.setattr(config, "LLM_TENANT_ALLOWLIST", ["tenant_a", "tenant_b"])
    assert _reason(llm.check("generate", "tenant_b")) == (False, "tenant")  # the denylist still wins
    assert _reason(llm.check("generate", "tenant_a")) == (True, "")
    assert _reason(llm.check("generate", "tenant_c")) == (False, "tenant")
    assert llm.check("generate", "").tenant_id == "default"
    monkeypatch.setattr(config, "LLM_TENANT_ALLOWLIST", ["default"])
    assert _reason(llm.check("generate", None)) == (True, "")


def test_new_features_are_opt_in_even_when_the_llm_is_on(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    for feature in ("rewrite", "verify", "judge"):
        assert _reason(llm.check(feature)) == (False, "feature")
    monkeypatch.setattr(config, "LLM_FEATURES", ["Rewrite", " verify "])
    assert _reason(llm.check("rewrite")) == (True, "") and _reason(llm.check("verify")) == (True, "")
    assert _reason(llm.check("generate")) == (False, "feature") and _reason(llm.check("judge")) == (False, "feature")
    refused = llm.check("nonsense")
    assert refused.allowed is False and refused.layer == "feature" and "unknown feature" in refused.reason
    assert refused.as_dict()["feature"] == "nonsense"


@pytest.mark.parametrize("url, external", [
    (None, True), ("", True), ("http://localhost:8000/v1", False), ("http://127.0.0.1:8000/v1", False),
    ("http://[::1]:8000/v1", False), ("http://10.1.2.3/v1", False), ("http://192.168.0.5:11434/v1", False),
    ("http://8.8.8.8/v1", True), ("https://api.example.com/v1", True), ("llm.internal:8000/v1", True),
])
def test_an_endpoint_is_external_unless_it_is_loopback_private_or_listed(monkeypatch, url, external):
    monkeypatch.setattr(config, "OPENAI_BASE_URL", url)
    assert llm.external_endpoint() is external


def test_a_listed_host_is_internal(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "http://LLM.Internal:8000/v1")
    assert llm.external_endpoint() is True
    monkeypatch.setattr(config, "LLM_INTERNAL_HOSTS", ["localhost", "llm.internal"])
    assert llm.external_endpoint() is False


def test_in_production_an_external_endpoint_is_refused_unless_allowed(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    assert _reason(llm.check("generate")) == (True, "")  # development: no endpoint rule
    monkeypatch.setenv("APP_ENV", "production")
    assert _reason(llm.check("generate")) == (False, "endpoint")
    assert "LLM_EXTERNAL_ALLOWED" in llm.production_problem()
    monkeypatch.setattr(config, "LLM_EXTERNAL_ALLOWED", True)
    assert _reason(llm.check("generate")) == (True, "") and llm.production_problem() is None
    monkeypatch.setattr(config, "LLM_EXTERNAL_ALLOWED", False)
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "http://10.0.0.5:8000/v1")
    assert _reason(llm.check("generate")) == (True, "") and llm.production_problem() is None
    monkeypatch.setattr(config, "OPENAI_BASE_URL", None)
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    assert llm.production_problem() is None  # the LLM is off: nothing leaves


def test_the_production_guard_reports_the_llm_problem(monkeypatch):
    from webapi import production_guard

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    assert any("LLM_EXTERNAL_ALLOWED" in p for p in production_guard.production_problems())
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    assert not any("LLM_EXTERNAL_ALLOWED" in p for p in production_guard.production_problems())


def test_generation_mode_falls_back_to_extractive_for_a_tenant_or_feature_that_is_not_allowed(monkeypatch):
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "llm")
    assert llm.generation_mode("tenant_a") == "llm"
    monkeypatch.setattr(config, "LLM_TENANT_DENYLIST", ["tenant_a"])
    assert llm.generation_mode("tenant_a") == "extractive" and llm.generation_mode("tenant_b") == "llm"
    monkeypatch.setattr(config, "LLM_FEATURES", ["rewrite"])
    assert llm.generation_mode("tenant_b") == "extractive"
    monkeypatch.setattr(config, "LLM_FEATURES", ["generate"])
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "extractive")
    assert llm.generation_mode("tenant_b") == "extractive"


def test_the_answer_path_asks_the_policy_with_the_tenant():
    import inspect

    from rag_core import qa

    source = inspect.getsource(qa)
    assert source.count("llm.generation_mode(tenant_id)") == 2 and "resolve_chat_generation_mode() ==" not in source


# --- complete() ---------------------------------------------------------------------------------------------


class _Client:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=outcome))])


class _Http(Exception):
    def __init__(self, status):
        super().__init__(f"http {status}")
        self.status_code = status


def _on(monkeypatch, features=("rewrite",)):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_FEATURES", list(features))


def test_complete_returns_the_text_with_the_model_limits_and_counts_the_call(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(config, "CHAT_MODEL", "m-1")
    client = _Client(["整えた文"])
    out = llm.complete([{"role": "user", "content": "x"}], feature="rewrite", client=client)
    assert out == "整えた文" and len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "m-1" and call["temperature"] == 0.0 and call["max_tokens"] == config.LLM_FEATURE_MAX_TOKENS
    assert call["timeout"] == float(config.CHAT_COMPLETION_TIMEOUT_SECONDS)
    assert llm.complete([], feature="rewrite", client=_Client([None]), max_tokens=64) == ""
    assert llm.stats()["rewrite"] == {"calls": 2, "errors": 0, "refused": 0}


def test_complete_refuses_without_calling_when_the_policy_says_no(monkeypatch):
    client = _Client(["never"])
    with pytest.raises(llm.LLMNotAllowed) as info:
        llm.complete([], feature="rewrite", client=client)
    assert info.value.decision.layer == "global" and client.calls == []
    _on(monkeypatch, features=("verify",))
    with pytest.raises(llm.LLMNotAllowed) as info:
        llm.complete([], feature="rewrite", tenant_id="t", client=client)
    assert info.value.decision.layer == "feature" and llm.stats()["rewrite"]["refused"] == 2 and client.calls == []


def test_complete_retries_only_transient_errors(monkeypatch):
    _on(monkeypatch)
    client = _Client([_Http(503), "回復した"])
    assert llm.complete([], feature="rewrite", client=client) == "回復した" and len(client.calls) == 2
    client = _Client([_Http(401), "never"])
    with pytest.raises(_Http):
        llm.complete([], feature="rewrite", client=client)
    assert len(client.calls) == 1
    client = _Client([TimeoutError("slow"), TimeoutError("slow")])
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)
    with pytest.raises(TimeoutError):
        llm.complete([], feature="rewrite", client=client)
    assert len(client.calls) == 2 and llm.stats()["rewrite"]["errors"] == 2
    client = _Client([_Http(429), _Http(500), "ok"])
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 2)
    assert llm.complete([], feature="rewrite", client=client) == "ok"


# --- the client without a key ---------------------------------------------------------------------------------


@pytest.fixture
def fake_sdk(monkeypatch):
    made = []

    class OpenAI:
        def __init__(self, **kwargs):
            made.append(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    return made


def test_a_local_endpoint_needs_no_api_key_but_the_default_endpoint_still_does(monkeypatch, fake_sdk):
    monkeypatch.setattr(utils, "OPENAI_API_KEY", "")
    monkeypatch.setattr(utils, "OPENAI_BASE_URL", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is missing"):
        utils.ensure_openai_client()
    utils.ensure_openai_client(base_url="http://localhost:8000/v1")
    assert fake_sdk[-1] == {"base_url": "http://localhost:8000/v1", "api_key": "not-needed"}
    monkeypatch.setattr(utils, "OPENAI_BASE_URL", "http://10.0.0.5/v1")
    utils.ensure_openai_client()
    assert fake_sdk[-1]["base_url"] == "http://10.0.0.5/v1"
    monkeypatch.setattr(utils, "OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(utils, "OPENAI_BASE_URL", None)
    utils.ensure_openai_client()
    assert fake_sdk[-1] == {"api_key": "sk-real"}
    utils.ensure_openai_client(base_url="http://localhost:8000/v1")
    assert fake_sdk[-1]["api_key"] == "sk-real"  # a key that is set is used
