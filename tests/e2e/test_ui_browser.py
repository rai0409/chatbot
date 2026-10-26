"""Stage 13: browser tests of the chat and admin screens (Playwright,
headless Chromium) against the real app served by uvicorn.

Only the answer generation, the chunk store and the feedback log are
replaced by fakes, so no model, vector store or LLM is needed. Synthetic
data only.

Skipped when Playwright or its Chromium is not installed; with
KURADEN_E2E_REQUIRED=1 (CI) that is a failure instead.
  python -m pip install playwright && python -m playwright install chromium-headless-shell
  python -m pytest -q tests/e2e
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import config

REQUIRED = os.getenv("KURADEN_E2E_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_TOKEN = "e2e-test-admin-token-not-a-secret-0123456789"
ANSWER = "経費精算の締め日は毎月25日です[1]。"
CITATION = {"number": 1, "source_doc": "経費規程.pdf", "source_pages": [3], "chunk_id": "c-expense-1"}
CHUNK = {"tenant_id": "default", "text": "第3条 経費精算の締め日は毎月25日とする。", "source_doc": "経費規程.pdf",
         "section_path": ["第1章 総則", "第3条"]}
XSS = '<img src=x onerror="window.__xss=1"> <script>window.__xss=2</script> [1]'


def _unavailable(reason: str):
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


def _final(text: str):
    payload = {
        "answer_text": text, "answer_with_footnotes": text, "citations": [CITATION],
        "intent": "grounded", "guard_reason": None, "used_fallback": False,
    }
    result = SimpleNamespace(to_dict=lambda: dict(payload), intent="grounded", guard_reason=None,
                             used_fallback=False, citations=[CITATION])
    return ("final", (result, {"request_id": "e2e", "answer_mode": "grounded", "citations_count": 1}))


def _fake_answer_stream(question, **kwargs):
    if "時間のかかる" in question:
        for _ in range(50):
            time.sleep(0.1)
            yield ("delta", {"text": "途中まで"})
        yield _final("途中まで")
        return
    text = XSS if "表示確認" in question else ANSWER
    yield ("delta", {"text": text[:6]})
    yield ("delta", {"text": text[6:]})
    yield _final(text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    uvicorn = pytest.importorskip("uvicorn")
    from rag_core import retrieval
    from webapi import main

    tmp = tmp_path_factory.mktemp("e2e")
    ingest_root = tmp / "ingest"
    ingest_root.mkdir()
    feedback = []
    mp = pytest.MonkeyPatch()
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ADMIN_AUTH_ALLOW_INSECURE_DEV", "CONVERSATION_HISTORY_ENABLED",
                 "INGEST_UPLOAD_DIR"):
        mp.delenv(name, raising=False)
    mp.setenv("ADMIN_AUTH_ENABLED", "true")
    mp.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    mp.setenv("OIDC_COOKIE_SECURE", "false")
    mp.setenv("INGEST_ALLOWED_ROOTS", str(ingest_root))
    mp.setattr(config, "RUNS_DIR", str(tmp / "runs"))
    mp.setattr(main, "answer_query_stream", _fake_answer_stream)
    mp.setattr(main, "ensure_openai_client", lambda base_url=None: None)
    mp.setattr(main, "_approved_qa_lookup", lambda question, tenant_id="default": None)
    mp.setattr(main, "_approved_exact_chat_lookup", lambda question, tenant_id: None)
    mp.setattr(retrieval, "chunk_row", lambda chunk_id, collection_name=None: dict(CHUNK) if chunk_id == CITATION["chunk_id"] else None)
    mp.setattr(main, "append_feedback_audit_event", lambda event: feedback.append(event) or True)

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    if not srv.started:
        mp.undo()
        pytest.fail("uvicorn did not start")
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", feedback=feedback, ingest_root=ingest_root)
    srv.should_exit = True
    thread.join(timeout=10)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright is not installed")
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch()
    except Exception as exc:  # browser binary or system libraries missing
        pw.stop()
        _unavailable(f"chromium cannot start: {str(exc).splitlines()[0]}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser):
    context = browser.new_context(locale="ja-JP")
    p = context.new_page()
    p.set_default_timeout(10000)
    yield p
    context.close()


def _expect():
    from playwright.sync_api import expect

    return expect


def _ask(page, server, question):
    page.goto(server.url + "/chat-ui")
    page.fill("#q", question)
    page.click("#send")


# --- chat screen ------------------------------------------------------------------------------------


def test_answer_with_citation_and_excerpt(page, server):
    expect = _expect()
    _ask(page, server, "経費精算の締め日はいつですか？")
    card = page.locator(".turn .card").last
    expect(card.locator(".card-head")).to_contain_text("文書から回答")
    expect(card.locator(".a")).to_contain_text("毎月25日")
    expect(page.locator("#citations .src-card")).to_contain_text("経費規程.pdf")
    card.locator("button.cite").first.click()
    expect(page.locator("#citations .excerpt")).to_contain_text("締め日は毎月25日とする")
    expect(page.locator("#citations .excerpt")).to_contain_text("第1章 総則 › 第3条")


def test_document_text_is_shown_as_text_not_markup(page, server):
    expect = _expect()
    _ask(page, server, "表示確認")
    card = page.locator(".turn .card").last
    expect(card.locator(".a")).to_contain_text('<img src=x onerror="window.__xss=1">')
    assert card.locator(".a img").count() == 0
    assert card.locator(".a script").count() == 0
    assert page.evaluate("window.__xss") is None


def test_enter_while_ime_is_composing_does_not_send(page, server):
    expect = _expect()
    page.goto(server.url + "/chat-ui")
    page.fill("#q", "けいひ")
    page.eval_on_selector(
        "#q", "el => el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}))"
    )
    page.wait_for_timeout(300)
    assert page.locator(".turn").count() == 0
    assert page.input_value("#q") == "けいひ"
    page.fill("#q", "経費精算の締め日はいつですか？")
    page.press("#q", "Enter")
    expect(page.locator(".turn .q")).to_have_text("経費精算の締め日はいつですか？")


def test_stop_button_ends_a_running_answer(page, server):
    expect = _expect()
    _ask(page, server, "時間のかかる質問")
    expect(page.locator("#send")).to_have_text("停止")
    expect(page.locator(".turn .card .a")).to_contain_text("途中まで")
    page.click("#send")
    expect(page.locator(".turn .card .err")).to_have_text("回答の生成を停止しました。")
    expect(page.locator("#send")).to_have_text("送信")


def test_bad_feedback_sends_the_chosen_reason(page, server):
    expect = _expect()
    server.feedback.clear()
    _ask(page, server, "経費精算の締め日はいつですか？")
    card = page.locator(".turn .card").last
    expect(card.locator(".card-head")).to_contain_text("文書から回答")
    card.get_by_role("button", name="👎 役に立たなかった").click()
    card.locator(".reasons").get_by_role("button", name="情報が古い").click()
    card.locator(".reasons textarea").fill("手順が変わっています")
    card.locator(".reasons").get_by_role("button", name="送信").click()
    expect(card.locator(".actions .done")).to_have_text("ご意見を受け付けました")
    assert len(server.feedback) == 1
    event = server.feedback[0]
    assert (event["feedback_type"], event["bad_reason"], event["comment"]) == ("bad", "outdated", "手順が変わっています")
    assert event["tenant_id"] == "default"


# --- admin screens ----------------------------------------------------------------------------------


def _login(page, server, next_query=""):
    page.goto(server.url + "/admin/login" + next_query)
    page.fill("#token", ADMIN_TOKEN)
    page.click("#submit")


def test_admin_page_redirects_to_login_and_back(page, server):
    expect = _expect()
    page.goto(server.url + "/admin/ingestion")
    expect(page).to_have_url(server.url + "/admin/login?next=/admin/ingestion")
    page.fill("#token", ADMIN_TOKEN)
    page.click("#submit")
    expect(page).to_have_url(server.url + "/admin/ingestion")


def test_wrong_token_is_refused(page, server):
    expect = _expect()
    page.goto(server.url + "/admin/login")
    page.fill("#token", "wrong-token")
    page.click("#submit")
    expect(page.locator("#message")).to_have_text("サインインできませんでした。トークンを確認してください。")
    expect(page).to_have_url(server.url + "/admin/login")


def test_login_never_returns_to_another_site(page, server):
    expect = _expect()
    _login(page, server, "?next=https://evil.example.co.jp/admin")
    expect(page).to_have_url(server.url + "/admin")


def test_dashboard_shows_metrics(page, server):
    expect = _expect()
    _login(page, server)
    expect(page).to_have_url(server.url + "/admin")
    expect(page.locator("#kpiAnswers")).not_to_have_text("–")
    expect(page.locator("#overall")).not_to_have_text("–")


def test_upload_and_csrf(page, server):
    expect = _expect()
    _login(page, server, "?next=/admin/ingestion")
    expect(page).to_have_url(server.url + "/admin/ingestion")
    page.set_input_files("#file", files=[
        {"name": "手順書.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n% e2e test\n"},
        {"name": "memo.txt", "mimeType": "text/plain", "buffer": b"plain text"},
    ])
    rows = page.locator("#files .file")
    expect(rows.filter(has_text="手順書.pdf").locator(".st.ok")).to_be_visible()
    expect(rows.filter(has_text="memo.txt").locator(".st.ng")).to_have_text("対応していない形式です")
    stored = [p for p in Path(server.ingest_root).rglob("*") if p.is_file()]
    assert len(stored) == 1 and stored[0].name.endswith("_手順書.pdf")
    # The same request without the CSRF header is refused even with a valid session.
    status = page.evaluate(
        """() => fetch('/admin/ingestion/upload?filename=x.pdf', {method: 'PUT', body: '%PDF-1.4',
                  credentials: 'same-origin'}).then(r => r.status)"""
    )
    assert status == 403


def test_document_delete_check_from_the_ingestion_page(page, server, monkeypatch):
    # Stage 15: the dry-run button posts the form and shows the report.
    from webapi import collection_admin

    calls = []

    def fake_delete(collection, *, tenant, source_docs, execute=False):
        calls.append((collection, tenant, list(source_docs), execute))
        return {"collection": "(served)", "tenant": tenant, "executed": execute,
                "documents": {d: 2 for d in source_docs}, "chunks": 2 * len(source_docs)}

    monkeypatch.setattr(collection_admin, "delete_documents", fake_delete)
    expect = _expect()
    _login(page, server, "?next=/admin/ingestion")
    expect(page).to_have_url(server.url + "/admin/ingestion")
    page.fill("#deleteTenant", "default")
    page.fill("#deleteDocs", "経費規程.pdf\n手順書.pdf")
    page.click("#deleteCheck")
    expect(page.locator("#deleteResult")).to_contain_text("chunks")
    expect(page.locator("#deleteResult")).to_contain_text("4")
    assert calls == [(None, "default", ["経費規程.pdf", "手順書.pdf"], False)]
