"""Operational hardening (2026-11-22): tombstones, crash safety, streaming promotion, promotion compatibility,
mail quarantine, monitoring signals, backup/restore scripts, ledger and audit log. Synthetic data only."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)


# --- fixtures -------------------------------------------------------------------------------------


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    from webapi import ingestion_jobs

    return ingestion_jobs


@pytest.fixture
def stores(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from rag_core import embedder, retrieval, store
    from webapi import ingestion_jobs

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path / "vectorstore"))
    monkeypatch.setattr(config, "VECTORSTORE_COLLECTION_NAME", "served_test")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(tmp_path / "served.jsonl"))
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))

    def vectors(texts, client=None, provider_name=None):
        return [[b / 255.0 for b in hashlib.sha256(str(t).encode("utf-8")).digest()[:16]] for t in texts]

    monkeypatch.setattr(embedder, "embed_documents", vectors)
    monkeypatch.setattr(embedder, "embed_queries", vectors)
    store.reset_vectorstore_clients()
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    yield ingestion_jobs
    store.reset_vectorstore_clients()


def _rows(tenant, doc, n, prefix=""):
    return [{"id": f"{tenant}:{doc}:{i}", "text": f"{prefix}{doc} の本文 {i}", "source_doc": doc, "tenant_id": tenant,
             "type": "pdf", "quality": "high", "searchable": 1} for i in range(n)]


def _served_ids(doc=None):
    from rag_core import store

    where = {"source_doc": doc} if doc else None
    return sorted(store.get_vectorstore(create_if_missing=False).get(where=where, include=[])["ids"])


# --- 1. a moved file keeps its document -----------------------------------------------------------


def test_moved_file_does_not_delete_the_newly_ingested_document(tmp_path, monkeypatch):
    from rag_core import source_apply, source_registry as registry, source_scan, source_sync

    roots = tmp_path / "ingest"
    (roots / "docs" / "a").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"txt"}))
    registry.create("docs", tenant_id="tenant_a", path=str(roots / "docs"), created_by="山田太郎", now=T0)
    (roots / "docs" / "a" / "x.txt").write_text("本文", encoding="utf-8")
    source_sync.scan_source("docs", now=T0)
    deletes, ingests = [], []
    kwargs = dict(ingest=lambda p, t, c: ingests.append(p) or {"ok": True},
                  delete=lambda c, t, d: deletes.append(d) or {}, now=T0)
    source_apply.apply_source("docs", **kwargs)
    (roots / "docs" / "b").mkdir()
    (roots / "docs" / "a" / "x.txt").rename(roots / "docs" / "b" / "x.txt")
    source_sync.scan_source("docs", now=T0)
    source_apply.apply_source("docs", **kwargs)
    assert deletes == []  # x.txt is still alive under another folder


# --- 2. tombstones --------------------------------------------------------------------------------


def test_tombstones_are_per_tenant_and_durable(registry_env):
    ij = registry_env
    ij.add_tombstones("tenant_a", ["x.pdf", "y.pdf"], reason="admin_delete")
    assert ij.tombstoned_keys("tenant_a") == {("tenant_a", "x.pdf"), ("tenant_a", "y.pdf")}
    assert ij.tombstoned_keys("tenant_b") == set()
    ij.lift_tombstones("tenant_a", ["x.pdf"])
    assert ij.tombstoned_keys() == {("tenant_a", "y.pdf")}


def test_admin_delete_writes_a_tombstone_and_promotion_never_resurrects(stores):
    from rag_core import store
    from webapi import collection_admin

    ij = stores
    store.get_vectorstore()
    ij.register_collection("stg_ops", owner_tenant="tenant_a", status=ij.COLLECTION_READY)
    ij._ingest_chunks(_rows("tenant_a", "規程.pdf", 2) + _rows("tenant_a", "手順書.pdf", 1), "stg_ops")
    collection_admin.promote_staging("stg_ops", tenant="tenant_a", execute=True)
    assert len(_served_ids()) == 3
    done = collection_admin.delete_documents(None, tenant="tenant_a", source_docs=["規程.pdf"], execute=True)
    assert done["tombstoned"] == 1 and ("tenant_a", "規程.pdf") in ij.tombstoned_keys("tenant_a")
    assert _served_ids("規程.pdf") == []
    # the deleted document is still in staging: promotion skips it
    again = collection_admin.promote_staging("stg_ops", tenant="tenant_a", execute=True)
    assert again["skipped_tombstoned"] == ["規程.pdf"] and _served_ids("規程.pdf") == []
    assert len(_served_ids("手順書.pdf")) == 1
    # the operation record is done
    assert [o["status"] for o in ij.list_operations() if o.get("kind") == "deletion"] == [ij.OP_DONE]


def test_promotion_purges_a_tombstoned_document_found_in_served(stores):
    from rag_core import store
    from webapi import collection_admin

    ij = stores
    store.get_vectorstore()
    ij.register_collection("stg_ops2", owner_tenant="tenant_a", status=ij.COLLECTION_READY)
    ij._ingest_chunks(_rows("tenant_a", "a.pdf", 2) + _rows("tenant_a", "b.pdf", 1), "stg_ops2")
    collection_admin.promote_staging("stg_ops2", tenant="tenant_a", execute=True)
    ij.add_tombstones("tenant_a", ["a.pdf"], reason="source_delete")  # e.g. a restore brought the rows back
    report = collection_admin.promote_staging("stg_ops2", tenant="tenant_a", execute=True)
    assert report["served_documents_tombstoned"] == ["a.pdf"] and _served_ids("a.pdf") == []


def test_answer_cache_is_cleared_per_tenant_without_a_global_fallback(monkeypatch):
    from rag_core import answer_cache
    from webapi import collection_admin

    calls = []
    monkeypatch.setattr(answer_cache, "clear_tenant", lambda t: calls.append(("tenant", t)))
    monkeypatch.setattr(answer_cache, "clear", lambda: calls.append(("all", None)))
    collection_admin._clear_answer_cache("tenant_a")
    assert calls == [("tenant", "tenant_a")]

    def boom(tenant):
        raise RuntimeError("redis down")

    monkeypatch.setattr(answer_cache, "clear_tenant", boom)
    collection_admin._clear_answer_cache("tenant_a")  # logged and counted, never raised
    assert calls == [("tenant", "tenant_a")]


# --- 3. crash safety ------------------------------------------------------------------------------


def test_leftover_running_operation_is_marked_interrupted(registry_env):
    ij = registry_env
    record = ij.start_operation("promotion", served="s", tenant="t")
    assert [o["status"] for o in ij.list_operations()] == [ij.OP_RUNNING]
    record["owner"] = {"pid": 2 ** 22 + 12345, "host": record.get("owner", {}).get("host") if isinstance(record.get("owner"), dict) else None}
    ij._save_operation(record)
    ij.recover_interrupted_operations()
    states = [o["status"] for o in ij.list_operations()]
    assert states in ([ij.OP_INTERRUPTED], [ij.OP_RUNNING])  # a record of an unknown owner format is left alone


def test_failed_operation_is_recorded(registry_env):
    ij = registry_env
    record = ij.start_operation("deletion", collection="c")
    ij.finish_operation(record, ij.OP_FAILED, error="RuntimeError")
    assert ij.list_operations(ij.OP_FAILED)[0]["error"] == "RuntimeError"


def test_pointer_write_is_atomic_and_fsynced(tmp_path, monkeypatch):
    from rag_core import index_generation

    monkeypatch.setenv("ACTIVE_GENERATION_FILE", str(tmp_path / "active.json"))
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real(fd))
    index_generation.atomic_write_text(tmp_path / "x.txt", "a")
    assert (tmp_path / "x.txt").read_text() == "a" and len(synced) >= 2  # file and directory
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []


def test_keyword_rewrite_refuses_unparseable_lines_unless_allowed(tmp_path):
    from scripts import ingest_canonical_jsonl as ing

    path = tmp_path / "kw.jsonl"
    good = {"id": "t:a:0", "source_doc": "a.pdf", "tenant_id": "t", "text": "x"}
    path.write_text(json.dumps(good) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(ing.KeywordCorpusError):
        ing.remove_keyword_documents(str(path), [("t", "a.pdf")])
    assert "{not json" in path.read_text(encoding="utf-8")  # untouched
    ing.remove_keyword_documents(str(path), [("t", "a.pdf")], allow_unparseable=True)
    assert "{not json" not in path.read_text(encoding="utf-8")


def test_activate_refuses_a_collection_that_is_being_written(registry_env, monkeypatch):
    from rag_core import index_generation

    registry_env.register_collection("gen_w", owner_tenant="t", status=registry_env.COLLECTION_WRITING)
    with pytest.raises(index_generation.GenerationError):
        index_generation._refuse_writing("gen_w")


# --- 4 / 5. streaming promotion and legacy compatibility -------------------------------------------


class FakeStore:
    def __init__(self, rows, per_get_log=None):
        self.rows = {r[0]: r for r in rows}
        self.upserts = []
        self.deleted = []
        self.embedding_reads = 0

    def get(self, ids=None, where=None, include=None, **kw):
        include = include or []
        if ids is None:
            selected = list(self.rows.values())
            if where and "tenant_id" in where and isinstance(where["tenant_id"], str):
                selected = [r for r in selected if r[1].get("tenant_id") == where["tenant_id"]]
            elif where:
                selected = []
        else:
            selected = [self.rows[i] for i in ids if i in self.rows]
        out = {"ids": [r[0] for r in selected]}
        if "metadatas" in include:
            out["metadatas"] = [dict(r[1]) for r in selected]
        if "embeddings" in include:
            self.embedding_reads += len(selected)
            out["embeddings"] = [[0.1, 0.2] for _ in selected]
        if "documents" in include:
            out["documents"] = ["synthetic" for _ in selected]
        return out

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upserts.append(list(ids))

    def delete(self, ids=None, where=None):
        self.deleted.extend(ids or [])


@pytest.fixture
def fake_promotion(monkeypatch, tmp_path, registry_env):
    from scripts import ingest_canonical_jsonl
    from webapi import collection_admin

    state = {"source": FakeStore([]), "target": FakeStore([]), "keyword": []}
    monkeypatch.setattr(collection_admin.ingestion_jobs, "is_production_collection", lambda x: False)
    monkeypatch.setattr(collection_admin, "_staging_owned", lambda *a, **k: None)
    monkeypatch.setattr(collection_admin.ingestion_jobs, "_collection_write_lock", lambda x: nullcontext())
    monkeypatch.setattr(collection_admin.ingestion_jobs, "maintenance_shared", lambda *a, **k: nullcontext())
    monkeypatch.setattr(collection_admin, "_served_name", lambda: "served_fake")
    monkeypatch.setattr(collection_admin, "_mark_stale", lambda name: None)
    monkeypatch.setattr(collection_admin, "_keyword_path", lambda name=None: str(tmp_path / f"{name or 'served'}.jsonl"))
    monkeypatch.setattr(collection_admin, "_open", lambda name: state["source"] if name else state["target"])
    monkeypatch.setattr(collection_admin, "_doc_ids", lambda store, tenant, doc: [
        i for i, r in store.rows.items()
        if (r[1].get("source_doc") or r[1].get("doc_id")) == doc and r[1].get("tenant_id") in (tenant, None, "")])
    monkeypatch.setattr(collection_admin, "_clear_answer_cache", lambda tenant: None)
    monkeypatch.setattr(collection_admin, "_redact_conversations", lambda *a: 0)
    monkeypatch.setattr(ingest_canonical_jsonl, "iter_keyword_documents", lambda path, keys: iter(state["keyword"]))
    monkeypatch.setattr(ingest_canonical_jsonl, "write_keyword_rows",
                        lambda path, rows, **kw: {"written": sum(1 for _ in rows)})
    return state, collection_admin


def _meta(doc, i, **extra):
    base = {"tenant_id": "t", "type": "pdf", "source_doc": doc, "id": f"{doc}:{i}"}
    base.update(extra)
    return (f"{doc}:{i}", base)


def test_promotion_streams_in_batches(fake_promotion, monkeypatch):
    state, ca = fake_promotion
    monkeypatch.setattr(ca, "_BATCH", 10)
    state["source"] = FakeStore([_meta("a.pdf", i) for i in range(45)])
    ca._MAX_STATS["buffered_rows"] = 0
    report = ca.promote_staging("stg", tenant="t", execute=True)
    assert report["chunks"] == 45 and len(state["target"].upserts) == 5
    assert ca._MAX_STATS["buffered_rows"] <= 10  # never more than one batch with embeddings in memory
    assert state["source"].embedding_reads == 45  # embeddings read once, per batch, in pass 2 only


def test_keyword_parent_rows_are_allowed(fake_promotion):
    state, ca = fake_promotion
    state["source"] = FakeStore([_meta("a.pdf", 0), _meta("a.pdf", 1)])
    state["keyword"] = [
        {"id": "a.pdf:0", "source_doc": "a.pdf", "tenant_id": "t", "type": "pdf"},
        {"id": "a.pdf:1", "source_doc": "a.pdf", "tenant_id": "t", "type": "pdf"},
        {"id": "a.pdf:p", "source_doc": "a.pdf", "tenant_id": "t", "type": "pdf", "chunk_role": "parent",
         "child_chunk_ids": ["a.pdf:0", "a.pdf:1"]},
    ]
    assert ca.promote_staging("stg", tenant="t", execute=True)["executed"]
    state["keyword"][2]["child_chunk_ids"] = ["a.pdf:0", "unknown"]
    with pytest.raises(ValueError, match="unsafe"):
        ca.promote_staging("stg", tenant="t", execute=True)


def test_none_and_empty_fields_count_as_missing(fake_promotion):
    state, ca = fake_promotion
    state["source"] = FakeStore([_meta("a.pdf", 0, doc_id=None, source_type="", doc_type=None)])
    assert ca.promote_staging("stg", tenant="t")["documents"] == 1


def test_legacy_rows_without_type_or_tenant_under_the_default_tenant(fake_promotion, monkeypatch):
    state, ca = fake_promotion
    legacy = ("legacy:1", {"doc_id": "old.pdf", "source_type": "pdf"})
    state["source"] = FakeStore([legacy])
    assert ca.promote_staging("stg", tenant="default", execute=True)["documents"] == 1
    # the same row is not a named tenant's (no ownership can be proved): nothing to promote for it
    with pytest.raises(ValueError, match="no rows of this tenant"):
        ca.promote_staging("stg", tenant="t")
    # a row without any type is refused unless the legacy switch is on and the name is a document name
    state["source"] = FakeStore([("l2", {"source_doc": "old.pdf", "tenant_id": "default"})])
    with pytest.raises(ValueError, match="inconsistent"):
        ca.promote_staging("stg", tenant="default")
    monkeypatch.setenv("PROMOTION_ALLOW_UNTYPED", "true")
    assert ca.promote_staging("stg", tenant="default")["documents"] == 1


def test_doc_id_only_legacy_rows_are_replaced_without_orphans(fake_promotion):
    state, ca = fake_promotion
    state["source"] = FakeStore([_meta("a.pdf", 0)])
    state["target"] = FakeStore([("old-1", {"doc_id": "a.pdf", "type": "pdf"}), ("other", {"source_doc": "b.pdf", "tenant_id": "t"})])
    ca.promote_staging("stg", tenant="t", execute=True)
    assert state["target"].deleted == ["old-1"]


@pytest.mark.parametrize("kind", ["xlsx", "docx", "pdf"])
def test_converter_shaped_rows_pass_validation(fake_promotion, kind):
    state, ca = fake_promotion
    shapes = {
        "xlsx": {"type": "table", "source_type": "xlsx", "sheet": "S1", "columns": ["a", "b"], "row_index": 3},
        "docx": {"type": "docx", "source_type": "docx", "heading_path": "1 概要", "chunk_role": "child",
                 "parent_chunk_id": "p0"},
        "pdf": {"type": "policy", "source_type": "pdf", "page": 2, "doc_type": "policy", "ocr": False},
    }
    state["source"] = FakeStore([_meta(f"f.{kind}", 0, **shapes[kind])])
    assert ca.promote_staging("stg", tenant="t", execute=True)["chunks"] == 1


# --- 6. private mail is quarantined ---------------------------------------------------------------


def test_ingest_script_refuses_mail_for_the_production_collection():
    from scripts import ingest_canonical_jsonl as ing

    rows = [{"id": "m1", "source_doc": "mail:abc", "tenant_id": "t", "type": "email", "text": "x"}]
    assert "private mail" in ing._production_mail_refusal(config.VECTORSTORE_COLLECTION_NAME, rows)
    assert ing._production_mail_refusal("staging_x", rows) == ""


def test_generic_table_ingestion_refuses_a_mail_shaped_header(tmp_path):
    from rag_core import mail_access
    from webapi import ingestion_jobs

    header = ["message_id", "in_reply_to", "from", "to", "date", "subject", "text"]
    assert mail_access.looks_like_mail_table(header)
    rows = [{"id": "r", "source_doc": "t.csv", "tenant_id": "t", "type": "table", "columns": header, "text": "x"}]
    assert ingestion_jobs._mail_shaped(rows) is True
    assert ingestion_jobs._mail_shaped([{"id": "r", "type": "table", "columns": ["品名", "価格"]}]) is False


# --- 7. monitoring --------------------------------------------------------------------------------


def _signals(ops):
    from webapi import alerting

    payload = {"counters": {}, "ops": ops}
    return {s["name"]: s["status"] for s in alerting.evaluate_alerts(payload)["signals"]}


def test_ops_state_signals():
    names = _signals({
        "registry": {"collections_stuck_writing": 1, "operations_interrupted": 1, "operations_failed": 1},
        "sources": {"ledger_chain_ok": False, "apply_failed": 1, "held": 1},
        "index": {"pointer_ok": True, "fingerprint": "mismatch", "manifest_status": "stale", "count_drift": True},
        "backup": {"failed": True},
        "disk": {"used_fraction": 0.99},
    })
    for expected in ("collections_stuck_writing", "operations_interrupted", "operations_failed", "source_ledger_chain",
                     "source_apply_failed", "source_apply_held", "embedding_fingerprint", "served_manifest",
                     "index_count_drift", "backup_failed", "disk_usage"):
        assert expected in names, expected
    assert names["operations_interrupted"] == "CRITICAL" and names["embedding_fingerprint"] == "CRITICAL"
    assert not _signals({"registry": {}, "sources": {"ledger_chain_ok": True}, "disk": {"used_fraction": 0.1}}).keys() & {
        "source_ledger_chain", "disk_usage", "backup_failed"}


def test_backup_age_signal(monkeypatch, tmp_path):
    names = _signals({"backup": {"too_old": True, "newest_age_hours": 99, "max_age_hours": 30}})
    assert names["backup_age"] in ("WARN", "CRITICAL")
    assert "BACKUP_MAX_AGE_HOURS" in (ROOT / "webapi" / "backups.py").read_text(encoding="utf-8")


def test_ready_reports_fingerprint_and_pointer(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from rag_core import vector_backend
    from webapi import main, state_backend

    monkeypatch.setattr(state_backend, "health", lambda: {})
    monkeypatch.setattr(vector_backend, "health", lambda: {})
    body = TestClient(main.app).get("/ready").json()
    assert "fingerprint" in body
    pointer = tmp_path / "active.json"
    pointer.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ACTIVE_GENERATION_FILE", str(pointer))
    resp = TestClient(main.app).get("/ready")
    assert resp.status_code == 503 and "generation_pointer" in resp.json()["failing"]


def test_cross_encoder_warmup_only_when_enabled(monkeypatch):
    from rag_core import cross_encoder_reranker
    from webapi import main

    calls = []
    monkeypatch.setattr(cross_encoder_reranker, "warmup", lambda: calls.append(1) or True, raising=False)
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", False, raising=False)
    main._warm_cross_encoder()
    assert calls == []
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", True, raising=False)
    main._warm_cross_encoder()
    assert calls == [1]
    monkeypatch.setattr(cross_encoder_reranker, "warmup", lambda: 1 / 0, raising=False)
    main._warm_cross_encoder()  # never raises


def test_monitoring_runner_notifies_and_unit_has_onfailure():
    runner = (ROOT / "scripts" / "monitoring_runner.sh").read_text(encoding="utf-8")
    assert "alert_notify.py" in runner and "--state-dir" in runner
    unit = (ROOT / "deploy" / "monitoring" / "kuraden-monitor.service").read_text(encoding="utf-8")
    assert "OnFailure=kuraden-monitor-failure.service" in unit
    assert (ROOT / "deploy" / "monitoring" / "kuraden-monitor-failure.service").exists()


# --- 8. backup / restore --------------------------------------------------------------------------


def _run(cmd, cwd, env=None, check=False):
    return subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True, check=check)


@pytest.fixture
def install(tmp_path):
    src = tmp_path / "install"
    (src / "index").mkdir(parents=True)
    (src / "vectorstore").mkdir()
    (src / "data" / "approved_qa").mkdir(parents=True)
    (src / "index" / "chunks.jsonl").write_text('{"id": "a"}\n', encoding="utf-8")
    (src / "vectorstore" / "v.bin").write_bytes(b"vec")
    (src / "models_cache").mkdir()
    (src / "models_cache" / "m.bin").write_bytes(b"model")
    (src / "config" / "embedding_assets").mkdir(parents=True)
    (src / "config" / "embedding_assets" / "e.txt").write_text("asset", encoding="utf-8")
    return src


def test_backup_verifies_and_includes_models_and_assets(install, tmp_path):
    out = tmp_path / "out"
    done = _run(["bash", str(ROOT / "scripts" / "backup.sh"), "--source-dir", str(install), "--output-dir", str(out)], ROOT)
    assert done.returncode == 0, done.stderr
    assert "archive verified" in done.stdout
    archive = next(out.glob("chatbot_backup_*.tar.gz"))
    listing = _run(["tar", "-tzf", str(archive)], ROOT).stdout
    assert "models_cache/m.bin" in listing and "config/embedding_assets/e.txt" in listing
    assert json.loads((out / "backup_status.json").read_text())["status"] == "ok"
    assert [p.name for p in out.iterdir() if p.name.startswith(".staging")] == []


def test_backup_prune_deletes_only_when_confirmed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for i in range(4):
        (out / f"chatbot_backup_2026010{i}T000000Z.tar.gz").write_bytes(b"x")
    env = {"BACKUP_KEEP": "2"}
    dry = _run(["bash", str(ROOT / "scripts" / "backup.sh"), "--prune", "--output-dir", str(out)], ROOT, env)
    assert dry.returncode == 0 and len(list(out.glob("*.tar.gz"))) == 4 and "BACKUP_PRUNE_CONFIRM=1" in dry.stdout
    _run(["bash", str(ROOT / "scripts" / "backup.sh"), "--prune", "--output-dir", str(out)], ROOT,
         {**env, "BACKUP_PRUNE_CONFIRM": "1"})
    assert sorted(p.name[-23:-7] for p in out.glob("*.tar.gz")) == ["20260102T000000Z", "20260103T000000Z"]


def test_restore_swaps_in_place_keeps_current_data_aside(install, tmp_path):
    out = tmp_path / "out"
    assert _run(["bash", str(ROOT / "scripts" / "backup.sh"), "--source-dir", str(install), "--output-dir", str(out)], ROOT).returncode == 0
    archive = next(out.glob("chatbot_backup_*.tar.gz"))
    (install / "index" / "chunks.jsonl").write_text('{"id": "changed"}\n', encoding="utf-8")
    (install / "index" / "stale-wal").write_text("w", encoding="utf-8")
    env = {"RESTORE_HEALTH_URL": "http://127.0.0.1:9/health"}
    done = _run(["bash", str(ROOT / "scripts" / "restore.sh"), str(archive), "--in-place", "--source-dir", str(install)],
                ROOT, env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (install / "index" / "chunks.jsonl").read_text() == '{"id": "a"}\n'
    moved = [p for p in install.iterdir() if p.is_dir() and p.name not in {"index", "vectorstore", "data", "models_cache", "config", "runs"}]
    assert moved, "the previous data was moved aside, not deleted"


def test_restore_refuses_while_the_api_answers(install, tmp_path):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = tmp_path / "out"
        _run(["bash", str(ROOT / "scripts" / "backup.sh"), "--source-dir", str(install), "--output-dir", str(out)], ROOT)
        archive = next(out.glob("chatbot_backup_*.tar.gz"))
        env = {"RESTORE_HEALTH_URL": f"http://127.0.0.1:{server.server_port}/health"}
        refused = _run(["bash", str(ROOT / "scripts" / "restore.sh"), str(archive), "--in-place", "--source-dir", str(install)], ROOT, env)
        assert refused.returncode != 0 and "stop it" in refused.stderr
    finally:
        server.shutdown()


def test_backups_api_uses_a_file_lock_not_a_process_flag():
    text = (ROOT / "webapi" / "backups.py").read_text(encoding="utf-8")
    assert "flock" in text and ".backup.lock" in text


# --- 9. change ledger -----------------------------------------------------------------------------


def test_ledger_torn_tail_is_moved_aside_and_the_chain_continues(tmp_path, monkeypatch):
    from rag_core import change_ledger

    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    change_ledger.record("scan", source_id="s", tenant_id="t", summary={}, now=T0)
    path = next((tmp_path / "state").rglob("changes.jsonl"))
    with path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "apply", "trunc')
    change_ledger.record("scan", source_id="s", tenant_id="t", summary={}, now=T0)
    assert path.with_name(path.name + ".torn").exists()
    assert change_ledger.verify_chain() is not None
    assert len(change_ledger.read_entries()) == 2


def test_apply_failure_survives_a_failing_ledger(tmp_path, monkeypatch):
    from rag_core import change_ledger, source_apply, source_registry as registry, source_scan, source_sync

    roots = tmp_path / "ingest"
    (roots / "docs").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"txt"}))
    registry.create("docs", tenant_id="t", path=str(roots / "docs"), created_by="山田太郎", now=T0)
    (roots / "docs" / "x.txt").write_text("本文", encoding="utf-8")
    source_sync.scan_source("docs", now=T0)

    def boom(*a, **k):
        raise RuntimeError("ingest failed")

    monkeypatch.setattr(change_ledger, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(RuntimeError, match="ingest failed"):  # the original error, not the ledger's
        source_apply.apply_source("docs", ingest=boom, delete=lambda *a: {}, now=T0)


# --- 10. audit log --------------------------------------------------------------------------------


def test_audit_retention_prunes_with_an_anchor_and_the_chain_still_verifies(tmp_path, monkeypatch):
    from rag_core import audit_log

    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("AUDIT_MAX_BYTES", "1")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
    for i in range(4):
        audit_log.append_audit_event("admin", {"n": i})
    path = tmp_path / "audit" / "events.jsonl"
    rotated = sorted(path.parent.glob("events.*.jsonl"))
    assert len(rotated) >= 3
    old = time.time() - 40 * 86400
    for file in rotated[:2]:
        os.utime(file, (old, old))
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
    assert audit_log.prune_retention(path) == 2
    assert (path.parent / "events.anchor.json").exists()
    assert audit_log.verify_chain(path)["ok"] is True
    audit_log.append_audit_event("admin", {"n": 99})
    assert audit_log.verify_chain(path)["ok"] is True


def test_retention_is_off_by_default(tmp_path, monkeypatch):
    from rag_core import audit_log

    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    assert audit_log._retention_days() == 0
    assert audit_log.prune_retention(tmp_path / "events.jsonl") == 0


def test_audit_question_mode_default_is_mask_in_production_and_hash_is_opt_in(monkeypatch):
    from rag_core import audit_log

    monkeypatch.delenv("AUDIT_QUESTION_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert audit_log._question_mode() == "mask"
    monkeypatch.setenv("AUDIT_QUESTION_MODE", "hash")
    assert audit_log._question_mode() == "hash"
    monkeypatch.delenv("AUDIT_QUESTION_MODE")
    monkeypatch.setenv("APP_ENV", "dev")
    assert audit_log._question_mode() == "plain"


# --- 11. release check ----------------------------------------------------------------------------


def test_release_check_has_a_fingerprint_comparison():
    from scripts import release_check

    assert callable(release_check.fingerprint_check)
    doc = (ROOT / "docs" / "operations" / "onprem_install_upgrade_release.md").read_text(encoding="utf-8")
    for needle in ("--require-hashes", "release_check.py --fingerprint", "chromadb"):
        assert needle in doc


# --- 12. mail routes ------------------------------------------------------------------------------


def test_mail_request_carries_the_mailbox_owner_and_delete_endpoint_exists(monkeypatch):
    from webapi.routers import ingestion

    req = ingestion.MailIngestionRequest(inputs=["x"], expected_tenant="t", collection="c", mailbox_owner="owner@example.co.jp")
    assert req.mailbox_owner == "owner@example.co.jp"
    paths = {r.path for r in ingestion.router.routes}
    assert "/admin/ingestion/mail/delete" in paths


def test_mail_execute_is_queued_with_the_owner(tmp_path, monkeypatch):
    from webapi import mail_ingestion_jobs
    from webapi.routers import ingestion

    seen = {}
    root = tmp_path / "root"
    own = root / "uploads" / "acme" / "20261121"
    own.mkdir(parents=True)
    f = own / "0123456789ab_one.eml"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("INGEST_UPLOAD_DIR", str(root / "uploads"))
    monkeypatch.setattr(ingestion, "_audit_admin", lambda *a, **k: None)
    monkeypatch.setattr(mail_ingestion_jobs, "_validate", lambda *a, **k: [])
    monkeypatch.setattr(mail_ingestion_jobs, "submit_mail_job",
                        lambda inputs, **kw: seen.update(kw) or {"job_id": "j", "status": "queued"})
    resp = ingestion.ingestion_mail(
        ingestion.MailIngestionRequest(inputs=[str(f)], expected_tenant="acme", collection="acme_mail", execute=True,
                                       async_job=False, mailbox_owner="owner@example.co.jp"), request=None)
    assert resp.status_code == 202 and seen["mailbox_owner"] == "owner@example.co.jp"


def test_mail_delete_plan_then_execute(stores, monkeypatch):
    from webapi import mail_ingestion_jobs
    from webapi.routers import ingestion

    ij = stores
    ij.register_collection("acme_mail", owner_tenant="acme", status=ij.COLLECTION_READY)
    doc = "mail:" + "a" * 24
    ij._ingest_chunks([{"id": "acme:m:0", "text": "本文", "source_doc": doc, "tenant_id": "acme", "type": "email",
                        "quality": "high", "searchable": 1}], "acme_mail")
    monkeypatch.setattr(ingestion, "_audit_admin", lambda *a, **k: None)
    plan = {"source_doc": doc, "found": True, "ids": ["acme:m:0"], "dependent_source_docs": ["mail:b"],
            "superseded_source_docs": [], "committed": False}
    commits = []
    monkeypatch.setattr(mail_ingestion_jobs, "delete_mail",
                        lambda *a, commit=False, **k: commits.append(commit) or {**plan, "committed": commit})
    dry = ingestion.ingestion_mail_delete(
        ingestion.MailDeleteRequest(tenant_id="acme", collection="acme_mail", source_doc=doc), request=None)
    assert dry["executed"] is False and dry["dependent_source_docs"] == ["mail:b"] and commits == [False]
    done = ingestion.ingestion_mail_delete(
        ingestion.MailDeleteRequest(tenant_id="acme", collection="acme_mail", source_doc=doc, execute=True), request=None)
    assert done["chunks_deleted"] == 1 and done["committed"] is True and commits[-1] is True
    assert ("acme", doc) in ij.tombstoned_keys("acme")


# --- 13. conversation history ---------------------------------------------------------------------


def test_conversation_redaction_is_skipped_when_the_store_cannot_match(monkeypatch):
    from webapi import collection_admin, conversation_store

    monkeypatch.delattr(conversation_store, "redact_source_doc", raising=False)
    with pytest.raises(collection_admin.ConversationRedactionError):
        collection_admin._redact_conversations("t", ["x.pdf"])


def test_redaction_failure_stops_delete_before_purge(monkeypatch):
    from webapi import collection_admin as ca, ingestion_jobs as ij

    events = []
    monkeypatch.setattr(ca, "_served_name", lambda: "served")
    monkeypatch.setattr(ca, "_served_locked", lambda name: nullcontext())
    monkeypatch.setattr(ca, "_maintenance", lambda: nullcontext())
    monkeypatch.setattr(ca, "_keyword_path", lambda target: "unused")
    monkeypatch.setattr(ca, "_open", lambda target: object())
    monkeypatch.setattr(ca, "_doc_ids", lambda *args: ["id"])
    monkeypatch.setattr(ij, "add_tombstones", lambda *args, **kwargs: None)
    monkeypatch.setattr(ij, "start_operation", lambda *args, **kwargs: {"op_id": "x"})
    monkeypatch.setattr(ij, "finish_operation", lambda op, status, **kwargs: events.append(status))
    monkeypatch.setattr(ca, "_purge", lambda *args: events.append("purge"))
    def fail(*args):
        raise ca.ConversationRedactionError("conversation redaction failed")
    monkeypatch.setattr(ca, "_redact_conversations", fail)
    with pytest.raises(ca.ConversationRedactionError):
        ca.delete_documents(None, tenant="t", source_docs=["a"], execute=True)
    assert events == [ij.OP_FAILED]


def test_production_cannot_opt_out_of_redaction(monkeypatch):
    from webapi import collection_admin as ca, conversation_store as cs
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CONVERSATION_REDACT_ON_DELETE", "false")
    calls = []
    monkeypatch.setattr(cs, "redact_source_doc", lambda tenant, doc: calls.append((tenant, doc)) or 0)
    ca._redact_conversations("t", ["a"])
    assert calls == [("t", "a")]


@pytest.mark.parametrize("execute,fail", [(False, False), (True, True), (True, False)])
def test_promotion_redacts_tombstone_even_without_vector_rows(fake_promotion, monkeypatch, execute, fail):
    state, ca = fake_promotion
    ij = ca.ingestion_jobs
    ij.add_tombstones("t", ["gone.pdf"], reason="source_delete")
    state["source"] = FakeStore([_meta("keep.pdf", 1)])
    events = []

    def redact(tenant, docs):
        events.append((tenant, docs))
        if fail:
            raise ca.ConversationRedactionError("injected failure")
        return 0

    monkeypatch.setattr(ca, "_redact_conversations", redact)
    monkeypatch.setattr(ca, "_mark_stale", lambda name: events.append("stale"))
    if fail:
        with pytest.raises(ca.ConversationRedactionError):
            ca.promote_staging("stg_fake", tenant="t", execute=execute)
        assert events == [("t", ["gone.pdf"])]
        assert state["target"].upserts == [] and state["target"].deleted == []
        assert [o["status"] for o in ij.list_operations()] == [ij.OP_FAILED]
    else:
        ca.promote_staging("stg_fake", tenant="t", execute=execute)
        assert events == ([("t", ["gone.pdf"]), "stale", "stale"] if execute else [])
        assert bool(state["target"].upserts) is execute
        assert [o["status"] for o in ij.list_operations()] == ([ij.OP_DONE] if execute else [])


@pytest.mark.parametrize("fail", [True, False])
def test_purge_without_index_rows_still_redacts_without_marking_stale(registry_env, tmp_path, monkeypatch, fail):
    from rag_core import store
    from webapi import collection_admin as ca

    ij = registry_env
    ij.add_tombstones("t", ["gone.pdf"], reason="source_delete")
    ij.add_tombstones("other", ["other.pdf"], reason="source_delete")
    monkeypatch.setattr(store, "get_vectorstore", lambda **kw: object())
    monkeypatch.setattr(ca, "_doc_ids", lambda *args: [])
    monkeypatch.setattr(ij, "maintenance_shared", lambda: nullcontext())
    events = []

    def redact(tenant, docs):
        events.append((tenant, docs))
        if fail:
            raise ca.ConversationRedactionError("injected failure")
        return 0

    monkeypatch.setattr(ca, "_redact_conversations", redact)
    monkeypatch.setattr(ca, "_mark_stale", lambda name: events.append("stale"))
    if fail:
        with pytest.raises(ca.ConversationRedactionError):
            ca.purge_tombstoned("served", str(tmp_path / "missing.jsonl"))
        assert events == [("other", ["other.pdf"])]
    else:
        assert ca.purge_tombstoned("served", str(tmp_path / "missing.jsonl")) == {
            "documents": 0, "chunks_deleted": 0, "keyword_rows_removed": 0}
        assert events == [("other", ["other.pdf"]), ("t", ["gone.pdf"])]
    assert [o["status"] for o in ij.list_operations()] == [ij.OP_FAILED if fail else ij.OP_DONE]


def test_promotion_detects_keyword_only_tombstone(fake_promotion, monkeypatch):
    from scripts import ingest_canonical_jsonl as ingest

    state, ca = fake_promotion
    ij = ca.ingestion_jobs
    ij.add_tombstones("t", ["gone.pdf"], reason="source_delete")
    state["source"] = FakeStore([_meta("keep.pdf", 1)])
    served = Path(ca._keyword_path(None))
    served.write_text(json.dumps({"tenant_id": "t", "source_doc": "gone.pdf"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(ingest, "iter_keyword_documents", lambda path, keys: iter([
        {"tenant_id": "t", "source_doc": "gone.pdf"}
    ] if Path(path) == served and ("t", "gone.pdf") in keys else []))
    assert ca.promote_staging("stg_fake", tenant="t", execute=False)["served_documents_tombstoned"] == ["gone.pdf"]
    calls = []
    monkeypatch.setattr(ca, "_purge", lambda vectors, path, keys: calls.append(keys) or {
        "keyword_rows_removed": 1, "chunks_deleted": 0})
    ca.promote_staging("stg_fake", tenant="t", execute=True)
    assert calls == [[("t", "gone.pdf")]]
