from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry


# production_safe profile's max_candidates_internal (see
# configs/product_profiles/production_safe.json). Used to assert the clamp.
SAFE_MAX_INTERNAL = 8


class _Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _fake_answer(question="質問です"):
    ans = SimpleNamespace(
        intent="faq",
        guard_reason=None,
        used_fallback=False,
        citations=[],
        to_dict=lambda: {"answer_text": "回答です", "citations": [], "retrieved": []},
    )
    trace = {
        "request_id": "req-1",
        "normalized_query": question,
        "intent": "faq",
        "final_guard_reason": None,
        "final_used_fallback": False,
        "citations_count": 0,
        "latency_ms": 1,
        "after_rerank": [],
        "answer_mode": "grounded",
    }
    return ans, trace


def _setup(monkeypatch, tmp_path, *, flag=False, auth=False):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("API_AUTH_TENANT_MAP", raising=False)
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", flag)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "TOP_K", 20)

    if auth:
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b")
        monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a,key-b=tenant_a|tenant_b")

    answer_cache.clear()
    metrics_registry.reset()

    pipeline = _Recorder(result=_fake_answer())
    stream_pipeline = _Recorder(result=iter(()))
    audit = []
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", pipeline)
    monkeypatch.setattr(main, "answer_query_stream", stream_pipeline)
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)
    monkeypatch.setattr(main, "append_audit_event", lambda kind, event: audit.append((kind, event)))
    return SimpleNamespace(pipeline=pipeline, stream_pipeline=stream_pipeline, audit=audit)


def _last_chat_event(audit):
    for kind, event in reversed(audit):
        if kind == "chat":
            return event
    return None


# --- Unit: resolve_chat_runtime_profile ------------------------------------


def test_resolver_returns_none_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    assert main.resolve_chat_runtime_profile("default") is None


def test_resolver_default_tenant_resolves_production_safe(monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)
    rp = main.resolve_chat_runtime_profile("default")
    assert rp is not None
    assert rp["profile_name"] == "production_safe"
    assert rp["max_candidates_internal"] == SAFE_MAX_INTERNAL


def test_resolver_unknown_tenant_falls_back_to_safe(monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)
    rp = main.resolve_chat_runtime_profile("no_such_tenant_xyz")
    # default mapping unknown_tenant_policy=default_profile=production_safe
    assert rp["profile_name"] == "production_safe"
    assert rp["max_candidates_internal"] == SAFE_MAX_INTERNAL


def test_resolver_invalid_profile_fails_closed_to_safe(monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *a, **k: {"resolved_profile": "definitely_not_a_real_profile", "decision": "resolved"},
    )
    rp = main.resolve_chat_runtime_profile("default")
    assert rp["profile_name"] == "production_safe"
    assert rp["decision"] == "invalid_profile_fallback_safe"


def test_resolver_resolution_exception_fails_closed_to_safe(monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)

    def _boom(*a, **k):
        raise RuntimeError("mapping unavailable")

    monkeypatch.setattr(main, "resolve_tenant_product_profile", _boom)
    rp = main.resolve_chat_runtime_profile("default")
    assert rp["profile_name"] == "production_safe"
    # resolution failed, but the safe profile still loads with its limit
    assert rp["max_candidates_internal"] == SAFE_MAX_INTERNAL


# --- /chat default-off unchanged -------------------------------------------


def test_chat_default_off_unchanged(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=False)
    client = TestClient(main.app)

    resp = client.post("/chat", json={"question": "質問です", "top_k": 17, "tenant_id": "default"})

    assert resp.status_code == 200
    # top_k passed through unchanged (no profile clamp)
    assert fakes.pipeline.calls[0][1]["top_k"] == 17
    event = _last_chat_event(fakes.audit)
    assert "product_profile" not in event
    assert "tenant_profile_decision" not in event
    assert "chat_tenant_profile_total" not in metrics_registry.snapshot()


def test_chat_stream_default_off_unchanged(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=False)
    ans, trace = _fake_answer()
    fakes.stream_pipeline.result = iter([("final", (ans, trace))])
    client = TestClient(main.app)

    resp = client.post("/chat/stream", json={"question": "質問です", "top_k": 17, "tenant_id": "default"})

    assert resp.status_code == 200
    assert fakes.stream_pipeline.calls[0][1]["top_k"] == 17
    event = _last_chat_event(fakes.audit)
    assert "product_profile" not in event
    assert "chat_tenant_profile_total" not in metrics_registry.snapshot()


# --- /chat flag-on resolves and uses the tenant profile --------------------


def test_chat_flag_on_resolves_and_clamps(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True)
    client = TestClient(main.app)

    resp = client.post("/chat", json={"question": "質問です", "top_k": 20, "tenant_id": "default"})

    assert resp.status_code == 200
    # top_k clamped to production_safe max_candidates_internal
    assert fakes.pipeline.calls[0][1]["top_k"] == SAFE_MAX_INTERNAL
    assert fakes.pipeline.calls[0][1]["tenant_id"] == "default"
    event = _last_chat_event(fakes.audit)
    assert event["product_profile"] == "production_safe"
    assert event["tenant_profile_decision"]
    assert metrics_registry.snapshot()["chat_tenant_profile_total"] == {"production_safe": 1}


def test_chat_stream_flag_on_resolves_and_clamps(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True)
    ans, trace = _fake_answer()
    fakes.stream_pipeline.result = iter([("final", (ans, trace))])
    client = TestClient(main.app)

    resp = client.post("/chat/stream", json={"question": "質問です", "top_k": 20, "tenant_id": "default"})

    assert resp.status_code == 200
    assert "event: final" in resp.text
    assert fakes.stream_pipeline.calls[0][1]["top_k"] == SAFE_MAX_INTERNAL
    event = _last_chat_event(fakes.audit)
    assert event["product_profile"] == "production_safe"
    assert metrics_registry.snapshot()["chat_tenant_profile_total"] == {"production_safe": 1}


def test_chat_flag_on_unknown_tenant_uses_safe_profile(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True)
    client = TestClient(main.app)

    resp = client.post("/chat", json={"question": "質問です", "top_k": 20, "tenant_id": "ghost_tenant"})

    assert resp.status_code == 200
    assert fakes.pipeline.calls[0][1]["top_k"] == SAFE_MAX_INTERNAL
    event = _last_chat_event(fakes.audit)
    assert event["product_profile"] == "production_safe"


def test_chat_flag_on_invalid_profile_fails_closed(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True)
    monkeypatch.setattr(
        main,
        "resolve_tenant_product_profile",
        lambda *a, **k: {"resolved_profile": "definitely_not_a_real_profile", "decision": "resolved"},
    )
    client = TestClient(main.app)

    resp = client.post("/chat", json={"question": "質問です", "top_k": 20, "tenant_id": "default"})

    assert resp.status_code == 200
    event = _last_chat_event(fakes.audit)
    assert event["product_profile"] == "production_safe"
    assert event["tenant_profile_decision"] == "invalid_profile_fallback_safe"


# --- Authorization / isolation / safety with flag on -----------------------


def test_tenant_authorization_blocks_before_profile_runtime(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True, auth=True)
    client = TestClient(main.app)

    # key-a is mapped to tenant_a only; requesting tenant_b must 403 before
    # any pipeline/profile runtime work.
    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 403
    assert fakes.pipeline.calls == []
    # no profile metric recorded for a rejected request
    assert "chat_tenant_profile_total" not in metrics_registry.snapshot()
    assert "key-a" not in resp.text


def test_authorized_tenant_threads_isolation_with_flag_on(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True, auth=True)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 200
    # tenant isolation: the authorized tenant id is threaded to the pipeline
    assert fakes.pipeline.calls[0][1]["tenant_id"] == "tenant_a"


def test_no_raw_api_key_in_audit_or_metrics_with_flag_on(monkeypatch, tmp_path):
    fakes = _setup(monkeypatch, tmp_path, flag=True, auth=True)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 200
    blob = json.dumps([e for _, e in fakes.audit], ensure_ascii=False) + json.dumps(metrics_registry.snapshot())
    assert "key-a" not in blob
    assert "key-a" not in resp.text
