"""Scale benchmark: ingestion time, search latency under concurrent access and
memory, for a corpus of the target size (50,000 documents by default).

Synthetic data only (generated text; no real names or documents). Everything
is written under --work: the served index, vector store and runs/ are never
touched, and the commands only read their own files.

  generate  <work>/corpus.jsonl (canonical chunk rows) and <work>/queries.jsonl
  convert   document conversion throughput: synthetic PDF and DOCX files ->
            canonical chunks with N processes (as INGEST_CONVERT_WORKERS)
  ingest    embed + upsert into an isolated vector store, write the keyword
            corpus; seconds per phase, rows/s, peak memory, size on disk
  search    in-process hybrid retrieval with 1..N threads: first query
            (keyword index build), p50/p95/p99, queries/s, hit@k, memory
  http      load test of a running API (/search or /chat): concurrency levels
            for a fixed duration; latency, throughput, status counts and the
            server memory (--server-pid, same host)
  all       generate + ingest + search

  python scripts/scale_benchmark.py all --work runs/scale --docs 50000 --embedding model
  python scripts/scale_benchmark.py convert --work runs/scale --docs 1000 --workers 4
  python scripts/scale_benchmark.py http --work runs/scale --url http://127.0.0.1:8000 \\
      --endpoint /search --concurrency 1,4,16 --duration 60 --server-pid <uvicorn pid>

--embedding model  the configured embedding (EMBED_PROVIDER / LOCAL_EMBED_MODEL;
                   real numbers, needs the model files)
--embedding dummy  deterministic hashed vectors (no model): measures the vector
                   store, keyword index and pipeline overhead only
--backend chroma (default) | qdrant-local (QDRANT_PATH under --work; functional
                   only, no HNSW: not for numbers) | qdrant-url (--qdrant-url)
Reports: <work>/reports/<command>.json. Memory is read from /proc (Linux).
"""
from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import hashlib
import http.client
import json
import math
import os
import random
import resource
import threading
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

COLLECTION = "scale_bench"

# --- synthetic corpus ------------------------------------------------------------------------------

TOPICS = [
    ("経費精算", "精算書"), ("出張旅費", "旅費申請"), ("有給休暇", "休暇届"), ("在宅勤務", "勤務申請"),
    ("情報セキュリティ", "持出申請"), ("設備点検", "点検記録"), ("品質管理", "検査記録"), ("購買申請", "購買依頼書"),
    ("契約審査", "審査依頼"), ("教育研修", "受講申込"), ("文書管理", "廃棄申請"), ("安全衛生", "点検表"),
]
ITEMS = ["上限金額", "申請期限", "承認者", "提出先", "保管期間", "対象範囲", "必要書類", "例外の扱い"]
DEPTS = ["総務部", "経理部", "人事部", "情報システム部", "品質保証部", "購買部", "設備管理課", "法務部"]
PERIODS = ["毎月", "四半期ごとに", "半期ごとに", "毎年", "毎週"]
SYSTEMS = ["申請システム", "社内ポータル", "ワークフロー", "所定の様式"]


def _sentences(rng: random.Random, code: str, topic: str, form: str, article: int) -> Iterable[str]:
    while True:
        item = rng.choice(ITEMS)
        dept = rng.choice(DEPTS)
        yield rng.choice([
            f"{code} 第{article}条 {topic}の{item}は{dept}が{rng.choice(PERIODS)}確認する。",
            f"{item}の上限は{rng.randint(1, 200) * 1000}円とし、超える場合は{dept}の承認を得る。",
            f"{form}は{rng.randint(3, 30)}日以内に{rng.choice(SYSTEMS)}から提出する。",
            f"{topic}に関する問い合わせは{dept}が受け付け、{rng.randint(1, 10)}営業日以内に回答する。",
            f"{form}の保管期間は{rng.randint(1, 10)}年とし、期間が過ぎたものは{dept}が廃棄する。",
            f"やむを得ない事情がある場合は、{dept}に事前に相談したうえで例外として扱うことができる。",
        ])


def document_rows(index: int, *, seed: int, chunks: int, chars: int, tenants: int) -> List[Dict[str, Any]]:
    """Canonical chunk rows of one synthetic document (deterministic per seed and index)."""
    rng = random.Random(f"{seed}:{index}")
    topic, form = TOPICS[index % len(TOPICS)]
    code = f"R-{index:05d}"
    title = f"{topic}規程（{code}）"
    tenant = "default" if tenants <= 1 else f"tenant_{index % tenants:02d}"
    rows = []
    for j in range(chunks):
        parts, size = [], 0
        for sentence in _sentences(rng, code, topic, form, j + 1):
            parts.append(sentence)
            size += len(sentence)
            if size >= chars:
                break
        chunk_id = f"scale-{index:05d}-{j:02d}"
        rows.append({
            "id": chunk_id, "chunk_id": chunk_id, "text": "".join(parts), "type": "pdf", "quality": "high",
            "searchable": 1, "source_doc": f"scale_{index:05d}.pdf", "source_file": f"scale_{index:05d}.pdf",
            "doc_id": f"scale_{index:05d}", "chunk_index": j, "source_pages": [j + 1],
            "source_page_start": j + 1, "source_page_end": j + 1, "doc_type": "document", "chunk_type": "text",
            "source_type": "pdf", "parser": "synthetic", "tenant_id": tenant, "section_path": [title, f"第{j + 1}条"],
        })
    return rows


def make_queries(count: int, *, docs: int, seed: int, tenants: int) -> List[Dict[str, Any]]:
    rng = random.Random(f"{seed}:queries")
    out = []
    for n in range(count):
        index = rng.randrange(docs)
        topic, form = TOPICS[index % len(TOPICS)]
        tenant = "default" if tenants <= 1 else f"tenant_{index % tenants:02d}"
        if n % 2 == 0:
            query = f"R-{index:05d} の{rng.choice(ITEMS)}を教えてください"
            expected = f"scale_{index:05d}.pdf"
        else:
            query = f"{topic}の{form}はいつまでに提出しますか"
            expected = None  # generic question: many documents match
        out.append({"query": query, "tenant_id": tenant, "expected_source_doc": expected})
    return out


# --- measurement helpers -------------------------------------------------------------------------


def _status_kb(pid: str, field: str) -> Optional[int]:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _process_tree(pid: int) -> List[int]:
    """pid and its descendants (uvicorn workers), from /proc."""
    children: Dict[int, List[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
        except (OSError, ValueError, IndexError):
            continue
    out, todo = [], [pid]
    while todo:
        current = todo.pop()
        out.append(current)
        todo.extend(children.get(current, []))
    return out


def rss_mb(pid: Optional[int] = None) -> Optional[float]:
    if pid is None:
        kb = _status_kb("self", "VmRSS")
        return round(kb / 1024, 1) if kb is not None else None
    values = [_status_kb(str(p), "VmRSS") for p in _process_tree(pid)]
    values = [v for v in values if v is not None]
    return round(sum(values) / 1024, 1) if values else None


def peak_rss_mb() -> float:
    kb = _status_kb("self", "VmHWM")
    return round((kb if kb is not None else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024, 1)


class MemorySampler:
    """Peak resident memory of this process (or of --server-pid and its children) while running."""

    def __init__(self, pid: Optional[int] = None, interval: float = 0.2) -> None:
        self.pid, self.interval, self.peak = pid, interval, None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            value = rss_mb(self.pid)
            if value is not None:
                self.peak = value if self.peak is None else max(self.peak, value)
            self._stop.wait(self.interval)

    def __enter__(self) -> "MemorySampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()


def latency_summary(ms: Sequence[float]) -> Dict[str, Optional[float]]:
    if not ms:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None, "mean_ms": None}
    ordered = sorted(ms)

    def pct(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))], 1)

    return {"count": len(ordered), "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99),
            "max_ms": round(ordered[-1], 1), "mean_ms": round(sum(ordered) / len(ordered), 1)}


def dir_size_mb(path: Path) -> float:
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0
    return round(total / (1024 * 1024), 1)


def write_report(work: Path, name: str, report: Dict[str, Any]) -> Dict[str, Any]:
    out = work / "reports" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _levels(text: str) -> List[int]:
    return [int(v) for v in str(text).split(",") if v.strip()]


# --- isolated configuration and dummy embedding -----------------------------------------------------


def configure(work: Path, backend: str, qdrant_url: Optional[str]) -> None:
    """Point the app at --work. Must run before config / rag_core are imported."""
    if "config" in sys.modules:
        if os.environ.get("SCALE_BENCH_WORK") == str(work.resolve()):
            return  # "all": ingest already configured this process
        raise RuntimeError("configure() must run before config is imported")
    os.environ.update({
        "SCALE_BENCH_WORK": str(work.resolve()),
        "VECTORSTORE_DIR": str(work / "vectorstore"),
        "CHUNKS_JSONL_PATH": str(work / "keyword.jsonl"),
        "VECTORSTORE_COLLECTION_NAME": COLLECTION,
        "INDEX_DIR": str(work / "index"),
        "RUNS_DIR": str(work / "runs"),
        "KEYWORD_INDEX_PERSIST": "false",
    })
    os.environ.pop("CHROMA_COLLECTION", None)
    if backend == "chroma":
        os.environ["VECTOR_BACKEND"] = "chroma"
    elif backend == "qdrant-local":
        os.environ.update({"VECTOR_BACKEND": "qdrant", "QDRANT_PATH": str(work / "qdrant")})
    elif backend == "qdrant-url":
        if not qdrant_url:
            raise SystemExit("--backend qdrant-url needs --qdrant-url")
        os.environ.update({"VECTOR_BACKEND": "qdrant", "QDRANT_URL": qdrant_url, "QDRANT_PATH": ""})
    else:
        raise SystemExit(f"unknown backend: {backend}")


def dummy_vectors(texts: Sequence[str], dim: int) -> List[List[float]]:
    """Character-bigram feature hashing, L2-normalised: deterministic, no model."""
    import numpy as np

    out = np.zeros((len(texts), dim), dtype=np.float32)
    for row, text in enumerate(texts):
        text = str(text)
        for a, b in zip(text, text[1:]):
            h = int.from_bytes(hashlib.blake2b((a + b).encode("utf-8"), digest_size=8).digest(), "little")
            out[row, h % dim] += 1.0 if (h >> 63) == 0 else -1.0
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (out / norms).tolist()


class EmbedTimer:
    """Wraps rag_core.embedder so the embedding share of the time is known."""

    def __init__(self, mode: str, dim: int) -> None:
        from rag_core import embedder

        self.seconds, self.calls = 0.0, 0
        # "all" creates a second timer: wrap the original functions, not the wrapper.
        real_docs = getattr(embedder, "_scale_bench_original_docs", embedder.embed_documents)
        real_queries = getattr(embedder, "_scale_bench_original_queries", embedder.embed_queries)
        embedder._scale_bench_original_docs, embedder._scale_bench_original_queries = real_docs, real_queries

        def timed(fn: Callable) -> Callable:
            def wrapper(texts, client=None, provider_name=None):
                started = time.perf_counter()
                try:
                    if mode == "dummy":
                        return dummy_vectors(texts, dim)
                    return fn(texts, client=client, provider_name=provider_name)
                finally:
                    self.seconds += time.perf_counter() - started
                    self.calls += 1
            return wrapper

        embedder.embed_documents = timed(real_docs)
        embedder.embed_queries = timed(real_queries)


# --- commands ------------------------------------------------------------------------------------


def cmd_generate(args) -> Dict[str, Any]:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rows = 0
    with (work / "corpus.jsonl").open("w", encoding="utf-8") as f:
        for i in range(args.docs):
            for row in document_rows(i, seed=args.seed, chunks=args.chunks_per_doc, chars=args.chunk_chars, tenants=args.tenants):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows += 1
    queries = make_queries(args.queries, docs=args.docs, seed=args.seed, tenants=args.tenants)
    with (work / "queries.jsonl").open("w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    return write_report(work, "generate", {
        "docs": args.docs, "rows": rows, "queries": len(queries), "chunks_per_doc": args.chunks_per_doc,
        "chunk_chars": args.chunk_chars, "tenants": args.tenants, "seed": args.seed,
        "corpus_mb": round((work / "corpus.jsonl").stat().st_size / (1024 * 1024), 1),
        "seconds": round(time.perf_counter() - started, 2),
    })


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def cmd_ingest(args) -> Dict[str, Any]:
    work = Path(args.work)
    configure(work, args.backend, args.qdrant_url)
    from scripts import ingest_canonical_jsonl as ingest

    timer = EmbedTimer(args.embedding, args.dummy_dim)
    with MemorySampler() as memory:
        started = time.perf_counter()
        rows = _read_jsonl(work / "corpus.jsonl")
        loaded = time.perf_counter()
        generation = ingest.new_generation_id()
        result = ingest.ingest_canonical_rows(rows, collection_name=COLLECTION, batch=args.batch, reset=True,
                                              generation_id=generation)
        vectors_done = time.perf_counter()
        keyword = ingest.write_keyword_rows(work / "keyword.jsonl", rows, generation_id=generation)
        done = time.perf_counter()
    vector_seconds = vectors_done - loaded
    return write_report(work, "ingest", {
        "rows": len(rows), "ingested": result.get("ingested"), "skipped": result.get("skipped"),
        "embedding": args.embedding, "backend": args.backend, "batch": args.batch,
        "seconds": {"load_jsonl": round(loaded - started, 2), "embed": round(timer.seconds, 2),
                    "vector_store_other": round(vector_seconds - timer.seconds, 2),
                    "keyword_corpus": round(done - vectors_done, 2), "total": round(done - started, 2)},
        "rows_per_second": round(len(rows) / max(done - started, 1e-9), 1),
        "memory_mb": {"peak_sampled": memory.peak, "peak_process": peak_rss_mb(), "end": rss_mb()},
        "disk_mb": {"vector_store": dir_size_mb(work / ("qdrant" if args.backend == "qdrant-local" else "vectorstore")),
                    "keyword_corpus": round((work / "keyword.jsonl").stat().st_size / (1024 * 1024), 1)},
        "keyword": keyword,
        "note": "embedding=dummy: hashed vectors, no model; embed seconds are not model time" if args.embedding == "dummy" else "",
    })


def _search_once(retrieval, q: Dict[str, Any], top_k: int) -> Tuple[float, bool]:
    started = time.perf_counter()
    results = retrieval.hybrid_retrieve(q["query"], None, top_k=top_k, tenant_id=q.get("tenant_id") or "default")
    elapsed = (time.perf_counter() - started) * 1000
    expected = q.get("expected_source_doc")
    hit = bool(expected) and any((r.metadata or {}).get("source_doc") == expected for r in results)
    return elapsed, hit


def cmd_search(args) -> Dict[str, Any]:
    work = Path(args.work)
    configure(work, args.backend, args.qdrant_url)
    from rag_core import retrieval

    EmbedTimer(args.embedding, args.dummy_dim)
    queries = _read_jsonl(work / "queries.jsonl")
    if not queries:
        raise SystemExit("no queries: run generate first")
    with MemorySampler() as memory:
        before = rss_mb()
        first_ms, _ = _search_once(retrieval, queries[0], args.top_k)
        after_first = rss_mb()
        levels = []
        for concurrency in _levels(args.concurrency):
            batch = (queries * math.ceil(args.requests / len(queries)))[: args.requests]
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                outcomes = list(pool.map(lambda q: _search_once(retrieval, q, args.top_k), batch))
            wall = time.perf_counter() - started
            specific = [hit for (_, hit), q in zip(outcomes, batch) if q.get("expected_source_doc")]
            levels.append({
                "concurrency": concurrency, "requests": len(batch), "seconds": round(wall, 2),
                "queries_per_second": round(len(batch) / max(wall, 1e-9), 1),
                "latency": latency_summary([ms for ms, _ in outcomes]),
                "hit_at_k_specific": round(sum(specific) / len(specific), 3) if specific else None,
                "rss_mb": rss_mb(),
            })
    return write_report(work, "search", {
        "embedding": args.embedding, "backend": args.backend, "top_k": args.top_k,
        "first_query_ms": round(first_ms, 1), "levels": levels,
        "memory_mb": {"before_first_query": before, "after_first_query": after_first,
                      "peak_sampled": memory.peak, "peak_process": peak_rss_mb()},
        "note": "first query builds the keyword index; hit_at_k_specific: questions naming one document code",
    })


# conversion runs in worker processes: module-level functions only


def _write_docx(path: Path, rows: List[Dict[str, Any]]) -> None:
    from xml.sax.saxutils import escape

    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{escape(r['text'])}</w:t></w:r></w:p>" for r in rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')


def _write_pdf(path: Path, index: int, pages: int) -> None:
    from docextract.testing import simple_pdf

    # simple_pdf uses Helvetica, so the PDF text is ASCII.
    specs = []
    for page in range(pages):
        lines = [(72, 72 + 14 * n, 10, f"Rule R-{index:05d} article {page + 1} line {n}: approval within {n + 3} days.")
                 for n in range(40)]
        specs.append({"texts": lines})
    path.write_bytes(simple_pdf(specs))


def _convert_one(path: str) -> Tuple[str, float, int]:
    from rag_core.document_converters import convert_file_to_canonical_chunks

    started = time.perf_counter()
    chunks = convert_file_to_canonical_chunks(path)
    return path, (time.perf_counter() - started) * 1000, len(chunks)


def cmd_convert(args) -> Dict[str, Any]:
    work = Path(args.work)
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(args.docs):
        if i % 2 == 0:
            path = raw / f"scale_{i:05d}.pdf"
            _write_pdf(path, i, args.pdf_pages)
        else:
            path = raw / f"scale_{i:05d}.docx"
            _write_docx(path, document_rows(i, seed=args.seed, chunks=args.chunks_per_doc, chars=args.chunk_chars, tenants=1))
        files.append(str(path))
    import multiprocessing

    with MemorySampler() as memory:
        started = time.perf_counter()
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            results = list(pool.map(_convert_one, files))
        wall = time.perf_counter() - started
    by_kind: Dict[str, List[float]] = {}
    for path, ms, _ in results:
        by_kind.setdefault(Path(path).suffix.lstrip("."), []).append(ms)
    return write_report(work, "convert", {
        "docs": len(files), "workers": args.workers, "pdf_pages": args.pdf_pages, "seconds": round(wall, 2),
        "docs_per_second": round(len(files) / max(wall, 1e-9), 2),
        "chunks": sum(n for _, _, n in results),
        "latency_per_doc": {kind: latency_summary(ms) for kind, ms in by_kind.items()},
        "memory_mb": {"parent_peak_sampled": memory.peak,
                      "largest_worker_peak": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)},
        "estimate_for_50000_docs_minutes": round(50000 / max(len(files) / max(wall, 1e-9), 1e-9) / 60, 1),
        "note": "synthetic PDFs (ASCII text, no OCR) and DOCX; scanned pages with OCR are much slower",
    })


def _http_worker(url: str, endpoint: str, queries: List[Dict[str, Any]], offset: int, deadline: float,
                 headers: Dict[str, str], timeout: float, top_k: int) -> List[Tuple[int, float]]:
    parts = urlsplit(url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = None
    out: List[Tuple[int, float]] = []
    n = offset
    while time.perf_counter() < deadline:
        q = queries[n % len(queries)]
        n += 1
        if endpoint.startswith("/chat"):
            body = {"question": q["query"], "tenant_id": q.get("tenant_id")}
        else:
            body = {"query": q["query"], "top_k": top_k, "tenant_id": q.get("tenant_id")}
        started = time.perf_counter()
        try:
            if conn is None:
                conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
            conn.request("POST", (parts.path.rstrip("/") + endpoint), body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                         headers=headers)
            resp = conn.getresponse()
            resp.read()
            status = resp.status
        except (OSError, http.client.HTTPException):
            status = 0  # connection error or timeout
            if conn is not None:
                conn.close()
            conn = None
        out.append((status, (time.perf_counter() - started) * 1000))
    if conn is not None:
        conn.close()
    return out


def cmd_http(args) -> Dict[str, Any]:
    work = Path(args.work)
    queries = _read_jsonl(work / "queries.jsonl")
    if not queries:
        raise SystemExit("no queries: run generate first")
    headers = {"Content-Type": "application/json"}
    if args.api_key_env:
        key = os.environ.get(args.api_key_env, "")
        if not key:
            raise SystemExit(f"{args.api_key_env} is empty")
        headers["X-Api-Key"] = key  # from the environment only; never printed or saved
    levels = []
    for concurrency in _levels(args.concurrency):
        _http_worker(args.url, args.endpoint, queries, 0, time.perf_counter() + args.warmup, headers, args.timeout, args.top_k)
        with MemorySampler(pid=args.server_pid) as memory:
            before = rss_mb(args.server_pid) if args.server_pid else None
            deadline = time.perf_counter() + args.duration
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(_http_worker, args.url, args.endpoint, queries, i * 7919, deadline, headers,
                                       args.timeout, args.top_k) for i in range(concurrency)]
                samples = [s for f in futures for s in f.result()]
            wall = time.perf_counter() - started
        statuses: Dict[str, int] = {}
        for status, _ in samples:
            statuses[str(status)] = statuses.get(str(status), 0) + 1
        ok = [ms for status, ms in samples if 200 <= status < 300]
        levels.append({
            "concurrency": concurrency, "seconds": round(wall, 2), "requests": len(samples),
            "ok_per_second": round(len(ok) / max(wall, 1e-9), 2),
            "error_rate": round(1 - len(ok) / len(samples), 4) if samples else None,
            "status_counts": statuses, "latency_ok": latency_summary(ok),
            "server_rss_mb": {"before": before, "peak": memory.peak} if args.server_pid else None,
        })
    return write_report(work, f"http_{args.endpoint.strip('/').replace('/', '_')}", {
        "url": args.url, "endpoint": args.endpoint, "duration_seconds": args.duration, "levels": levels,
        "note": "status 0 = connection error or timeout; server memory needs --server-pid on the same host",
    })


def cmd_all(args) -> Dict[str, Any]:
    cmd_generate(args)
    return {"ingest": cmd_ingest(args), "search": cmd_search(args)}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, *, corpus=False, store=False):
        p.add_argument("--work", required=True, help="output folder (created; nothing outside it is written)")
        if corpus:
            p.add_argument("--docs", type=int, default=50000)
            p.add_argument("--chunks-per-doc", type=int, default=4)
            p.add_argument("--chunk-chars", type=int, default=400)
            p.add_argument("--tenants", type=int, default=1)
            p.add_argument("--queries", type=int, default=200)
            p.add_argument("--seed", type=int, default=13)
        if store:
            p.add_argument("--embedding", choices=["model", "dummy"], default="model")
            p.add_argument("--dummy-dim", type=int, default=512, help="vector size of --embedding dummy (ruri-v3-130m: 512)")
            p.add_argument("--backend", choices=["chroma", "qdrant-local", "qdrant-url"], default="chroma")
            p.add_argument("--qdrant-url", default=None)
            p.add_argument("--batch", type=int, default=64)
            p.add_argument("--top-k", type=int, default=10)
            p.add_argument("--concurrency", default="1,4,8")
            p.add_argument("--requests", type=int, default=200, help="search: requests per concurrency level")
        return p

    common(sub.add_parser("generate"), corpus=True).set_defaults(func=cmd_generate)
    common(sub.add_parser("ingest"), store=True).set_defaults(func=cmd_ingest)
    common(sub.add_parser("search"), store=True).set_defaults(func=cmd_search)
    common(sub.add_parser("all"), corpus=True, store=True).set_defaults(func=cmd_all)
    conv = common(sub.add_parser("convert"), corpus=True)
    conv.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    conv.add_argument("--pdf-pages", type=int, default=3)
    conv.set_defaults(func=cmd_convert)
    web = common(sub.add_parser("http"))
    web.add_argument("--url", required=True, help="e.g. http://127.0.0.1:8000")
    web.add_argument("--endpoint", default="/search", choices=["/search", "/chat"])
    web.add_argument("--concurrency", default="1,4,16")
    web.add_argument("--duration", type=float, default=60.0, help="seconds per concurrency level")
    web.add_argument("--warmup", type=float, default=3.0)
    web.add_argument("--timeout", type=float, default=60.0)
    web.add_argument("--top-k", type=int, default=10)
    web.add_argument("--api-key-env", default=None, help="name of the environment variable holding the API key")
    web.add_argument("--server-pid", type=int, default=None, help="uvicorn master pid (workers are included)")
    web.set_defaults(func=cmd_http)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
