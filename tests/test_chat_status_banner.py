"""S5: the chat screen says when the AI that writes the answers is unavailable, and what it asks the server for that."""
from pathlib import Path

from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "webapi" / "static"


def _page():
    from webapi import main

    client = TestClient(main.app)
    response = client.get("/chat-ui")
    assert response.status_code == 200
    return response.text


def test_the_chat_page_has_the_banner_and_asks_the_status_route():
    html = _page()
    assert 'id="serviceBanner"' in html and 'role="status"' in html
    assert "/system/status?tenant_id=" in html and "service_notice" in html
    assert "AI による文章生成" in html  # the wording the user sees


def test_the_banner_is_hidden_until_the_server_says_the_service_is_degraded():
    html = _page()
    assert 'class="service-banner" role="status" hidden' in html
    assert 's.service === "degraded"' in html  # only the code decides; nothing else is shown


def test_the_older_admin_pages_link_to_the_new_console():
    assert 'href="/console/"' in (STATIC / "admin_nav.html").read_text(encoding="utf-8")
