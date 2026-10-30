"""S5+: the golden questions written in the console are what an evaluation judges on."""
from argparse import Namespace

import pytest

import config
from rag_core import golden_qa, golden_store
from scripts import tenant_eval


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "GOLDEN_QA_DIR", "")
    return tmp_path


def _args(**kw):
    base = dict(profile="volcano_demo", tenant="tenant_a", golden_source="console", golden_tenant=None, exclude_change=None, folds=None,
                held_out=None, seed="")
    base.update(kw)
    return Namespace(**base)


def _profile(monkeypatch, rows):
    monkeypatch.setattr(tenant_eval.profile_loader, "load_rag_profile", lambda profile_id: {"golden_qa": rows})


def test_console_cases_are_what_is_judged_when_asked_for(store, monkeypatch):
    _profile(monkeypatch, [{"case_id": "p1", "question": "プロファイルの質問", "reference": "基準"}])
    golden_store.create("tenant_a", {"case_id": "c1", "question": "コンソールの質問", "expected_any": ["3営業日"], "priority": 3}, by="山田太郎")
    golden_store.create("tenant_a", {"case_id": "c2", "question": "無効な質問", "reference": "x", "enabled": False}, by="山田太郎")
    console_only = tenant_eval._select_cases(_args())
    assert [c["case_id"] for c in console_only] == ["c1"]  # the disabled one is not judged, the profile's is not read
    both = tenant_eval._select_cases(_args(golden_source="both"))
    assert [c["case_id"] for c in both] == ["c1", "p1"]  # highest priority first, as everywhere
    profile_only = tenant_eval._select_cases(_args(golden_source="profile"))
    assert [c["case_id"] for c in profile_only] == ["p1"]  # the default is unchanged
    assert tenant_eval._select_cases(_args(golden_source=None)) == profile_only


def test_a_case_in_both_places_is_a_problem_and_not_a_silent_choice(store, monkeypatch):
    _profile(monkeypatch, [{"case_id": "same", "question": "q", "reference": "r"}])
    golden_store.create("tenant_a", {"case_id": "same", "question": "q2", "reference": "r2"}, by="山田太郎")
    with pytest.raises(ValueError, match="is in the profile and in the console"):
        tenant_eval._select_cases(_args(golden_source="both"))


def test_the_console_tenant_can_differ_from_the_evaluated_tenant_and_nothing_to_judge_is_refused(store, monkeypatch):
    _profile(monkeypatch, [])
    golden_store.create("tenant_b", {"case_id": "b1", "question": "q", "reference": "r"}, by="山田太郎")
    assert [c["case_id"] for c in tenant_eval._select_cases(_args(golden_tenant="tenant_b"))] == ["b1"]
    with pytest.raises(ValueError, match="no cases to evaluate"):
        tenant_eval._select_cases(_args(golden_tenant="tenant_none"))


def test_what_the_console_exports_is_what_the_evaluation_reads(store):
    golden_store.create("tenant_a", {"case_id": "c1", "question": "q", "reference": "r", "tags": ["x"]}, by="山田太郎")
    rows = golden_store.export_cases("tenant_a")
    cases, problems = golden_qa.load_cases({"golden_qa": rows})
    assert problems == [] and [c["case_id"] for c in cases] == ["c1"] and "created_by" not in rows[0]
