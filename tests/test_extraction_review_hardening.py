"""PR E: decisions on identical lines, concurrent writers, typed errors, bounds. Synthetic data only."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

import config
from rag_core import extraction_review as review, extraction_store as store

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
TENANT = "tenant_a"
DOC = "規程.pdf"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    return tmp_path


def _save(lines, page=1):
    pages = {page: {"page": page, "dpi": 300, "engine": "f", "width_px": 100, "height_px": 200, "confidence": 50.0,
                    "lines": [{"text": t, "confidence": c, "bbox": [0, 0, 1, 1]} for t, c in lines]}}
    return store.save_record(store.build_record(TENANT, DOC, "h", pages, now=T0))


def test_deciding_one_of_two_identical_lines_leaves_the_other_open(env):
    _save([("合計", 30.0), ("金額 100", 95.0), ("合計", 30.0)])
    review.confirm_line(TENANT, DOC, 1, "p1-l1", by="山田太郎", now=T0)
    items = {i["line_id"]: i["state"] for i in review.review_items(TENANT, include_reviewed=True)}
    assert items == {"p1-l1": "confirmed", "p1-l3": "open"}
    row = review.documents(TENANT)[0]
    assert (row["open"], row["confirmed"]) == (1, 1)


def test_corrections_of_identical_lines_hit_the_right_occurrence(env):
    _save([("合計", 30.0), ("間", 95.0), ("合計", 30.0)])
    review.submit_correction(TENANT, DOC, 1, "p1-l3", "小計", by="山田太郎", now=T0)  # the second one only
    pages = [{"page": 1, "text": "合計\n間\n合計", "extraction_method": "ocr_fallback"}]
    out, stats = review.apply_corrections(pages, TENANT, DOC)
    assert out[0]["text"] == "合計\n間\n小計" and stats["applied"] == 1


def test_both_identical_lines_can_be_corrected_differently(env):
    _save([("合計", 30.0), ("合計", 30.0)])
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "小計", by="山田太郎", now=T0)
    review.submit_correction(TENANT, DOC, 1, "p1-l2", "総計", by="山田太郎", now=T0)
    pages = [{"page": 1, "text": "合計\n合計", "extraction_method": "ocr_fallback"}]
    out, stats = review.apply_corrections(pages, TENANT, DOC)
    assert out[0]["text"] == "小計\n総計" and stats["applied"] == 2


def test_first_occurrence_ids_are_unchanged_so_saved_decisions_still_match(env):
    # The id of a decision on the first occurrence must stay what earlier versions wrote.
    assert review._entry_id(1, "合計", 0) == review._entry_id(1, "合計")


def test_revert_removes_only_that_occurrence(env):
    _save([("合計", 30.0), ("合計", 30.0)])
    review.confirm_line(TENANT, DOC, 1, "p1-l1", by="a", now=T0)
    review.confirm_line(TENANT, DOC, 1, "p1-l2", by="a", now=T0)
    assert review.revert(TENANT, DOC, 1, "p1-l2") is True
    states = {i["line_id"]: i["state"] for i in review.review_items(TENANT, include_reviewed=True)}
    assert states == {"p1-l1": "confirmed", "p1-l2": "open"}


def test_concurrent_decisions_are_not_lost(env):
    _save([(f"行{i}", 10.0) for i in range(12)])
    errors = []

    def work(i):
        try:
            review.confirm_line(TENANT, DOC, 1, f"p1-l{i}", by="a", now=T0)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(1, 13)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(review.load_corrections(TENANT, DOC)["entries"]) == 12


def test_not_found_is_a_typed_error(env):
    _save([("合計", 30.0)])
    with pytest.raises(store.ExtractionNotFound):
        review.confirm_line(TENANT, DOC, 1, "p9-l9", by="a")
    with pytest.raises(store.ExtractionNotFound):
        review.confirm_line(TENANT, "無い.pdf", 1, "p1-l1", by="a")
    with pytest.raises(store.ExtractionNotFound):
        store.render_page_png({"pages": [], "file_path": None}, 1)


def test_http_status_follows_the_error_type_not_the_wording():
    from webapi.routers import extraction

    assert extraction._extraction_http_error(store.ExtractionNotFound("anything")).status_code == 404
    assert extraction._extraction_http_error(store.ExtractionError("this line is not available text")).status_code == 400


def test_page_number_is_bounded():
    from pydantic import ValidationError

    from webapi.routers.extraction import ExtractionLineRequest

    with pytest.raises(ValidationError):
        ExtractionLineRequest(source_doc="a.pdf", page=10**9, line_id="p1-l1")
