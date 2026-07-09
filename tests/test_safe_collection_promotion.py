from __future__ import annotations

import json

import config
from webapi import collection_promotion as cp

SYNTH = "eval/cases/qa_pair_chunks.jsonl"  # existing synthetic, non-customer data


def test_refuses_production_or_default_collection():
    for served in ("", "default", config.VECTORSTORE_COLLECTION_NAME):
        plan = cp.plan_promotion([SYNTH], served)
        assert plan["approved"] is False
        assert plan["checks"]["non_production_target"] is False


def test_clean_manifest_nonprod_is_approvable():
    plan = cp.plan_promotion([SYNTH], "pilot_served_v1")
    assert plan["checks"]["non_production_target"] is True
    assert plan["checks"]["manifest_clean"] is True
    assert plan["approved"] is True
    # required gates are present
    assert "tenant_isolation_check" in plan["required_steps"]
    assert "backup_point" in plan["required_steps"]


def test_dirty_manifest_blocks_promotion(tmp_path):
    # duplicate chunk ids -> manifest not clean -> not approved
    dirty = tmp_path / "dirty.jsonl"
    dirty.write_text(
        json.dumps({"id": "d1", "text": "a", "source_doc": "x.pdf", "tenant_id": "default"}) + "\n" +
        json.dumps({"id": "d1", "text": "b", "source_doc": "x.pdf", "tenant_id": "default"}) + "\n",
        encoding="utf-8",
    )
    plan = cp.plan_promotion([str(dirty)], "pilot_served_v1")
    assert plan["checks"]["manifest_clean"] is False
    assert plan["approved"] is False
    assert "manifest_not_clean" in plan["reasons"]


def test_rollback_plan_references_prior_backup():
    plan = cp.plan_promotion([SYNTH], "pilot_served_v1", prior_backup="backups/chatbot_backup_x.tar.gz")
    assert plan["rollback_plan"]["prior_backup"] == "backups/chatbot_backup_x.tar.gz"
    assert "restore" in plan["rollback_plan"]["command"]


def test_report_markdown_and_no_secrets():
    plan = cp.plan_promotion([SYNTH], "pilot_served_v1")
    md = cp.approval_report_markdown(plan)
    assert "Collection Promotion Approval Report" in md
    blob = md + json.dumps(plan)
    for forbidden in ("sk-", "Bearer ", "X-Api-Key", "OIDC_CLIENT_SECRET", "ADMIN_AUTH_TOKEN"):
        assert forbidden not in blob


def test_no_live_vectorstore_mutation_by_planning(monkeypatch):
    # plan_promotion must not call into the vectorstore at all.
    import rag_core.store as store
    called = {"n": 0}
    monkeypatch.setattr(store, "get_vectorstore", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    cp.plan_promotion([SYNTH], "pilot_served_v1")
    assert called["n"] == 0
