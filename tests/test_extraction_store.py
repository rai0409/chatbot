import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
from rag_core import extraction_store as store

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path):
    roots = tmp_path / "ingest"
    roots.mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("EXTRACTION_STORE_ENABLED", raising=False)
    monkeypatch.delenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    return roots


def _page(page, lines, confidence=90.0):
    return {"page": page, "dpi": 300, "engine": "fake", "width_px": 2480, "height_px": 3508, "confidence": confidence,
            "lines": [{"text": t, "confidence": c, "bbox": [10.0, 20.0 * (i + 1), 500.0, 20.0 * (i + 1) + 15] if c != "nobox" else None}
                      for i, (t, c) in enumerate(lines)]}


def test_a_record_keeps_every_line_with_its_confidence_and_box_and_flags_weak_pages(env):
    pdf = env / "scan.pdf"
    pdf.write_bytes(b"%PDF")
    record = store.build_record("tenant_a", "規程.pdf", "abc", {
        1: _page(1, [("経費精算の締め日", 96.0), ("毎月25日", 91.5)], 94.0),
        "3": _page(3, [("支払期日", 95.0), ("翌月末日", 41.0)], 92.0),
        5: _page(5, [("本文", None)], None),
    }, file_path=pdf, now=T0)
    assert [p["page"] for p in record["pages"]] == [1, 3, 5] and record["schema"] == 1 and record["saved_at"] == T0.isoformat()
    first = record["pages"][0]
    assert first["needs_review"] is False and first["width_px"] == 2480 and first["confidence"] == 94.0
    assert first["lines"][0] == {"id": "p1-l1", "text": "経費精算の締め日", "confidence": 96.0, "bbox": [10.0, 20.0, 500.0, 35.0]}
    assert record["pages"][1]["needs_review"] is True  # one line is at 41
    assert record["pages"][2]["needs_review"] is True and record["pages"][2]["lines"][0]["confidence"] is None
    assert record["file_path"] == str(pdf.resolve())


def test_the_review_threshold_is_a_setting(env, monkeypatch):
    pages = {1: _page(1, [("本文", 75.0)], 75.0)}
    assert store.build_record("t", "d", "x", pages)["pages"][0]["needs_review"] is True
    monkeypatch.setenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", "70")
    assert store.build_record("t", "d", "x", pages)["pages"][0]["needs_review"] is False
    monkeypatch.setenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", "bad")
    assert store.review_max_confidence() == 80.0


def test_the_file_path_is_kept_only_for_a_file_inside_the_allowed_roots(env, tmp_path):
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"%PDF")
    pages = {1: _page(1, [("a", 90.0)])}
    assert store.build_record("t", "d", "x", pages, file_path=outside)["file_path"] is None
    assert store.build_record("t", "d", "x", pages, file_path=env / "missing.pdf")["file_path"] is None
    assert store.build_record("t", "d", "x", pages)["file_path"] is None


def test_records_are_saved_atomically_per_tenant_and_document_and_loaded_back(env, tmp_path):
    pages = {1: _page(1, [("本文", 90.0)])}
    first = store.build_record("tenant_a", "a.pdf", "h1", pages, now=T0)
    path = store.save_record(first)
    assert path.parent == tmp_path / "store" / "tenant_a" and path.suffix == ".json" and list(path.parent.glob(".*tmp*")) == []
    assert store.load_record("tenant_a", "a.pdf") == first
    assert store.load_record("tenant_a", "b.pdf") is None and store.load_record("tenant_b", "a.pdf") is None
    assert store.doc_key("tenant_a", "a.pdf") != store.doc_key("tenant_b", "a.pdf") != store.doc_key("tenant_a", "b.pdf")
    store.save_record(store.build_record("tenant_a", "a.pdf", "h2", {1: _page(1, [("新しい本文", 99.0)])}, now=T0))
    assert store.load_record("tenant_a", "a.pdf")["file_sha256"] == "h2"  # rewritten at every ingestion
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(store.ExtractionError, match="unreadable"):
        store.load_record("tenant_a", "a.pdf")
    path.write_text(json.dumps({"schema": 9}), encoding="utf-8")
    with pytest.raises(store.ExtractionError, match="unsupported"):
        store.load_record("tenant_a", "a.pdf")


@pytest.mark.parametrize("tenant", ["../x", "a/b", "", "x" * 65, "日本語", None])
def test_an_unsafe_tenant_id_is_refused(env, tenant):
    with pytest.raises(store.ExtractionError, match="invalid tenant"):
        store.doc_key(tenant, "a.pdf")
    with pytest.raises(store.ExtractionError):
        store.record_path(tenant, "a.pdf")


def test_save_from_report_never_raises_and_can_be_switched_off(env, monkeypatch):
    pdf = env / "scan.pdf"
    pdf.write_bytes(b"%PDF")
    pages = {1: _page(1, [("本文", 90.0)])}
    assert store.save_from_report("tenant_a", "scan.pdf", pdf, pages) is True
    assert store.load_record("tenant_a", "scan.pdf")["file_sha256"]
    assert store.save_from_report("tenant_a", "scan.pdf", pdf, {}) is False  # nothing captured
    assert store.save_from_report("../bad", "scan.pdf", pdf, pages) is False  # an error is swallowed
    assert store.save_from_report("tenant_a", "scan.pdf", env / "missing.pdf", pages) is False
    monkeypatch.setenv("EXTRACTION_STORE_ENABLED", "false")
    assert store.enabled() is False and store.save_from_report("tenant_b", "scan.pdf", pdf, pages) is False
    assert store.load_record("tenant_b", "scan.pdf") is None


# --- in the converter and the adapter -----------------------------------------------------------------------------


def test_the_converter_captures_lines_only_when_the_ocr_result_has_them():
    from scripts import pdf_to_canonical_jsonl as conv

    assert conv._capture_lines(SimpleNamespace(text="x", confidence=90.0), 1, 300) is None  # no lines attribute
    assert conv._capture_lines(SimpleNamespace(text="x", confidence=90.0, lines=[]), 1, 300) is None
    result = SimpleNamespace(text="x", confidence=88.0, engine="tess", image_size=(100, 200), lines=[
        SimpleNamespace(text="行1", confidence=90.0, bbox=(1, 2, 3, 4)), SimpleNamespace(text="行2", confidence=None, bbox=None)])
    assert conv._capture_lines(result, 7, 300) == {
        "page": 7, "dpi": 300, "engine": "tess", "width_px": 100, "height_px": 200, "confidence": 88.0,
        "lines": [{"text": "行1", "confidence": 90.0, "bbox": [1.0, 2.0, 3.0, 4.0]}, {"text": "行2", "confidence": None, "bbox": None}]}
    bare = conv._capture_lines(SimpleNamespace(text="x", lines=[SimpleNamespace(text="a")]), 1, 150)
    assert bare["width_px"] is None and bare["lines"] == [{"text": "a", "confidence": None, "bbox": None}]


def _scanned_pdf(path):
    from rag_core import extraction

    extraction.load()
    from docextract.testing import simple_pdf

    path.write_bytes(simple_pdf([{"rects": [(72, 72, 300, 200)], "lines": [(72, 250, 500, 250)]}]))
    return path


def test_a_scanned_pdf_leaves_its_lines_in_the_store_and_its_chunks_unchanged(env, monkeypatch):
    pytest.importorskip("pdfplumber")
    from rag_core.document_converters import pdf_adapter
    from scripts import pdf_to_canonical_jsonl as conv

    monkeypatch.setenv("INGEST_PDF_OCR", "on")
    monkeypatch.setattr(conv, "_ensure_ocr_available", lambda: None)
    result = SimpleNamespace(
        text="経費精算の締め日は毎月25日とする。", confidence=70.0, engine="fake", image_size=(2480, 3508),
        lines=[SimpleNamespace(text="経費精算の締め日は", confidence=95.0, bbox=(10, 20, 900, 60)),
               SimpleNamespace(text="毎月25日とする。", confidence=42.0, bbox=(10, 70, 800, 110))])
    monkeypatch.setattr(conv, "_ocr_page_result", lambda page, dpi, lang: result)
    pdf = _scanned_pdf(env / "0123456789ab_scan.pdf")
    rows, report = pdf_adapter.convert_pdf_with_report(pdf, tenant_id="tenant_a", source_doc="経費規程.pdf")
    assert rows and "締め日" in rows[0]["text"] and "ocr_pages" not in report
    record = store.load_record("tenant_a", "経費規程.pdf")
    assert record["source_doc"] == "経費規程.pdf" and record["file_path"] == str(pdf.resolve())
    page = record["pages"][0]
    assert page["page"] == 1 and page["needs_review"] is True and page["width_px"] == 2480 and page["engine"] == "fake"
    assert [(l["id"], l["confidence"], l["bbox"]) for l in page["lines"]] == [
        ("p1-l1", 95.0, [10.0, 20.0, 900.0, 60.0]), ("p1-l2", 42.0, [10.0, 70.0, 800.0, 110.0])]

    monkeypatch.setenv("EXTRACTION_STORE_ENABLED", "false")
    same_rows, _ = pdf_adapter.convert_pdf_with_report(pdf, tenant_id="tenant_b", source_doc="経費規程.pdf")
    assert [r["text"] for r in same_rows] == [r["text"] for r in rows] and store.load_record("tenant_b", "経費規程.pdf") is None


def test_a_store_that_cannot_be_written_does_not_fail_the_ingestion(env, monkeypatch, tmp_path):
    pytest.importorskip("pdfplumber")
    from rag_core.document_converters import pdf_adapter
    from scripts import pdf_to_canonical_jsonl as conv

    monkeypatch.setenv("INGEST_PDF_OCR", "on")
    monkeypatch.setattr(conv, "_ensure_ocr_available", lambda: None)
    monkeypatch.setattr(conv, "_ocr_page_result", lambda page, dpi, lang: SimpleNamespace(
        text="本文があります。", confidence=90.0, lines=[SimpleNamespace(text="本文があります。", confidence=90.0, bbox=(1, 2, 3, 4))]))
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the store directory should be", encoding="utf-8")
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(blocker))
    rows = pdf_adapter.convert_pdf(_scanned_pdf(env / "scan.pdf"), tenant_id="tenant_a")
    assert rows and "本文" in rows[0]["text"]
