"""Offline product retrieval evaluation with isolated per-run storage.

A fresh work subdirectory and collection are created on every execution.
Qdrant uses only embedded storage under that directory, never a remote server.
Keyword questions use deterministic non-morphological extraction and therefore
are a different evaluation series from the older morpheme-based benchmark.
Missing-document negatives are synthetic; withheld FAQ rows are not assumed
unanswerable. Answer correctness uses strict full-answer substring matching
and is reported separately from citation correctness.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import tempfile
import uuid
import json
import os
import random
import statistics
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COLLECTION = "product_eval_jagov"  # replaced by a fresh identifier for each run


def configure(work: Path, backend: str = "chroma", collection: str | None = None) -> None:
    """Point the app at --work. Must run before config / rag_core are imported."""
    os.environ.update({
        "VECTORSTORE_DIR": str(work / "vectorstore"),
        "CHUNKS_JSONL_PATH": str(work / "keyword.jsonl"),
        "VECTORSTORE_COLLECTION_NAME": collection or COLLECTION,
        "HF_HOME": str(work / "hf_cache"),
        "HF_HUB_CACHE": str(work / "hf_cache" / "hub"),
        "TRANSFORMERS_CACHE": str(work / "hf_cache" / "transformers"),
        "SENTENCE_TRANSFORMERS_HOME": str(work / "sentence_transformers"),
        "TORCH_HOME": str(work / "torch_cache"),
        "XDG_CACHE_HOME": str(work / "xdg_cache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "CHAT_GENERATION_MODE": "extractive",
        "LLM_ENABLED": "false",
        "QDRANT_PATH": str(work / "qdrant"),
        "INDEX_DIR": str(work / "index"),
        "RUNS_DIR": str(work / "runs"),
        "KEYWORD_INDEX_PERSIST": "false",
    })
    # Qdrant is embedded in this newly-created run directory, never a shared server.
    if backend not in {"chroma", "qdrant"}:
        raise ValueError("unsupported isolated backend")
    os.environ["VECTOR_BACKEND"] = backend
    os.environ.pop("CHROMA_COLLECTION", None)


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def rank_metrics(ranks):
    """ranks: 1-based rank of the gold document, or None when it is not in the list."""
    n = len(ranks)
    if not n:
        return {"R@1": None, "R@5": None, "R@10": None, "MRR@10": None}
    out = {f"R@{k}": round(100 * sum(1 for r in ranks if r and r <= k) / n, 1) for k in (1, 5, 10)}
    out["MRR@10"] = round(100 * sum(1 / r for r in ranks if r and r <= 10) / n, 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=3000)
    ap.add_argument("--queries", type=int, default=150)
    ap.add_argument("--unanswerable", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--work", default=str(ROOT / "data" / "external" / "product_eval"))
    ap.add_argument("--backend", choices=("chroma", "qdrant"), default="chroma")
    ap.add_argument("--data", default=str(ROOT / "data" / "external" / "jagovfaqs" / "data.jsonl"))
    args = ap.parse_args()
    source = Path(args.data).resolve()
    if not source.is_file():
        raise SystemExit(f"dataset missing: {source}")
    if args.docs <= 0 or args.queries <= 0 or args.unanswerable < 0:
        raise SystemExit("docs and queries must be positive; unanswerable must be nonnegative")
    if "config" in sys.modules or any(name == "rag_core" or name.startswith("rag_core.") for name in sys.modules):
        raise SystemExit("isolated evaluation requires a fresh process before application modules are loaded")
    work_base = Path(args.work).resolve()
    external = (ROOT / "data" / "external").resolve()
    allowed = [Path("/tmp").resolve()]
    if external.is_relative_to(ROOT.resolve()):
        allowed.append(external)
    if not any(work_base == base or work_base.is_relative_to(base) for base in allowed):
        raise SystemExit("--work must be under /tmp or data/external")
    work_base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="jagov-run-", dir=work_base))
    collection = "jagov_" + uuid.uuid4().hex
    configure(work, args.backend, collection)

    import config
    from rag_core import qa, retrieval, embedding_provider
    if not embedding_provider.is_local_provider():
        raise SystemExit("isolated evaluation requires a local offline embedding provider")
    from scripts import ingest_canonical_jsonl as ingest

    # same selection as tools/eval_real_vector_jagov.py
    data_bytes = source.read_bytes()
    rows = [json.loads(line) for line in data_bytes.decode("utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("Question"), str)
            and isinstance(r.get("Answer"), str) and 10 <= len(r["Question"]) <= 120
            and 20 <= len(r["Answer"]) <= 600]
    if len(rows) < args.docs or args.queries > args.docs:
        raise SystemExit(f"insufficient eligible data: need {args.docs} documents and {args.queries} queries, have {len(rows)}")
    random.Random(args.seed).shuffle(rows)
    rows = rows[: args.docs]
    # Withheld rows are not automatically unanswerable: another document may answer them.
    # Use explicit nonexistent synthetic document identifiers instead.
    corpus_text = "\n".join(r["Question"] + r["Answer"] for r in rows)
    held_out = []
    for i in range(args.unanswerable):
        marker = "EVAL-MISSING-" + hashlib.sha256(f"{args.seed}:{i}".encode()).hexdigest()[:24]
        if marker.casefold() in corpus_text.casefold():
            raise SystemExit("synthetic missing-document marker collides with corpus")
        held_out.append({"Question": f"資料識別子 {marker} の内容は何ですか？"})
    # Identical full questions with conflicting answers are not a valid single
    # gold answer. Keep the corpus but exclude those cases from scoring.
    answers_by_question = {}
    for row in rows:
        question_key = unicodedata.normalize("NFKC", row["Question"]).strip().casefold()
        answer_key = unicodedata.normalize("NFKC", row["Answer"]).strip().casefold()
        answers_by_question.setdefault(question_key, set()).add(answer_key)
    sample = [i for i, row in enumerate(rows)
              if len(answers_by_question[unicodedata.normalize("NFKC", row["Question"]).strip().casefold()]) == 1]
    excluded_ambiguous = len(rows) - len(sample)
    if len(sample) < args.queries:
        raise SystemExit(f"insufficient unambiguous questions: need {args.queries}, have {len(sample)}")
    random.Random(args.seed + 1).shuffle(sample)
    sample = sample[: args.queries]

    chunk_rows = []
    for i, r in enumerate(rows):
        chunk_id = f"jg-{i:05d}"
        chunk_rows.append({
            "id": chunk_id, "chunk_id": chunk_id,
            "text": f"Q: {r['Question'].strip()}\nA: {r['Answer'].strip()}",
            "type": "pdf", "quality": "high", "searchable": 1,
            "source_doc": f"jg_{i:05d}.pdf", "source_file": f"jg_{i:05d}.pdf", "doc_id": f"jg_{i:05d}",
            "chunk_index": 0, "source_pages": [1], "source_page_start": 1, "source_page_end": 1,
            "doc_type": "faq", "chunk_type": "text", "source_type": "pdf", "parser": "jagovfaqs",
            "tenant_id": "default", "section_path": ["JaGovFaqs"],
        })

    t0 = time.perf_counter()
    generation = ingest.new_generation_id()
    result = ingest.ingest_canonical_rows(chunk_rows, collection_name=collection, batch=64, reset=False, replace_documents=False,
                                          generation_id=generation)
    ingest.write_keyword_rows(work / "keyword.jsonl", chunk_rows, generation_id=generation)
    ingest_seconds = round(time.perf_counter() - t0, 1)
    print(f"ingested {result.get('ingested')} rows in {ingest_seconds}s", flush=True)

    def keyword_query(q):
        words = re.findall(r"[A-Za-z0-9]+|[一-龥々〆〤]{2,}|[ァ-ヴー]{2,}", unicodedata.normalize("NFKC", q))
        return " ".join(words[:6]) or q

    def partial_query(q):
        return q[: max(6, int(len(q) * 0.55))]

    report = {
        "docs": len(rows), "queries": len(sample), "unanswerable": len(held_out), "ingest_seconds": ingest_seconds,
        "excluded_ambiguous_questions": excluded_ambiguous,
        "evaluation_series": "product_jagov_deterministic_v2_not_comparable_to_morpheme_v1",
        "data_sha256": hashlib.sha256(data_bytes).hexdigest(),
        "settings": {
            "seed": args.seed, "backend": args.backend, "collection": collection,
            "model_fingerprint": embedding_provider.active_fingerprint(),
            "answer_correctness": "strict normalized full-answer substring; not semantic equivalence",
            "negative_labels": "synthetic nonexistent document identifiers; held-out FAQ questions are not assumed unanswerable",
            "embed_model": getattr(config, "LOCAL_EMBED_MODEL", None),
            "chat_generation_mode": getattr(config, "CHAT_GENERATION_MODE", None),
            "keyword_tokenizer": getattr(config, "KEYWORD_TOKENIZER", None),
            "cross_encoder_enabled": getattr(config, "CROSS_ENCODER_ENABLED", None),
            "rag_dist_calibrated_model": getattr(config, "RAG_DIST_CALIBRATED_MODEL", None),
        },
        "retrieval": {}, "answer": {},
    }

    variants = (("keyword", keyword_query), ("partial", partial_query), ("full", lambda q: q))

    # 1. product retrieval: hybrid_retrieve
    retrieval.hybrid_retrieve(rows[0]["Question"], None, top_k=10, tenant_id="default")  # warm-up: model + keyword index
    for name, make in variants:
        ranks, ms = [], []
        for i in sample:
            started = time.perf_counter()
            hits = retrieval.hybrid_retrieve(make(rows[i]["Question"]), None, top_k=10, tenant_id="default")
            ms.append((time.perf_counter() - started) * 1000)
            docs = [(h.metadata or {}).get("source_doc") for h in hits]
            gold = f"jg_{i:05d}.pdf"
            ranks.append(docs.index(gold) + 1 if gold in docs else None)
        report["retrieval"][name] = {**rank_metrics(ranks), "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95)}
        print(f"[retrieval {name}] {report['retrieval'][name]}", flush=True)

    # 2. product answer path (default: extractive, guard on)
    def run_answer(question):
        started = time.perf_counter()
        answer, trace = qa.answer_query_with_trace(question, top_k=20, tenant_id="default")
        elapsed = (time.perf_counter() - started) * 1000
        return answer, trace, elapsed

    for name, make in variants:
        reasons, cited_gold, cited_any, correct, ms = {}, 0, 0, 0, []
        for i in sample:
            answer, _trace, elapsed = run_answer(make(rows[i]["Question"]))
            ms.append(elapsed)
            reason = answer.guard_reason or "answered"
            reasons[reason] = reasons.get(reason, 0) + 1
            if not answer.guard_reason:
                cited = {c.source_doc for c in answer.citations}
                cited_any += bool(cited)
                cited_gold += f"jg_{i:05d}.pdf" in cited
                expected = unicodedata.normalize("NFKC", rows[i]["Answer"]).strip().casefold()
                actual = unicodedata.normalize("NFKC", answer.answer_text or "").strip().casefold()
                correct += bool(expected and expected in actual)
        n = len(sample)
        answered = reasons.get("answered", 0)
        report["answer"][name] = {
            "answered_pct": round(100 * answered / n, 1),
            # Partial and keyword queries can become ambiguous when context is
            # removed; do not claim their outcome is a verified error rate.
            "label_status": "source_answer_present_no_exact_conflicts" if name == "full" else "diagnostic_query_label_unverified",
            "false_abstain_pct": round(100 * (n - answered) / n, 1) if name == "full" else None,
            "strict_answer_correct_pct_of_all": round(100 * correct / n, 1) if name == "full" else None,
            "strict_wrong_answer_pct_of_all": round(100 * (answered - correct) / n, 1) if name == "full" else None,
            "gold_cited_pct_of_all": round(100 * cited_gold / n, 1),
            "gold_cited_pct_of_answered": round(100 * cited_gold / answered, 1) if answered else None,
            "outcomes": reasons, "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95),
            "mean_ms": round(statistics.mean(ms), 1),
        }
        print(f"[answer {name}] {report['answer'][name]}", flush=True)

    reasons, ms = {}, []
    for r in held_out:
        answer, _trace, elapsed = run_answer(r["Question"])
        ms.append(elapsed)
        reason = answer.guard_reason or "answered"
        reasons[reason] = reasons.get(reason, 0) + 1
    n = len(held_out)
    report["answer"]["unanswerable"] = {
        "abstained_pct": round(100 * (n - reasons.get("answered", 0)) / n, 1) if n else None,
        "answered_anyway_pct": round(100 * reasons.get("answered", 0) / n, 1) if n else None,
        "wrong_answer_pct": round(100 * reasons.get("answered", 0) / n, 1) if n else None,
        "outcomes": reasons, "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95),
    }
    print(f"[answer unanswerable] {report['answer']['unanswerable']}", flush=True)

    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report: {work / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
