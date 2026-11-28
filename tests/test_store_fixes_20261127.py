"""2026-11-27: the approved-QA citation existence check backed by the served corpus, the CSV import stamps the
importer as the editor, and conversation retention applies on read and to every owner. Synthetic data only."""
from __future__ import annotations

import json
import os

import pytest

import config
from rag_core import approved_qa_exact_lookup as exact
from rag_core import approved_qa_store as store
from webapi import conversation_store


# --- served corpus source-doc check -----------------------------------------------------------------------------


def _write_corpus(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def corpus(monkeypatch, tmp_path):
    for name in ("KEYWORD_INDEX_REFRESH", "APP_ENV", "INDEX_POINTER_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    path = tmp_path / "chunks.jsonl"
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(path))
    exact.clear_served_doc_cache()
    yield path
    exact.clear_served_doc_cache()
    exact.set_source_doc_exists_check(None)


def test_served_source_doc_exists_answers_per_tenant(corpus):
    _write_corpus(corpus, [
        {"id": "a1", "text": "本文一", "source_doc": "規程.pdf", "tenant_id": "default"},
        {"id": "b1", "text": "本文二", "source_doc": "別社.pdf", "tenant_id": "tenant_b"},
    ])
    assert exact.served_source_doc_exists("default", "規程.pdf") is True
    assert exact.served_source_doc_exists("default", "無い.pdf") is False
    assert exact.served_source_doc_exists("default", "別社.pdf") is False  # another tenant's document
    assert exact.served_source_doc_exists("tenant_b", "別社.pdf") is True


def test_served_source_doc_set_follows_a_corpus_change(corpus):
    _write_corpus(corpus, [{"id": "a1", "text": "本文一", "source_doc": "規程.pdf", "tenant_id": "default"}])
    assert exact.served_source_doc_exists("default", "新規.pdf") is False
    _write_corpus(corpus, [{"id": "a1", "text": "本文一", "source_doc": "規程.pdf", "tenant_id": "default"},
                           {"id": "a2", "text": "追加の本文です", "source_doc": "新規.pdf", "tenant_id": "default"}])
    stamp = corpus.stat().st_mtime + 5  # the keyword index notices a change by the file mtime
    os.utime(corpus, (stamp, stamp))
    assert exact.served_source_doc_exists("default", "新規.pdf") is True


def test_unknown_corpus_is_none_and_keeps_hits(corpus):
    assert exact.served_source_doc_exists("default", "規程.pdf") is None  # no corpus file
    exact.install_default_source_doc_exists_check()
    assert exact.cited_sources_exist("default", ["規程.pdf"]) is True


def test_installed_check_drops_a_hit_whose_document_is_gone(corpus):
    _write_corpus(corpus, [{"id": "a1", "text": "本文一", "source_doc": "規程.pdf", "tenant_id": "default"}])
    exact.install_default_source_doc_exists_check()
    assert exact.cited_sources_exist("default", ["規程.pdf"]) is True
    assert exact.cited_sources_exist("default", ["削除済み.pdf"]) is False


# --- CSV import stamps the importer -----------------------------------------------------------------------------

TENANT = "tenant_a"
ALICE = {"name": "山田太郎", "id": "fp-alice", "verified": True}
BOB = {"name": "佐藤次郎", "id": "fp-bob", "verified": True}
CITE = [{"source_doc": "manual.pdf", "source_pages": [2], "title": "利用案内"}]


def test_csv_update_makes_the_importer_the_editor_who_cannot_approve(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(tmp_path / "approved" / "live.jsonl"))
    monkeypatch.setenv("APPROVED_QA_REQUIRE_APPROVER", "1")
    created = store.save({"tenant_id": TENANT, "question": "申請期限はいつですか？", "approved_answer": "毎月末日です。",
                          "citations": CITE}, by=BOB, reason="新規")
    header = ",".join(store.TEMPLATE_HEADERS)
    row = "申請期限はいつですか？,翌月5日です。,manual.pdf,2,,,tenant_a,,有効" + "," * (len(store.TEMPLATE_HEADERS) - 9)
    result = store.import_rows(header + "\n" + row, default_tenant=TENANT, allowed_tenants=None, apply=True, by=ALICE,
                               reason="取り込み", on_conflict="update")
    assert result["counts"]["update"] == 1
    record = store.get(TENANT, created["qa_id"])
    assert record["updated_by"] == "山田太郎" and record["updated_by_id"] == "fp-alice" and record["updated_by_verified"] is True
    with pytest.raises(store.StoreError):
        store.set_status(TENANT, created["qa_id"], "approved", by=ALICE, reason="承認")
    store.set_status(TENANT, created["qa_id"], "approved", by=BOB, reason="承認")


# --- conversation retention -------------------------------------------------------------------------------------


@pytest.fixture
def history(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    for name in ("CONVERSATION_STORE_BACKEND", "STATE_BACKEND", "CONVERSATION_HISTORY_MAX_AGE_DAYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVERSATION_RETENTION_DAYS", "1")
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(conversation_store, "_clock", lambda: clock["t"])
    return clock


def test_expired_turns_are_not_returned_before_a_purge(history):
    conversation_store.append_turn("default", "fp-1", "t1", question="古い質問", answer_text="a")
    history["t"] += 2 * 86400
    assert conversation_store.get_thread("default", "fp-1", "t1") == []
    assert conversation_store.list_threads("default", "fp-1") == []


def test_purge_all_removes_expired_turns_of_every_owner(history):
    conversation_store.append_turn("default", "fp-1", "t1", question="q1", answer_text="a")
    conversation_store.append_turn("tenant_b", "fp-2", "t2", question="q2", answer_text="b")
    history["t"] += 2 * 86400
    conversation_store.append_turn("default", "fp-3", "t3", question="q3", answer_text="c")
    assert conversation_store.purge_all() == 2
    assert conversation_store.get_thread("default", "fp-3", "t3")[0]["question"] == "q3"
    assert conversation_store.purge_all() == 0
