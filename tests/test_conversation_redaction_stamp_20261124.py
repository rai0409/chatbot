"""Conversation redaction, legacy type stamping, mail isolation default. Synthetic data only."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import config
from rag_core import conversation_context
from webapi import collection_admin, conversation_store, mail_ingestion_jobs

NOTICE = "（削除された文書に基づく回答のため非表示）"


@pytest.fixture
def runs(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("CONVERSATION_STORE_BACKEND", raising=False)
    monkeypatch.delenv("CONVERSATION_RETENTION_DAYS", raising=False)


def test_redact_source_doc_hides_answers_of_citing_turns_only(runs):
    cs = conversation_store
    cs.append_turn("t", "u1", "th", question="Q1", answer_text="A1", source_docs=["a.pdf", "b.pdf"])
    cs.append_turn("t", "u2", "th", question="Q2", answer_text="A2", source_docs=["a.pdf"])
    cs.append_turn("t", "u1", "th", question="Q3", answer_text="A3", source_docs=["b.pdf"])
    cs.append_turn("t", "u1", "th", question="Q4", answer_text="A4")  # legacy: no citations
    cs.append_turn("other", "u1", "th", question="Q5", answer_text="A5", source_docs=["a.pdf"])
    assert cs.redact_source_doc("t", "a.pdf") == 2
    assert cs.redact_source_doc("t", "a.pdf") == 0  # idempotent
    turns = cs.get_thread("t", "u1", "th")
    assert [x["answer_text"] for x in turns] == [NOTICE, "A3", "A4"]
    assert turns[0]["redacted"] is True and "redacted" not in turns[1]
    assert cs.get_thread("t", "u2", "th")[0]["answer_text"] == NOTICE
    assert cs.get_thread("other", "u1", "th")[0]["answer_text"] == "A5"


def test_admin_path_is_real_and_default_on(runs, monkeypatch):
    monkeypatch.delenv("CONVERSATION_REDACT_ON_DELETE", raising=False)
    conversation_store.append_turn("t", "u1", "th", question="Q", answer_text="A", source_docs=["x.pdf"])
    assert collection_admin._redact_conversations("t", ["x.pdf", "y.pdf"]) == 1
    conversation_store.append_turn("t", "u1", "th2", question="Q", answer_text="A", source_docs=["z.pdf"])
    monkeypatch.setenv("CONVERSATION_REDACT_ON_DELETE", "false")
    assert collection_admin._redact_conversations("t", ["z.pdf"]) == 0


def test_redacted_turns_are_not_context(runs):
    conversation_store.append_turn("t", "u1", "th", question="山田太郎の申請手順", answer_text="A", source_docs=["a.pdf"])
    conversation_store.append_turn("t", "u1", "th", question="保管期間は", answer_text="B", source_docs=["b.pdf"])
    conversation_store.redact_source_doc("t", "a.pdf")
    turns = conversation_store.get_thread("t", "u1", "th")
    assert conversation_context.previous_questions_from_turns(turns) == ["保管期間は"]


def test_retention_days_prunes_old_turns(runs, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(conversation_store, "_clock", lambda: now[0])
    conversation_store.append_turn("t", "u1", "th", question="old", answer_text="x")
    now[0] += 10 * 86400
    conversation_store.append_turn("t", "u1", "th", question="new", answer_text="y")
    assert len(conversation_store.get_thread("t", "u1", "th")) == 2  # default 0 keeps everything
    monkeypatch.setenv("CONVERSATION_RETENTION_DAYS", "5")
    conversation_store.append_turn("t", "u1", "th", question="newer", answer_text="z")
    assert [x["question"] for x in conversation_store.get_thread("t", "u1", "th")] == ["new", "newer"]


def test_source_docs_are_recorded_and_capped(runs):
    rec = conversation_store.append_turn("t", "u1", "th", question="Q", source_docs=["a", "a", "b"])
    assert rec["source_docs"] == ["a", "b"]
    with pytest.raises(ValueError):
        conversation_store.append_turn("t", "u1", "th", question="Q", source_docs=["c%d" % i for i in range(21)])


# --- stamp_legacy_types ------------------------------------------------------------------------------------------


class FakeStore:
    def __init__(self, rows):
        self.rows = dict(rows)

    def get(self, ids=None, where=None, include=None, limit=None, offset=None):
        items = list(self.rows.items())[offset or 0:][:limit]
        return {"ids": [i for i, _ in items], "metadatas": [m for _, m in items]}

    def update(self, ids, metadatas):
        for i, m in zip(ids, metadatas):
            self.rows[i] = {**self.rows[i], **m}


def _stamp(monkeypatch, store, kw, tmp_path, **kw_args):
    from scripts import stamp_legacy_types as mod
    from webapi import ingestion_jobs

    monkeypatch.setattr(ingestion_jobs, "is_production_collection", lambda c: c == "prod")
    return mod.run(kw_args.pop("collection", "stg"), store=store, keyword_path=kw, report_path=tmp_path / "r" / "rep.json",
                   **kw_args)


def _rows():
    return {
        "a": {"source_doc": "a.pdf", "tenant_id": "t"},
        "b": {"source_doc": "b.DOCX", "tenant_id": "t"},
        "c": {"source_doc": "plain", "tenant_id": "t", "parser": "faq-import"},
        "d": {"source_doc": "note", "tenant_id": "t"},
        "e": {"source_doc": "mail:abc", "tenant_id": "t"},
        "f": {"source_doc": "f.pdf", "tenant_id": "t", "type": "pdf"},
    }


def test_stamp_dry_run_then_execute(tmp_path, monkeypatch):
    store = FakeStore(_rows())
    kw = tmp_path / "kw.jsonl"
    kw_rows = [{"id": k, **v} for k, v in _rows().items()]
    kw.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kw_rows), encoding="utf-8")
    report = _stamp(monkeypatch, store, kw, tmp_path, execute=False, allow_production=False)
    assert report["mode"] == "dry-run" and report["vector"]["to_stamp"] == 4
    assert "type" not in store.rows["a"]
    assert report["vector"]["mail_skipped_ids"] == ["e"]
    report = _stamp(monkeypatch, store, kw, tmp_path, execute=True, allow_production=False)
    assert {k: store.rows[k].get("type") for k in "abcdef"} == {
        "a": "pdf", "b": "docx", "c": "faq", "d": "document", "e": None, "f": "pdf"}
    got = {json.loads(ln)["id"]: json.loads(ln) for ln in kw.read_text(encoding="utf-8").splitlines()}
    assert len(got) == 6 and got["a"]["type"] == "pdf" and got["d"]["type"] == "document" and "type" not in got["e"]
    assert report["keyword"]["written"] == 4
    assert json.loads((tmp_path / "r" / "rep.json").read_text(encoding="utf-8"))["mode"] == "execute"


def test_stamp_refuses_production(tmp_path, monkeypatch):
    store = FakeStore(_rows())
    with pytest.raises(PermissionError):
        _stamp(monkeypatch, store, None, tmp_path, collection="prod", execute=True, allow_production=False)
    assert "type" not in store.rows["a"]
    _stamp(monkeypatch, store, None, tmp_path, collection="prod", execute=True, allow_production=True)
    assert store.rows["a"]["type"] == "pdf"


# --- mail isolation / state warning ----------------------------------------------------------------------------


def test_convert_mail_inputs_isolates_by_default(monkeypatch, tmp_path):
    from rag_core import mail_to_canonical as m

    seen = {}

    def fake(paths, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    monkeypatch.setattr(m, "convert_mail_batch", fake)
    with pytest.raises(RuntimeError):
        m.convert_mail_inputs([Path("x.eml")], tenant_id="t")
    assert seen["isolate"] is True
    with pytest.raises(RuntimeError):
        m.convert_mail_inputs([Path("x.eml")], tenant_id="t", isolate=False)
    assert seen["isolate"] is False


def test_cli_isolated_by_default_and_in_process_flag(monkeypatch, tmp_path, capsys):
    from scripts import mail_to_canonical_jsonl as cli

    src = tmp_path / "in.eml"
    src.write_text("From: a@example.co.jp\nTo: b@example.co.jp\nSubject: s\nMessage-ID: <1@example.co.jp>\n"
                   "Date: Mon, 1 Jan 2024 00:00:00 +0000\n\nbody\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "_convert_isolated", lambda *a: calls.append(a) or ([], {}))
    cli.main(["--input", str(src), "--out", str(tmp_path / "o.jsonl"), "--tenant", "t"])
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setenv("MAIL_CONVERT_TIMEOUT_SECONDS", "60")
    cli.main(["--input", str(src), "--out", str(tmp_path / "o2.jsonl"), "--tenant", "t", "--in-process"])
    assert calls == []


def test_cli_isolated_worker_end_to_end(monkeypatch, tmp_path):
    from scripts import mail_to_canonical_jsonl as cli

    monkeypatch.setenv("MAIL_CONVERT_TIMEOUT_SECONDS", "60")
    src = tmp_path / "in.eml"
    src.write_text("From: a@example.co.jp\nTo: b@example.co.jp\nSubject: s\nMessage-ID: <1@example.co.jp>\n"
                   "Date: Mon, 1 Jan 2024 00:00:00 +0000\n\nbody text\n", encoding="utf-8")
    assert cli.main(["--input", str(src), "--out", str(tmp_path / "o.jsonl"), "--tenant", "t"]) == 0
    assert (tmp_path / "o.jsonl").read_text(encoding="utf-8").strip()


def test_mail_state_warns_with_shared_registry(monkeypatch, caplog):
    monkeypatch.setattr(mail_ingestion_jobs, "_LOCAL_STATE_WARNED", {"done": False})
    monkeypatch.setenv("INGESTION_REGISTRY_BACKEND", "local")
    assert mail_ingestion_jobs.warn_local_state_if_shared_registry() is False
    monkeypatch.setenv("INGESTION_REGISTRY_BACKEND", "postgres")
    with caplog.at_level(logging.WARNING):
        assert mail_ingestion_jobs.warn_local_state_if_shared_registry() is True
        mail_ingestion_jobs.warn_local_state_if_shared_registry()
    assert sum("node" in r.message for r in caplog.records) == 1


def test_redaction_and_concurrent_append_keep_both_changes(runs, monkeypatch):
    import multiprocessing
    from rag_core import index_generation

    ctx = multiprocessing.get_context("fork")
    snapshot, contending, resume = (ctx.Event() for _ in range(3))
    cs = conversation_store
    cs.append_turn("t", "u", "th", question="old", answer_text="private", source_docs=["x.pdf"])
    rewrite = cs._rewrite_lines
    lock = index_generation.flock_exclusive

    def paused_rewrite(path, lines):
        snapshot.set()
        assert resume.wait(10)
        rewrite(path, lines)

    def append():
        def signal_lock(*args, **kwargs):
            contending.set()
            lock(*args, **kwargs)

        index_generation.flock_exclusive = signal_lock
        cs.append_turn("t", "u", "th", question="new")

    monkeypatch.setattr(cs, "_rewrite_lines", paused_rewrite)
    redact = ctx.Process(target=cs.redact_source_doc, args=("t", "x.pdf"))
    writer = ctx.Process(target=append)
    redact.start()
    try:
        assert snapshot.wait(10)
        writer.start()
        assert contending.wait(10)
    finally:
        resume.set()
        redact.join(12)
        if writer.pid is not None:
            writer.join(12)
    assert (redact.exitcode, writer.exitcode) == (0, 0)
    turns = cs.get_thread("t", "u", "th")
    assert [r["question"] for r in turns] == ["old", "new"]
    assert turns[0]["answer_text"] == NOTICE


def test_jsonl_lock_timeout_and_exception_cleanup(runs, monkeypatch):
    from rag_core import index_generation

    cs = conversation_store
    path = cs._thread_path("t", "u", "th")
    cs.append_turn("t", "u", "th", question="old", source_docs=["x.pdf"])
    monkeypatch.setattr(index_generation, "_LOCK_WAIT_SECONDS", 0)
    with cs._tenant_lock(path.parent.parent):
        with pytest.raises(index_generation.LockTimeout):
            cs.append_turn("t", "u", "th", question="lost")
        with pytest.raises(index_generation.LockTimeout):
            cs.delete_thread("t", "u", "th")
    def fail_replace(*args):
        raise OSError("synthetic replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(cs.os, "replace", fail_replace)
        with pytest.raises(OSError):
            cs.redact_source_doc("t", "x.pdf")
    assert not path.with_name(path.name + ".tmp").exists()
    assert cs.redact_source_doc("t", "x.pdf") == 1
    assert [r["question"] for r in cs.get_thread("t", "u", "th")] == ["old"]


def test_stamp_checks_vector_id_even_with_different_metadata_id():
    from scripts import stamp_legacy_types as stamp

    plan = stamp._new_plan()
    stamp._classify("tenant:email:abc:h1", {"id": "public", "source_doc": "public.pdf"}, plan)
    assert plan["mail_skipped"] == ["tenant:email:abc:h1"]
    assert not plan["stamp"]
