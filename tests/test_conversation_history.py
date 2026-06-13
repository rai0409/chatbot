from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import conversation_store, main, metrics_registry


def _store_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))


# --- module: round-trip, isolation, retention -----------------------------


def test_append_and_get_round_trip(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "t1", question="手順は？", answer_text="A", citations_count=2)
    turns = conversation_store.get_thread("tenant_a", "fp1", "t1")
    assert len(turns) == 1
    assert turns[0]["question"] == "手順は？" and turns[0]["answer_text"] == "A"
    assert turns[0]["citations_count"] == 2


def test_tenant_isolation(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "t1", question="A-secret", answer_text="x")
    # different tenant, same identity -> cannot see tenant_a's thread
    assert conversation_store.get_thread("tenant_b", "fp1", "t1") == []
    assert conversation_store.list_threads("tenant_b", "fp1") == []


def test_user_identity_isolation(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "t1", question="only-mine", answer_text="x")
    # same tenant, different identity -> cannot see fp1's thread
    assert conversation_store.get_thread("tenant_a", "fp2", "t1") == []
    assert conversation_store.list_threads("tenant_a", "fp2") == []


def test_path_traversal_is_sanitized(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "../../escape", question="q", answer_text="a")
    # the thread is stored under the owner dir with a sanitized name, not outside
    owner = tmp_path / "runs" / "conversations" / "tenant_a" / "fp1"
    files = list(owner.glob("*.jsonl"))
    assert files and all(".." not in f.name for f in files)


def test_retention_max_count(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    for i in range(5):
        conversation_store.append_turn("tenant_a", "fp1", f"t{i}", question="q", answer_text="a")
    conversation_store.purge("tenant_a", "fp1", max_threads=2)
    assert len(conversation_store.list_threads("tenant_a", "fp1")) == 2


def test_retention_max_age(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "old", question="q", answer_text="a")
    import os, time
    p = tmp_path / "runs" / "conversations" / "tenant_a" / "fp1" / "old.jsonl"
    old = time.time() - 200 * 86400
    os.utime(p, (old, old))
    conversation_store.purge("tenant_a", "fp1", max_age_days=90)
    assert conversation_store.list_threads("tenant_a", "fp1") == []


def test_delete_thread(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "t1", question="q", answer_text="a")
    assert conversation_store.delete_thread("tenant_a", "fp1", "t1") is True
    assert conversation_store.get_thread("tenant_a", "fp1", "t1") == []


def test_no_secret_key_stored(monkeypatch, tmp_path):
    _store_root(monkeypatch, tmp_path)
    conversation_store.append_turn("tenant_a", "fp1", "t1", question="q", answer_text="a")
    root = tmp_path / "runs" / "conversations"
    blob = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*.jsonl"))
    for forbidden in ("sk-", "Bearer ", "X-Api-Key", "API_AUTH_KEYS"):
        assert forbidden not in blob


# --- endpoints: default-off + auth/tenant scoping --------------------------


def _setup_app(monkeypatch, tmp_path, *, enabled=False, auth=False):
    for var in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED", "CONVERSATION_HISTORY_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    if enabled:
        monkeypatch.setenv("CONVERSATION_HISTORY_ENABLED", "true")
    if auth:
        monkeypatch.setenv("API_AUTH_ENABLED", "true")
        monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b")
        monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a,key-b=tenant_a|tenant_b")
    metrics_registry.reset()
    answer_cache.clear()


def test_endpoints_404_when_disabled(monkeypatch, tmp_path):
    _setup_app(monkeypatch, tmp_path, enabled=False)
    client = TestClient(main.app)
    assert client.get("/chat/threads").status_code == 404
    assert client.post("/chat/threads", json={"thread_id": "t1", "question": "q"}).status_code == 404


def test_endpoints_round_trip_when_enabled(monkeypatch, tmp_path):
    _setup_app(monkeypatch, tmp_path, enabled=True)
    client = TestClient(main.app)
    assert client.post("/chat/threads", json={"thread_id": "t1", "question": "手順", "answer_text": "A"}).status_code == 200
    listed = client.get("/chat/threads").json()["threads"]
    assert any(t["thread_id"] == "t1" for t in listed)
    turns = client.get("/chat/threads/t1").json()["turns"]
    assert turns and turns[0]["question"] == "手順"


def test_endpoints_enforce_tenant_authorization(monkeypatch, tmp_path):
    _setup_app(monkeypatch, tmp_path, enabled=True, auth=True)
    client = TestClient(main.app)
    # key-a mapped to tenant_a only; requesting tenant_b must 403.
    r = client.get("/chat/threads", params={"tenant_id": "tenant_b"}, headers={"X-Api-Key": "key-a"})
    assert r.status_code == 403
    # no key -> 401
    assert client.get("/chat/threads", params={"tenant_id": "tenant_a"}).status_code == 401
    # authorized
    ok = client.get("/chat/threads", params={"tenant_id": "tenant_a"}, headers={"X-Api-Key": "key-a"})
    assert ok.status_code == 200


def test_endpoint_threads_isolated_by_identity_fingerprint(monkeypatch, tmp_path):
    _setup_app(monkeypatch, tmp_path, enabled=True, auth=True)
    client = TestClient(main.app)
    # key-a writes a thread under tenant_a
    client.post("/chat/threads", json={"thread_id": "ta", "question": "mine", "tenant_id": "tenant_a"},
                headers={"X-Api-Key": "key-a"})
    # key-b (different fingerprint) on tenant_a must not see key-a's thread
    other = client.get("/chat/threads", params={"tenant_id": "tenant_a"}, headers={"X-Api-Key": "key-b"}).json()["threads"]
    assert all(t["thread_id"] != "ta" for t in other)
    # raw key never leaks in responses
    blob = json.dumps(other)
    assert "key-a" not in blob and "key-b" not in blob
