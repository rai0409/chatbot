from __future__ import annotations

import json
import logging

import config
from rag_core import retrieval
from webapi import main


def _reset_keyword_index_state():
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    retrieval._KEYWORD_INDEX_WARNED_KEYS.clear()


def _degraded_warnings(caplog):
    return [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "keyword_index_degraded" in record.getMessage()
    ]


def _warning_payload(record):
    message = record.getMessage()
    return json.loads(message.split("keyword_index_degraded ", 1)[1])


def _write_corpus(path, rows):
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_missing_corpus_status_and_single_warning(monkeypatch, tmp_path, caplog):
    missing = tmp_path / "missing_corpus.jsonl"
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(missing))
    _reset_keyword_index_state()

    with caplog.at_level(logging.WARNING, logger="rag_core.retrieval"):
        status = retrieval.keyword_index_status()
        again = retrieval.keyword_index_status()

    assert status == {
        "keyword_index_loaded": False,
        "keyword_index_records": 0,
        "keyword_index_path": str(missing),
    }
    assert again == status
    warnings = _degraded_warnings(caplog)
    assert len(warnings) == 1
    payload = _warning_payload(warnings[0])
    assert payload["keyword_index_loaded"] is False
    assert payload["keyword_index_records"] == 0
    assert payload["keyword_index_path"] == str(missing)
    assert payload["reason"] == "missing_file"


def test_empty_corpus_status_and_single_warning(monkeypatch, tmp_path, caplog):
    corpus = tmp_path / "empty_corpus.jsonl"
    corpus.write_text("\n{bad json\n" + json.dumps({"id": "c1", "text": ""}) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    _reset_keyword_index_state()

    with caplog.at_level(logging.WARNING, logger="rag_core.retrieval"):
        status = retrieval.keyword_index_status()
        retrieval.keyword_index_status()

    assert status == {
        "keyword_index_loaded": False,
        "keyword_index_records": 0,
        "keyword_index_path": str(corpus),
    }
    warnings = _degraded_warnings(caplog)
    assert len(warnings) == 1
    assert _warning_payload(warnings[0])["reason"] == "empty_corpus"


def test_loaded_corpus_status_without_warning(monkeypatch, tmp_path, caplog):
    corpus = tmp_path / "corpus.jsonl"
    _write_corpus(
        corpus,
        [
            {"id": "c1", "text": "パスワードの再設定手順"},
            {"id": "c2", "text": "問い合わせ窓口の案内"},
        ],
    )
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    _reset_keyword_index_state()

    with caplog.at_level(logging.WARNING, logger="rag_core.retrieval"):
        status = retrieval.keyword_index_status()

    assert status == {
        "keyword_index_loaded": True,
        "keyword_index_records": 2,
        "keyword_index_path": str(corpus),
    }
    assert _degraded_warnings(caplog) == []


def test_health_includes_keyword_index_fields(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    _write_corpus(corpus, [{"id": "c1", "text": "パスワードの再設定手順"}])
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    _reset_keyword_index_state()

    payload = main.health()

    assert payload["status"] == "ok"
    assert payload["keyword_index_loaded"] is True
    assert payload["keyword_index_records"] == 1
    assert payload["keyword_index_path"] == str(corpus)


def test_health_reports_missing_corpus(monkeypatch, tmp_path):
    missing = tmp_path / "missing_corpus.jsonl"
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(missing))
    _reset_keyword_index_state()

    payload = main.health()

    assert payload["status"] == "ok"
    assert payload["keyword_index_loaded"] is False
    assert payload["keyword_index_records"] == 0
    assert payload["keyword_index_path"] == str(missing)
