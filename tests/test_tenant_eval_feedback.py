import json
from datetime import datetime, timezone

import pytest

from eval.tenant_eval import feedback, proposals
from rag_core import profile_loader, query_preprocess

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def _event(token, kind="bad", reason="wrong_intent", tenant="default", comment="個人の連絡先 03-1234-5678"):
    return {"feedback_token": token, "feedback_type": kind, "bad_reason": reason if kind == "bad" else None,
            "tenant_id": tenant, "comment": comment}


def test_every_ui_reason_has_categories_and_unknown_ones_are_other():
    assert feedback.categories_for("wrong_intent") == ("synonym", "boost_rule")
    assert feedback.categories_for("outdated") == ("stale_content",)
    for reason in ("wrong_intent", "incorrect", "outdated", "wrong_source", "incomplete"):
        assert feedback.categories_for(reason)[0] in proposals.CATEGORIES
    assert feedback.categories_for("other") == ("other",) and feedback.categories_for(None) == ("other",)
    assert set(c for cats in feedback.REASON_CATEGORIES.values() for c in cats) <= set(proposals.CATEGORIES)


def test_one_vote_per_token_the_latest_wins_and_tokenless_events_each_count():
    events = [_event("t1", "bad"), _event("t1", "good"), _event("t2", "bad"), _event("", "bad"), _event("", "bad")]
    result = feedback.aggregate(events)
    row = result["tenants"]["default"]
    assert (row["good"], row["bad"], row["total"]) == (1, 3, 4)


def test_aggregate_counts_per_tenant_with_intervals_and_ignores_other_event_types():
    events = ([_event(f"a{i}", "bad", "wrong_intent", "t1") for i in range(6)]
              + [_event(f"b{i}", "good", tenant="t1") for i in range(14)]
              + [_event(f"c{i}", "bad", "outdated", "t2") for i in range(2)]
              + [{"feedback_token": "x", "feedback_type": "neutral", "tenant_id": "t1"}])
    result = feedback.aggregate(events, min_count=5)
    t1 = result["tenants"]["t1"]
    assert t1["total"] == 20 and t1["bad"] == 6 and t1["bad_rate"] == pytest.approx(0.3)
    assert t1["bad_rate_low"] < 0.3 < t1["bad_rate_high"]
    assert t1["reasons"] == {"wrong_intent": 6} and t1["categories"] == {"synonym": 6}
    assert result["tenants"]["t2"]["categories"] == {"stale_content": 2}
    assert [(o["tenant_id"], o["reason"], o["count"]) for o in result["observations"]] == [("t1", "wrong_intent", 6)]
    obs = result["observations"][0]
    assert obs["primary_category"] == "synonym" and obs["share_of_bad"] == 1.0
    assert obs["feedback_tokens"] == ["a0", "a1", "a2", "a3", "a4"] and "comment" not in json.dumps(obs)
    assert feedback.aggregate([]) == {"tenants": {}, "observations": []}


def test_a_missing_reason_is_other_and_observations_are_ordered_by_count():
    events = ([_event(f"a{i}", "bad", None) for i in range(5)] + [_event(f"b{i}", "bad", "incomplete") for i in range(7)])
    obs = feedback.aggregate(events, min_count=5)["observations"]
    assert [(o["reason"], o["count"]) for o in obs] == [("incomplete", 7), ("other", 5)]


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("TENANT_EVAL_PROPOSALS_DIR", str(tmp_path / "proposals"))
    return tmp_path


def test_open_observations_creates_observed_proposals_once_per_day_without_personal_text(store):
    events = [_event(f"a{i}", "bad", "wrong_intent", "t1/x") for i in range(6)]
    observations = feedback.aggregate(events, min_count=5)["observations"]
    created = feedback.open_observations(observations, profile_id="tenant_a", actor="田中太郎", now=T0)
    assert created == ["fb-t1-x-wrong_intent-20261027"]
    proposal = proposals.get(created[0])
    assert proposal["state"] == "observed" and proposal["profile_id"] == "tenant_a"
    assert proposal["source"]["count"] == 6 and proposal["source"]["suggested_categories"] == ["synonym", "boost_rule"]
    assert "03-1234-5678" not in json.dumps(proposal) and proposal["history"][0]["actor"] == "田中太郎"
    assert feedback.open_observations(observations, profile_id="tenant_a", actor="田中太郎", now=T0) == []
    later = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
    assert feedback.open_observations(observations, profile_id="tenant_a", actor="田中太郎", now=later) == [
        "fb-t1-x-wrong_intent-20261028"]
    proposals.classify(created[0], actor="田中太郎", category=proposal["source"]["suggested_categories"][0], now=T0)
    assert proposals.get(created[0])["state"] == "classified"


def test_open_observations_reports_a_real_error_instead_of_swallowing_it(store):
    observations = feedback.aggregate([_event(f"a{i}", "bad") for i in range(6)], min_count=5)["observations"]
    with pytest.raises(proposals.ProposalError, match="invalid profile id"):
        feedback.open_observations(observations, profile_id="../x", actor="田中太郎", now=T0)
