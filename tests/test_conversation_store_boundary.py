"""Storage-layer privacy tests, synthetic data without services."""
import json

import pytest

import config
from rag_core import index_generation
from webapi import conversation_store as cs


@pytest.fixture(autouse=True)
def local_store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CONVERSATION_STORE_BACKEND", "local")
    monkeypatch.setenv("CONVERSATION_RETENTION_DAYS", "0")


@pytest.mark.parametrize("identifiers", [("t", "_t"), ("a/b", "a_b"), ("あ", "い"), ("x" * 128 + "a", "x" * 128 + "b")])
def test_identifier_isolation(identifiers):
    a, b = identifiers
    for t, u, th, answer in [(a, a, a, "first"), (b, a, a, "tenant"), (a, b, a, "owner"), (a, a, b, "thread")]:
        assert cs.get_thread(t, u, th) == []
        cs.append_turn(t, u, th, question="q", answer_text=answer, source_docs=["x"])
    assert cs.redact_source_doc(a, "x") == 3
    assert cs.get_thread(b, a, a)[0]["answer_text"] == "tenant"
    assert {r["thread_id"] for r in cs.list_threads(a, a)} == {a, b}
    assert cs.delete_thread(a, a, a)
    assert cs.get_thread(a, b, a)[0]["redacted"]
    assert cs.purge(a, a, max_threads=0) == 1
    assert cs.get_thread(b, a, a)[0]["answer_text"] == "tenant"


def test_invalid_marker_fails_closed():
    cs.append_turn("t", "u", "th", question="q", answer_text="private", source_docs=["x"])
    marker = cs._tenant_dir("t") / ".redactions.json"
    marker.write_text("invalid", encoding="utf-8")
    for op in [lambda: cs.get_thread("t", "u", "th"), lambda: cs.list_threads("t", "u"),
               lambda: cs.append_turn("t", "u", "th", question="late"), lambda: cs.redact_source_doc("t", "x")]:
        with pytest.raises(ValueError):
            op()


def test_marker_before_first_append_and_failure(monkeypatch):
    assert cs.redact_source_doc("t", "x") == 0
    assert cs.append_turn("t", "u", "th", question="q", answer_text="private", source_docs=["x"])["redacted"]
    rewrite = cs._rewrite_lines

    def fail_record(path, lines):
        if path.suffix == ".jsonl":
            raise OSError("injected failure")
        rewrite(path, lines)

    cs.append_turn("t", "u", "th", question="q2", answer_text="private", source_docs=["y"])
    with monkeypatch.context() as patch:
        patch.setattr(cs, "_rewrite_lines", fail_record)
        with pytest.raises(OSError):
            cs.redact_source_doc("t", "y")
    assert all(r["answer_text"] == cs.REDACTED_NOTICE for r in cs.get_thread("t", "u", "th"))
    assert cs.redact_source_doc("t", "y") == 1
    assert cs.redact_source_doc("t", "y") == 0


def test_mismatched_thread_record_not_exposed():
    cs.append_turn("t", "u", "th", question="q")
    path = cs._thread_path("t", "u", "th")
    path.write_text(json.dumps({"thread_id": "other", "answer_text": "private"}) + "\n", encoding="utf-8")
    assert cs.get_thread("t", "u", "th") == []
    assert cs.list_threads("t", "u") == []


def test_read_lock_timeout(monkeypatch):
    cs.append_turn("t", "u", "th", question="q")
    monkeypatch.setattr(index_generation, "_LOCK_WAIT_SECONDS", 0)
    with cs._tenant_lock(cs._tenant_dir("t")):
        with pytest.raises(index_generation.LockTimeout):
            cs.get_thread("t", "u", "th")
        with pytest.raises(index_generation.LockTimeout):
            cs.list_threads("t", "u")


def test_invalid_provenance_rejected_before_storage(monkeypatch, tmp_path):
    import pytest
    import config
    from webapi import conversation_store as cs

    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path))
    for kwargs in ({"citations_count": 1}, {"source_docs": [""]},
                   {"source_docs": [1]}, {"source_docs": ["x" * 513]},
                   {"source_docs": [str(i) for i in range(21)]}):
        with pytest.raises(ValueError):
            cs.append_turn("t", "u", "th", question="q", **kwargs)
    assert not (tmp_path / "conversations").exists()
    rec = cs.append_turn("t", "u", "th", question="q", citations_count=2, source_docs=[" a ", "a", "b"])
    assert rec["source_docs"] == ["a", "b"]
