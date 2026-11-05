"""Stage 10: connections, OIDC, admin session security, metrics, extraction wiring, UI.

Synthetic data only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config

ROOT = Path(__file__).resolve().parents[1]
_ADMIN_TOKEN = "stage10-admin-token"


@pytest.fixture
def admin_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", _ADMIN_TOKEN)
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    for name in ("ADMIN_AUTH_ALLOW_INSECURE_DEV", "SESSION_REVOCATION_BACKEND", "API_AUTH_ENABLED", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    from webapi import main as main_mod

    main_mod._ADMIN_LOGIN_LIMITER.reset()
    return main_mod


def _login(main_mod) -> TestClient:
    client = TestClient(main_mod.app)
    assert client.post("/admin/login", json={"token": _ADMIN_TOKEN}).status_code == 200
    return client


# --- admin browser session: CSRF, revocation, redirect, role -------------------------------


def test_cookie_post_requires_csrf_header_and_same_origin(admin_env):
    client = _login(admin_env)
    body = {"review_id": "x", "action_type": "approve_candidate"}
    refused = client.post("/admin/review/action", json=body)
    assert refused.status_code == 403 and "csrf" in refused.text
    csrf = client.cookies.get("admin_csrf")
    ok = client.post("/admin/review/action", json=body, headers={"X-CSRF-Token": csrf})
    assert ok.status_code not in (401, 403)
    cross = client.post(
        "/admin/review/action", json=body, headers={"X-CSRF-Token": csrf, "Origin": "https://other.example.co.jp"}
    )
    assert cross.status_code == 403
    # Header-token clients (scripts) are not affected by CSRF.
    plain = TestClient(admin_env.app)
    assert plain.post("/admin/review/action", json=body, headers={"X-Admin-Token": _ADMIN_TOKEN}).status_code not in (401, 403)


def test_logout_revokes_the_session_on_the_server(admin_env):
    client = _login(admin_env)
    stolen = client.cookies.get("admin_session")
    assert client.post("/admin/logout").status_code == 200
    replay = TestClient(admin_env.app)
    assert replay.get("/admin/ingestion", headers={"Cookie": f"admin_session={stolen}"}).status_code == 401


def test_logout_all_ends_every_admin_session(admin_env):
    first, second = _login(admin_env), _login(admin_env)
    csrf = first.cookies.get("admin_csrf")
    assert first.post("/admin/logout-all", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert second.get("/admin/ingestion").status_code == 401


def test_admin_page_redirects_browsers_to_login(admin_env):
    client = TestClient(admin_env.app)
    page = client.get("/admin/ingestion", headers={"Accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/admin/login?next=/admin/ingestion"
    assert client.get("/admin/ingestion", headers={"Accept": "application/json"}).status_code == 401


def test_role_is_user_when_admin_auth_is_off(monkeypatch):
    from webapi import branding

    monkeypatch.delenv("ADMIN_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_ALLOW_INSECURE_DEV", raising=False)
    assert branding.resolve_role({}) == "user"
    monkeypatch.setenv("ADMIN_AUTH_ALLOW_INSECURE_DEV", "true")
    assert branding.resolve_role({}) == "admin"


def test_admin_pages_share_the_header(admin_env):
    body = admin_env.admin_review_page().body.decode("utf-8")
    assert "kd-admin-nav" in body and "adminFetch" in body and "Review Queue" in body
    home = _login(admin_env).get("/admin")
    assert home.status_code == 200 and "/admin/metrics/summary" in home.text


# --- uploads -----------------------------------------------------------------------------------


def test_upload_saves_inside_ingest_root_and_checks_content(admin_env, monkeypatch, tmp_path):
    root = tmp_path / "ingest"
    root.mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    client = TestClient(admin_env.app)
    auth = {"X-Admin-Token": _ADMIN_TOKEN, "Content-Type": "application/octet-stream"}
    ok = client.put("/admin/ingestion/upload?filename=規程.pdf&tenant=tenant_a", content=b"%PDF-1.7 synthetic", headers=auth)
    assert ok.status_code == 200
    saved = Path(ok.json()["path"])
    assert saved.is_file() and saved.resolve().is_relative_to(root.resolve()) and "tenant_a" in saved.parts
    assert client.put("/admin/ingestion/upload?filename=x.pdf", content=b"not a pdf", headers=auth).status_code == 400
    assert client.put("/admin/ingestion/upload?filename=x.exe", content=b"MZ", headers=auth).status_code == 400
    monkeypatch.setenv("INGEST_UPLOAD_MAX_BYTES", "10")
    assert client.put("/admin/ingestion/upload?filename=y.pdf", content=b"%PDF" + b"0" * 100, headers=auth).status_code == 413


# --- readiness and shared services -------------------------------------------------------------


class _DownRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis down")

        return fail

    def pipeline(self):
        return self


def test_rate_limit_falls_back_to_local_window_when_redis_is_down(monkeypatch):
    from webapi import rate_limit, state_backend

    monkeypatch.setattr(state_backend, "redis_client", lambda: _DownRedis())
    rate_limit.reset_limiter()
    limiter = rate_limit.RedisRateLimiter()
    assert limiter.check("bucket-a", 1) == (True, 0.0)
    assert limiter.check("bucket-a", 1)[0] is False  # the local window still limits


def test_answer_cache_is_skipped_when_redis_is_down(monkeypatch):
    from rag_core import answer_cache
    from webapi import state_backend

    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True, raising=False)
    monkeypatch.setenv("ANSWER_CACHE_BACKEND", "redis")
    monkeypatch.setattr(state_backend, "redis_client", lambda: _DownRedis())
    assert answer_cache.get(("k",)) is None
    answer_cache.put(("k",), {"answer_text": "x"})  # no exception


def test_session_revocation_fails_closed_by_default(monkeypatch):
    from webapi import state_backend

    monkeypatch.setenv("SESSION_REVOCATION_BACKEND", "redis")
    monkeypatch.setattr(state_backend, "redis_client", lambda: _DownRedis())
    monkeypatch.delenv("SESSION_REVOCATION_FAIL_CLOSED", raising=False)
    assert state_backend.is_session_revoked("sid-1", "fp", 1.0) is True
    monkeypatch.setenv("SESSION_REVOCATION_FAIL_CLOSED", "false")
    assert state_backend.is_session_revoked("sid-1", "fp", 1.0) is False


def test_ready_reports_unreachable_services(admin_env, monkeypatch):
    from rag_core import vector_backend
    from webapi import state_backend

    monkeypatch.setattr(state_backend, "health", lambda: {"redis": {"ok": False, "error": "ConnectionError"}})
    monkeypatch.setattr(vector_backend, "health", lambda: {})
    resp = TestClient(admin_env.app).get("/ready")
    assert resp.status_code == 503 and resp.json() == {"status": "not_ready", "failing": ["redis"]}
    monkeypatch.setattr(state_backend, "health", lambda: {"redis": {"ok": True, "ms": 1.0}})
    assert TestClient(admin_env.app).get("/ready").json() == {"status": "ready"}


def test_state_backend_unavailable_maps_to_503(admin_env, monkeypatch):
    from webapi import state_backend

    def down(*args, **kwargs):
        raise state_backend.StateBackendUnavailable("postgres is unavailable")

    monkeypatch.setattr(admin_env.ingestion_jobs, "list_jobs", down)
    resp = TestClient(admin_env.app).get("/admin/ingestion/jobs", headers={"X-Admin-Token": _ADMIN_TOKEN})
    assert resp.status_code == 503 and "postgres" not in resp.text


def test_qdrant_calls_retry_and_existence_is_cached(monkeypatch):
    from rag_core import vector_backend

    monkeypatch.setattr(vector_backend.time, "sleep", lambda s: None)
    monkeypatch.setenv("QDRANT_RETRIES", "2")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("reset")
        return "ok"

    assert vector_backend.with_retry(flaky) == "ok" and calls["n"] == 3
    with pytest.raises(ValueError):
        vector_backend.with_retry(lambda: (_ for _ in ()).throw(ValueError("bad request")))

    class FakeClient:
        exists_calls = 0

        def collection_exists(self, name):
            FakeClient.exists_calls += 1
            return True

    vector_backend._EXISTS.clear()
    collection = vector_backend.QdrantCollection("stage10_c", client=FakeClient())
    assert collection.exists() and collection.exists()
    assert FakeClient.exists_calls == 1


# --- OIDC -----------------------------------------------------------------------------------------

_OIDC_SECRET = "s" * 40


def test_legacy_session_without_sid_is_revoked_by_logout(monkeypatch, tmp_path):
    from webapi import oidc_auth

    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("SESSION_REVOCATION_BACKEND", raising=False)
    monkeypatch.setenv("ENTERPRISE_OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_SESSION_SECRET", _OIDC_SECRET)
    session = oidc_auth._sign({"fp": "abc123", "authz": False}, _OIDC_SECRET, 3600)
    headers = {"cookie": f"{oidc_auth.SESSION_COOKIE}={session}"}
    assert oidc_auth.resolve_oidc_session(headers) is not None
    assert oidc_auth.revoke_session_from_headers(headers) is True
    assert oidc_auth.resolve_oidc_session(headers) is None


def test_oidc_algorithm_allowlist_and_end_session(monkeypatch):
    from webapi import oidc_auth

    assert "HS256" not in oidc_auth._ASYMMETRIC_ALGS and "NONE" not in oidc_auth._ASYMMETRIC_ALGS
    monkeypatch.setenv("OIDC_ID_TOKEN_ALGS", "HS256,none")
    assert oidc_auth._id_token_jwt() is not None  # falls back to RS256 only
    monkeypatch.setenv("OIDC_END_SESSION_ENDPOINT", "https://idp.example.co.jp/logout")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-1")
    monkeypatch.setenv("OIDC_POST_LOGOUT_REDIRECT_URI", "https://chat.example.co.jp/chat-ui")
    url = oidc_auth.end_session_info()["end_session_url"]
    assert url.startswith("https://idp.example.co.jp/logout?") and "client_id=client-1" in url
    monkeypatch.setenv("OIDC_CA_BUNDLE", "/etc/ssl/private-ca.pem")
    assert oidc_auth.http_verify() == "/etc/ssl/private-ca.pem"


def test_discovery_uses_last_good_copy_when_idp_is_unreachable():
    from webapi import oidc_hardening

    doc = {
        "issuer": "https://idp.example.co.jp",
        "authorization_endpoint": "https://idp.example.co.jp/a",
        "token_endpoint": "https://idp.example.co.jp/t",
        "jwks_uri": "https://idp.example.co.jp/k",
    }
    clock = {"t": 1000.0}
    oidc_hardening._DISCOVERY_CACHE.clear()
    first = oidc_hardening.discover("https://idp.example.co.jp", fetch=lambda url: doc, now=lambda: clock["t"])
    clock["t"] += 10_000

    def down(url):
        raise ConnectionError("idp down")

    assert oidc_hardening.discover("https://idp.example.co.jp", fetch=down, now=lambda: clock["t"]) == first
    oidc_hardening._DISCOVERY_CACHE.clear()


# --- keyword index cache signature ------------------------------------------------------------------


def test_tampered_keyword_cache_is_rebuilt(tmp_path, monkeypatch):
    from rag_core import retrieval

    corpus = tmp_path / "chunks.jsonl"
    corpus.write_text(json.dumps({"id": "c1", "text": "備品の貸出"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index", raising=False)
    monkeypatch.setenv("KEYWORD_INDEX_PERSIST", "true")
    retrieval._build_keyword_index(str(corpus), "heuristic", "rank_bm25", 1.5, 0.75)
    cache = next((tmp_path / "index" / ".keyword_cache").glob("*.pkl"))
    blob = bytearray(cache.read_bytes())
    blob[-1] ^= 0xFF
    cache.write_bytes(bytes(blob))
    rebuilt = {"n": 0}
    original = retrieval._build_keyword_index_fresh

    def counting(*args, **kwargs):
        rebuilt["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(retrieval, "_build_keyword_index_fresh", counting)
    index = retrieval._build_keyword_index(str(corpus), "heuristic", "rank_bm25", 1.5, 0.75)
    assert rebuilt["n"] == 1 and index.metas[0]["id"] == "c1"


# --- metrics over several workers ----------------------------------------------------------------------


def test_metrics_are_summed_over_worker_files(monkeypatch, tmp_path):
    from webapi import metrics_registry

    monkeypatch.setenv("METRICS_MULTIPROC_DIR", str(tmp_path))
    (tmp_path / "999-1.json").write_text(json.dumps({"pid": 999, "counters": {"stage10_total": {"total": 2}}}), encoding="utf-8")
    metrics_registry.reset()
    metrics_registry.increment("stage10_total")
    assert metrics_registry.scope() == "all_workers"
    assert metrics_registry.aggregate_snapshot()["stage10_total"] == {"total": 3}
    assert metrics_registry.snapshot()["stage10_total"] == {"total": 1}
    metrics_registry.reset()
    monkeypatch.delenv("METRICS_MULTIPROC_DIR")
    assert metrics_registry.scope() == "this_process"


# --- audit chain with another writer ---------------------------------------------------------------------


def test_audit_chain_stays_valid_when_another_process_appends(tmp_path):
    from rag_core import audit_log

    path = tmp_path / "audit.jsonl"
    audit_log._write(path, {"event": "a"})
    prev = audit_log._tail_hash(path)
    foreign = {"event": "other_worker", "prev_hash": prev}
    foreign["hash"] = audit_log._record_hash(prev, foreign)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(foreign) + "\n")
    audit_log._write(path, {"event": "c"})
    result = audit_log.verify_chain(path)
    assert result["ok"] and result["checked"] == 3


# --- citation excerpt -------------------------------------------------------------------------------------


def test_chat_source_returns_only_same_tenant_text(monkeypatch):
    from rag_core import retrieval
    from webapi import main as main_mod

    for name in ("API_AUTH_ENABLED", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    rows = {
        "c1": {"id": "c1", "tenant_id": "default", "text": "本文の抜粋", "source_doc": "規程.pdf", "section_path": ["第1条"]},
        "c2": {"id": "c2", "tenant_id": "tenant_b", "text": "他テナント", "source_doc": "b.pdf"},
    }
    monkeypatch.setattr(retrieval, "chunk_row", lambda chunk_id: rows.get(chunk_id))
    client = TestClient(main_mod.app)
    ok = client.get("/chat/source?chunk_id=c1")
    assert ok.status_code == 200 and ok.json()["text"] == "本文の抜粋" and ok.json()["section_path"] == ["第1条"]
    assert client.get("/chat/source?chunk_id=c2").status_code == 404
    assert client.get("/chat/source?chunk_id=missing").status_code == 404


# --- extraction wiring ----------------------------------------------------------------------------------------


def test_office_ocr_drops_low_confidence_text(monkeypatch):
    from rag_core import extraction
    from rag_core.document_converters import base

    dx = extraction.load()

    class Fake:
        def __init__(self, confidence):
            self.confidence = confidence

        def ocr_image(self, data, name="", tesseract_lang=None):
            return dx.OcrResult(text="画像の文字", engine="fake", confidence=self.confidence)

    monkeypatch.setattr(extraction, "load", lambda: Fake(30.0))
    assert base.ocr_image_bytes(b"x", "image1.png") == ""
    monkeypatch.setattr(extraction, "load", lambda: Fake(90.0))
    assert base.ocr_image_bytes(b"x", "image1.emf") == "画像の文字"


def test_pdf_tables_switch_and_backend_choice(monkeypatch):
    import scripts.pdf_to_canonical_jsonl as pdf

    monkeypatch.setenv("PDF_TABLES", "false")
    assert pdf._table_backend() is None
    monkeypatch.setenv("PDF_TABLES", "true")
    monkeypatch.setenv("DOCEXTRACT_TABLE_BACKEND", "none")
    assert pdf._table_backend() is None
    assert pdf._tesseract_lang("jpn") is None and pdf._tesseract_lang("jpn_vert") == "jpn_vert"


# --- UI ------------------------------------------------------------------------------------------------------------


def test_pdf_conversion_uses_no_pymupdf(tmp_path):
    pytest.importorskip("pdfplumber")
    import scripts.pdf_to_canonical_jsonl as pdf
    from rag_core import extraction

    extraction.load()
    from docextract.testing import simple_pdf

    source = (ROOT / "scripts/pdf_to_canonical_jsonl.py").read_text(encoding="utf-8")
    assert "import fitz" not in source
    path = tmp_path / "manual.pdf"
    path.write_bytes(simple_pdf([{"texts": [(72, 72, 12, "Backups are verified every morning by the operations team.")]}]))
    rows, report = pdf.convert_pdf_to_rows(path)
    assert report["pdf_engine"] == "pdfplumber+pypdfium2" and report["pages_text"] == 1
    assert any("Backups are verified" in r["text"] for r in rows)


def test_chat_ui_product_features():
    html = (ROOT / "webapi/static/chat.html").read_text(encoding="utf-8")
    for needle in ("/chat/source", "AbortController", "isComposing", "/chat/threads", "承認済み回答", "prefers-color-scheme", "/static/js/render.js"):
        assert needle in html, needle
    assert 'class="cite"' in (ROOT / "webapi/static/js/render.js").read_text(encoding="utf-8")
    ingestion = (ROOT / "webapi/static/ingestion.html").read_text(encoding="utf-8")
    assert "/admin/ingestion/upload" in ingestion and "<!--admin-nav-->" in ingestion
