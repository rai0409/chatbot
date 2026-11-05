"""PR E: a correction of OCR text can need a second person's approval before it enters the knowledge base.
Synthetic data only."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import config
from rag_core import extraction_review as review, extraction_store as store

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
TENANT, DOC = "tenant_a", "規程.pdf"
ALICE = {"name": "山田太郎", "id": "fp-alice", "verified": True}
BOB = {"name": "佐藤次郎", "id": "fp-bob", "verified": True}
ALICE_TYPED = {"name": "山田太郎", "id": None, "verified": False}
BOB_TYPED = {"name": "佐藤次郎", "id": None, "verified": False}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("EXTRACTION_REVIEW_MAX_CONFIDENCE", raising=False)
    monkeypatch.delenv("EXTRACTION_REQUIRE_APPROVAL", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "APPROVAL_REQUIRE_SSO", False, raising=False)
    pages = {1: {"page": 1, "dpi": 300, "engine": "f", "width_px": 1, "height_px": 1, "confidence": 40.0,
                 "lines": [{"text": "毎月25日とする。", "confidence": 30.0, "bbox": [0, 0, 1, 1]}]}}
    store.save_record(store.build_record(TENANT, DOC, "h", pages, now=T0))
    return tmp_path


def _pages():
    return [{"page": 1, "text": "毎月25日とする。", "extraction_method": "ocr_fallback"}]


def _apply():
    return review.apply_corrections(_pages(), TENANT, DOC)


def test_without_the_setting_a_correction_applies_at_once_as_before(env):
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    out, stats = _apply()
    assert out[0]["text"] == "毎月20日とする。" and stats["applied"] == 1


def test_with_the_setting_an_unapproved_correction_is_not_applied(env, monkeypatch):
    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    out, stats = _apply()
    assert out[0]["text"] == "毎月25日とする。" and stats["applied"] == 0 and stats["awaiting_approval"] == 1


def test_a_different_person_approves_and_the_correction_applies(env, monkeypatch):
    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    entry = review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB, now=T0)
    assert entry["approved_by"] == "佐藤次郎" and entry["approved_at"] == T0.isoformat()
    out, stats = _apply()
    assert out[0]["text"] == "毎月20日とする。" and stats["applied"] == 1


def test_the_author_cannot_approve_their_own_correction(env):
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    with pytest.raises(store.ExtractionError, match="own"):
        review.approve_correction(TENANT, DOC, 1, "p1-l1", by=ALICE, now=T0)
    with pytest.raises(store.ExtractionError, match="own"):
        review.approve_correction(TENANT, DOC, 1, "p1-l1", by={"name": " 山田太郎 ", "id": None, "verified": False}, now=T0)


def test_typed_names_are_compared_when_nobody_is_identified(env):
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE_TYPED, now=T0)
    with pytest.raises(store.ExtractionError, match="own"):
        review.approve_correction(TENANT, DOC, 1, "p1-l1", by=ALICE_TYPED, now=T0)
    review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB_TYPED, now=T0)


def test_with_require_sso_only_identified_people_may_approve(env, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_REQUIRE_SSO", True, raising=False)
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE_TYPED, now=T0)
    with pytest.raises(store.ExtractionError, match="single sign-on"):
        review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB, now=T0)  # the author was not identified
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    with pytest.raises(store.ExtractionError, match="single sign-on"):
        review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB_TYPED, now=T0)
    review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB, now=T0)


def test_changing_a_correction_after_approval_needs_a_new_approval(env, monkeypatch):
    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB, now=T0)
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月21日とする。", by=ALICE, now=T0)
    out, stats = _apply()
    assert out[0]["text"] == "毎月25日とする。" and stats["awaiting_approval"] == 1


def test_only_a_correction_can_be_approved(env):
    review.confirm_line(TENANT, DOC, 1, "p1-l1", by=ALICE, now=T0)
    with pytest.raises(store.ExtractionError, match="correction"):
        review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB, now=T0)
    with pytest.raises(store.ExtractionNotFound):
        review.approve_correction(TENANT, DOC, 1, "p9-l9", by=BOB, now=T0)


def test_the_document_row_counts_corrections_awaiting_approval(env, monkeypatch):
    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE, now=T0)
    assert review.documents(TENANT)[0]["awaiting_approval"] == 1
    review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB, now=T0)
    assert review.documents(TENANT)[0]["awaiting_approval"] == 0


def test_plain_string_names_still_work(env):
    entry = review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by="山田太郎", now=T0)
    assert entry["by"] == "山田太郎" and entry.get("by_verified") is False


# --- the route ---------------------------------------------------------------------------------------------------------------------------


def test_the_approve_route(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    token = "installation-admin-token-not-a-secret-0123"
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", token)
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.setenv("EXTRACTION_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    pages = {1: {"page": 1, "dpi": 300, "engine": "f", "width_px": 1, "height_px": 1, "confidence": 40.0,
                 "lines": [{"text": "毎月25日とする。", "confidence": 30.0, "bbox": [0, 0, 1, 1]}]}}
    store.save_record(store.build_record("tenant_a", DOC, "h", pages, now=T0))
    from webapi import main

    client = TestClient(main.app)
    headers = {"x-admin-token": token}
    body = {"tenant_id": "tenant_a", "source_doc": DOC, "page": 1, "line_id": "p1-l1"}
    assert client.post("/admin/extraction/correct", headers=headers, json={**body, "text": "毎月20日とする。", "actor": "山田太郎"}).status_code == 200
    assert client.post("/admin/extraction/approve", headers=headers, json=body).status_code == 400  # no approver name
    own = client.post("/admin/extraction/approve", headers=headers, json={**body, "actor": "山田太郎"})
    assert own.status_code == 400 and "own" in own.json()["detail"]
    ok = client.post("/admin/extraction/approve", headers=headers, json={**body, "actor": "佐藤次郎"})
    assert ok.status_code == 200 and ok.json()["approved_by"] == "佐藤次郎"
    assert client.post("/admin/extraction/approve", headers=headers, json={**body, "page": 9, "actor": "佐藤次郎"}).status_code == 404


# --- what the console needs to show ---------------------------------------------------------------------------------------------------------


def test_the_page_data_says_who_corrected_and_whether_it_is_approved(env, monkeypatch):
    from webapi.routers import extraction as route

    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE_TYPED, now=T0)
    data = route.extraction_page_data(source_doc=DOC, page=1, tenant_id=TENANT, request=None)
    assert data["require_approval"] is True
    line = data["lines"][0]
    assert line["state"] == "corrected" and line["approval"] == "awaiting"
    assert line["corrected_by"] == "山田太郎" and line["approved_by"] is None
    review.approve_correction(TENANT, DOC, 1, "p1-l1", by=BOB_TYPED, now=T0)
    line = route.extraction_page_data(source_doc=DOC, page=1, tenant_id=TENANT, request=None)["lines"][0]
    assert line["approval"] == "approved" and line["approved_by"] == "佐藤次郎"


def test_without_the_setting_nothing_waits_for_approval(env):
    from webapi.routers import extraction as route

    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE_TYPED, now=T0)
    data = route.extraction_page_data(source_doc=DOC, page=1, tenant_id=TENANT, request=None)
    assert data["require_approval"] is False and data["lines"][0]["approval"] is None


def test_open_and_confirmed_lines_have_no_approval_state(env, monkeypatch):
    from webapi.routers import extraction as route

    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.confirm_line(TENANT, DOC, 1, "p1-l1", by=ALICE_TYPED, now=T0)
    assert route.extraction_page_data(source_doc=DOC, page=1, tenant_id=TENANT, request=None)["lines"][0]["approval"] is None


def test_review_items_carry_the_approval_state(env, monkeypatch):
    monkeypatch.setenv("EXTRACTION_REQUIRE_APPROVAL", "true")
    review.submit_correction(TENANT, DOC, 1, "p1-l1", "毎月20日とする。", by=ALICE_TYPED, now=T0)
    item = review.review_items(TENANT, include_reviewed=True)[0]
    assert item["state"] == "corrected" and item["approval"] == "awaiting"
