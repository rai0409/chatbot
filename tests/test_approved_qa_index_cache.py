"""PR G: the approved-QA index cache never serves one tenant's index to another. Synthetic data only."""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

import config
from webapi.routers import chat


@pytest.fixture
def cache(monkeypatch, tmp_path):
    path = tmp_path / "approved.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    loads = []

    def fake_load(p, tenant_id="default"):
        loads.append(tenant_id)
        time.sleep(0.0005)  # a real load reads a file: let other threads run
        return SimpleNamespace(tenant=tenant_id)

    monkeypatch.setattr(chat, "load_approved_qa", fake_load)
    monkeypatch.setattr(chat, "_approved_qa_index", None, raising=False)
    monkeypatch.setattr(chat, "_approved_qa_index_path", None, raising=False)
    if hasattr(chat, "_approved_qa_cache"):
        chat._approved_qa_cache.clear()
    return path, loads


def test_each_tenant_gets_its_own_index_under_concurrent_requests(cache):
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    wrong = []

    def work(tenant):
        for _ in range(300):
            index = chat._approved_qa_index_for(tenant)
            if index is None or index.tenant != tenant:
                wrong.append((tenant, getattr(index, "tenant", None)))

    try:
        threads = [threading.Thread(target=work, args=(t,)) for t in ("tenant_a", "tenant_b", "tenant_c", "tenant_a")]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        sys.setswitchinterval(old)
    assert wrong == []


def test_alternating_tenants_do_not_reload_every_time(cache):
    _path, loads = cache
    for _ in range(20):
        chat._approved_qa_index_for("tenant_a")
        chat._approved_qa_index_for("tenant_b")
    assert sorted(loads) == ["tenant_a", "tenant_b"]


def test_an_edited_file_is_reloaded(cache):
    path, loads = cache
    chat._approved_qa_index_for("tenant_a")
    time.sleep(0.01)
    path.write_text("{}\n{}\n", encoding="utf-8")
    chat._approved_qa_index_for("tenant_a")
    assert loads == ["tenant_a", "tenant_a"]


def test_stale_entries_of_an_old_file_version_are_dropped(cache):
    path, _loads = cache
    chat._approved_qa_index_for("tenant_a")
    time.sleep(0.01)
    path.write_text("{}\n{}\n", encoding="utf-8")
    chat._approved_qa_index_for("tenant_a")
    assert len(chat._approved_qa_cache) == 1


def test_disabled_returns_none(cache, monkeypatch):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    assert chat._approved_qa_index_for("tenant_a") is None
