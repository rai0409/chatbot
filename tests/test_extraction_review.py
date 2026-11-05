import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
from rag_core import change_ledger, extraction_review as review, extraction_store as store
from rag_core import source_apply, source_registry as registry, source_scan, source_sync

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
TENANT = "tenant_a"


@pytest.fixture
def env(monkeypatch, tmp_path):
    roots = tmp_path / "ingest"
    (roots / "docs").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    return roots


def _save(source_doc="規程.pdf", tenant=TENANT, lines=None, page=1):
    lines = lines or [("経費精算の締め日は", 96.0), ("毎月25日とする。", 42.0), ("支払は翌月末日", None)]
    pages = {page: {"page": page, "dpi": 300, "engine": "f", "width_px": 100, "height_px": 200, "confidence": 80.0,
                    "lines": [{"text": t, "confidence": c, "bbox": [0, 0, 1, 1]} for t, c in lines]}}
    return store.save_record(store.build_record(tenant, source_doc, "h", pages, now=T0))


def test_review_items_are_the_weak_lines_lowest_first_and_without_decided_ones(env):
    _save()
    _save("別の規程.pdf", lines=[("低い行", 10.0), ("高い行", 99.0)], page=2)
    items = review.review_items(TENANT)
    assert [(i["source_doc"], i["line_id"], i["confidence"]) for i in items] == [
        ("規程.pdf", "p1-l3", None), ("別の規程.pdf", "p2-l1", 10.0), ("規程.pdf", "p1-l2", 42.0)]
    assert all(i["state"] == "open" for i in items)
    assert [i["line_id"] for i in review.review_items(TENANT, max_confidence=50)] == ["p1-l3", "p2-l1", "p1-l2"]
    assert [i["line_id"] for i in review.review_items(TENANT, max_confidence=20)] == ["p1-l3", "p2-l1"]
    assert [i["source_doc"] for i in review.review_items(TENANT, source_doc="規程.pdf")] == ["規程.pdf", "規程.pdf"]
    assert len(review.review_items(TENANT, limit=1)) == 1 and review.review_items("tenant_b") == []
    with pytest.raises(store.ExtractionError):
        review.review_items("../x")


def test_a_correction_and_a_confirmation_take_a_line_out_of_the_open_items(env):
    _save()
    entry = review.submit_correction(TENANT, "規程.pdf", 1, "p1-l2", "毎月20日とする。", by="山田太郎", note="原本を確認", now=T0)
    assert entry["state"] == "corrected" and entry["before"] == "毎月25日とする。" and entry["after"] == "毎月20日とする。"
    assert entry["by"] == "山田太郎" and entry["at"] == T0.isoformat() and entry["note"] == "原本を確認"
    confirmed = review.confirm_line(TENANT, "規程.pdf", 1, "p1-l3", by="佐藤次郎", now=T0)
    assert confirmed["state"] == "confirmed" and confirmed["after"] is None
    assert review.review_items(TENANT) == []
    shown = review.review_items(TENANT, include_reviewed=True)
    assert [(i["line_id"], i["state"]) for i in shown] == [("p1-l3", "confirmed"), ("p1-l2", "corrected")]
    assert review.revert(TENANT, "規程.pdf", 1, "p1-l2") is True and review.revert(TENANT, "規程.pdf", 1, "p1-l2") is False
    assert [i["line_id"] for i in review.review_items(TENANT)] == ["p1-l2"]


@pytest.mark.parametrize("text, message", [("", "must be 1 to"), ("   ", "must be 1 to"), ("x" * 2001, "must be 1 to"),
                                           ("a\x00b", "must be 1 to"), ("毎月25日とする。", "unchanged")])
def test_a_bad_correction_is_refused(env, text, message):
    _save()
    with pytest.raises(store.ExtractionError, match=message):
        review.submit_correction(TENANT, "規程.pdf", 1, "p1-l2", text, by="山田太郎")
    assert review.load_corrections(TENANT, "規程.pdf")["entries"] == []


def test_who_decided_and_which_line_are_required_and_checked(env):
    _save()
    for who in ("", "  ", "x" * 65, None):
        with pytest.raises(store.ExtractionError, match="who decided"):
            review.confirm_line(TENANT, "規程.pdf", 1, "p1-l2", by=who)
    with pytest.raises(store.ExtractionError, match="no such line"):
        review.confirm_line(TENANT, "規程.pdf", 1, "p9-l9", by="山田太郎")
    with pytest.raises(store.ExtractionError, match="no such line"):
        review.confirm_line(TENANT, "規程.pdf", 2, "p1-l2", by="山田太郎")
    with pytest.raises(store.ExtractionError, match="no extraction record"):
        review.confirm_line(TENANT, "ない.pdf", 1, "p1-l2", by="山田太郎")
    with pytest.raises(store.ExtractionError):
        review.confirm_line("tenant_b", "規程.pdf", 1, "p1-l2", by="山田太郎")


def test_decisions_survive_the_record_being_rewritten_and_follow_a_line_that_still_reads_the_same(env):
    _save()
    review.submit_correction(TENANT, "規程.pdf", 1, "p1-l2", "毎月20日とする。", by="山田太郎")
    review.confirm_line(TENANT, "規程.pdf", 1, "p1-l3", by="山田太郎")
    # a new ingestion: the lines have moved (different ids) and one of them reads differently now
    _save(lines=[("新しい行", 30.0), ("経費精算の締め日は", 96.0), ("支払は翌月末日", None), ("毎月25日とする。", 42.0)])
    open_items = review.review_items(TENANT)
    assert [i["text"] for i in open_items] == ["新しい行"]  # the other two keep their decision
    assert review.documents(TENANT)[0]["corrected"] == 1 and review.documents(TENANT)[0]["confirmed"] == 1


def test_the_corrected_wording_replaces_the_ocr_text_before_chunking(env):
    _save()
    review.submit_correction(TENANT, "規程.pdf", 1, "p1-l2", "毎月20日とする。", by="山田太郎")
    review.confirm_line(TENANT, "規程.pdf", 1, "p1-l3", by="山田太郎")
    pages = [{"page": 1, "text": "経費精算の締め日は 毎月25日とする。 毎月25日とする。", "extraction_method": "ocr_fallback"},
             {"page": 2, "text": "毎月25日とする。", "extraction_method": "ocr_fallback"},
             {"page": 1, "text": "毎月25日とする。", "extraction_method": "text"}]
    out, stats = review.apply_corrections(pages, TENANT, "規程.pdf")
    assert out[0]["text"] == "経費精算の締め日は 毎月20日とする。 毎月25日とする。"  # the first occurrence only
    assert out[1]["text"] == pages[1]["text"] and out[2]["text"] == pages[2]["text"]  # another page, a text-layer page
    assert stats == {"applied": 1, "unmatched": 0} and pages[0]["text"].count("25日") == 2  # the input is not modified


def test_a_correction_whose_text_is_gone_is_unmatched_and_the_normalizer_is_used(env):
    _save()
    review.submit_correction(TENANT, "規程.pdf", 1, "p1-l2", "毎月20日とする。", by="山田太郎")
    gone = [{"page": 1, "text": "全く別の本文", "extraction_method": "ocr_fallback"}]
    assert review.apply_corrections(gone, TENANT, "規程.pdf")[1] == {"applied": 0, "unmatched": 1}
    joined = [{"page": 1, "text": "毎月２５日とする。", "extraction_method": "ocr_fallback"}]
    out, stats = review.apply_corrections(joined, TENANT, "規程.pdf", normalize=lambda s: s.replace("25", "２５"))
    assert stats["applied"] == 1 and out[0]["text"] == "毎月20日とする。"


def test_a_missing_or_broken_corrections_file_changes_nothing_and_never_raises(env):
    pages = [{"page": 1, "text": "本文", "extraction_method": "ocr_fallback"}]
    assert review.apply_corrections(pages, TENANT, "規程.pdf") == (pages, {"applied": 0, "unmatched": 0})
    _save()
    review.confirm_line(TENANT, "規程.pdf", 1, "p1-l2", by="山田太郎")
    review._path(TENANT, "規程.pdf").write_text("{broken", encoding="utf-8")
    out, stats = review.apply_corrections(pages, TENANT, "規程.pdf")
    assert out == pages and stats == {"applied": 0, "unmatched": 0, "error": True}
    assert review.apply_corrections(pages, "../bad", "規程.pdf")[1]["error"] is True
    review._path(TENANT, "規程.pdf").write_text(json.dumps({"schema": 9, "entries": []}), encoding="utf-8")
    with pytest.raises(store.ExtractionError, match="unsupported"):
        review.load_corrections(TENANT, "規程.pdf")


def test_documents_summarize_the_open_work_and_the_preview(env):
    pdf = env / "docs" / "規程.pdf"
    pdf.write_bytes(b"%PDF")
    store.save_record(store.build_record(TENANT, "規程.pdf", "h", {1: {"page": 1, "confidence": 50.0, "lines": [
        {"text": "a", "confidence": 10.0, "bbox": None}, {"text": "b", "confidence": 99.0, "bbox": None}]}}, file_path=pdf))
    _save("別.pdf", lines=[("x", 95.0)])
    rows = review.documents(TENANT)
    assert [(r["source_doc"], r["lines"], r["open"], r["preview_available"]) for r in rows] == [("規程.pdf", 2, 1, True), ("別.pdf", 1, 0, False)]
    review.confirm_line(TENANT, "規程.pdf", 1, "p1-l1", by="山田太郎")
    assert review.documents(TENANT)[0]["open"] == 0 and review.documents("tenant_b") == []


# --- in the converter ------------------------------------------------------------------------------------------------


def _scanned_pdf(path):
    from rag_core import extraction

    extraction.load()
    from docextract.testing import simple_pdf

    path.write_bytes(simple_pdf([{"rects": [(72, 72, 300, 200)], "lines": [(72, 250, 500, 250)]}]))
    return path


def test_a_correction_reaches_the_chunks_of_the_next_conversion(env, monkeypatch):
    pytest.importorskip("pdfplumber")
    from rag_core.document_converters import pdf_adapter
    from scripts import pdf_to_canonical_jsonl as conv

    monkeypatch.setenv("INGEST_PDF_OCR", "on")
    monkeypatch.setattr(conv, "_ensure_ocr_available", lambda: None)
    result = SimpleNamespace(text="経費精算の締め日は毎月25日とする。", confidence=70.0, engine="f", image_size=(10, 10), lines=[
        SimpleNamespace(text="経費精算の締め日は", confidence=95.0, bbox=(1, 1, 2, 2)),
        SimpleNamespace(text="毎月25日とする。", confidence=42.0, bbox=(1, 3, 2, 4))])
    monkeypatch.setattr(conv, "_ocr_page_result", lambda page, dpi, lang: result)
    pdf = _scanned_pdf(env / "scan.pdf")
    rows, report = pdf_adapter.convert_pdf_with_report(pdf, tenant_id=TENANT, source_doc="規程.pdf")
    assert "25日" in rows[0]["text"] and "corrections" not in report
    review.submit_correction(TENANT, "規程.pdf", 1, "p1-l2", "毎月20日とする。", by="山田太郎")
    rows, report = pdf_adapter.convert_pdf_with_report(pdf, tenant_id=TENANT, source_doc="規程.pdf")
    assert "毎月20日" in rows[0]["text"] and "25日" not in rows[0]["text"] and report["corrections"] == {"applied": 1, "unmatched": 0}
    other, other_report = pdf_adapter.convert_pdf_with_report(pdf, tenant_id="tenant_b", source_doc="規程.pdf")
    assert "25日" in other[0]["text"] and "corrections" not in other_report  # another tenant is not affected


# --- re-ingestion ----------------------------------------------------------------------------------------------------------


def test_a_correction_can_ask_the_sources_to_ingest_the_document_again(env, monkeypatch):
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"pdf"}))
    folder = env / "docs"
    for name in ("規程.pdf", "other.pdf", "sub/規程-2.pdf"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF " + name.encode())
    registry.create("docs", tenant_id=TENANT, path=str(folder), now=T0)
    registry.create("other-tenant", tenant_id="tenant_b", path=str(folder), now=T0)
    source_sync.scan_source("docs", now=T0)
    registry.save_manifest("docs", "applied", registry.load_manifest("docs", "observed"))
    assert source_apply.plan_source("docs")["counts"] == {"added": 0, "modified": 0, "deleted": 0}
    assert review.request_reingest(TENANT, "規程.pdf") == ["docs"]
    assert sorted(registry.load_manifest("docs", "applied")) == ["other.pdf", "sub/規程-2.pdf"]
    assert source_apply.plan_source("docs")["added"] == ["規程.pdf"]  # the next apply ingests it again
    entry = change_ledger.read_entries("docs")[-1]
    assert entry["kind"] == "reingest" and entry["summary"] == {"reason": "extraction_correction", "files": 1}
    assert review.request_reingest(TENANT, "ない.pdf") == [] and review.request_reingest("tenant_c", "規程.pdf") == []
    assert registry.load_manifest("other-tenant", "applied") == {}  # a source of another tenant is not touched
