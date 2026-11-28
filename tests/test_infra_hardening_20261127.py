"""Infrastructure hardening (2026-11-27): vector client lifecycle, ingestion shutdown grace, skip allowlist,
pip-audit ignore list, pytest discovery and CI gates. Synthetic data only."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from rag_core import store, vector_backend
from scripts import check_audit_ignores as audit
from webapi import ingestion_jobs

ROOT = Path(config.BASE_DIR)


class _FakeClient:
    def __init__(self, fail=False):
        self.closed = 0
        self.fail = fail

    def close(self):
        self.closed += 1
        if self.fail:
            raise RuntimeError("synthetic close failure")


# --- 1. Qdrant / Chroma client lifecycle ---------------------------------------------------------------------------


def test_close_clients_closes_every_cached_client_and_clears_the_caches(monkeypatch):
    good, bad, plain = _FakeClient(), _FakeClient(fail=True), object()
    monkeypatch.setitem(vector_backend._CLIENT, "url:a", bad)
    monkeypatch.setitem(vector_backend._CLIENT, "url:b", good)
    monkeypatch.setitem(vector_backend._CLIENT, "url:c", plain)
    vector_backend._EXISTS[(1, "c")] = 1.0
    vector_backend._META_CACHE[(1, "c")] = (1.0, {})

    vector_backend.close_clients()  # a failing close does not stop the others

    assert bad.closed == 1 and good.closed == 1
    assert vector_backend._CLIENT == {} and vector_backend._EXISTS == {} and vector_backend._META_CACHE == {}


def test_embedded_path_is_single_process_with_a_clear_error_and_released_on_close(monkeypatch, tmp_path):
    qdrant_client = pytest.importorskip("qdrant_client")
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "qdrant"))
    vector_backend.close_clients()
    first = vector_backend._client()
    assert vector_backend._client() is first  # cached

    with pytest.raises(RuntimeError, match="single-process.*WEB_CONCURRENCY=1.*QDRANT_URL"):
        vector_backend._open_local_client(qdrant_client.QdrantClient, str(tmp_path / "qdrant"))

    vector_backend.close_clients()  # the folder lock is released
    second = vector_backend._client()
    assert second is not first
    vector_backend.close_clients()


def test_other_local_open_errors_are_not_rewritten():
    def factory(path):
        raise RuntimeError("synthetic: something else")

    with pytest.raises(RuntimeError, match="^synthetic: something else$"):
        vector_backend._open_local_client(factory, "unused")


def test_reset_vectorstore_clients_closes_chroma_clients(monkeypatch):
    good, bad, plain = _FakeClient(), _FakeClient(fail=True), object()
    monkeypatch.setattr(store, "_CLIENT_CACHE", {"a": bad, "b": good, "c": plain})

    store.reset_vectorstore_clients()

    assert bad.closed == 1 and good.closed == 1 and store._CLIENT_CACHE == {}


def test_store_does_not_import_chromadb_until_a_chroma_client_is_needed():
    code = (
        "import sys; from rag_core import store; assert 'chromadb' not in sys.modules, 'eager';"
        "store.chromadb; assert 'chromadb' in sys.modules, 'lazy attribute'"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_store_unknown_attribute_still_raises():
    with pytest.raises(AttributeError):
        getattr(store, "no_such_attribute_for_the_test")


def test_bench_qdrant_backend_close_closes_the_client():
    from eval.bench.backends import QdrantBackend

    backend = QdrantBackend({})
    client = SimpleNamespace(deleted=[], closed=0)
    client.delete_collection = lambda name: client.deleted.append(name)

    def close():
        client.closed += 1

    client.close = close
    backend.client, backend.collection = client, "bench_x"
    backend.close()
    backend.close()  # idempotent
    assert client.deleted == ["bench_x"] and client.closed == 1 and backend.client is None


# --- 2. ingestion shutdown grace -----------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("", 20.0), ("5", 5.0), ("0", 0.0), ("2.5", 2.5), ("-1", 20.0), ("abc", 20.0), ("nan", 20.0), ("999", 55.0),
])
def test_shutdown_grace_setting(monkeypatch, raw, expected):
    monkeypatch.setenv("INGEST_SHUTDOWN_GRACE_SECONDS", raw)
    assert ingestion_jobs.shutdown_grace_seconds() == expected


def _pool_with_jobs(monkeypatch, durations):
    """A one-worker pool with tracked jobs: the first runs, the others are queued."""
    monkeypatch.setattr(ingestion_jobs.isolated, "shutdown", lambda: None)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setitem(ingestion_jobs._EXECUTOR, "pool", pool)
    started = threading.Event()
    finished = []

    def job(index, seconds):
        if index == 0:
            started.set()
        time.sleep(seconds)
        finished.append(index)

    for index, seconds in enumerate(durations):
        future = pool.submit(job, index, seconds)
        ingestion_jobs._FUTURES.add(future)
        future.add_done_callback(ingestion_jobs._forget_future)
    assert started.wait(5)
    return pool, finished


def test_shutdown_lets_the_running_job_finish_and_cancels_the_queued_ones(monkeypatch):
    pool, finished = _pool_with_jobs(monkeypatch, [0.3, 0.3, 0.3])

    result = ingestion_jobs.shutdown(grace_seconds=5)

    assert finished == [0]
    assert result == {"cancelled": 2, "unfinished": 0}
    assert ingestion_jobs._EXECUTOR["pool"] is None and not ingestion_jobs._FUTURES


def test_shutdown_grace_is_bounded(monkeypatch):
    pool, finished = _pool_with_jobs(monkeypatch, [2.0])
    started = time.monotonic()

    result = ingestion_jobs.shutdown(grace_seconds=0.1)

    assert time.monotonic() - started < 1.5
    assert result == {"cancelled": 0, "unfinished": 1} and finished == []
    pool.shutdown(wait=True)


def test_shutdown_without_a_pool_still_stops_the_conversion_workers(monkeypatch):
    calls = []
    monkeypatch.setattr(ingestion_jobs.isolated, "shutdown", lambda: calls.append(1))
    monkeypatch.setitem(ingestion_jobs._EXECUTOR, "pool", None)
    assert ingestion_jobs.shutdown(wait=False) == {"cancelled": 0, "unfinished": 0}
    assert calls == [1]


# --- 3. unexpected skips -------------------------------------------------------------------------------------------


def _conftest():
    spec = importlib.util.spec_from_file_location("_conftest_under_test", ROOT / "tests" / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skip_allowlist_parses_and_never_allows_a_missing_package():
    conftest = _conftest()
    allowlist = conftest.load_skip_allowlist()
    assert len(allowlist) >= 4
    missing = [
        (f"tests/test_x.py::test_{name}", reason)
        for name in ("qdrant_client", "sudachipy", "pdfplumber", "pypdfium2", "PIL", "pyflakes.api", "chromadb",
                     "anthropic", "redis", "playwright")
        for reason in (f"could not import '{name}': No module named '{name}'", f"{name} is not installed")
    ]
    assert conftest.unexpected_skips(missing, allowlist) == missing
    for path in ("tests/test_stage1_security.py", "tests/test_real_vector_quality_baseline.py"):
        nodeid = f"{path}::test_x"
        assert conftest.unexpected_skips([(nodeid, "could not import 'pdfplumber': No module named 'pdfplumber'")],
                                         allowlist)


def test_skip_allowlist_matches_the_deliberate_skips():
    conftest = _conftest()
    allowlist = conftest.load_skip_allowlist()
    allowed = [
        ("tests/test_real_vector_quality_baseline.py::test_current_contract_and_report_validate",
         "Real-vector evidence is a release gate, not an ordinary CI gate"),
        ("libs/docextract/tests/test_docextract.py::test_pp_ocrv6_reads_japanese",
         "model test (downloads PaddleOCR models): set DOCEXTRACT_RUN_MODEL_TESTS=1"),
        ("tests/test_stage12_additions.py::test_x", "one CPU: parallel conversion is not used"),
        ("tests/test_stage1_security.py::test_x", "symlinks not available"),
    ]
    assert conftest.unexpected_skips(allowed, allowlist) == []
    # The same reason in another file is not allowed.
    assert conftest.unexpected_skips([("tests/test_other.py::test_x", "symlinks not available")], allowlist)


def test_malformed_allowlist_line_is_an_error(tmp_path):
    conftest = _conftest()
    path = tmp_path / "allow.txt"
    path.write_text("# comment\n\ntests/a.py::*\tonly two columns\n", encoding="utf-8")
    with pytest.raises(ValueError, match="allow.txt:3"):
        conftest.load_skip_allowlist(path)


def _session(reports, exitstatus=pytest.ExitCode.OK):
    reporter = SimpleNamespace(stats={"skipped": reports})
    config_ = SimpleNamespace(stash=pytest.Stash(), pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter))
    return SimpleNamespace(config=config_, exitstatus=exitstatus)


def _skip(nodeid, reason):
    return SimpleNamespace(nodeid=nodeid, skipped=True, longrepr=("f.py", 1, f"Skipped: {reason}"))


def test_sessionfinish_fails_only_on_unexpected_skips_when_enabled(monkeypatch):
    conftest = _conftest()
    reports = [_skip("tests/test_stage1_security.py::test_x", "symlinks not available"),
               _skip("tests/test_y.py::test_z", "could not import 'qdrant_client': No module named 'qdrant_client'")]

    monkeypatch.delenv("CI_FAIL_ON_UNEXPECTED_SKIP", raising=False)
    session = _session(reports)
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.OK

    monkeypatch.setenv("CI_FAIL_ON_UNEXPECTED_SKIP", "1")
    session = _session(reports[:1])
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.OK

    session = _session(reports)
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert session.config.stash[conftest._UNEXPECTED_SKIPS_KEY] == [
        ("tests/test_y.py::test_z", "could not import 'qdrant_client': No module named 'qdrant_client'")
    ]
    lines = []
    reporter = SimpleNamespace(section=lambda *a, **k: lines.append(a[0]), write_line=lines.append)
    conftest.pytest_terminal_summary(reporter, 1, session.config)
    assert any("tests/test_y.py::test_z" in line for line in lines)


# --- 4. pip-audit ignore list --------------------------------------------------------------------------------------


def _ignore_line(vuln="PYSEC-2026-1", package="examplepkg", expires="2027-01-31"):
    return "\t".join([vuln, package, "synthetic reason", expires, "security-reviewer"])


def test_audit_ignore_file_in_the_repository_is_valid_today():
    entries, errors = audit.parse(audit.DEFAULT_FILE.read_text(encoding="utf-8"))
    assert errors == [] and entries
    assert audit.main([]) == 0


def test_audit_ignores_print_args_and_fail_when_expired(tmp_path, capsys):
    path = tmp_path / "ignore.txt"
    path.write_text("# c\n" + _ignore_line() + "\n" + _ignore_line("GHSA-abcd-efgh-ijkl", expires="2027-03-01") + "\n",
                    encoding="utf-8")
    assert audit.main(["--file", str(path), "--today", "2027-01-31", "--print-args"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "--ignore-vuln PYSEC-2026-1 --ignore-vuln GHSA-abcd-efgh-ijkl"
    assert "expires on 2027-01-31" in out.err  # warning only

    assert audit.main(["--file", str(path), "--today", "2027-02-01", "--print-args"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "PYSEC-2026-1 (examplepkg) expired on 2027-01-31" in out.err


@pytest.mark.parametrize("line, message", [
    ("PYSEC-2026-1\texamplepkg\treason\t2027-01-31", "columns"),
    (_ignore_line(expires="31/01/2027"), "YYYY-MM-DD"),
    (_ignore_line(vuln="not an id"), "invalid advisory id"),
    (_ignore_line() + "\n" + _ignore_line(), "duplicate"),
    ("\t".join(["PYSEC-2026-1", "examplepkg", " ", "2027-01-31", "security-reviewer"]), "columns"),
])
def test_audit_ignores_reject_malformed_entries(tmp_path, capsys, line, message):
    path = tmp_path / "ignore.txt"
    path.write_text(line + "\n", encoding="utf-8")
    assert audit.main(["--file", str(path), "--today", "2027-01-01", "--print-args"]) == 1
    assert message in capsys.readouterr().err


# --- 5. discovery and CI -------------------------------------------------------------------------------------------


def test_pytest_ini_keeps_the_deletion_folder_out_of_collection():
    import configparser

    ini = configparser.ConfigParser()
    ini.read(ROOT / "pytest.ini", encoding="utf-8")
    assert ini["pytest"]["testpaths"].split() == ["tests", "libs/docextract/tests"]
    assert {"即削除", "data", "runs", "node_modules", "webui", ".venv"} <= set(ini["pytest"]["norecursedirs"].split())
    assert "--strict-markers" in ini["pytest"]["addopts"].split()


def test_ci_gates_the_full_suite_bundle_budget_and_audit():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test"]["steps"]
    by_id = {s["id"]: s for s in steps if s.get("id")}
    gate = steps[-1]["run"]
    for step_id in by_id:
        assert f"'{step_id}'" in gate, step_id
    full = by_id["full_suite"]
    assert full["env"]["CI_FAIL_ON_UNEXPECTED_SKIP"] == "1" and "not a gate" not in full["name"]
    audit_run = by_id["dependency_audit"]["run"]
    assert "check_audit_ignores.py --print-args" in audit_run and "PYSEC-" not in audit_run
    for lock in ("requirements.lock.txt", "requirements-embedding.lock.txt"):
        assert f"--require-hashes --disable-pip -r {lock}" in audit_run
    budget = by_id["webui_bundle_budget"]["run"]
    assert "ENTRY_BUDGET, TOTAL_BUDGET = 120000, 220000" in budget


def test_eval_tool_accepts_a_repository_internal_work_directory():
    text = (ROOT / "tools" / "eval_product_pipeline_jagov.py").read_text(encoding="utf-8")
    assert 'ROOT / "runs"' in text
