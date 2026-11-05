import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import config
from rag_core import change_ledger, source_registry as registry, source_scan, source_sync
from rag_core.source_registry import SourceError, SourceUnavailable
from scripts import sources as cli

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path):
    roots = tmp_path / "ingest"
    (roots / "policies").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("INGEST_MAX_FILE_BYTES", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    # the tests write small text files: let them count as supported (the real list is the converters' formats)
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"txt", "md", "pdf", "docx"}))
    return roots


def _write(folder, rel, text="本文"):
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _create(env, source_id="policies", **extra):
    kwargs = {"tenant_id": "tenant_a", "path": str(env / "policies"), "created_by": "山田太郎", "now": T0}
    kwargs.update(extra)
    return registry.create(source_id, **kwargs)


# --- the registry ------------------------------------------------------------------------------------------


def test_a_folder_source_is_registered_with_its_defaults(env):
    record = _create(env)
    assert record["collection"] == "src-policies" and record["enabled"] is True and record["schedule_minutes"] == 0
    assert record["path"] == str((env / "policies").resolve()) and record["created_by"] == "山田太郎"
    assert registry.get("policies") == record and [r["source_id"] for r in registry.list_sources()] == ["policies"]
    assert registry.list_sources("tenant_b") == [] and registry.root_of(record) == env / "policies"


@pytest.mark.parametrize("source_id", ["", "Upper", "a b", "../x", "x" * 49, "日本語", "-lead"])
def test_unsafe_source_ids_are_refused(env, source_id):
    with pytest.raises(SourceError, match="invalid source id"):
        _create(env, source_id)
    with pytest.raises(SourceError):
        registry.get(source_id)


def test_the_folder_must_be_a_real_directory_inside_the_allowed_roots(env, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(SourceError, match="outside the allowed"):
        _create(env, "o1", path=str(outside))
    with pytest.raises(SourceError, match="outside the allowed"):
        _create(env, "o2", path=str(env / ".." / "elsewhere"))
    with pytest.raises(SourceError, match="does not exist"):
        _create(env, "o3", path=str(env / "missing"))
    (env / "a-file").write_text("x", encoding="utf-8")
    with pytest.raises(SourceError, match="does not exist"):
        _create(env, "o4", path=str(env / "a-file"))
    os.symlink(outside, env / "link")
    with pytest.raises(SourceError, match="symlinked"):
        _create(env, "o5", path=str(env / "link"))
    with pytest.raises(SourceError, match="a folder source needs a path"):
        _create(env, "o6", path=None)
    assert registry.list_sources() == []


def test_without_ingest_roots_nothing_can_be_registered(env, monkeypatch):
    monkeypatch.delenv("INGEST_ALLOWED_ROOTS")
    with pytest.raises(SourceError, match="not configured"):
        _create(env)


def test_the_collection_is_a_staging_name_and_never_the_served_one(env, monkeypatch):
    monkeypatch.setattr(config, "resolve_chroma_collection_name", lambda explicit=None: "served")
    with pytest.raises(SourceError, match="never the served"):
        _create(env, "s1", collection="served")
    with pytest.raises(SourceError, match="invalid collection"):
        _create(env, "s2", collection="../x")
    assert _create(env, "s3", collection="my-staging")["collection"] == "my-staging"


@pytest.mark.parametrize("extra, message", [
    ({"tenant_id": "bad/tenant"}, "invalid tenant"), ({"kind": "ftp"}, "unknown kind"),
    ({"schedule_minutes": -1}, "schedule_minutes"), ({"schedule_minutes": 10081}, "schedule_minutes"),
    ({"schedule_minutes": True}, "schedule_minutes"), ({"schedule_minutes": 1.5}, "schedule_minutes"),
])
def test_other_input_is_validated(env, extra, message):
    with pytest.raises(SourceError, match=message):
        _create(env, **extra)


def test_a_duplicate_id_enable_disable_and_remove(env):
    _create(env)
    with pytest.raises(SourceError, match="already exists"):
        _create(env)
    assert registry.set_enabled("policies", False)["enabled"] is False and registry.get("policies")["enabled"] is False
    registry.update_state("policies", last_error="x")
    with pytest.raises(SourceError, match="unknown state field"):
        registry.update_state("policies", path="/etc")
    registry.save_manifest("policies", "applied", {"a.txt": {"sha256": "s"}})
    registry.remove("policies")
    assert registry.list_sources() == [] and registry.load_manifest("policies", "applied") == {"a.txt": {"sha256": "s"}}
    with pytest.raises(SourceError, match="not found"):
        registry.remove("policies")


def test_a_broken_registry_is_reported_and_a_stale_write_is_refused(env):
    _create(env)
    path = registry._registry_file()
    stale = registry._load()
    registry.set_enabled("policies", False)
    with pytest.raises(SourceError, match="changed by someone else"):
        registry._save(stale)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SourceError, match="unreadable"):
        registry.list_sources()
    path.write_text(json.dumps({"schema": 9}), encoding="utf-8")
    with pytest.raises(SourceError, match="unsupported format"):
        registry.get("policies")


def test_manifests_are_stored_per_source_and_kind(env):
    with pytest.raises(SourceError, match="unknown manifest"):
        registry.load_manifest("policies", "other")
    assert registry.load_manifest("policies", "observed") == {}
    registry.save_manifest("policies", "observed", {"x.pdf": {"size": 1}})
    assert registry.load_manifest("policies", "observed") == {"x.pdf": {"size": 1}} and registry.load_manifest("policies", "applied") == {}


# --- scanning --------------------------------------------------------------------------------------------------


def test_a_scan_lists_supported_files_with_hashes_and_counts_what_it_skips(env):
    folder = env / "policies"
    _write(folder, "a.txt", "A")
    _write(folder, "sub/b.md", "B")
    _write(folder, ".hidden.txt")
    _write(folder, "~$lock.docx")
    _write(folder, "c.tmp")
    _write(folder, "data.xyz")
    _write(folder, ".git/x.txt")
    os.symlink(folder / "a.txt", folder / "alias.txt")
    result = source_scan.scan_folder(folder)
    assert sorted(result.manifest) == ["a.txt", "sub/b.md"]
    assert result.manifest["a.txt"]["sha256"] == source_scan.sha256_of(folder / "a.txt") and result.hashed == 2
    assert result.skipped["unsupported"] == 1 and result.skipped["symlink"] == 1 and result.skipped["hidden"] == 4


def test_a_file_whose_size_and_time_are_unchanged_is_not_read_again(env):
    folder = env / "policies"
    _write(folder, "a.txt", "A")
    _write(folder, "b.txt", "B")
    first = source_scan.scan_folder(folder)
    reads = []
    again = source_scan.scan_folder(folder, previous=first.manifest, hasher=lambda p: reads.append(p.name) or "x")
    assert reads == [] and again.manifest == first.manifest and again.hashed == 0
    _write(folder, "b.txt", "BB")
    third = source_scan.scan_folder(folder, previous=first.manifest, hasher=lambda p: reads.append(p.name) or source_scan.sha256_of(p))
    assert reads == ["b.txt"] and third.manifest["b.txt"]["sha256"] != first.manifest["b.txt"]["sha256"]


def test_large_files_are_skipped_and_a_missing_folder_is_unavailable_not_empty(env, monkeypatch):
    folder = env / "policies"
    _write(folder, "big.txt", "x" * 20)
    _write(folder, "small.txt", "x")
    monkeypatch.setenv("INGEST_MAX_FILE_BYTES", "10")
    result = source_scan.scan_folder(folder)
    assert list(result.manifest) == ["small.txt"] and result.skipped["too_large"] == ["big.txt"]
    with pytest.raises(SourceUnavailable):
        source_scan.scan_folder(env / "not-mounted")


def test_the_supported_types_are_those_of_the_converters(monkeypatch):
    monkeypatch.undo()
    assert source_scan.supported_extensions() >= {"pdf", "docx", "xlsx", "pptx", "csv"}


def test_the_diff_names_added_modified_and_deleted_and_the_name_conflicts():
    applied = {"a": {"sha256": "1"}, "b": {"sha256": "2"}, "c": {"sha256": "3"}}
    observed = {"a": {"sha256": "1", "mtime_ns": 5}, "b": {"sha256": "9"}, "d": {"sha256": "4"}}
    assert source_scan.diff_manifests(applied, observed) == {"added": ["d"], "modified": ["b"], "deleted": ["c"]}
    assert source_scan.diff_manifests({}, {}) == {"added": [], "modified": [], "deleted": []}
    assert source_scan.name_conflicts(["x/a.pdf", "y/a.pdf", "b.pdf", "z/b.txt"]) == {"a.pdf": ["x/a.pdf", "y/a.pdf"]}
    assert source_scan.document_name("x/y/z.pdf") == "z.pdf" and source_scan.document_name("z.pdf") == "z.pdf"


# --- the ledger --------------------------------------------------------------------------------------------------


def test_the_ledger_chains_entries_stores_the_full_changes_and_finds_tampering(env):
    changes = [{"path": f"f{i}.txt", "op": "added"} for i in range(150)]
    first = change_ledger.record("scan", source_id="s", tenant_id="t", summary={"added": 150}, changes=changes, now=T0)
    second = change_ledger.record("apply", source_id="s", tenant_id="t", summary={"ingested": 150}, now=T0)
    assert first["prev"] == change_ledger.GENESIS and second["prev"] == first["sha256"] and second["seq"] == 2
    assert first["changes_count"] == 150 and len(first["changes_preview"]) == change_ledger.PREVIEW and "changes_sha256" not in second
    assert change_ledger.read_changes(first["run_id"]) == changes and change_ledger.verify_chain() == 2
    assert [e["kind"] for e in change_ledger.read_entries("s")] == ["scan", "apply"] and change_ledger.read_entries("other") == []
    assert len(change_ledger.read_entries(limit=1)) == 1

    path = change_ledger.ledger_file()
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('"added": 150', '"added": 1', 1), encoding="utf-8")
    with pytest.raises(change_ledger.LedgerError, match="entry 1"):
        change_ledger.verify_chain()
    with pytest.raises(change_ledger.LedgerError):
        change_ledger.record("scan", source_id="s", tenant_id="t", summary={})
    path.write_text(original, encoding="utf-8")
    change_ledger.run_file(first["run_id"]).write_text("[]\n", encoding="utf-8")
    with pytest.raises(change_ledger.LedgerError, match="does not match the ledger"):
        change_ledger.read_changes(first["run_id"])
    with pytest.raises(change_ledger.LedgerError, match="no stored changes"):
        change_ledger.read_changes(second["run_id"])
    with pytest.raises(change_ledger.LedgerError, match="invalid run id"):
        change_ledger.run_file("../x")


# --- scan_source and due_sources ---------------------------------------------------------------------------------------


def test_a_scan_records_the_pending_changes_and_does_not_apply_them(env):
    folder = env / "policies"
    _write(folder, "a.txt", "A")
    _write(folder, "b.txt", "B")
    _create(env)
    first = source_sync.scan_source("policies", now=T0)
    assert first["counts"] == {"added": 2, "modified": 0, "deleted": 0} and first["total_files"] == 2
    assert registry.load_manifest("policies", "applied") == {} and len(registry.load_manifest("policies", "observed")) == 2
    assert registry.get("policies")["state"]["last_scan_run"] == first["run_id"]

    registry.save_manifest("policies", "applied", registry.load_manifest("policies", "observed"))  # as if 5-2 had applied them
    _write(folder, "a.txt", "A2")
    (folder / "b.txt").unlink()
    _write(folder, "c.pdf", "C")
    second = source_sync.scan_source("policies", now=T0 + timedelta(hours=1))
    assert second["changes"] == {"added": ["c.pdf"], "modified": ["a.txt"], "deleted": ["b.txt"]}
    entries = change_ledger.read_entries("policies")
    assert [e["kind"] for e in entries] == ["scan", "scan"] and entries[1]["summary"]["modified"] == 1
    assert change_ledger.read_changes(second["run_id"]) == [{"path": "c.pdf", "op": "added"}, {"path": "a.txt", "op": "modified"},
                                                            {"path": "b.txt", "op": "deleted"}]
    again = source_sync.scan_source("policies", now=T0 + timedelta(hours=2))
    assert again["changes"] == second["changes"] and again["counts"] == second["counts"]  # still pending, not accumulated


def test_a_folder_that_is_not_there_is_a_failed_scan_not_a_mass_deletion(env):
    folder = env / "policies"
    _write(folder, "a.txt")
    _create(env)
    source_sync.scan_source("policies", now=T0)
    registry.save_manifest("policies", "applied", registry.load_manifest("policies", "observed"))
    os.rename(folder, env / "policies-unmounted")
    out = source_sync.scan_source("policies", now=T0 + timedelta(hours=1))
    assert out["error"] == "source_unavailable"
    assert registry.load_manifest("policies", "applied") != {} and len(registry.load_manifest("policies", "observed")) == 1
    assert registry.get("policies")["state"]["last_error"] == "source_unavailable"
    assert change_ledger.read_entries("policies")[-1]["kind"] == "scan_failed"
    os.rename(env / "policies-unmounted", folder)
    assert source_sync.scan_source("policies", now=T0 + timedelta(hours=2))["counts"]["deleted"] == 0
    assert registry.get("policies")["state"]["last_error"] is None


def test_a_remote_kind_needs_a_connector(env, monkeypatch):
    monkeypatch.setattr(registry, "_inside_allowed_roots", lambda path: True)
    registry.create("remote", tenant_id="t", kind="sharepoint", options={"drive_id": "d", "folder": "/Docs"})
    mirror = registry.mirror_dir("remote")
    calls = []
    monkeypatch.setitem(source_sync.PULLERS, "sharepoint", lambda record: calls.append(record["source_id"]) or {"pulled": 1})
    mirror.mkdir(parents=True)
    (mirror / "x.txt").write_text("x", encoding="utf-8")
    out = source_sync.scan_source("remote", now=T0)
    assert calls == ["remote"] and out["counts"]["added"] == 1
    assert change_ledger.read_entries("remote")[0]["summary"]["pulled"] == {"pulled": 1}


def test_due_sources_follow_the_schedule(env):
    _create(env, "never", schedule_minutes=60)
    _create(env, "manual", schedule_minutes=0)
    _create(env, "paused", schedule_minutes=60)
    registry.set_enabled("paused", False)
    _create(env, "recent", schedule_minutes=60)
    registry.update_state("recent", last_scan_at=(T0 - timedelta(minutes=30)).isoformat())
    _create(env, "old", schedule_minutes=60)
    registry.update_state("old", last_scan_at=(T0 - timedelta(minutes=60)).isoformat())
    assert source_sync.due_sources(T0) == ["never", "old"]
    assert source_sync.due_sources(T0 + timedelta(minutes=31)) == ["never", "old", "recent"]


# --- the command line ---------------------------------------------------------------------------------------------------


def test_the_command_line_round_trip(env, capsys):
    folder = env / "policies"
    _write(folder, "a.txt")

    def run(*argv):
        code = cli.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    code, added = run("add", "--id", "policies", "--tenant", "tenant_a", "--path", str(folder), "--schedule", "30", "--by", "田中太郎")
    assert code == 0 and added["collection"] == "src-policies" and added["schedule_minutes"] == 30
    assert run("list")[1]["sources"][0]["source_id"] == "policies" and run("list", "--tenant", "x")[1] == {"sources": []}
    assert run("show", "--id", "policies")[1]["tenant_id"] == "tenant_a"
    assert run("due")[1] == {"due": ["policies"]}
    code, scanned = run("scan", "--id", "policies")
    assert code == 0 and scanned["counts"]["added"] == 1
    assert run("due")[1] == {"due": []}
    code, listed = run("changes", "--id", "policies")
    assert code == 0 and [e["kind"] for e in listed["entries"]] == ["scan"]
    assert run("verify")[1] == {"entries": 1, "chain": "ok"}
    assert run("disable", "--id", "policies")[1]["enabled"] is False and run("enable", "--id", "policies")[1]["enabled"] is True
    assert run("remove", "--id", "policies")[1] == {"removed": "policies"}
    for argv in (("show", "--id", "policies"), ("scan", "--id", "nope"), ("changes", "--id", "nope"), ("add", "--id", "X", "--tenant", "t", "--path", str(folder))):
        code, error = run(*argv)
        assert code == 3 and "SourceError" in error["error"]
