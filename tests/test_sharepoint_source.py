import json

import pytest

import config
from rag_core import change_ledger, integrations, source_registry as registry, source_sync
from rag_core.connectors import sharepoint as sp
from rag_core.source_registry import SourceError
from scripts import sources as cli

DRIVE, FOLDER = "d1", "/Shared Documents/Policies"
ROOT = f"{sp.GRAPH}/drives/{DRIVE}/root/delta"


class Graph:
    """A fake transport: url -> a list of (status, payload, headers) answered in order (the last one repeats)."""

    def __init__(self):
        self.routes, self.calls = {}, []

    def on(self, url, *responses):
        self.routes[url] = list(responses)

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers)))
        queue = self.routes[url]
        status, payload, response_headers = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        return status, payload if isinstance(payload, bytes) else payload.encode("utf-8"), response_headers

    def downloads(self):
        return [url for _m, url, _h in self.calls if url.startswith("https://dl.example/")]


def ok(payload, headers=None):
    return 200, payload, headers or {}


def file_item(item_id, name, parent=FOLDER, size=10, sig="h1", download=True, **extra):
    item = {"id": item_id, "name": name, "size": size, "file": {"hashes": {"quickXorHash": sig}},
            "parentReference": {"path": f"/drives/{DRIVE}/root:{parent}"}}
    if download:
        item["@microsoft.graph.downloadUrl"] = f"https://dl.example/{item_id}-{sig}"
    item.update(extra)
    return item


@pytest.fixture
def env(monkeypatch, tmp_path):
    roots = tmp_path / "ingest"
    roots.mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(roots / "state"))
    monkeypatch.delenv("INGEST_MAX_FILE_BYTES", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", str(tmp_path / "settings"))  # the console switches of this test only
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", [])
    return roots


def _client(graph, sleeps=None):
    return sp.GraphClient(lambda: "tok-123", graph, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def _record(env, source_id="sp1"):
    return registry.create(source_id, tenant_id="tenant_a", kind="sharepoint",
                           options={"drive_id": DRIVE, "folder": FOLDER}, created_by="山田太郎")


def _feed(graph, *pages, final="https://graph.example/delta?token=1"):
    """The delta feed as pages chained by nextLink, the last one ending in a deltaLink; returns the first url."""
    urls = [ROOT] + [f"https://graph.example/page{i}" for i in range(1, len(pages))]
    for index, (url, items) in enumerate(zip(urls, pages)):
        body = {"value": items}
        if index + 1 < len(pages):
            body["@odata.nextLink"] = urls[index + 1]
        else:
            body["@odata.deltaLink"] = final
        graph.on(url, ok(body))
    return ROOT


def _body(graph, item_id, sig, data=b"%PDF"):
    graph.on(f"https://dl.example/{item_id}-{sig}", ok(data))


# --- the registration ----------------------------------------------------------------------------------------------------


def test_a_sharepoint_source_needs_a_drive_a_folder_and_a_mirror_inside_the_allowed_roots(env, monkeypatch, tmp_path):
    assert _record(env)["kind"] == "sharepoint" and registry.mirror_dir("sp1") == env / "state" / "mirror" / "sp1"
    for options, message in (({}, "options.drive_id"), ({"drive_id": "d"}, "options.folder"),
                             ({"drive_id": "d", "folder": "Docs"}, "must start with")):
        with pytest.raises(SourceError, match=message):
            registry.create("bad", tenant_id="t", kind="sharepoint", options=options)
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "outside"))
    with pytest.raises(SourceError, match="mirror folder"):
        registry.create("bad", tenant_id="t", kind="sharepoint", options={"drive_id": "d", "folder": "/Docs"})


# --- tokens and the client --------------------------------------------------------------------------------------------------


def test_a_fixed_token_or_client_credentials_and_nothing_else():
    assert sp.token_provider({"SHAREPOINT_ACCESS_TOKEN": " tok "})() == "tok"
    with pytest.raises(SourceError, match="not configured"):
        sp.token_provider({"SHAREPOINT_TENANT_ID": "t", "SHAREPOINT_CLIENT_ID": "c"})


def test_client_credentials_are_requested_once_cached_and_never_leaked():
    calls, now = [], [1000.0]

    def transport(method, url, headers, body):
        calls.append((method, url, body.decode()))
        return 200, json.dumps({"access_token": f"t{len(calls)}", "expires_in": 3600}).encode(), {}

    env = {"SHAREPOINT_TENANT_ID": "ten", "SHAREPOINT_CLIENT_ID": "cid", "SHAREPOINT_CLIENT_SECRET": "s3cr3t"}
    get = sp.token_provider(env, transport, clock=lambda: now[0])
    assert get() == "t1" and get() == "t1" and len(calls) == 1
    assert calls[0][1] == sp.TOKEN_URL.format(tenant="ten") and "grant_type=client_credentials" in calls[0][2]
    now[0] += 3600 - 59  # inside the last minute: refresh
    assert get() == "t2" and len(calls) == 2

    def failing(method, url, headers, body):
        return 401, b'{"error": "invalid_client", "error_description": "s3cr3t is wrong"}', {}

    with pytest.raises(sp.GraphError) as info:
        sp.token_provider(env, failing)()
    assert "s3cr3t" not in str(info.value) and "HTTP 401" in str(info.value)


def test_the_client_retries_transient_errors_honours_retry_after_and_gives_up(env):
    graph, sleeps = Graph(), []
    graph.on(ROOT, (429, b"", {"retry-after": "7"}), (503, b"", {}), ok({"value": [], "@odata.deltaLink": "x"}))
    pages = list(_client(graph, sleeps).delta_pages(DRIVE))
    assert len(pages) == 1 and sleeps == [7.0, 2.0] and len(graph.calls) == 3
    assert graph.calls[0][2]["Authorization"] == "Bearer tok-123"
    graph.on(ROOT, (429, b"", {"retry-after": "9999"}))
    sleeps.clear()
    with pytest.raises(sp.GraphError, match="HTTP 429"):
        list(_client(graph, sleeps).delta_pages(DRIVE))
    assert sleeps == [60.0, 60.0, 60.0]  # a long Retry-After is capped
    graph.on(ROOT, (401, b"unauthorized tok-123", {}))
    with pytest.raises(sp.GraphError) as info:
        list(_client(graph).delta_pages(DRIVE))
    assert info.value.status == 401 and "tok-123" not in str(info.value)
    graph.on(ROOT, (410, b"", {}))
    with pytest.raises(sp.ResyncRequired):
        list(_client(graph).delta_pages(DRIVE))


def test_the_feed_is_followed_over_pages_and_bounded():
    graph = Graph()
    graph.on(ROOT, ok({"value": [1], "@odata.nextLink": "https://graph.example/p2"}))
    graph.on("https://graph.example/p2", ok({"value": [2], "@odata.nextLink": "https://graph.example/p2"}))
    with pytest.raises(sp.GraphError, match="too many pages"):
        list(sp.GraphClient(lambda: "t", graph, max_pages=5).delta_pages(DRIVE))
    graph.on("https://graph.example/delta2", ok({"value": [3], "@odata.deltaLink": "z"}))
    assert [p["value"] for p in sp.GraphClient(lambda: "t", graph).delta_pages(DRIVE, "https://graph.example/delta2")] == [[3]]


def test_a_download_uses_no_authorization_header_and_respects_the_limit():
    graph = Graph()
    graph.on("https://dl.example/x", ok(b"12345"))
    client = sp.GraphClient(lambda: "tok-123", graph)
    assert client.download("https://dl.example/x", max_bytes=10) == b"12345"
    assert "Authorization" not in graph.calls[-1][2]
    with pytest.raises(sp.GraphError, match="larger than the limit"):
        client.download("https://dl.example/x", max_bytes=3)


@pytest.mark.parametrize("raw, expected", [
    ("a.pdf", "a.pdf"), ("sub/dir/a.pdf", "sub/dir/a.pdf"), ("日本語/報告書.pdf", "日本語/報告書.pdf"),
    ("../a.pdf", None), ("a/../../b.pdf", None), ("/abs.pdf", None), ("a\\b.pdf", None), ("a//b.pdf", None), ("./a.pdf", None),
    ("a/\x00b.pdf", None), ("", None), ("a/./b.pdf", None),
])
def test_only_plain_relative_paths_are_accepted(raw, expected):
    assert sp.safe_relative(raw) == expected


# --- pulling --------------------------------------------------------------------------------------------------------------------


def test_the_first_pull_mirrors_the_supported_files_of_the_folder_and_keeps_the_delta_link(env):
    graph = Graph()
    items1 = [
        file_item("f1", "a.pdf"), {"id": "dir", "name": "sub", "folder": {}, "parentReference": {"path": f"/drives/{DRIVE}/root:{FOLDER}"}},
        file_item("f2", "b.docx", parent=f"{FOLDER}/sub"), file_item("f3", "c.pdf", parent="/Shared Documents/Other"),
        file_item("f4", "notes.xyz"), file_item("f5", "big.pdf", size=10 ** 9), file_item("f6", ".hidden.pdf"),
        file_item("f8", "~$lock.docx"), file_item("f9", "..", parent=FOLDER),
    ]
    _feed(graph, items1, [file_item("f7", "d.pdf")], final="https://graph.example/delta?token=A")
    for item_id in ("f1", "f2", "f7"):
        _body(graph, item_id, "h1", f"data-{item_id}".encode())
    record = _record(env)
    summary = sp.pull(record, client=_client(graph))
    mirror = registry.mirror_dir("sp1")
    assert sorted(str(p.relative_to(mirror)) for p in mirror.rglob("*") if p.is_file()) == ["a.pdf", "d.pdf", "sub/b.docx"]
    assert (mirror / "sub" / "b.docx").read_bytes() == b"data-f2"
    assert summary == {"downloaded": 3, "removed": 0, "unsupported": 1, "too_large": 1, "unsafe": 0, "hidden": 3, "unchanged": 0,
                       "resync": False, "pages": 2}
    state = json.loads((env / "state" / "state" / "sp1.sharepoint.json").read_text(encoding="utf-8"))
    assert state["delta_link"] == "https://graph.example/delta?token=A" and sorted(state["items"]) == ["f1", "f2", "f7"]
    assert "tok-123" not in json.dumps(state) and "Authorization" not in json.dumps(state)


def test_the_next_pull_starts_from_the_delta_link_and_applies_changes_moves_and_deletions(env):
    graph = Graph()
    _feed(graph, [file_item("f1", "a.pdf"), file_item("f2", "b.pdf"), file_item("f3", "c.pdf")], final="https://graph.example/d1")
    for item_id in ("f1", "f2", "f3"):
        _body(graph, item_id, "h1")
    record = _record(env)
    sp.pull(record, client=_client(graph))
    mirror = registry.mirror_dir("sp1")

    graph = Graph()
    graph.on("https://graph.example/d1", ok({"value": [
        file_item("f1", "a.pdf", sig="h2"),                          # modified
        {"id": "f2", "deleted": {}},                                  # deleted (no name, no parent)
        file_item("f3", "c-renamed.pdf", sig="h1"),                   # renamed
        file_item("f4", "e.pdf", parent="/Shared Documents/Other"),   # outside the folder: ignored
    ], "@odata.deltaLink": "https://graph.example/d2"}))
    _body(graph, "f1", "h2", b"new")
    _body(graph, "f3", "h1")
    summary = sp.pull(record, client=_client(graph))
    assert sorted(p.name for p in mirror.iterdir()) == ["a.pdf", "c-renamed.pdf"] and (mirror / "a.pdf").read_bytes() == b"new"
    assert (summary["downloaded"], summary["removed"], summary["resync"]) == (2, 1, False) and graph.calls[0][1] == "https://graph.example/d1"

    graph = Graph()
    graph.on("https://graph.example/d2", ok({"value": [file_item("f1", "a.pdf", parent="/Shared Documents/Other", sig="h2")],
                                             "@odata.deltaLink": "https://graph.example/d3"}))
    again = sp.pull(record, client=_client(graph))
    assert sorted(p.name for p in mirror.iterdir()) == ["c-renamed.pdf"] and again["removed"] == 1  # moved out of the folder = deleted


def test_a_file_that_did_not_change_is_not_downloaded_again(env):
    graph = Graph()
    _feed(graph, [file_item("f1", "a.pdf")], final="https://graph.example/d1")
    _body(graph, "f1", "h1")
    record = _record(env)
    sp.pull(record, client=_client(graph))
    graph.calls.clear()
    graph.on("https://graph.example/d1", ok({"value": [file_item("f1", "a.pdf")], "@odata.deltaLink": "https://graph.example/d2"}))
    summary = sp.pull(record, client=_client(graph))
    assert summary["unchanged"] == 1 and summary["downloaded"] == 0 and graph.downloads() == []
    registry.mirror_dir("sp1").joinpath("a.pdf").unlink()  # the copy was lost: it is fetched again
    graph.on("https://graph.example/d2", ok({"value": [file_item("f1", "a.pdf")], "@odata.deltaLink": "https://graph.example/d3"}))
    assert sp.pull(record, client=_client(graph))["downloaded"] == 1


def test_an_expired_delta_link_starts_again_and_removes_what_the_library_no_longer_has(env):
    graph = Graph()
    _feed(graph, [file_item("f1", "a.pdf"), file_item("f2", "b.pdf")], final="https://graph.example/old")
    _body(graph, "f1", "h1")
    _body(graph, "f2", "h1")
    record = _record(env)
    sp.pull(record, client=_client(graph))

    graph = Graph()
    graph.on("https://graph.example/old", (410, b"", {}))
    _feed(graph, [file_item("f1", "a.pdf")], final="https://graph.example/new")  # b.pdf is gone from the library
    summary = sp.pull(record, client=_client(graph))
    mirror = registry.mirror_dir("sp1")
    assert summary["resync"] is True and sorted(p.name for p in mirror.iterdir()) == ["a.pdf"] and summary["removed"] == 1
    assert json.loads((env / "state" / "state" / "sp1.sharepoint.json").read_text(encoding="utf-8"))["delta_link"] == "https://graph.example/new"


def test_unsafe_names_never_reach_the_mirror(env):
    graph = Graph()
    _feed(graph, [file_item("f1", "x\\evil.pdf"), file_item("f2", "ok.pdf", parent=f"{FOLDER}/../up"), file_item("f3", "a\x00.pdf"),
                  file_item("f4", "fine.pdf")])
    _body(graph, "f4", "h1")
    summary = sp.pull(_record(env), client=_client(graph))
    mirror = registry.mirror_dir("sp1")
    assert [p.name for p in mirror.iterdir()] == ["fine.pdf"] and summary["unsafe"] == 3
    assert not (env / "evil.pdf").exists() and not (env / "state" / "up").exists()


def test_a_failed_pull_leaves_the_state_where_it_was(env):
    graph = Graph()
    _feed(graph, [file_item("f1", "a.pdf")], final="https://graph.example/d1")
    _body(graph, "f1", "h1")
    record = _record(env)
    sp.pull(record, client=_client(graph))
    before = (env / "state" / "state" / "sp1.sharepoint.json").read_text(encoding="utf-8")
    graph = Graph()
    graph.on("https://graph.example/d1", (500, b"boom", {}))
    with pytest.raises(sp.GraphError, match="HTTP 500"):
        sp.pull(record, client=_client(graph))
    assert (env / "state" / "state" / "sp1.sharepoint.json").read_text(encoding="utf-8") == before
    (env / "state" / "state" / "sp1.sharepoint.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(SourceError, match="unreadable"):
        sp.pull(record, client=_client(graph))


# --- through the sync -------------------------------------------------------------------------------------------------------------


def test_a_scan_pulls_the_library_and_then_scans_the_mirror(env, monkeypatch):
    graph = Graph()
    _feed(graph, [file_item("f1", "a.pdf"), file_item("f2", "b.pdf")])
    _body(graph, "f1", "h1")
    _body(graph, "f2", "h1")
    monkeypatch.setattr(sp, "default_client", lambda: _client(graph))
    _record(env)
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["sharepoint"])  # environment layer
    integrations.set_switch("sharepoint", True, by="山田太郎", reason="test")  # console switch
    out = source_sync.scan_source("sp1")
    assert out["counts"] == {"added": 2, "modified": 0, "deleted": 0} and out["total_files"] == 2
    pulled = change_ledger.read_entries("sp1")[0]["summary"]["pulled"]
    assert pulled["downloaded"] == 2 and "a.pdf" not in json.dumps(pulled)
    assert sorted(registry.load_manifest("sp1", "observed")) == ["a.pdf", "b.pdf"]


def test_a_scan_is_recorded_as_failed_and_never_as_deleted_while_the_integration_is_off(env, monkeypatch):
    monkeypatch.setattr(sp, "default_client", lambda: pytest.fail("the connector must not be reached while it is off"))
    _record(env)
    registry.save_manifest("sp1", "applied", {"a.pdf": {"sha256": "x", "size": 1}})
    out = source_sync.scan_source("sp1")  # no layer allows it: the environment layer is empty
    assert out["error"] == "integration_disabled" and out["reason"] == "env_not_allowed"
    entry = change_ledger.read_entries("sp1")[0]
    assert entry["kind"] == "scan_failed" and entry["summary"]["reason"] == "integration_off:env_not_allowed"
    assert registry.get("sp1")["state"]["last_error"] == "integration_off:env_not_allowed"
    assert sorted(registry.load_manifest("sp1", "applied")) == ["a.pdf"]  # nothing was treated as deleted


def test_the_command_line_registers_a_sharepoint_source(env, capsys):
    code = cli.main(["add", "--id", "sp2", "--tenant", "tenant_a", "--kind", "sharepoint", "--drive-id", "d9",
                     "--folder", "/Shared Documents/Rules", "--schedule", "30", "--by", "田中太郎"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["kind"] == "sharepoint" and out["options"] == {"drive_id": "d9", "folder": "/Shared Documents/Rules"}
    assert out["path"] is None and out["collection"] == "src-sp2"
    assert cli.main(["add", "--id", "sp3", "--tenant", "t", "--kind", "sharepoint"]) == 3
    assert "options.drive_id" in json.loads(capsys.readouterr().out)["error"]
