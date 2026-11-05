import json
from datetime import datetime, timezone

import pytest

from eval.tenant_eval import ledger, proposals
from eval.tenant_eval.proposals import ProposalError
from rag_core import profile_loader, profile_store, query_preprocess

PID = "tenant_a"
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("TENANT_EVAL_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("TENANT_EVAL_PROPOSALS_DIR", str(tmp_path / "proposals"))
    source = tmp_path / "profiles" / PID / "exported"
    source.mkdir(parents=True)
    for name, value in {"profile.json": {"project_id": PID}, "question_type_rules.json": {"rules": []},
                        "domain_terms.json": {"terms": []}, "synonyms.json": {"synonyms": []},
                        "retrieval_boost_rules.json": {"rules": []}, "validation_rules.json": {"rules": []},
                        "answer_templates.json": {"templates": []}}.items():
        (source / name).write_text(json.dumps(value), encoding="utf-8")
    (source / "golden_qa.jsonl").write_text("", encoding="utf-8")
    return profile_store.create_version(PID)["version"]


def _evaluated(version, *, pid="p1", category="synonym", decision="pass", change=None):
    """A proposal that has been through create, classify, propose and evaluate."""
    proposals.create(pid, profile_id=PID, title="同義語の追加", actor="山田太郎", source={"feedback": 3}, now=T0)
    proposals.classify(pid, actor="山田太郎", category=category, now=T0)
    change = change or {"profile_version": version}
    proposals.propose(pid, actor="山田太郎", change=change, now=T0)
    entry = ledger.record({"proposal_id": pid, "eval_set_sha256": "s", "decision": decision, "run_id": "r1",
                           "conditions": {"declared": proposals.declared_change(proposals.get(pid)["change"])}})
    return proposals.attach_evaluation(pid, actor="山田太郎", ledger_seq=entry["seq"], now=T0)


def test_the_whole_life_of_a_change_with_every_move_recorded(env):
    p = _evaluated(env)
    assert p["state"] == "evaluated" and p["evaluation"]["decision"] == "pass" and p["proposed_by"] == "山田太郎"
    p = proposals.approve("p1", actor="佐藤次郎", note="確認した", now=T0)
    assert p["state"] == "approved" and p["approvals"][0]["actor"] == "佐藤次郎"
    for expected in ("shadow", "canary", "live", "monitoring"):
        p = proposals.advance("p1", actor="田中太郎", evidence=f"{expected} の確認結果", now=T0)
        assert p["state"] == expected
    p = proposals.retire("p1", actor="田中太郎", reason="次の版に置き換え", now=T0)
    assert p["state"] == "retired"
    moves = [(h["from"], h["to"]) for h in p["history"]]
    assert moves == [(None, "observed"), ("observed", "classified"), ("classified", "proposed"),
                     ("proposed", "evaluated"), ("evaluated", "approved"), ("approved", "shadow"),
                     ("shadow", "canary"), ("canary", "live"), ("live", "monitoring"), ("monitoring", "retired")]
    assert all(h["actor"] and h["at"] for h in p["history"]) and p["rev"] == len(moves) - 1
    assert proposals.get("p1") == p and [x["id"] for x in proposals.list_proposals("retired")] == ["p1"]
    assert proposals.list_proposals("live") == []


def test_a_finished_proposal_cannot_be_moved_again(env):
    _evaluated(env)
    proposals.approve("p1", actor="佐藤次郎", now=T0)
    proposals.retire("p1", actor="佐藤次郎", reason="中止", now=T0)
    for call in (lambda: proposals.advance("p1", actor="a", evidence="e"), lambda: proposals.retire("p1", actor="a", reason="r"),
                 lambda: proposals.reject("p1", actor="a", reason="r"), lambda: proposals.approve("p1", actor="a")):
        with pytest.raises(ProposalError, match="is retired"):
            call()


def test_steps_cannot_be_skipped_or_repeated(env):
    proposals.create("p1", profile_id=PID, title="t", actor="山田太郎", now=T0)
    with pytest.raises(ProposalError, match="needs classified or evaluated"):
        proposals.propose("p1", actor="山田太郎", change={"profile_version": env})
    with pytest.raises(ProposalError, match="needs proposed"):
        proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=1)
    with pytest.raises(ProposalError, match="needs evaluated"):
        proposals.approve("p1", actor="佐藤次郎")
    with pytest.raises(ProposalError, match="needs approved or shadow"):
        proposals.advance("p1", actor="佐藤次郎", evidence="e")
    proposals.classify("p1", actor="山田太郎", category="synonym", now=T0)
    with pytest.raises(ProposalError, match="needs observed"):
        proposals.classify("p1", actor="山田太郎", category="synonym")


def test_the_proposer_cannot_approve_and_nobody_approves_twice(env):
    _evaluated(env, category="synonym")
    with pytest.raises(ProposalError, match="proposer cannot approve"):
        proposals.approve("p1", actor="山田太郎")
    proposals.approve("p1", actor="佐藤次郎", now=T0)
    assert proposals.get("p1")["state"] == "approved"


def test_a_failed_evaluation_cannot_be_approved_and_an_insufficient_one_needs_a_reason(env):
    _evaluated(env, pid="bad", decision="fail")
    with pytest.raises(ProposalError, match="failed change cannot be approved"):
        proposals.approve("bad", actor="佐藤次郎", accept_insufficient_reason="だめでも通したい")
    _evaluated(env, pid="few", decision="insufficient")
    for reason in (None, "", "   "):
        with pytest.raises(ProposalError, match="accept_insufficient_reason"):
            proposals.approve("few", actor="佐藤次郎", accept_insufficient_reason=reason)
    p = proposals.approve("few", actor="佐藤次郎", accept_insufficient_reason="件数が少ないが影子で確認する", now=T0)
    assert p["state"] == "approved" and p["approvals"][0]["accepted_insufficient"].startswith("件数")
    assert p["history"][-1]["evidence"]["accepted_insufficient"].startswith("件数")


def test_a_distance_guard_change_needs_two_different_approvers(env):
    p = _evaluated(env, pid="guard", category="guard_threshold", change={"knobs": {"RAG_HARD_MAX_DIST": 0.9}})
    assert p["change"] == {"knobs": {"RAG_HARD_MAX_DIST": 0.9}} and proposals.approvals_required(p) == 2
    p = proposals.approve("guard", actor="佐藤次郎", now=T0)
    assert p["state"] == "evaluated" and len(p["approvals"]) == 1
    with pytest.raises(ProposalError, match="already approved"):
        proposals.approve("guard", actor="佐藤次郎")
    with pytest.raises(ProposalError, match="proposer cannot approve"):
        proposals.approve("guard", actor="山田太郎")
    p = proposals.approve("guard", actor="田中太郎", now=T0)
    assert p["state"] == "approved" and [a["actor"] for a in p["approvals"]] == ["佐藤次郎", "田中太郎"]
    low = _evaluated(env, pid="low", category="retrieval_setting", change={"knobs": {"HYBRID_RRF_K": 40}})
    assert proposals.approvals_required(low) == 1


def test_the_evaluation_must_be_of_this_proposal_and_this_change(env):
    proposals.create("p1", profile_id=PID, title="t", actor="山田太郎", now=T0)
    proposals.classify("p1", actor="山田太郎", category="retrieval_setting", now=T0)
    proposals.propose("p1", actor="山田太郎", change={"knobs": {"HYBRID_RRF_K": 40}}, now=T0)
    other_proposal = ledger.record({"proposal_id": "p2", "decision": "pass",
                                    "conditions": {"declared": {"knobs": {"HYBRID_RRF_K": 40}}}})
    other_change = ledger.record({"proposal_id": "p1", "decision": "pass",
                                  "conditions": {"declared": {"knobs": {"HYBRID_RRF_K": 30}}}})
    no_conditions = ledger.record({"proposal_id": "p1", "decision": "pass"})
    with pytest.raises(ProposalError, match="another proposal"):
        proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=other_proposal["seq"])
    with pytest.raises(ProposalError, match="different change"):
        proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=other_change["seq"])
    with pytest.raises(ProposalError, match="different change"):
        proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=no_conditions["seq"])
    with pytest.raises(ProposalError, match="no entry 99"):
        proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=99)
    good = ledger.record({"proposal_id": "p1", "decision": "pass", "run_id": "r9",
                          "conditions": {"declared": {"knobs": {"HYBRID_RRF_K": 40}}}})
    assert proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=good["seq"], now=T0)["evaluation"]["run_id"] == "r9"


def test_a_tampered_ledger_does_not_count_as_an_evaluation(env):
    proposals.create("p1", profile_id=PID, title="t", actor="山田太郎", now=T0)
    proposals.classify("p1", actor="山田太郎", category="retrieval_setting", now=T0)
    proposals.propose("p1", actor="山田太郎", change={"knobs": {"HYBRID_RRF_K": 40}}, now=T0)
    entry = ledger.record({"proposal_id": "p1", "decision": "fail", "conditions": {"declared": {"knobs": {"HYBRID_RRF_K": 40}}}})
    path = ledger.ledger_path()
    path.write_text(path.read_text(encoding="utf-8").replace('"fail"', '"pass"'), encoding="utf-8")
    with pytest.raises(ProposalError, match="evaluation ledger"):
        proposals.attach_evaluation("p1", actor="山田太郎", ledger_seq=entry["seq"])


def test_the_change_must_match_its_category_and_be_valid(env):
    def fresh(category, pid="x"):
        proposals.create(pid, profile_id=PID, title="t", actor="山田太郎", now=T0)
        proposals.classify(pid, actor="山田太郎", category=category, now=T0)

    fresh("synonym", "a")
    with pytest.raises(ProposalError, match="needs a profile_version"):
        proposals.propose("a", actor="山田太郎", change={"knobs": {"HYBRID_RRF_K": 40}})
    with pytest.raises(ProposalError, match="profile version"):
        proposals.propose("a", actor="山田太郎", change={"profile_version": "0123456789abcdef"})
    with pytest.raises(ProposalError, match="profile_version and/or knobs"):
        proposals.propose("a", actor="山田太郎", change={"profile_version": env, "other": 1})
    with pytest.raises(ProposalError):
        proposals.propose("a", actor="山田太郎", change={"profile_version": env, "manual": "x"})
    fresh("guard_threshold", "b")
    with pytest.raises(ProposalError, match="needs knobs"):
        proposals.propose("b", actor="山田太郎", change={"profile_version": env})
    for knobs in ({"DATABASE_URL": 1}, {"RAG_HARD_MAX_DIST": 5.0}):
        with pytest.raises(ValueError):
            proposals.propose("b", actor="山田太郎", change={"knobs": knobs})
    assert proposals.get("b")["state"] == "classified"  # nothing was recorded by the refusals


def test_a_manual_proposal_is_closed_by_hand_and_cannot_be_evaluated_or_approved(env):
    proposals.create("m", profile_id=PID, title="マニュアルの追加が必要", actor="山田太郎", now=T0)
    proposals.classify("m", actor="山田太郎", category="ingest_gap", now=T0)
    with pytest.raises(ProposalError, match="manual"):
        proposals.propose("m", actor="山田太郎", change={"profile_version": env})
    proposals.propose("m", actor="山田太郎", change={"manual": "資料を取り込む"}, now=T0)
    entry = ledger.record({"proposal_id": "m", "decision": "pass", "conditions": {"declared": {}}})
    with pytest.raises(ProposalError, match="manual proposal cannot be evaluated"):
        proposals.attach_evaluation("m", actor="山田太郎", ledger_seq=entry["seq"])
    assert proposals.retire("m", actor="佐藤次郎", reason="資料を取り込んだ", now=T0)["state"] == "retired"


def test_an_unevaluated_change_is_rejected_not_retired_and_a_rejection_needs_a_reason(env):
    proposals.create("r", profile_id=PID, title="t", actor="山田太郎", now=T0)
    proposals.classify("r", actor="山田太郎", category="synonym", now=T0)
    proposals.propose("r", actor="山田太郎", change={"profile_version": env}, now=T0)
    with pytest.raises(ProposalError, match="rejected, not retired"):
        proposals.retire("r", actor="佐藤次郎", reason="x")
    with pytest.raises(ProposalError, match="reason is required"):
        proposals.reject("r", actor="佐藤次郎", reason=" ")
    assert proposals.reject("r", actor="佐藤次郎", reason="目的に合わない", now=T0)["state"] == "rejected"
    with pytest.raises(ProposalError):
        proposals.reject("r", actor="佐藤次郎", reason="again")


def test_revising_an_evaluated_change_clears_the_evaluation_and_the_approvals(env):
    p = _evaluated(env, pid="v", category="guard_threshold", change={"knobs": {"RAG_HARD_MAX_DIST": 0.9}})
    proposals.approve("v", actor="佐藤次郎", now=T0)
    p = proposals.propose("v", actor="山田太郎", change={"knobs": {"RAG_HARD_MAX_DIST": 0.85}}, now=T0)
    assert p["state"] == "proposed" and p["evaluation"] is None and p["approvals"] == []
    assert p["history"][-1]["note"] == "revised" and p["change"] == {"knobs": {"RAG_HARD_MAX_DIST": 0.85}}
    with pytest.raises(ProposalError, match="needs evaluated"):
        proposals.approve("v", actor="佐藤次郎")


def test_actors_text_and_ids_are_validated(env):
    for bad in ("../x", "", "a/b", "x" * 65, "日本語"):
        with pytest.raises(ProposalError, match="invalid proposal id"):
            proposals.create(bad, profile_id=PID, title="t", actor="山田太郎")
        with pytest.raises(ProposalError, match="invalid proposal id"):
            proposals.get(bad)
    with pytest.raises(ProposalError, match="invalid profile id"):
        proposals.create("ok", profile_id="../x", title="t", actor="山田太郎")
    with pytest.raises(ProposalError, match="actor"):
        proposals.create("ok", profile_id=PID, title="t", actor=" ")
    with pytest.raises(ProposalError, match="title"):
        proposals.create("ok", profile_id=PID, title="", actor="山田太郎")
    proposals.create("ok", profile_id=PID, title="t", actor="山田太郎")
    with pytest.raises(ProposalError, match="already exists"):
        proposals.create("ok", profile_id=PID, title="t", actor="山田太郎")
    with pytest.raises(ProposalError, match="unknown category"):
        proposals.classify("ok", actor="山田太郎", category="free_text")
    with pytest.raises(ProposalError, match="not found"):
        proposals.get("missing")


def test_a_concurrent_update_is_refused_and_a_broken_file_is_reported(env, monkeypatch):
    proposals.create("c", profile_id=PID, title="t", actor="山田太郎", now=T0)
    stale = proposals.get("c")
    proposals.classify("c", actor="山田太郎", category="synonym", now=T0)
    with pytest.raises(ProposalError, match="changed by someone else"):
        proposals._save(stale, expected_rev=stale["rev"])
    assert proposals.get("c")["state"] == "classified"
    (proposals.store_dir() / "c.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ProposalError, match="unreadable"):
        proposals.get("c")
    (proposals.store_dir() / "c.json").write_text(json.dumps({"schema": 9}), encoding="utf-8")
    with pytest.raises(ProposalError, match="unsupported format"):
        proposals.get("c")
