"""Stage 9: admin browser login, UI wiring, monitoring, extraction, persistence.

Synthetic data only.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


# --- admin session cookie ---------------------------------------------------------


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "stage9-admin-token")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.delenv("ADMIN_AUTH_ALLOW_INSECURE_DEV", raising=False)


def test_admin_session_cookie_accepted_expired_and_tampered_rejected(admin_env, monkeypatch, tmp_path):
    import config
    from webapi import admin_auth

    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))  # session revocation store

    cookie, ttl = admin_auth.issue_admin_session(now=1000)
    assert ttl == 28800
    expected = "stage9-admin-token"
    assert admin_auth._valid_admin_session(cookie, expected, now=1000 + ttl - 1)
    assert not admin_auth._valid_admin_session(cookie, expected, now=1000 + ttl + 1)
    # Stage 10 cookie: sid.issued_at.expires_at.signature
    sid, issued, expires, sig = cookie.split(".")
    assert not admin_auth._valid_admin_session(f"{sid}.{issued}.{int(expires) + 100}.{sig}", expected, now=1000)
    # Rotating the token ends existing sessions.
    assert not admin_auth._valid_admin_session(cookie, "rotated-token", now=1000)

    live, _ = admin_auth.issue_admin_session()
    admin_auth.enforce_admin_token({"cookie": f"other=1; {admin_auth.ADMIN_SESSION_COOKIE}={live}"})
    with pytest.raises(HTTPException) as exc:
        admin_auth.enforce_admin_token({"cookie": "other=1"})
    assert exc.value.status_code == 401


def test_admin_login_route_sets_cookie_and_opens_admin_pages(admin_env):
    from fastapi.testclient import TestClient
    from webapi import main as main_mod
    from webapi.admin_auth import ADMIN_SESSION_COOKIE

    main_mod._ADMIN_LOGIN_LIMITER.reset()
    client = TestClient(main_mod.app)
    assert client.get("/admin/ingestion").status_code == 401
    assert client.post("/admin/login", json={"token": "wrong"}).status_code == 403
    resp = client.post("/admin/login", json={"token": "stage9-admin-token"})
    assert resp.status_code == 200
    assert ADMIN_SESSION_COOKIE in resp.cookies
    set_cookie = resp.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    assert "stage9-admin-token" not in resp.text
    assert client.get("/admin/ingestion").status_code == 200
    client.post("/admin/logout")
    client.cookies.clear()
    assert client.get("/admin/ingestion").status_code == 401


def test_admin_login_is_rate_limited(admin_env, monkeypatch):
    from fastapi.testclient import TestClient
    from webapi import main as main_mod

    monkeypatch.setenv("ADMIN_LOGIN_ATTEMPTS_PER_MINUTE", "2")
    main_mod._ADMIN_LOGIN_LIMITER.reset()
    client = TestClient(main_mod.app)
    codes = [client.post("/admin/login", json={"token": "wrong"}).status_code for _ in range(3)]
    assert codes == [403, 403, 429]
    main_mod._ADMIN_LOGIN_LIMITER.reset()


# --- UI wiring --------------------------------------------------------------------------


def test_chat_ui_sends_thread_id_and_records_turns():
    html = (ROOT / "webapi/static/chat.html").read_text(encoding="utf-8")
    assert "thread_id: threadId" in html
    assert '"/chat/threads"' in html
    assert "threadId = uuid();" in html  # reset by newConversation


def test_ingestion_ui_uses_cookie_and_polls_async_jobs():
    html = (ROOT / "webapi/static/ingestion.html").read_text(encoding="utf-8")
    assert "X-Admin-Token" not in html
    assert "async_job" in html and "pollJob" in html
    assert "/admin/ingestion/jobs/" in html
    login = (ROOT / "webapi/static/admin_login.html").read_text(encoding="utf-8")
    assert '"/admin/login"' in login and "localStorage" not in login


# --- monitoring ---------------------------------------------------------------------------


def test_audit_write_failures_signal_is_critical_only_when_present():
    from webapi.alerting import evaluate_alerts

    base = {"counters": {"chat_answer_mode_total": {"grounded": 30}}}
    assert "audit_write_failures" not in {s["name"] for s in evaluate_alerts(base)["signals"]}
    failing = {"counters": {**base["counters"], "audit_write_failures_total": {"total": 2}}}
    res = evaluate_alerts(failing)
    assert res["overall"] == "CRITICAL"
    assert {"name": "audit_write_failures", "value": 2}.items() <= next(
        s for s in res["signals"] if s["name"] == "audit_write_failures"
    ).items()


def test_alert_rules_and_runner_cover_audit_and_metrics_token():
    rules = (ROOT / "deploy/observability/alert_rules.yml").read_text(encoding="utf-8")
    assert "KuradenAuditWriteFailures" in rules
    runner = (ROOT / "scripts/monitoring_runner.sh").read_text(encoding="utf-8")
    assert "KURADEN_METRICS_TOKEN" in runner and "curl -K -" in runner
    assert "verify_audit_chain.py" in runner and "check_freshness.py" in runner


# --- chunking: merged article heading ------------------------------------------------------


def test_article_title_and_body_on_one_line_are_split():
    from rag_core.chunking_ja import CHUNKER_VERSION, build_ja_chunk_records

    assert CHUNKER_VERSION == "ja-chunker-v4"
    text = "第1条（目的）この規程は、備品の貸出手続を定める。\n第2条（定義）備品とは、会社が貸し出す物品をいう。"
    rows = build_ja_chunk_records(doc_id="rule.pdf", source_doc="rule.pdf", text=text, doc_type="policy_spec", title="規程")
    paths = [" ".join(r.get("section_path") or []) for r in rows]
    # NFKC turns "（目的）" into "(目的)"; the body is no longer in the heading.
    assert any("第1条" in p and "(目的)" in p and "この規程は" not in p for p in paths)
    assert any("この規程は、備品の貸出手続を定める。" in r["text"] for r in rows)


def test_short_article_heading_is_unchanged():
    from rag_core.chunking_ja import _expand_heading_lines

    assert _expand_heading_lines(["第1条 目的"]) == ["第1条 目的"]
    assert _expand_heading_lines(["第3条 本規程は全社員に適用する。"]) == ["第3条", "本規程は全社員に適用する。"]


# --- DOCX nested table ----------------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _write_docx(path: Path, body: str) -> None:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def test_docx_nested_table_text_is_kept(tmp_path):
    from rag_core.document_converters.docx_converter import convert_docx

    nested = f"<w:tbl><w:tr><w:tc>{_p('内側A')}</w:tc><w:tc>{_p('内側B')}</w:tc></w:tr></w:tbl>"
    table = (
        "<w:tbl>"
        f"<w:tr><w:tc>{_p('項目')}</w:tc><w:tc>{_p('内容')}</w:tc></w:tr>"
        f"<w:tr><w:tc>{_p('備品')}</w:tc><w:tc>{_p('外側')}{nested}</w:tc></w:tr>"
        "</w:tbl>"
    )
    path = tmp_path / "nested.docx"
    _write_docx(path, table)
    rows = convert_docx(path)
    blob = "\n".join(r["text"] for r in rows)
    assert "外側" in blob and "内側A" in blob and "内側B" in blob


def test_office_ocr_is_off_by_default(monkeypatch):
    from rag_core.document_converters import base

    monkeypatch.delenv("OFFICE_OCR_IMAGES", raising=False)
    assert base.office_ocr_enabled() is False
    assert base.ocr_image_bytes(b"not an image") == ""


# --- PDF tables (pdfplumber page) -------------------------------------------------------------


def test_pdf_page_tables_and_text_outside(tmp_path):
    # Stage 11: pages come from docextract.pdf (pdfplumber) instead of PyMuPDF.
    pytest.importorskip("pdfplumber")
    import scripts.pdf_to_canonical_jsonl as pdf
    from rag_core import extraction

    extraction.load()
    from docextract.pdf import open_pdf
    from docextract.testing import simple_pdf

    rows = [("Item", "Qty"), ("Desk", "3")]
    texts = [(72, 60, 11, "Body paragraph.")]
    lines = [(72, 140 + i * 24, 372, 140 + i * 24) for i in range(3)] + [(x, 140, x, 188) for x in (72, 222, 372)]
    texts += [(80, 146 + i * 24, 10, a) for i, (a, _) in enumerate(rows)] + [(230, 146 + i * 24, 10, b) for i, (_, b) in enumerate(rows)]
    path = tmp_path / "t.pdf"
    path.write_bytes(simple_pdf([{"texts": texts, "lines": lines}]))
    with open_pdf(path) as doc:
        page = doc.pages[0]
        found = pdf._page_tables(page, 1)
        assert len(found) == 1 and found[0]["rows"] == [["Item", "Qty"], ["Desk", "3"]]
        outside = pdf._text_outside(page, [found[0]["bbox"]])
        assert "Body paragraph." in outside and "Desk" not in outside


def test_pdf_page_without_find_tables_keeps_old_behaviour():
    import scripts.pdf_to_canonical_jsonl as pdf

    assert pdf._page_tables(object()) == []


# --- keyword index persistence ------------------------------------------------------------------


def test_keyword_index_is_reused_from_disk(tmp_path, monkeypatch):
    import config
    from rag_core import retrieval

    corpus = tmp_path / "chunks.jsonl"
    corpus.write_text(
        "\n".join(json.dumps({"id": f"c{i}", "text": f"備品の貸出手続 その{i}"}, ensure_ascii=False) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index", raising=False)
    monkeypatch.setenv("KEYWORD_INDEX_PERSIST", "true")
    first = retrieval._build_keyword_index(str(corpus), "heuristic", "rank_bm25", 1.5, 0.75)
    assert list((tmp_path / "index" / ".keyword_cache").glob("*.pkl"))

    def _no_rebuild(*args, **kwargs):
        raise AssertionError("should load the persisted index")

    monkeypatch.setattr(retrieval, "_build_keyword_index_fresh", _no_rebuild)
    second = retrieval._build_keyword_index(str(corpus), "heuristic", "rank_bm25", 1.5, 0.75)
    assert [m["id"] for m in second.metas] == [m["id"] for m in first.metas]


def test_keyword_index_not_persisted_by_default(tmp_path, monkeypatch):
    import config
    from rag_core import retrieval

    corpus = tmp_path / "chunks.jsonl"
    corpus.write_text(json.dumps({"id": "c1", "text": "備品"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "index", raising=False)
    monkeypatch.delenv("KEYWORD_INDEX_PERSIST", raising=False)
    retrieval._build_keyword_index(str(corpus), "heuristic", "rank_bm25", 1.5, 0.75)
    assert not (tmp_path / "index" / ".keyword_cache").exists()


# --- answer cache on Redis ------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.data = {"ac:1": "x", "ac:2": "y", "rl:other": "z"}

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [k for k in list(self.data) if k.startswith(prefix)]

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


def test_answer_cache_size_and_clear_on_redis(monkeypatch):
    from rag_core import answer_cache
    from webapi import state_backend

    fake = _FakeRedis()
    monkeypatch.setenv("ANSWER_CACHE_BACKEND", "redis")
    monkeypatch.setattr(state_backend, "redis_client", lambda: fake)
    assert answer_cache.size() == 2
    answer_cache.clear()
    assert answer_cache.size() == 0
    assert "rl:other" in fake.data


# --- production exact index -------------------------------------------------------------------------


def _approved(qa_id, question, answer, tenant="default", aliases=None):
    row = {
        "qa_id": qa_id,
        "question": question,
        "approved_answer": answer,
        "status": "approved",
        "tenant_id": tenant,
        "updated_at": "2026-01-01",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1]}],
    }
    if aliases:
        row["approved_aliases"] = aliases
    return json.dumps(row, ensure_ascii=False)


def test_production_exact_index_is_tenant_scoped(tmp_path):
    from tools.build_production_exact_index import main

    approved = tmp_path / "approved.jsonl"
    approved.write_text(
        "\n".join([
            _approved("q1", "営業時間は？", "9時から17時です。", aliases=["何時から開いていますか"]),
            _approved("q2", "営業時間は？", "10時から18時です。", tenant="tenant_b"),
        ]) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "exact_index.json"
    assert main(["--approved", str(approved), "--out", str(out)]) == 0
    index = json.loads(out.read_text(encoding="utf-8"))
    tenants = sorted(item["metadata"]["tenant_id"] for items in index.values() for item in items)
    assert tenants == ["default", "default", "tenant_b"]  # question + alias, other tenant's question


def test_production_exact_index_refuses_eval_sources(tmp_path):
    from tools.build_production_exact_index import main

    src = tmp_path / "fixed_qa_eval" / "cases.jsonl"
    src.parent.mkdir()
    src.write_text("", encoding="utf-8")
    assert main(["--approved", str(src), "--out", str(tmp_path / "x.json")]) == 1
