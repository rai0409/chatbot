"""S1: the built console is served under /console/, safely."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from webapi import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>console</title>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build", encoding="utf-8")
    monkeypatch.setenv("CONSOLE_DIST_DIR", str(dist))
    return TestClient(main.app), tmp_path


def test_the_shell_and_the_assets_are_served_with_the_right_caching(client):
    c, _tmp = client
    shell = c.get("/console/")
    assert shell.status_code == 200 and "console" in shell.text and shell.headers["cache-control"] == "no-store"
    asset = c.get("/console/assets/app-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert c.get("/console", follow_redirects=False).status_code == 307


def test_a_route_of_the_app_gets_the_shell_so_a_reload_works(client):
    c, _tmp = client
    page = c.get("/console/system")
    assert page.status_code == 200 and "<title>console</title>" in page.text


def test_a_missing_file_is_not_the_app(client):
    c, _tmp = client
    assert c.get("/console/assets/missing.js").status_code == 404


def test_nothing_outside_the_build_is_served(client):
    c, _tmp = client
    for path in ("/console/../secret.txt", "/console/%2e%2e/secret.txt", "/console/..%2fsecret.txt", "/console/assets/../../secret.txt"):
        response = c.get(path)
        assert "outside the build" not in response.text, path


def test_without_a_build_the_console_says_so(monkeypatch, tmp_path):
    from webapi import main

    monkeypatch.setenv("CONSOLE_DIST_DIR", str(tmp_path / "nothing"))
    response = TestClient(main.app).get("/console/")
    assert response.status_code == 404 and "not built" in response.json()["detail"]
