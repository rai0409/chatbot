"""Stage 13: scale benchmark (scripts/scale_benchmark.py) and the Sudachi
tokenizer under concurrent use, which the benchmark found failing
("Already borrowed"). Synthetic data only; small sizes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts import scale_benchmark as sb

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scale_benchmark.py"


def test_synthetic_corpus_is_deterministic_and_sized():
    a = sb.document_rows(7, seed=1, chunks=3, chars=200, tenants=1)
    b = sb.document_rows(7, seed=1, chunks=3, chars=200, tenants=1)
    assert a == b and len(a) == 3
    assert all(len(r["text"]) >= 200 and r["source_doc"] == "scale_00007.pdf" for r in a)
    assert "R-00007" in a[0]["text"]
    multi = sb.document_rows(5, seed=1, chunks=1, chars=50, tenants=4)
    assert multi[0]["tenant_id"] == "tenant_01"
    queries = sb.make_queries(10, docs=100, seed=1, tenants=1)
    assert len(queries) == 10 and queries[0]["expected_source_doc"] and queries[1]["expected_source_doc"] is None


def test_latency_summary_and_dummy_vectors():
    s = sb.latency_summary([float(v) for v in range(1, 101)])
    assert (s["p50_ms"], s["p95_ms"], s["p99_ms"], s["max_ms"]) == (50.0, 95.0, 99.0, 100.0)
    assert sb.latency_summary([])["count"] == 0
    v1, v2 = sb.dummy_vectors(["経費精算の締め日", "経費精算の締め日"], 64)
    assert v1 == v2 and abs(sum(x * x for x in v1) - 1.0) < 1e-5


def _run(args, tmp_path, extra_env=None):
    env = dict(os.environ, HOME=str(tmp_path), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", **(extra_env or {}))
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)


def test_all_with_dummy_embedding_writes_reports(tmp_path):
    pytest.importorskip("chromadb")
    work = tmp_path / "scale"
    proc = _run(["all", "--work", str(work), "--docs", "12", "--queries", "6", "--requests", "6",
                 "--embedding", "dummy", "--dummy-dim", "64", "--concurrency", "1,2"], tmp_path)
    assert proc.returncode == 0, proc.stderr[-3000:]
    ingest = json.loads((work / "reports" / "ingest.json").read_text(encoding="utf-8"))
    search = json.loads((work / "reports" / "search.json").read_text(encoding="utf-8"))
    assert ingest["rows"] == ingest["ingested"] == 48
    assert ingest["memory_mb"]["peak_process"] > 0
    assert [level["concurrency"] for level in search["levels"]] == [1, 2]
    assert all(level["latency"]["count"] == 6 for level in search["levels"])
    assert search["levels"][0]["hit_at_k_specific"] >= 0.5
    # Nothing is written outside --work.
    assert not (ROOT / "vectorstore" / "scale_bench").exists()


def test_convert_measures_documents(tmp_path):
    pytest.importorskip("pdfplumber")
    src = str(ROOT / "libs" / "docextract" / "src")
    proc = _run(["convert", "--work", str(tmp_path / "conv"), "--docs", "2", "--workers", "1", "--pdf-pages", "1"],
                tmp_path, {"PYTHONPATH": os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH")]))})
    assert proc.returncode == 0, proc.stderr[-3000:]
    report = json.loads((tmp_path / "conv" / "reports" / "convert.json").read_text(encoding="utf-8"))
    assert report["docs"] == 2 and report["chunks"] > 0 and set(report["latency_per_doc"]) == {"pdf", "docx"}


class _Stub(BaseHTTPRequestHandler):
    status = 200
    seen = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append((self.path, json.loads(body), self.headers.get("X-Api-Key")))
        payload = b'{"results": []}'
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("status,ok", [(200, True), (500, False)])
def test_http_load_counts_statuses(tmp_path, monkeypatch, status, ok):
    work = tmp_path / "w"
    work.mkdir()
    (work / "queries.jsonl").write_text(json.dumps({"query": "経費精算の締め日", "tenant_id": "default"}) + "\n", encoding="utf-8")
    _Stub.status, _Stub.seen = status, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SCALE_TEST_KEY", "test-key-placeholder")
    try:
        sb.main(["http", "--work", str(work), "--url", f"http://127.0.0.1:{server.server_port}", "--endpoint", "/search",
                 "--concurrency", "1,2", "--duration", "0.3", "--warmup", "0", "--api-key-env", "SCALE_TEST_KEY"])
    finally:
        server.shutdown()
    report = json.loads((work / "reports" / "http_search.json").read_text(encoding="utf-8"))
    assert [level["concurrency"] for level in report["levels"]] == [1, 2]
    for level in report["levels"]:
        assert level["requests"] > 0
        assert level["error_rate"] == (0.0 if ok else 1.0)
        assert set(level["status_counts"]) == {str(status)}
    path, body, key = _Stub.seen[0]
    assert path == "/search" and body["query"] == "経費精算の締め日" and key == "test-key-placeholder"
    assert "test-key-placeholder" not in json.dumps(report)


# --- Sudachi tokenizer under concurrent use -------------------------------------------------------


def test_each_thread_gets_its_own_tokenizer(monkeypatch):
    import config
    from rag_core import ja_tokenizer

    created = []

    def fake(dict_name, split_mode):
        created.append(object())
        return created[-1]

    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "sudachi")
    monkeypatch.setattr(ja_tokenizer, "_create_sudachi_tokenizer", fake)
    ja_tokenizer.reset_cache()
    try:
        main_tokenizer = ja_tokenizer._get_sudachi()
        assert ja_tokenizer._get_sudachi() is main_tokenizer
        others = []
        threads = [threading.Thread(target=lambda: others.append(ja_tokenizer._get_sudachi())) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len({id(t) for t in others}) == 3 and main_tokenizer not in others
    finally:
        ja_tokenizer.reset_cache()


def test_real_sudachi_tokenizes_from_many_threads(monkeypatch):
    pytest.importorskip("sudachipy")
    import config
    from rag_core import ja_tokenizer

    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "sudachi")
    ja_tokenizer.reset_cache()
    text = "経費精算の申請期限は毎月25日で、出張旅費は申請システムから提出する。" * 20
    expected = ja_tokenizer.morpheme_tokens(text)
    errors, results = [], []

    def work():
        try:
            for _ in range(30):
                results.append(ja_tokenizer.morpheme_tokens(text))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    try:
        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        ja_tokenizer.reset_cache()
    assert not errors, errors[:1]
    assert len(results) == 240 and all(r == expected for r in results)
