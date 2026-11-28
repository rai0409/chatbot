"""Security and store-consistency fixes (2026-11-27). Synthetic data only."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import audit_log, ingest_input_guard, source_apply, source_registry, source_scan
from rag_core.secure_compare import same_secret

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}


def _admin_env(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED",
                 "ADMIN_AUTH_ALLOW_INSECURE_DEV", "INGEST_UPLOAD_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))


# --- B1: other tenants' uploads and source mirrors ------------------------------------------------------------


@pytest.fixture
def ingest(monkeypatch, tmp_path):
    _admin_env(monkeypatch, tmp_path)
    root = tmp_path / "ingest"
    for rel in ("docs-a", "uploads/tenant_a/20261127", "uploads/tenant_b/20261127"):
        (root / rel).mkdir(parents=True)
    (root / "docs-a" / "a.pdf").write_bytes(b"%PDF-a")
    (root / "uploads" / "tenant_a" / "20261127" / "own.pdf").write_bytes(b"%PDF-own")
    (root / "uploads" / "tenant_b" / "20261127" / "secret.pdf").write_bytes(b"%PDF-b")
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("SOURCES_DIR", str(root / "state"))
    monkeypatch.setattr(source_scan, "supported_extensions", lambda: frozenset({"pdf"}))
    for source_id, tenant in (("mirror-a", "tenant_a"), ("mirror-b", "tenant_b")):
        source_registry.create(source_id, tenant_id=tenant, kind="sharepoint",
                               options={"drive_id": "d", "folder": "/Shared Documents", "access_model": "tenant_wide"})
        mirror = source_registry.mirror_dir(source_id)
        mirror.mkdir(parents=True)
        (mirror / f"{source_id}.pdf").write_bytes(b"%PDF-m")
    return root


def test_guard_refuses_ancestors_and_other_tenants_folders(ingest):
    check = ingest_input_guard.check_path
    for ancestor in (ingest, ingest / "uploads", ingest / "state", ingest / "state" / "mirror"):
        with pytest.raises(PermissionError):
            check(ancestor, "tenant_a")
    with pytest.raises(PermissionError):
        check(ingest / "uploads" / "tenant_b" / "20261127" / "secret.pdf", "tenant_a")
    with pytest.raises(PermissionError):
        check(source_registry.mirror_dir("mirror-b") / "mirror-b.pdf", "tenant_a")
    with pytest.raises(PermissionError):
        check(source_registry.mirror_dir("unknown"), "tenant_a")
    # Allowed: the tenant's own upload folder, its own source's mirror, and ordinary folders.
    check(ingest / "uploads" / "tenant_a" / "20261127" / "own.pdf", "tenant_a")
    check(source_registry.mirror_dir("mirror-a") / "mirror-a.pdf", "tenant_a")
    check(ingest / "docs-a", "tenant_a")
    check("ingest/docs-a/a.pdf", "tenant_a")  # relative to BASE_DIR


def test_scan_of_an_ancestor_never_walks_into_uploads_or_mirrors(ingest):
    result = source_scan.scan_folder(ingest)
    assert list(result.manifest) == ["docs-a/a.pdf"]
    assert result.skipped["protected"] == 2


def test_a_folder_source_over_the_ingest_root_is_refused_by_the_api(ingest):
    from webapi import main

    client = TestClient(main.app)
    for headers in (TENANT_A, ADMIN):
        refused = client.post("/admin/sources", headers=headers,
                              json={"source_id": "all", "tenant_id": "tenant_a", "path": str(ingest)})
        assert refused.status_code == 403
    ok = client.post("/admin/sources", headers=TENANT_A,
                     json={"source_id": "docs", "tenant_id": "tenant_a", "path": str(ingest / "docs-a")})
    assert ok.status_code == 200


def test_raw_ingestion_of_another_tenants_mirror_is_refused(ingest):
    from webapi import main

    client = TestClient(main.app)
    other = source_registry.mirror_dir("mirror-b") / "mirror-b.pdf"
    response = client.post("/admin/ingestion/raw-documents", headers=TENANT_A,
                           json={"inputs": [str(other)], "expected_tenant": "tenant_a", "collection": "staging_a"})
    assert response.status_code == 403
    dry = client.post("/admin/ingestion/dry-run", headers=TENANT_A,
                      json={"inputs": [str(ingest)], "expected_tenant": "tenant_a"})
    assert dry.status_code == 403


def test_a_source_registered_over_an_ancestor_is_not_scanned_or_applied(ingest):
    from webapi import main

    # Registered directly (as before the rule existed): the scan route and apply refuse it.
    source_registry.create("legacy", tenant_id="tenant_a", path=str(ingest))
    client = TestClient(main.app)
    assert client.post("/admin/sources/legacy/scan", headers=TENANT_A).status_code == 403
    from rag_core import source_sync

    source_sync.scan_source("legacy")  # a scheduled scan: the protected folders are skipped
    observed = source_registry.load_manifest("legacy", "observed")
    assert list(observed) == ["docs-a/a.pdf"]
    ingested = []
    with pytest.raises(PermissionError):
        source_apply.apply_source("legacy", ingest=lambda paths, tenant, coll: ingested.append(paths) or {"ok": True},
                                  delete=lambda *a: {"chunks": 0})
    assert ingested == []


# --- A1-sec: review action tenant check over the whole queue --------------------------------------------------


@pytest.fixture
def review_api(monkeypatch, tmp_path):
    _admin_env(monkeypatch, tmp_path)
    queue = tmp_path / "runs" / "review" / "review_queue.jsonl"
    queue.parent.mkdir(parents=True)
    rows = [{"review_id": f"r-{n}", "tenant_id": "tenant_a", "status": "open"} for n in range(600)]
    rows.append({"review_id": "r-late-b", "tenant_id": "tenant_b", "status": "open"})
    rows.append({"review_id": "r-late-a", "tenant_id": "tenant_a", "status": "open"})
    queue.write_text("not json\n" + "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from webapi import main

    return TestClient(main.app)


def test_review_action_past_500_rows_is_still_tenant_checked(review_api):
    body = {"review_id": "r-late-b", "action_type": "reject_candidate", "tenant_id": "tenant_a"}
    assert review_api.post("/admin/review/action", headers=TENANT_A, json=body).status_code == 403
    body["tenant_id"] = "tenant_b"
    assert review_api.post("/admin/review/action", headers=TENANT_A, json=body).status_code == 403
    own = {"review_id": "r-late-a", "action_type": "reject_candidate", "tenant_id": "tenant_a"}
    assert review_api.post("/admin/review/action", headers=TENANT_A, json=own).status_code == 200


def test_review_action_for_an_unknown_id_is_404(review_api):
    body = {"review_id": "r-missing", "action_type": "reject_candidate", "tenant_id": "tenant_a"}
    assert review_api.post("/admin/review/action", headers=ADMIN, json=body).status_code == 404


# --- A3: non-ASCII secrets never raise ------------------------------------------------------------------------


def test_same_secret_handles_any_text():
    assert same_secret("トークン", "トークン") and same_secret(b"x", "x")
    assert not same_secret("トークン", "token") and not same_secret("\udc80", "a")
    assert not same_secret(None, None) and not same_secret("", "") and not same_secret("a", None)


def test_non_ascii_credentials_are_refused_not_500(monkeypatch, tmp_path):
    _admin_env(monkeypatch, tmp_path)
    from webapi import admin_auth, enterprise_auth, production_guard
    from webapi.routers import infra
    from rag_core import product_contract

    # CSRF header and a session signature.
    cookie, _ttl = admin_auth.issue_admin_session()
    with pytest.raises(HTTPException) as exc:
        admin_auth._enforce_csrf({"x-csrf-token": "改ざん"}, cookie)
    assert exc.value.status_code == 403
    now = int(time.time())
    assert admin_auth._valid_admin_session(f"sid.{now * 1000}.{now + 60}.署名", ADMIN_TOKEN, now=now) is False
    # Metrics bearer token.
    monkeypatch.setattr(production_guard, "is_production", lambda: True)
    monkeypatch.setenv("METRICS_TOKEN", "metrics-token-example")
    with pytest.raises(HTTPException) as exc:
        infra.require_metrics_access(SimpleNamespace(headers={"authorization": "Bearer 秘密"}))
    assert exc.value.status_code == 403
    # Enterprise trust header and tenant header.
    monkeypatch.setenv("ENTERPRISE_AUTH_ENABLED", "true")
    monkeypatch.setenv("ENTERPRISE_AUTH_TRUST_TOKEN", "trust-token-example")
    monkeypatch.setenv("ENTERPRISE_AUTH_TENANT_MAP", "sample=tenant_a")
    with pytest.raises(HTTPException) as exc:
        enterprise_auth.resolve_enterprise_auth({"x-enterprise-auth-trust": "信頼", "x-enterprise-tenant": "sample"})
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        enterprise_auth.resolve_enterprise_auth({"x-enterprise-auth-trust": "trust-token-example",
                                                 "x-enterprise-tenant": "テナント", "x-enterprise-user": "u"})
    assert exc.value.status_code in (401, 403)
    # Feedback token signature.
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "feedback-secret-example")
    assert product_contract.verify_feedback_token(f"v1.nonce.{now + 60}.署名", "default") == "invalid"


def test_non_ascii_admin_header_over_http_is_not_500(monkeypatch, tmp_path):
    _admin_env(monkeypatch, tmp_path)
    from webapi import main

    client = TestClient(main.app)
    response = client.get("/admin/usage", headers={"x-admin-token": "é".encode("latin-1")})
    assert response.status_code in (401, 403)


# --- A4: OIDC cookie lifetime ---------------------------------------------------------------------------------


def test_oidc_session_cookie_max_age_follows_the_ttl(monkeypatch):
    from webapi import oidc_auth
    from webapi.routers import auth

    monkeypatch.setenv("OIDC_SESSION_TTL_SECONDS", "7200")
    monkeypatch.setattr(oidc_auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(oidc_auth, "complete_login", lambda **kw: "signed-session")
    request = SimpleNamespace(cookies={oidc_auth.TXN_COOKIE: "txn"})
    response = auth.oidc_callback(request, code="c", state="s")
    cookies = [v.decode("latin-1") for k, v in response.raw_headers if k == b"set-cookie"]
    session = next(c for c in cookies if c.startswith(f"{oidc_auth.SESSION_COOKIE}="))
    assert "Max-Age=7200" in session


# --- A6+B5: tenant upload quota re-checked at commit ------------------------------------------------------------


def test_upload_commit_rechecks_the_quota_and_ignores_partials(tmp_path):
    from webapi.routers import ingestion

    tenant_dir = tmp_path / "uploads" / "tenant_a"
    day = tenant_dir / "20261127"
    day.mkdir(parents=True)
    (day / "0123456789ab_old.pdf").write_bytes(b"x" * 80)
    (day / ".0123456789ab_other.pdf.part").write_bytes(b"x" * 500)  # another upload in flight: not counted
    partial = day / ".0123456789ac_new.pdf.part"
    partial.write_bytes(b"y" * 30)
    target = day / "0123456789ac_new.pdf"
    with pytest.raises(HTTPException) as exc:
        ingestion._commit_upload(partial, target, tenant_dir, 30, 100)
    assert exc.value.status_code == 413 and not target.exists()
    ingestion._commit_upload(partial, target, tenant_dir, 30, 200)
    assert target.read_bytes() == b"y" * 30


def test_concurrent_upload_commits_cannot_jointly_exceed_the_quota(tmp_path):
    from webapi.routers import ingestion

    tenant_dir = tmp_path / "uploads" / "tenant_a"
    day = tenant_dir / "20261127"
    day.mkdir(parents=True)
    outcomes = []
    barrier = threading.Barrier(4)

    def commit(n):
        partial = day / f".{n:012x}_f.pdf.part"
        partial.write_bytes(b"z" * 60)
        barrier.wait()
        try:
            ingestion._commit_upload(partial, day / f"{n:012x}_f.pdf", tenant_dir, 60, 100)
            outcomes.append("ok")
        except HTTPException as exc:
            outcomes.append(exc.status_code)

    threads = [threading.Thread(target=commit, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes, key=str) == [413, 413, 413, "ok"]


def test_upload_route_still_saves_a_file(monkeypatch, tmp_path):
    _admin_env(monkeypatch, tmp_path)
    root = tmp_path / "ingest"
    root.mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("INGEST_UPLOAD_TENANT_QUOTA_BYTES", "40")
    from webapi import main

    client = TestClient(main.app)
    headers = {**TENANT_A, "Content-Type": "application/octet-stream"}
    first = client.put("/admin/ingestion/upload?filename=a.pdf&tenant=tenant_a", content=b"%PDF-" + b"0" * 20, headers=headers)
    assert first.status_code == 200
    second = client.put("/admin/ingestion/upload?filename=b.pdf&tenant=tenant_a", content=b"%PDF-" + b"0" * 20, headers=headers)
    assert second.status_code == 413
    assert not list((root / "uploads").rglob("*.part"))


# --- B2: mail state writes hold the maintenance lock ----------------------------------------------------------


def test_committing_a_mail_deletion_holds_the_maintenance_lock(monkeypatch):
    from contextlib import contextmanager

    from webapi import mail_ingestion_jobs as mij

    order = []

    @contextmanager
    def maintenance():
        order.append("maintenance")
        yield

    @contextmanager
    def collection_lock(target):
        order.append("collection")
        yield

    monkeypatch.setattr(mij.ij, "maintenance_shared", maintenance)
    monkeypatch.setattr(mij.ij, "_collection_write_lock", collection_lock)
    monkeypatch.setattr(mij.ij, "get_collection", lambda name: None)
    monkeypatch.setattr(mij, "load_mail_state", lambda tenant, target: {"m1.eml": {}})
    monkeypatch.setattr(mij, "save_mail_state", lambda tenant, target, state: order.append("save"))
    monkeypatch.setattr(mij, "plan_mail_deletion", lambda state, tenant, **kw: {"found": True, "source_doc": "m1.eml"})
    mij.delete_mail("tenant_a", "staging_mail", source_doc="m1.eml", commit=False)
    assert order == ["collection"]  # a plan only: no maintenance lock needed
    order.clear()
    mij.delete_mail("tenant_a", "staging_mail", source_doc="m1.eml", commit=True)
    assert order == ["maintenance", "collection", "save"]


# --- B3: the source registry under concurrent writers -----------------------------------------------------------


def test_registry_updates_from_threads_are_all_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    root = tmp_path / "ingest"
    (root / "docs").mkdir(parents=True)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("SOURCES_DIR", str(tmp_path / "state"))
    for n in range(4):
        source_registry.create(f"s{n}", tenant_id="tenant_a", path=str(root / "docs"))
    errors = []

    def work(n):
        try:
            for i in range(10):
                source_registry.update_state(f"s{n}", last_error=f"e{i}")
        except Exception as exc:  # noqa: BLE001  collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = json.loads((tmp_path / "state" / "registry.json").read_text(encoding="utf-8"))
    assert data["rev"] == 4 + 40
    assert all(data["sources"][f"s{n}"]["state"]["last_error"] == "e9" for n in range(4))
    assert not [p for p in (tmp_path / "state").iterdir() if ".tmp-" in p.name]


# --- B4: SharePoint downloads are read with a bound ------------------------------------------------------------


def test_a_download_reads_at_most_one_byte_over_the_limit(monkeypatch):
    from rag_core.connectors import sharepoint

    reads = []

    class Response:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            reads.append(size)
            body = b"x" * 1000
            return body if size is None or size < 0 else body[:size]

    monkeypatch.setattr(sharepoint.urllib.request, "urlopen", lambda request, timeout=None: Response())
    monkeypatch.setattr(sharepoint.integrations, "require_egress", lambda name: None)
    client = sharepoint.GraphClient(lambda: "token", sharepoint.transport_for("sharepoint"))
    with pytest.raises(sharepoint.GraphError, match="larger than the limit"):
        client.download("https://dl.example.co.jp/x", max_bytes=10)
    assert reads == [11]
    assert client.download("https://dl.example.co.jp/x", max_bytes=5000) == b"x" * 1000


# --- C2, C3: usage of batches and failed usage writes ---------------------------------------------------------------


@pytest.fixture
def usage_env(monkeypatch, tmp_path):
    from rag_core import llm_quota, llm_usage

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "LLM_USAGE_DIR", "", raising=False)
    monkeypatch.setattr(config, "LLM_USAGE_ENABLED", True)
    monkeypatch.setattr(config, "BUSINESS_HOURS_UTC_OFFSET", 0.0)
    for name in ("USER_DAILY_CALLS", "USER_DAILY_TOKENS", "TENANT_DAILY_TOKENS", "TENANT_DAILY_COST", "GLOBAL_DAILY_COST"):
        monkeypatch.setattr(config, "LLM_QUOTA_" + name, 0)
    llm_quota.reset()
    llm_usage.reset_health()
    yield
    llm_usage.reset_health()
    llm_quota.reset()


def test_batch_usage_is_recorded_for_the_requester_not_the_collector(usage_env, monkeypatch):
    from rag_core import llm_batch, llm_usage

    profile = SimpleNamespace(name="large", model="m", host="h", location="cloud", cost=lambda i, o: 0.0)
    monkeypatch.setattr(llm_batch.llm_profiles, "all_profiles", lambda: {"large": profile})
    token = audit_log._ACTOR.set({"authenticated": True, "key_fingerprint": "collector0000", "role": "admin"})
    try:
        job = {"profile": "large", "feature": "judge", "tenant_id": "tenant_a",
               "caller": {"key_fingerprint": "requester000", "role": "user"}}
        llm_batch._record_usage(job, {"r1": llm_batch.BatchResult("r1", text="ok", input_tokens=3, output_tokens=2)}, None)
    finally:
        audit_log._ACTOR.reset(token)
    rows = [r for r in llm_usage.read(1) if r.get("event") == "call"]
    assert [(r["key_fingerprint"], r["role"], r["batch"]) for r in rows] == [("requester000", "user", True)]


def test_failed_usage_writes_are_counted_and_quota_fails_closed(usage_env, monkeypatch):
    from rag_core import llm_quota, llm_usage

    def broken(payload, now):
        raise OSError("disk full")

    call = dict(feature="generate", tenant_id="tenant_a", profile="p", model="m", host="h", location="local", outcome="ok")
    working = llm_usage._append
    monkeypatch.setattr(llm_usage, "_append", broken)
    llm_usage.record_call(**call)  # never raises
    assert llm_usage.write_failure_count() == 1 and llm_usage.recording_healthy() is False
    llm_quota.check("generate", "tenant_a", "fp")  # no limit configured: nothing to protect
    monkeypatch.setattr(config, "LLM_QUOTA_TENANT_DAILY_TOKENS", 1000)
    with pytest.raises(llm_quota.UsageRecordingFailed):
        llm_quota.check("generate", "tenant_a", "fp")
    assert isinstance(llm_quota.UsageRecordingFailed(), llm_quota.QuotaExceeded)
    # After the hold time a call may try again; a successful write clears the state at once.
    monkeypatch.setattr(config, "LLM_USAGE_FAILURE_HOLD_SECONDS", 0.0, raising=False)
    assert llm_usage.recording_healthy() is True
    monkeypatch.setattr(config, "LLM_USAGE_FAILURE_HOLD_SECONDS", 60.0, raising=False)
    assert llm_usage.recording_healthy() is False
    monkeypatch.setattr(llm_usage, "_append", working)
    llm_usage.record_call(**call)
    assert llm_usage.recording_healthy() is True


def test_metrics_report_usage_write_failures(usage_env, monkeypatch):
    from rag_core import llm_usage
    from webapi.routers import infra

    monkeypatch.setattr(llm_usage, "_append", lambda payload, now: (_ for _ in ()).throw(OSError("x")))
    llm_usage.record_call(feature="generate", tenant_id="t", profile="p", model="m", host="h", location="l", outcome="ok")
    payload = infra.metrics()
    assert payload["counters"]["llm_usage_write_failures_total"] == {"total": 1}


# --- C4, C5: the limits and assignments a tenant administrator sees and sets -------------------------------------------


@pytest.fixture
def limits_api(usage_env, monkeypatch, tmp_path):
    _admin_env(monkeypatch, tmp_path)
    data = {"profiles": {"small": {"base_url": "http://localhost:11434/v1", "model": "small-model"}},
            "routes": {"generate": ["small"]}, "tiers": {"premium": {"generate": ["small"]}}}
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "LLM_PROFILES_FILE", str(path))
    from rag_core import llm_usage
    from webapi import main

    for tenant in ("tenant_a", "tenant_b"):
        llm_usage.record_call(feature="generate", tenant_id=tenant, profile="small", model="m", host="h", location="local",
                              outcome="ok", input_tokens=5, cost=1.0)
    return TestClient(main.app)


def test_tenant_admin_does_not_see_installation_totals(limits_api):
    assert limits_api.post("/admin/limits", headers=ADMIN,
                           json={"name": "global_daily_cost", "value": 1, "reason": "pilot"}).status_code == 200
    full = limits_api.get("/admin/limits", headers=ADMIN).json()
    assert full["global"]["calls"] == 2 and any(w["scope"] == "global" for w in full["warnings"])
    seen = limits_api.get("/admin/limits", headers=TENANT_A).json()
    assert seen["global"] == {} and not any(w["scope"] == "global" for w in seen["warnings"])
    assert list(seen["tenants"]) == ["tenant_a"]


def test_tenant_admin_sees_only_their_assignments(limits_api):
    for kind, ident in (("tenant", "tenant_b"), ("tenant", "tenant_a"), ("user", "abcdef012345"), ("role", "admin")):
        assert limits_api.post("/admin/llm-assignments", headers=ADMIN,
                               json={"kind": kind, "id": ident, "tier": "premium", "reason": "x"}).status_code == 200
    everything = limits_api.get("/admin/llm-assignments", headers=ADMIN).json()["assignments"]
    assert {(r["kind"], r["id"]) for r in everything} == {("tenant", "tenant_b"), ("tenant", "tenant_a"),
                                                          ("user", "abcdef012345"), ("role", "admin")}
    seen = limits_api.get("/admin/llm-assignments", headers=TENANT_A).json()["assignments"]
    assert {(r["kind"], r["id"]) for r in seen} == {("tenant", "tenant_a"), ("role", "admin")}


def test_tenant_admin_cannot_lift_or_raise_the_installation_limit(limits_api):
    def set_limit(headers, value, tenant="tenant_a"):
        body = {"name": "tenant_daily_cost", "value": value, "reason": "x"}
        if tenant:
            body["tenant_id"] = tenant
        return limits_api.post("/admin/limits", headers=headers, json=body).status_code

    assert set_limit(TENANT_A, 0) == 200  # no installation limit yet: nothing to lift
    assert set_limit(ADMIN, 10, tenant=None) == 200
    assert set_limit(TENANT_A, 0) == 400
    assert set_limit(TENANT_A, 20) == 400
    assert set_limit(TENANT_A, 5) == 200
    assert set_limit(TENANT_A, None) == 200  # removing the tenant value falls back to the installation's
    assert set_limit(ADMIN, 0) == 200  # the installation administrator may


# --- C7: the active profile pointer -----------------------------------------------------------------------------------


def test_concurrent_pointer_changes_keep_a_consistent_history(monkeypatch, tmp_path):
    from rag_core import profile_loader, profile_store, query_preprocess

    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    versions = []
    for n in range(2):
        source = tmp_path / f"src{n}"
        source.mkdir()
        (source / "profile.json").write_text(json.dumps({"project_id": "tenant_a", "n": n}), encoding="utf-8")
        versions.append(profile_store.create_version("tenant_a", source=source)["version"])
    profile_store.activate("tenant_a", versions[0])
    errors = []

    def clear():
        try:
            profile_store.clear("tenant_a")
        except Exception as exc:  # noqa: BLE001  collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=clear) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    state = profile_store.status("tenant_a")
    pointer = json.loads(profile_store.pointer_path("tenant_a").read_text(encoding="utf-8"))
    assert pointer["active"] is None and [h["version"] for h in pointer["history"]] == [versions[0]]
    assert state is not None
    assert not [p for p in profile_store.pointer_path("tenant_a").parent.iterdir() if ".tmp-" in p.name]


# --- C8: golden questions are not dropped silently --------------------------------------------------------------------


def test_golden_store_refuses_to_rewrite_a_file_with_unreadable_lines(monkeypatch, tmp_path):
    from rag_core import golden_store

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "GOLDEN_QA_DIR", "")
    golden_store.create("tenant_a", {"case_id": "c1", "question": "質問", "expected_any": ["回答"]}, by="山田太郎")
    path = golden_store._path("tenant_a")
    path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert [r["case_id"] for r in golden_store.list_entries("tenant_a")] == ["c1"]  # reading still works
    with pytest.raises(golden_store.GoldenError, match="unreadable"):
        golden_store.create("tenant_a", {"case_id": "c2", "question": "別の質問", "expected_any": ["回答"]}, by="山田太郎")
    with pytest.raises(golden_store.GoldenError, match="unreadable"):
        golden_store.update("tenant_a", "c1", {"question": "変更"}, by="山田太郎")
    with pytest.raises(golden_store.GoldenError, match="unreadable"):
        golden_store.delete("tenant_a", "c1", by="山田太郎")
    assert path.read_text(encoding="utf-8") == before
