import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import extraction_store as store
from rag_core import source_apply, source_registry as registry, source_scan, source_sync

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}
STATIC = Path(__file__).resolve().parents[1] / "webapi" / "static"


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    ingest = tmp_path / "ingest"
    (ingest / "docs").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(ingest))
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    from webapi import main

    return TestClient(main.app), ingest, tmp_path


def _record(tenant="tenant_a", source_doc="規程.pdf", file_path=None):
    lines = [("経費精算の締め日は", 96.0, [10, 10, 400, 40]), ("毎月25日とする。", 42.0, [10, 50, 300, 80]), ("支払は翌月末日", None, None)]
    pages = {1: {"page": 1, "dpi": 72, "engine": "f", "width_px": 612, "height_px": 792, "confidence": 80.0,
                 "lines": [{"text": t, "confidence": c, "bbox": b} for t, c, b in lines]}}
    store.save_record(store.build_record(tenant, source_doc, "h", pages, file_path=file_path))


def _audit(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    return [json.loads(line)["action"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _body(**extra):
    return {"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 1, "line_id": "p1-l2", **extra}


def test_every_route_needs_the_admin_authentication(api):
    client, _ingest, _tmp = api
    for method, url in (("get", "/admin/extraction"), ("get", "/admin/extraction/docs"), ("get", "/admin/extraction/items"),
                        ("get", "/admin/extraction/page?source_doc=a&page=1"), ("get", "/admin/extraction/page-image?source_doc=a&page=1")):
        assert getattr(client, method)(url).status_code in {401, 403}, url
    for url in ("correct", "confirm", "revert", "reingest"):
        assert client.post(f"/admin/extraction/{url}", json=_body(text="x")).status_code in {401, 403}, url


def test_the_open_lines_come_lowest_first_and_a_decision_removes_them(api):
    client, _ingest, tmp_path = api
    _record()
    items = client.get("/admin/extraction/items", params={"tenant_id": "tenant_a"}, headers=ADMIN).json()["items"]
    assert [(i["line_id"], i["state"]) for i in items] == [("p1-l3", "open"), ("p1-l2", "open")]
    docs = client.get("/admin/extraction/docs", params={"tenant_id": "tenant_a"}, headers=ADMIN).json()["documents"]
    assert docs[0]["source_doc"] == "規程.pdf" and docs[0]["open"] == 2 and docs[0]["preview_available"] is False

    done = client.post("/admin/extraction/correct", json=_body(text="毎月20日とする。", note="原本"), headers=ADMIN)
    assert done.status_code == 200 and done.json()["state"] == "corrected" and done.json()["by"] == "installation"
    assert client.post("/admin/extraction/confirm", json=_body(line_id="p1-l3"), headers=ADMIN).json()["state"] == "confirmed"
    assert client.get("/admin/extraction/items", params={"tenant_id": "tenant_a"}, headers=ADMIN).json()["items"] == []
    shown = client.get("/admin/extraction/items", params={"tenant_id": "tenant_a", "include_reviewed": "true"}, headers=ADMIN).json()["items"]
    assert sorted(i["state"] for i in shown) == ["confirmed", "corrected"]
    assert client.post("/admin/extraction/revert", json=_body(), headers=ADMIN).json() == {"reverted": True}
    assert client.post("/admin/extraction/revert", json=_body(), headers=ADMIN).json() == {"reverted": False}
    assert {"extraction_corrected", "extraction_confirmed", "extraction_reverted"} <= set(_audit(tmp_path))


def test_the_page_data_carries_the_boxes_and_the_decision_of_each_line(api):
    client, _ingest, _tmp = api
    _record()
    client.post("/admin/extraction/confirm", json=_body(), headers=ADMIN)
    page = client.get("/admin/extraction/page", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 1}, headers=ADMIN).json()
    assert page["width_px"] == 612 and page["threshold"] == 80.0 and page["preview_available"] is False
    assert [(l["id"], l["state"], l["bbox"]) for l in page["lines"]] == [
        ("p1-l1", "open", [10, 10, 400, 40]), ("p1-l2", "confirmed", [10, 50, 300, 80]), ("p1-l3", "open", None)]
    assert client.get("/admin/extraction/page", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 9}, headers=ADMIN).status_code == 404
    assert client.get("/admin/extraction/page", params={"tenant_id": "tenant_a", "source_doc": "ない.pdf", "page": 1}, headers=ADMIN).status_code == 404


def test_bad_input_is_refused_with_a_clear_status(api):
    client, _ingest, _tmp = api
    _record()
    assert client.post("/admin/extraction/correct", json=_body(text="毎月25日とする。"), headers=ADMIN).status_code == 400  # unchanged
    assert client.post("/admin/extraction/correct", json=_body(text=""), headers=ADMIN).status_code == 400
    assert client.post("/admin/extraction/confirm", json=_body(line_id="p9-l9"), headers=ADMIN).status_code == 404
    assert client.post("/admin/extraction/confirm", json=_body(source_doc="ない.pdf"), headers=ADMIN).status_code == 404
    assert client.post("/admin/extraction/correct", json=_body(text="x", page=0), headers=ADMIN).status_code == 422
    assert client.get("/admin/extraction/items", params={"tenant_id": "../x"}, headers=ADMIN).status_code == 400
    assert client.get("/admin/extraction/items", params={"tenant_id": "tenant_a", "limit": 0}, headers=ADMIN).status_code == 422
    assert client.get("/admin/extraction/items", params={"tenant_id": "tenant_a", "max_confidence": 101}, headers=ADMIN).status_code == 422


def test_a_tenant_admin_reaches_only_its_own_tenant(api):
    client, _ingest, _tmp = api
    _record("tenant_a")
    _record("tenant_b")
    assert client.get("/admin/extraction/items", params={"tenant_id": "tenant_a"}, headers=TENANT_A).status_code == 200
    for method, url, body in (("get", "/admin/extraction/items?tenant_id=tenant_b", None), ("get", "/admin/extraction/docs?tenant_id=tenant_b", None),
                              ("get", "/admin/extraction/page?tenant_id=tenant_b&source_doc=規程.pdf&page=1", None),
                              ("get", "/admin/extraction/page-image?tenant_id=tenant_b&source_doc=規程.pdf&page=1", None),
                              ("post", "/admin/extraction/correct", _body(tenant_id="tenant_b", text="x")),
                              ("post", "/admin/extraction/confirm", _body(tenant_id="tenant_b")),
                              ("post", "/admin/extraction/reingest", {"tenant_id": "tenant_b", "source_doc": "規程.pdf"})):
        call = getattr(client, method)(url, headers=TENANT_A) if body is None else client.post(url, json=body, headers=TENANT_A)
        assert call.status_code == 403, url
    assert client.post("/admin/extraction/confirm", json=_body(), headers=TENANT_A).json()["by"] == "tenant_a"


def test_the_page_image_is_the_original_page_at_the_ocr_resolution_and_never_shows_the_path(api):
    pytest.importorskip("pypdfium2")
    from rag_core import extraction

    extraction.load()
    from docextract.testing import simple_pdf

    client, ingest, tmp_path = api
    pdf = ingest / "docs" / "規程.pdf"
    pdf.write_bytes(simple_pdf([{"rects": [(72, 72, 300, 200)], "lines": [(72, 250, 500, 250)]}]))
    _record(file_path=pdf)
    image = client.get("/admin/extraction/page-image", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 1}, headers=ADMIN)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png" and image.content[:8] == b"\x89PNG\r\n\x1a\n"
    page = client.get("/admin/extraction/page", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 1}, headers=ADMIN)
    assert page.json()["preview_available"] is True and str(tmp_path) not in page.text and "file_path" not in page.text
    assert str(tmp_path) not in client.get("/admin/extraction/docs", params={"tenant_id": "tenant_a"}, headers=ADMIN).text
    assert client.get("/admin/extraction/page-image", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 2}, headers=ADMIN).status_code == 404
    pdf.unlink()
    assert client.get("/admin/extraction/page-image", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 1}, headers=ADMIN).status_code == 404


def test_without_a_file_there_is_no_image_and_no_server_detail(api):
    client, _ingest, tmp_path = api
    _record()
    gone = client.get("/admin/extraction/page-image", params={"tenant_id": "tenant_a", "source_doc": "規程.pdf", "page": 1}, headers=ADMIN)
    assert gone.status_code == 404 and str(tmp_path) not in gone.text
    assert client.get("/admin/extraction/page-image", params={"tenant_id": "tenant_a", "source_doc": "ない.pdf", "page": 1}, headers=ADMIN).status_code == 404


def test_a_reingest_request_reaches_the_sources_of_the_tenant(api, monkeypatch):
    client, ingest, tmp_path = api
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"pdf"}))
    (ingest / "docs" / "規程.pdf").write_bytes(b"%PDF")
    registry.create("docs", tenant_id="tenant_a", path=str(ingest / "docs"))
    source_sync.scan_source("docs")
    registry.save_manifest("docs", "applied", registry.load_manifest("docs", "observed"))
    _record()
    out = client.post("/admin/extraction/reingest", json={"tenant_id": "tenant_a", "source_doc": "規程.pdf"}, headers=ADMIN).json()
    assert out["sources"] == ["docs"] and "next apply" in out["note"]
    assert source_apply.plan_source("docs")["added"] == ["規程.pdf"]
    none = client.post("/admin/extraction/reingest", json={"tenant_id": "tenant_a", "source_doc": "別.pdf"}, headers=ADMIN).json()
    assert none["sources"] == [] and "upload it again" in none["note"]
    assert "extraction_reingest_requested" in _audit(tmp_path)


def test_the_page_is_served_with_the_menu_and_the_menu_links_to_it(api):
    client, _ingest, _tmp = api
    page = client.get("/admin/extraction", headers=ADMIN)
    assert page.status_code == 200 and "抽出レビュー" in page.text and 'aria-label="管理メニュー"' in page.text
    assert '<a href="/admin/extraction">抽出レビュー</a>' in (STATIC / "admin_nav.html").read_text(encoding="utf-8")


def test_the_page_inserts_text_only_with_textcontent():
    html = (STATIC / "extraction_review.html").read_text(encoding="utf-8")
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html and "textContent" in html
    for needle in ("/admin/extraction/items", "/admin/extraction/page-image", "このまま正しい", "再取り込みを予約", "判断を取り消す"):
        assert needle in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_inline_script_of_the_page_is_valid_javascript():
    html = (STATIC / "extraction_review.html").read_text(encoding="utf-8")
    scripts = [s for s in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S) if s.strip()]
    assert len(scripts) == 1
    done = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=scripts[0],
                          text=True, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
