"""Browser tests of the console screens that were CLI-only before: approved QA, the ingestion settings and the backups
(Playwright, headless Chromium, the real app served by uvicorn and the built console in webui/dist). Synthetic data only.

Skipped when Playwright / Chromium / the built console is missing; with KURADEN_E2E_REQUIRED=1 (CI) that is a failure.
  (cd webui && npm ci && npm run build)
"""
from __future__ import annotations

import os
import re
import socket
import stat
import threading
import time
from types import SimpleNamespace

import pytest

import config

REQUIRED = os.getenv("KURADEN_E2E_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_TOKEN = "e2e-console-admin-token-not-a-secret-0123456789"


def _unavailable(reason: str):
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module", autouse=True)
def _isolated_admin_login_limiter():
    # Isolate login counters between browser test modules.
    # Production rate limits remain unchanged.
    from webapi.routers import admin_core

    admin_core._ADMIN_LOGIN_LIMITER.reset()
    try:
        yield
    finally:
        admin_core._ADMIN_LOGIN_LIMITER.reset()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    uvicorn = pytest.importorskip("uvicorn")
    from webapi import backups, main
    from webapi.routers import console as console_router

    if not (console_router.dist_dir() / "index.html").is_file():
        _unavailable("the console is not built (cd webui && npm run build)")
    tmp = tmp_path_factory.mktemp("e2e_console")
    mp = pytest.MonkeyPatch()
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ADMIN_AUTH_ALLOW_INSECURE_DEV", "APPROVED_QA_REQUIRE_APPROVER"):
        mp.delenv(name, raising=False)
    mp.setenv("ADMIN_AUTH_ENABLED", "true")
    mp.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    mp.setenv("OIDC_COOKIE_SECURE", "false")
    mp.setenv("BACKUP_DIR", str(tmp / "backups"))
    mp.setattr(config, "RUNS_DIR", str(tmp / "runs"))
    mp.setattr(config, "RUNTIME_SETTINGS_DIR", str(tmp / "settings"))
    mp.setattr(config, "APPROVED_QA_PATH", str(tmp / "approved" / "live.jsonl"))
    mp.setattr(config, "APPROVED_QA_ENABLED", True)
    fake = tmp / "fake_backup.sh"
    fake.write_text('#!/usr/bin/env bash\nout=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; *) shift;; esac; done\n'
                    'mkdir -p "$out"; echo data > "$out/chatbot_backup_20261101T090000Z.tar.gz"; echo "backup done"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    mp.setattr(backups, "script_path", lambda: fake)

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
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", tmp=tmp)
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
    except Exception as exc:  # noqa: BLE001 - browser binary or system libraries missing
        pw.stop()
        _unavailable(f"chromium cannot start: {str(exc).splitlines()[0]}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser, server):
    context = browser.new_context(locale="ja-JP")
    p = context.new_page()
    p.set_default_timeout(15000)
    p.goto(server.url + "/admin/login")
    p.fill("#token", ADMIN_TOKEN)
    p.click("#submit")
    p.wait_for_url(lambda url: "/admin/login" not in url)
    yield p
    context.close()


def _expect():
    from playwright.sync_api import expect

    return expect


def _row(page, text):
    """The table row whose row header (first cell) contains `text`."""
    return page.locator("tr", has=page.get_by_role("rowheader", name=re.compile(re.escape(text))))


def test_approved_qa_is_created_approved_and_answered_by_chat(page, server):
    expect = _expect()
    page.goto(server.url + "/console/approved-qa")
    expect(page.get_by_role("heading", name="承認済み QA", level=1)).to_be_visible()
    page.get_by_role("button", name="新規作成").click()
    form = page.get_by_role("region", name="QA を作る")
    form.get_by_label("テナント").fill("default")
    form.get_by_label("質問", exact=True).fill("申請期限はいつですか？")
    form.get_by_label("回答", exact=True).fill("毎月末日です。")
    form.get_by_label("言い換え（1 行に 1 つ）").fill("締め切りはいつ？")
    form.get_by_label("文書名").fill("manual.pdf")
    form.get_by_label("ページ（カンマ区切り）").fill("2")
    form.get_by_label("理由（必須）").fill("新規登録")
    form.get_by_label("あなたの名前（必須）").fill("山田太郎")
    form.get_by_role("button", name="保存").click()
    expect(page.get_by_text("保存しました。")).to_be_visible()
    row = _row(page, "申請期限はいつですか？")
    expect(row).to_contain_text("下書き")
    row.get_by_role("button", name="承認する").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("理由（必須）").fill("内容を確認した")
    dialog.get_by_role("button", name="保存").click()
    expect(page.get_by_text("「承認済み」にしました。")).to_be_visible()
    expect(_row(page, "申請期限はいつですか？")).to_contain_text("承認済み")
    # the same file is what chat reads
    from rag_core import approved_qa

    index = approved_qa.load_approved_qa(config.APPROVED_QA_PATH, tenant_id="default")
    found = approved_qa.lookup_approved_answer(index, "締め切りはいつ？", tenant_id="default")
    assert found is not None and found.approved_answer == "毎月末日です。"


def test_an_approved_answer_that_is_edited_goes_back_to_draft(page, server):
    expect = _expect()
    page.goto(server.url + "/console/approved-qa")
    page.get_by_role("button", name="編集: 申請期限はいつですか？").click()
    form = page.get_by_role("region", name="QA を編集")
    form.get_by_label("回答", exact=True).fill("毎月25日です。")
    form.get_by_label("理由（必須）").fill("期限が変わった")
    form.get_by_label("あなたの名前（必須）").fill("山田太郎")
    form.get_by_role("button", name="保存").click()
    expect(page.get_by_text(re.compile(r"下書きに戻りました"))).to_be_visible()
    expect(_row(page, "申請期限はいつですか？")).to_contain_text("下書き")


def test_a_csv_is_previewed_and_imported_as_drafts(page, server, tmp_path):
    expect = _expect()
    from rag_core import approved_qa_store as store

    csv_file = tmp_path / "qa.csv"
    csv_file.write_text(store.template_csv().replace("架空サービスの申請期限はいつですか？", "取込テストの質問です"), encoding="utf-8")
    page.goto(server.url + "/console/approved-qa")
    page.get_by_role("button", name="CSV から取り込む").click()
    panel = page.get_by_role("region", name="CSV から取り込む")
    panel.get_by_label("CSV ファイル").set_input_files(str(csv_file))
    panel.get_by_role("button", name="内容を確認（書き込まない）").click()
    expect(panel.get_by_text(re.compile(r"確認だけです"))).to_be_visible()
    panel.get_by_label("理由（必須）").fill("Excel から取り込み")
    panel.get_by_label("あなたの名前（必須）").fill("山田太郎")
    panel.get_by_role("button", name="取り込む").click()
    expect(panel.get_by_text(re.compile(r"取り込みました。"))).to_be_visible()
    expect(_row(page, "取込テストの質問です")).to_contain_text("下書き")


def test_an_ingestion_setting_is_changed_and_reset_with_a_reason(page, server):
    expect = _expect()
    page.goto(server.url + "/console/ingestion")
    expect(page.get_by_role("heading", name="取込の設定")).to_be_visible()
    page.get_by_role("button", name="変更: 同時アップロード数").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("同時アップロード数").fill("9")
    dialog.get_by_label("理由（必須）").fill("同時利用が増えた")
    dialog.get_by_label("あなたの名前（必須）").fill("山田太郎")
    dialog.get_by_role("button", name="保存").click()
    expect(page.get_by_text("保存しました。")).to_be_visible()
    row = _row(page, "同時アップロード数")
    expect(row).to_contain_text("画面で変更")
    assert os.environ["INGEST_UPLOAD_MAX_CONCURRENT"] == "9"  # the running server applies it at once
    page.get_by_role("button", name="元に戻す: 同時アップロード数").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("理由（必須）").fill("元に戻す")
    dialog.get_by_label("あなたの名前（必須）").fill("山田太郎")
    dialog.get_by_role("button", name="元に戻す").click()
    expect(page.get_by_text("元に戻しました。")).to_be_visible()
    assert "INGEST_UPLOAD_MAX_CONCURRENT" not in os.environ


def test_a_backup_is_started_and_appears_in_the_list(page, server):
    expect = _expect()
    page.goto(server.url + "/console/backups")
    page.get_by_role("button", name="今すぐバックアップを取る").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("あなたの名前（必須）").fill("山田太郎")
    dialog.get_by_role("button", name="実行する").click()
    expect(page.get_by_text("バックアップが完了しました。")).to_be_visible(timeout=15000)
    expect(_row(page, "chatbot_backup_20261101T090000Z.tar.gz")).to_be_visible()


def test_the_new_screens_are_in_the_menu(page, server):
    expect = _expect()
    page.goto(server.url + "/console/")
    nav = page.get_by_role("navigation")
    for name in ("承認済み QA", "バックアップ", "文書取込"):
        expect(nav.get_by_role("link", name=name)).to_be_visible()
