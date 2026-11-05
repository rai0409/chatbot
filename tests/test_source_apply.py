import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import config
from rag_core import change_ledger, source_apply, source_registry as registry, source_scan, source_sync
from rag_core.source_registry import SourceError
from scripts import sources as cli

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path):
    roots = tmp_path / "ingest"
    (roots / "docs").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("INGEST_MAX_FILES", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"pdf", "txt"}))
    monkeypatch.setattr(config, "SOURCE_MAX_DELETE_FRACTION", 0.2)
    monkeypatch.setattr(config, "SOURCE_MIN_DELETES_FOR_GUARD", 5)
    monkeypatch.setattr(config, "SOURCE_MAX_CHANGES", 500)
    registry.create("docs", tenant_id="tenant_a", path=str(roots / "docs"), created_by="山田太郎", now=T0)
    return roots / "docs"


class Fake:
    """Records what would be ingested and deleted; `bad` file names make an ingest job fail as a whole."""

    def __init__(self, bad=(), raises=None, missing=False):
        self.bad, self.raises, self.missing = set(bad), raises, missing
        self.ingests, self.deletes = [], []

    def ingest(self, paths, tenant, collection):
        self.ingests.append(([p.name for p in paths], tenant, collection))
        if self.raises:
            raise self.raises
        failing = [p.name for p in paths if p.name in self.bad]
        if failing:
            return {"ok": False, "status": "issues_found", "warnings": [{"file": n, "reason": "conversion_failed"} for n in failing]}
        return {"ok": True, "status": "ok", "warnings": []}

    def delete(self, collection, tenant, documents):
        self.deletes.append((collection, tenant, list(documents)))
        return {"chunks": len(documents)}


def _files(folder, names, text="本文"):
    for name in names:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _scan(when=T0):
    return source_sync.scan_source("docs", now=when)


def _apply(fake, **kwargs):
    return source_apply.apply_source("docs", ingest=fake.ingest, delete=fake.delete, now=T0, **kwargs)


# --- the plan ----------------------------------------------------------------------------------------------


def test_a_plan_needs_a_scan_and_lists_the_pending_changes(env):
    with pytest.raises(SourceError, match="scan it first"):
        source_apply.plan_source("docs")
    _files(env, ["a.pdf", "b.pdf"])
    _scan()
    plan = source_apply.plan_source("docs")
    assert plan["added"] == ["a.pdf", "b.pdf"] and plan["counts"] == {"added": 2, "modified": 0, "deleted": 0}
    assert plan["applied_files"] == 0 and plan["total_files"] == 2 and len(plan["plan_sha256"]) == 64
    assert plan == source_apply.plan_source("docs")  # deterministic



def test_scan_never_trusts_stale_content_hash_when_metadata_matches(tmp_path):
    """Regression: unchanged size/mtime/ctime must not conceal changed bytes."""
    from hashlib import sha256

    root = tmp_path / "synthetic"
    root.mkdir()
    target = root / "demo.pdf"
    target.write_bytes(b"version-B")
    meta = target.stat()
    prior = {"demo.pdf": {
        "size": meta.st_size,
        "mtime_ns": meta.st_mtime_ns,
        "ctime_ns": meta.st_ctime_ns,
        "sha256": sha256(b"version-A").hexdigest(),
    }}
    # Both modes have explicit contracts: fast mode avoids reads, registered
    # sources use strict mode to detect content modifications despite metadata.
    fast = source_scan.scan_folder(root, previous=prior, extensions={"pdf"})
    assert fast.hashed == 0
    result = source_scan.scan_folder(root, previous=prior, extensions={"pdf"}, force_rehash=True)
    assert result.hashed == 1
    assert result.manifest["demo.pdf"]["sha256"] == sha256(b"version-B").hexdigest()
    assert source_scan.diff_manifests(prior, result.manifest) == {
        "added": [], "modified": ["demo.pdf"], "deleted": [],
    }


def test_the_plan_hash_follows_the_changes_and_their_content(env):
    _files(env, ["a.pdf"])
    _scan()
    first = source_apply.plan_source("docs")["plan_sha256"]
    _files(env, ["a.pdf"], "別の本文")
    _scan()
    second = source_apply.plan_source("docs")["plan_sha256"]
    _files(env, ["b.pdf"])
    _scan()
    assert len({first, second, source_apply.plan_source("docs")["plan_sha256"]}) == 3


def test_files_that_share_a_document_name_are_reported_and_not_ingested(env):
    _files(env, ["x/same.pdf", "y/same.pdf", "unique.pdf"])
    _scan()
    plan = source_apply.plan_source("docs")
    assert plan["added"] == ["unique.pdf"] and plan["conflicts"] == {"same.pdf": ["x/same.pdf", "y/same.pdf"]}
    fake = Fake()
    out = _apply(fake)
    assert fake.ingests == [(["unique.pdf"], "tenant_a", "src-docs")] and out["conflicts"] == plan["conflicts"]
    assert sorted(registry.load_manifest("docs", "applied")) == ["unique.pdf"]


# --- applying ----------------------------------------------------------------------------------------------


def test_the_first_apply_ingests_everything_into_the_staging_collection_and_records_it(env):
    _files(env, ["a.pdf", "b.pdf", "sub/c.pdf"])
    _scan()
    fake = Fake()
    out = _apply(fake)
    assert out["status"] == "applied" and out["ingested"] == 3 and out["deleted"] == 0 and out["failed"] == []
    assert fake.ingests == [(["a.pdf", "b.pdf", "sub/c.pdf".split("/")[-1]], "tenant_a", "src-docs")] and fake.deletes == []
    assert registry.load_manifest("docs", "applied") == registry.load_manifest("docs", "observed")
    entry = change_ledger.read_entries("docs")[-1]
    assert entry["kind"] == "apply" and entry["summary"]["plan_sha256"] == out["plan_sha256"] and entry["run_id"] == out["run_id"]
    assert registry.get("docs")["state"]["last_apply_at"] == T0.isoformat() and change_ledger.verify_chain() == 2
    assert _apply(fake)["status"] == "nothing_to_do" and len(fake.ingests) == 1


def test_a_modified_file_is_ingested_again_and_a_deleted_one_is_removed_by_document_name(env):
    _files(env, ["a.pdf", "b.pdf", "sub/c.pdf"])
    _scan()
    _apply(Fake())
    _files(env, ["a.pdf"], "更新")
    (env / "sub" / "c.pdf").unlink()
    _scan()
    fake = Fake()
    out = _apply(fake)
    assert fake.ingests == [(["a.pdf"], "tenant_a", "src-docs")] and fake.deletes == [("src-docs", "tenant_a", ["c.pdf"])]
    assert out["status"] == "applied" and sorted(registry.load_manifest("docs", "applied")) == ["a.pdf", "b.pdf"]
    assert source_apply.plan_source("docs")["counts"] == {"added": 0, "modified": 0, "deleted": 0}


def test_files_are_ingested_in_batches(env, monkeypatch):
    monkeypatch.setenv("INGEST_MAX_FILES", "2")
    _files(env, [f"f{i}.pdf" for i in range(5)])
    _scan()
    fake = Fake()
    _apply(fake)
    assert [len(names) for names, _t, _c in fake.ingests] == [2, 2, 1]
    monkeypatch.setenv("INGEST_MAX_FILES", "x")
    assert source_apply._batch_size() == 100


def test_a_bad_file_does_not_block_the_rest_and_is_retried_by_the_next_apply(env):
    _files(env, ["a.pdf", "bad.pdf", "c.pdf"])
    _scan()
    fake = Fake(bad=["bad.pdf"])
    out = _apply(fake)
    assert out["status"] == "partial" and out["ingested"] == 2 and out["failed"] == [{"path": "bad.pdf", "reason": "conversion_failed"}]
    assert [names for names, _t, _c in fake.ingests] == [["a.pdf", "bad.pdf", "c.pdf"], ["a.pdf"], ["bad.pdf"], ["c.pdf"]]
    assert sorted(registry.load_manifest("docs", "applied")) == ["a.pdf", "c.pdf"]
    assert source_apply.plan_source("docs")["added"] == ["bad.pdf"]
    retry = Fake()
    assert _apply(retry)["status"] == "applied" and retry.ingests == [(["bad.pdf"], "tenant_a", "src-docs")]


def test_an_exception_keeps_what_succeeded_and_is_recorded_then_raised(env):
    _files(env, ["a.pdf"])
    _scan()
    with pytest.raises(PermissionError):
        _apply(Fake(raises=PermissionError("collection is owned by another tenant")))
    assert registry.load_manifest("docs", "applied") == {}
    last = change_ledger.read_entries("docs")[-1]
    assert last["kind"] == "apply_failed" and last["summary"]["error"] == "PermissionError"
    assert "owned" not in json.dumps(last)


# --- the guard ----------------------------------------------------------------------------------------------


def _ten_applied(env):
    _files(env, [f"f{i}.pdf" for i in range(10)])
    _scan()
    _apply(Fake())


def test_a_few_deletions_go_through(env):
    _ten_applied(env)
    (env / "f0.pdf").unlink()
    _scan()
    plan = source_apply.plan_source("docs")
    assert source_apply.guard(plan) == {"hold": False, "reasons": []}
    fake = Fake()
    assert _apply(fake)["status"] == "applied" and fake.deletes == [("src-docs", "tenant_a", ["f0.pdf"])]


def test_a_mass_deletion_is_held_and_nothing_is_written(env):
    _ten_applied(env)
    for i in range(6):
        (env / f"f{i}.pdf").unlink()
    _scan()
    plan = source_apply.plan_source("docs")
    assert source_apply.guard(plan) == {"hold": True, "reasons": ["mass_delete"]}
    fake = Fake()
    out = _apply(fake)
    assert out["status"] == "held" and out["reasons"] == ["mass_delete"] and out["plan_sha256"] == plan["plan_sha256"]
    assert fake.ingests == [] and fake.deletes == [] and len(registry.load_manifest("docs", "applied")) == 10
    assert change_ledger.read_entries("docs")[-1]["kind"] == "hold"
    wrong = _apply(fake, approval="0" * 64)
    assert wrong["status"] == "held" and fake.deletes == []  # an approval of some other plan does not release this one


def test_an_approved_plan_runs_and_the_approval_is_recorded(env):
    _ten_applied(env)
    for i in range(6):
        (env / f"f{i}.pdf").unlink()
    _scan()
    sha = source_apply.plan_source("docs")["plan_sha256"]
    with pytest.raises(SourceError, match="approver"):
        source_apply.approve("docs", sha, actor=" ")
    with pytest.raises(SourceError, match="plan changed"):
        source_apply.approve("docs", "0" * 64, actor="佐藤次郎")
    approved = source_apply.approve("docs", sha, actor="佐藤次郎", now=T0)
    assert approved["approved"] == sha and approved["by"] == "佐藤次郎"
    fake = Fake()
    out = _apply(fake, approval=sha, actor="佐藤次郎")
    assert out["status"] == "applied" and out["guard"] == ["mass_delete"] and out["approved_by"] == "佐藤次郎"
    assert fake.deletes == [("src-docs", "tenant_a", [f"f{i}.pdf" for i in range(6)])] and len(registry.load_manifest("docs", "applied")) == 4
    assert [e["kind"] for e in change_ledger.read_entries("docs")][-3:] == ["hold", "approve", "apply"] or \
        [e["kind"] for e in change_ledger.read_entries("docs")][-2:] == ["approve", "apply"]


def test_a_later_scan_that_changes_the_plan_invalidates_the_approval(env):
    _ten_applied(env)
    for i in range(6):
        (env / f"f{i}.pdf").unlink()
    _scan()
    sha = source_apply.plan_source("docs")["plan_sha256"]
    source_apply.approve("docs", sha, actor="佐藤次郎")
    (env / "f6.pdf").unlink()  # one more deletion after the approval
    _scan()
    fake = Fake()
    out = _apply(fake, approval=sha)
    assert out["status"] == "held" and fake.deletes == [] and out["plan_sha256"] != sha


def test_an_emptied_share_is_held_even_when_it_is_small(env):
    _files(env, ["a.pdf", "b.pdf", "c.pdf"])
    _scan()
    _apply(Fake())
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (env / name).unlink()
    _scan()
    assert source_apply.guard(source_apply.plan_source("docs")) == {"hold": True, "reasons": ["all_deleted"]}
    assert _apply(Fake())["status"] == "held"


def test_too_many_changes_are_held(env, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_MAX_CHANGES", 3)
    _files(env, [f"f{i}.pdf" for i in range(4)])
    _scan()
    out = _apply(Fake())
    assert out["status"] == "held" and out["reasons"] == ["too_many_changes"]
    assert _apply(Fake(), approval=out["plan_sha256"])["status"] == "applied"


def test_a_missing_collection_has_nothing_to_delete(env):
    _files(env, ["a.pdf", "b.pdf", "c.pdf"])
    _scan()
    _apply(Fake())
    (env / "a.pdf").unlink()
    _scan()
    out = source_apply.apply_source("docs", ingest=Fake().ingest, delete=lambda c, t, d: {"chunks": 0, "missing_collection": True}, now=T0)
    assert out["status"] == "applied" and "a.pdf" not in registry.load_manifest("docs", "applied")


# --- the default executors -----------------------------------------------------------------------------------------


def test_the_default_executors_use_the_ingestion_job_and_the_collection_admin(env, monkeypatch):
    from webapi import collection_admin, ingestion_jobs

    seen = {}

    def job(inputs, *, expected_tenant, collection, execute):
        seen["job"] = (inputs, expected_tenant, collection, execute)
        return {"status": "ok", "job_id": "j1", "warnings": [], "errors": []}

    def delete(collection, *, tenant, source_docs, execute):
        seen["delete"] = (collection, tenant, source_docs, execute)
        raise LookupError("no such collection")

    monkeypatch.setattr(ingestion_jobs, "run_raw_document_job", job)
    monkeypatch.setattr(collection_admin, "delete_documents", delete)
    assert source_apply.default_ingest([Path("/x/a.pdf")], "tenant_a", "src-docs") == {
        "ok": True, "status": "ok", "job_id": "j1", "warnings": [], "errors": []}
    assert seen["job"] == (["/x/a.pdf"], "tenant_a", "src-docs", True)
    assert source_apply.default_delete("src-docs", "tenant_a", ["a.pdf"]) == {"chunks": 0, "missing_collection": True}
    assert seen["delete"] == ("src-docs", "tenant_a", ["a.pdf"], True)
    monkeypatch.setattr(ingestion_jobs, "run_raw_document_job", lambda *a, **k: {"status": "issues_found", "warnings": [{"reason": "r"}]})
    assert source_apply.default_ingest([Path("/x/a.pdf")], "t", "c")["ok"] is False


# --- run_due and the command line -----------------------------------------------------------------------------------


def test_run_due_scans_and_applies_what_is_not_held_and_never_approves(env):
    registry.create("quiet", tenant_id="tenant_a", path=str(env.parent / "docs"), schedule_minutes=0)
    _ten_applied(env)
    registry._update("docs", lambda r: r.__setitem__("schedule_minutes", 60))
    for i in range(6):
        (env / f"f{i}.pdf").unlink()
    fake = Fake()
    rows = source_apply.run_due(apply=True, now=T0 + timedelta(hours=2), ingest=fake.ingest, delete=fake.delete)
    assert rows == [{"source_id": "docs", "status": "held", "counts": {"added": 0, "modified": 0, "deleted": 6},
                     "reasons": ["mass_delete"]}] and fake.deletes == []
    only_scan = source_apply.run_due(apply=False, now=T0 + timedelta(hours=5))
    assert only_scan == [{"source_id": "docs", "status": "scanned", "counts": {"added": 0, "modified": 0, "deleted": 6}}]


def test_run_due_reports_an_unavailable_source_and_a_failing_one_without_stopping(env, tmp_path):
    other = tmp_path / "ingest" / "other"
    other.mkdir()
    registry.create("other", tenant_id="tenant_b", path=str(other), schedule_minutes=60)
    registry._update("docs", lambda r: r.__setitem__("schedule_minutes", 60))
    _files(env, ["a.pdf"])
    os.rename(other, tmp_path / "ingest" / "other-gone")
    rows = source_apply.run_due(apply=True, now=T0, ingest=Fake(raises=RuntimeError("boom")).ingest, delete=Fake().delete)
    assert rows == [{"source_id": "docs", "status": "apply_failed", "error": "RuntimeError", "counts": {"added": 1, "modified": 0, "deleted": 0}},
                    {"source_id": "other", "status": "source_unavailable"}]


def test_the_command_line_plan_approve_and_apply(env, capsys, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(source_apply, "default_ingest", fake.ingest)
    monkeypatch.setattr(source_apply, "default_delete", fake.delete)

    def run(*argv):
        code = cli.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    _ten_applied(env)
    for i in range(6):
        (env / f"f{i}.pdf").unlink()
    assert run("scan", "--id", "docs")[0] == 0
    code, plan = run("plan", "--id", "docs")
    assert code == 0 and plan["guard"] == {"hold": True, "reasons": ["mass_delete"]} and plan["counts"]["deleted"] == 6
    code, held = run("apply", "--id", "docs")
    assert code == 0 and held["status"] == "held"
    assert run("approve", "--id", "docs", "--plan", "0" * 64, "--by", "佐藤次郎")[0] == 3
    code, ok = run("approve", "--id", "docs", "--plan", plan["plan_sha256"], "--by", "佐藤次郎")
    assert code == 0 and ok["approved"] == plan["plan_sha256"]
    code, done = run("apply", "--id", "docs", "--approval", plan["plan_sha256"], "--by", "佐藤次郎")
    assert code == 0 and done["status"] == "applied" and fake.deletes[-1][2] == [f"f{i}.pdf" for i in range(6)]
    assert run("run-due")[1] == {"results": []}
    assert run("plan", "--id", "missing")[0] == 3
