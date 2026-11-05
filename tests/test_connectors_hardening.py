"""PR G: SharePoint download integrity and partial progress, Outlook idempotency and limits. Synthetic data only."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

import config
from rag_core import contracts, source_registry as registry
from rag_core.connectors import outlook
from rag_core.connectors import sharepoint as sp

DRIVE, FOLDER = "d1", "/Shared Documents/Policies"
ROOT = f"{sp.GRAPH}/drives/{DRIVE}/root/delta"
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
TENANT = "tenant_a"


class Graph:
    def __init__(self):
        self.routes, self.calls = {}, []

    def on(self, url, *responses):
        self.routes[url] = list(responses)

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url))
        queue = self.routes[url]
        status, payload, hdrs = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        return status, payload, hdrs


def _client(graph):
    return sp.GraphClient(lambda: "tok", graph, sleep=lambda s: None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    roots = tmp_path / "ingest"
    roots.mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(roots / "state"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", [])
    return roots


def _record():
    return registry.create("sp1", tenant_id=TENANT, kind="sharepoint",
                           options={"drive_id": DRIVE, "folder": FOLDER}, created_by="山田太郎")


def _item(item_id, name, data, sha=None):
    hashes = {"quickXorHash": "q"}
    if sha is not None:
        hashes["sha256Hash"] = sha
    return {"id": item_id, "name": name, "size": len(data), "file": {"hashes": hashes},
            "parentReference": {"path": f"/drives/{DRIVE}/root:{FOLDER}"},
            "@microsoft.graph.downloadUrl": f"https://dl.example/{item_id}"}


def _feed(graph, items, final="https://graph.example/d1"):
    graph.on(ROOT, (200, {"value": items, "@odata.deltaLink": final}, {}))


def _state(env):
    return json.loads((env / "state" / "state" / "sp1.sharepoint.json").read_text(encoding="utf-8"))


def test_a_download_whose_hash_does_not_match_is_not_mirrored(env):
    good, bad = b"%PDF good", b"%PDF tampered"
    graph = Graph()
    _feed(graph, [_item("f1", "a.pdf", good, hashlib.sha256(good).hexdigest().upper()),
                  _item("f2", "b.pdf", bad, hashlib.sha256(b"something else").hexdigest().upper())])
    graph.on("https://dl.example/f1", (200, good, {}))
    graph.on("https://dl.example/f2", (200, bad, {}))
    summary = sp.pull(_record(), client=_client(graph))
    mirror = registry.mirror_dir("sp1")
    assert (mirror / "a.pdf").is_file() and not (mirror / "b.pdf").exists()
    assert summary["downloaded"] == 1 and summary["corrupt"] == 1
    assert "f2" not in _state(env)["items"]


def test_items_without_a_sha256_are_still_mirrored(env):
    data = b"%PDF plain"
    graph = Graph()
    _feed(graph, [_item("f1", "a.pdf", data)])
    graph.on("https://dl.example/f1", (200, data, {}))
    summary = sp.pull(_record(), client=_client(graph))
    assert summary["downloaded"] == 1 and "corrupt" not in summary


def test_a_failure_halfway_keeps_what_was_downloaded(env):
    one, two = b"%PDF one", b"%PDF two"
    graph = Graph()
    _feed(graph, [_item("f1", "a.pdf", one), _item("f2", "b.pdf", two)])
    graph.on("https://dl.example/f1", (200, one, {}))
    graph.on("https://dl.example/f2", (500, b"boom", {}))
    record = _record()
    with pytest.raises(sp.GraphError):
        sp.pull(record, client=_client(graph))
    state = _state(env)
    assert set(state["items"]) == {"f1"} and state["delta_link"] is None  # progress kept, feed not advanced
    graph.on("https://dl.example/f2", (200, two, {}))
    before = len(graph.calls)
    sp.pull(record, client=_client(graph))
    fetched = [url for _m, url in graph.calls[before:]]
    assert "https://dl.example/f1" not in fetched and "https://dl.example/f2" in fetched


def test_a_pull_stops_at_its_time_limit_and_keeps_progress(env, monkeypatch):
    items = [_item(f"f{i}", f"{i}.pdf", b"%PDF x") for i in range(3)]
    graph = Graph()
    _feed(graph, items)
    for item in items:
        graph.on(item["@microsoft.graph.downloadUrl"], (200, b"%PDF x", {}))
    monkeypatch.setattr(config, "GRAPH_PULL_MAX_SECONDS", 10, raising=False)
    clock = iter([0.0, 1.0, 2.0, 99.0, 100.0, 101.0, 102.0])
    monkeypatch.setattr(sp, "_monotonic", lambda: next(clock))
    with pytest.raises(sp.GraphError, match="time limit"):
        sp.pull(_record(), client=_client(graph))
    assert 1 <= len(_state(env)["items"]) < 3


# --- Outlook -----------------------------------------------------------------------------------------------------------------------


class MailGraph:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url))
        return 201, json.dumps({"id": f"AAMk-{len(self.calls)}", "webLink": "w"}).encode("utf-8"), {}


def _mail_client(graph):
    return sp.GraphClient(lambda: "tok", graph, sleep=lambda s: None)


@pytest.fixture
def contract(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path / "contracts"))
    fields = {"counterparty": {"value": "株式会社サンプル"}, "title": {"value": "業務委託契約"},
              "end_date": {"value": "2027-03-31"}, "auto_renewal": {"value": True}, "renewal_notice_days": {"value": 90},
              "termination_notice_days": {"value": 30}, "contact_email": {"value": "taro@example.co.jp"}}
    contracts.import_extracted(TENANT, "c1", {"schema": contracts.EXTRACT_SCHEMA, "fields": fields},
                               created_by="山田太郎", now=T0)
    for name in fields:
        contracts.confirm_field(TENANT, "c1", name, by="佐藤次郎")


def _draft(graph, **kw):
    return outlook.draft_for_deadline(TENANT, "c1", "non_renewal_notice", client=_mail_client(graph),
                                      mailbox="legal@example.co.jp", by="山田太郎", now=T0, **kw)


def test_asking_twice_for_the_same_deadline_makes_one_draft(contract):
    graph = MailGraph()
    first = _draft(graph)
    second = _draft(graph)
    assert len(graph.calls) == 1
    assert second["draft_id"] == first["draft_id"] and second["reused"] is True
    assert "reused" not in first or first["reused"] is False


def test_a_new_draft_can_be_forced(contract):
    graph = MailGraph()
    _draft(graph)
    _draft(graph, force_new=True)
    assert len(graph.calls) == 2


def test_a_save_conflict_after_the_draft_is_retried_so_the_draft_is_recorded(contract, monkeypatch):
    real_save = contracts._save
    state = {"failed": False}

    def flaky(contract_, *, expected_rev):
        if not state["failed"]:
            state["failed"] = True
            raise contracts.ContractError("the contract was changed by someone else; reload it")
        return real_save(contract_, expected_rev=expected_rev)

    monkeypatch.setattr(contracts, "_save", flaky)
    graph = MailGraph()
    first = _draft(graph)
    history = contracts.get(TENANT, "c1")["history"]
    assert any(h["action"] == "draft_created" and h["new"] == first["draft_id"] for h in history)
    assert _draft(graph)["draft_id"] == first["draft_id"] and len(graph.calls) == 1


def test_control_characters_in_the_subject_are_refused():
    graph = MailGraph()
    for subject in ("件名\r\nBcc: x@example.co.jp", "件名\nX", "件名\x00"):
        with pytest.raises(contracts.ContractError):
            outlook.create_draft(_mail_client(graph), "a@example.co.jp", to=["b@example.co.jp"], subject=subject, body="b")
    assert graph.calls == []


def test_only_allowed_mailboxes_when_a_list_is_set(monkeypatch):
    graph = MailGraph()
    monkeypatch.setenv("OUTLOOK_ALLOWED_MAILBOXES", "Legal@example.co.jp, other@example.co.jp")
    outlook.create_draft(_mail_client(graph), "legal@example.co.jp", to=["b@example.co.jp"], subject="s", body="b")
    with pytest.raises(contracts.ContractError, match="not allowed"):
        outlook.create_draft(_mail_client(graph), "boss@example.co.jp", to=["b@example.co.jp"], subject="s", body="b")
    assert len(graph.calls) == 1


# --- access model: SharePoint permissions are not carried into search, so the source must say so ------------------------------


def test_the_access_model_must_be_a_known_value(env):
    with pytest.raises(registry.SourceError, match="access_model"):
        registry.create("sp2", tenant_id=TENANT, kind="sharepoint", created_by="山田太郎",
                        options={"drive_id": DRIVE, "folder": FOLDER, "access_model": "per_user"})


def test_production_refuses_a_sharepoint_source_without_a_tenant_wide_declaration(env, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(registry.SourceError, match="tenant_wide"):
        registry.create("sp2", tenant_id=TENANT, kind="sharepoint", created_by="山田太郎",
                        options={"drive_id": DRIVE, "folder": FOLDER})
    record = registry.create("sp2", tenant_id=TENANT, kind="sharepoint", created_by="山田太郎",
                             options={"drive_id": DRIVE, "folder": FOLDER, "access_model": "tenant_wide"})
    assert record["options"]["access_model"] == "tenant_wide"


def test_production_pull_refuses_a_source_registered_before_the_declaration_existed(env, monkeypatch):
    record = _record()  # created outside production, without the option
    monkeypatch.setenv("APP_ENV", "production")
    graph = Graph()
    _feed(graph, [])
    with pytest.raises(registry.SourceError, match="tenant_wide"):
        sp.pull(record, client=_client(graph))
    assert graph.calls == []  # nothing was fetched


def test_development_is_unchanged_without_the_option(env, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    graph = Graph()
    _feed(graph, [])
    sp.pull(_record(), client=_client(graph))


# --- defaults: bounded unless an operator says otherwise ----------------------------------------------------------------------------


def test_a_sharepoint_pull_has_a_default_time_limit():
    assert config.GRAPH_PULL_MAX_SECONDS == 1800 and sp._pull_max_seconds() == 1800.0


def test_production_needs_a_mailbox_allow_list(monkeypatch):
    graph = MailGraph()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OUTLOOK_ALLOWED_MAILBOXES", raising=False)
    with pytest.raises(contracts.ContractError, match="OUTLOOK_ALLOWED_MAILBOXES"):
        outlook.create_draft(_mail_client(graph), "legal@example.co.jp", to=["b@example.co.jp"], subject="s", body="b")
    assert graph.calls == []
    monkeypatch.setenv("OUTLOOK_ALLOWED_MAILBOXES", "legal@example.co.jp")
    outlook.create_draft(_mail_client(graph), "legal@example.co.jp", to=["b@example.co.jp"], subject="s", body="b")
    assert len(graph.calls) == 1
