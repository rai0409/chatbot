from __future__ import annotations

from webapi import main


def test_product_preview_page_returns_html():
    response = main.product_preview_page()

    assert response.status_code == 200
    assert b"Product Preview Chat" in response.body


def test_product_preview_page_references_product_preview_and_feedback_apis():
    response = main.product_preview_page()
    body = response.body.decode("utf-8")

    assert "/chat/product-preview" in body
    assert "/chat/feedback" in body
    assert "approved_similar_candidate_only" in body
    assert "This is a candidate preview, not a final automatic answer." in body
