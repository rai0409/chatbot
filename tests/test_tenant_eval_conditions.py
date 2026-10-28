import pytest

import config
from eval.tenant_eval import conditions, evaluate, ledger
from eval.tenant_eval.conditions import ConditionsError
from rag_core import golden_qa, knob_registry, profile_loader, profile_store, query_preprocess

PID = "tenant_a"


def _base(**changes):
    base = {"profile_id": PID, "profile_version": "aaaaaaaaaaaaaaaa", "profile_pinned": True, "tenant_id": "default",
            "collection": "col", "generation_id": "g1", "knobs": {"HYBRID_RRF_K": 60, "PROFILE_BOOST_ENABLED": False}}
    knobs = changes.pop("knobs", {})
    base.update(changes)
    base["knobs"] = {**base["knobs"], **knobs}
    return base


def test_compare_lists_only_what_differs():
    cand = _base(profile_version="bbbbbbbbbbbbbbbb", knobs={"HYBRID_RRF_K": 40})
    assert conditions.compare_conditions(_base(), cand) == {
        "profile_version": {"baseline": "aaaaaaaaaaaaaaaa", "candidate": "bbbbbbbbbbbbbbbb"},
        "knobs.HYBRID_RRF_K": {"baseline": 60, "candidate": 40},
    }
    assert conditions.compare_conditions(_base(), _base()) == {}


def test_only_the_declared_change_may_differ():
    cand = _base(profile_version="bbbbbbbbbbbbbbbb", knobs={"HYBRID_RRF_K": 40})
    declared = {"profile_version": "bbbbbbbbbbbbbbbb", "knobs": {"HYBRID_RRF_K": 40}}
    assert set(conditions.assert_declared_changes(_base(), cand, declared)) == {"profile_version", "knobs.HYBRID_RRF_K"}

    with pytest.raises(ConditionsError, match="differs without being declared: generation_id"):
        conditions.assert_declared_changes(_base(), _base(generation_id="g2", knobs={"HYBRID_RRF_K": 40}),
                                           {"knobs": {"HYBRID_RRF_K": 40}})
    with pytest.raises(ConditionsError, match="differs without being declared: knobs.PROFILE_BOOST_ENABLED"):
        conditions.assert_declared_changes(_base(), _base(knobs={"HYBRID_RRF_K": 40, "PROFILE_BOOST_ENABLED": True}),
                                           {"knobs": {"HYBRID_RRF_K": 40}})
    with pytest.raises(ConditionsError, match="not in effect in the candidate run: knobs.HYBRID_RRF_K"):
        conditions.assert_declared_changes(_base(), _base(), {"knobs": {"HYBRID_RRF_K": 40}})
    with pytest.raises(ConditionsError, match="identical in the baseline"):
        conditions.assert_declared_changes(_base(), _base(), {"knobs": {"HYBRID_RRF_K": 60}})


@pytest.fixture
def versions(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.delenv("ACTIVE_GENERATION_FILE", raising=False)
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    source = tmp_path / "profiles" / PID / "exported"
    source.mkdir(parents=True)

    def write(variants):
        files = {"profile.json": {"project_id": PID}, "question_type_rules.json": {"rules": []},
                 "domain_terms.json": {"terms": []},
                 "synonyms.json": {"synonyms": [{"canonical": "サーバー", "variants": variants}]},
                 "retrieval_boost_rules.json": {"rules": []}, "validation_rules.json": {"rules": []},
                 "answer_templates.json": {"templates": []}}
        for name, value in files.items():
            (source / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        (source / "golden_qa.jsonl").write_text("", encoding="utf-8")

    write(["サーバ"])
    v1 = profile_store.create_version(PID)["version"]
    write(["サーバ", "ｻｰﾊﾞｰ"])
    v2 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v1)
    profile_loader.reload_rag_profile_cache()
    yield v1, v2
    profile_loader.reload_rag_profile_cache()


def test_collect_reads_the_profile_version_the_index_and_the_knobs(versions):
    v1, _v2 = versions
    found = conditions.collect(PID, tenant_id="t1")
    assert found["profile_version"] == v1 and found["profile_pinned"] is True and found["tenant_id"] == "t1"
    assert found["knobs"] == knob_registry.snapshot() and found["profile_id"] == PID
    assert set(found) == {"profile_id", "profile_version", "profile_pinned", "tenant_id", "collection",
                          "generation_id", "knobs"}


def test_the_candidate_environment_sets_the_version_and_knobs_then_restores_them(versions, monkeypatch):
    v1, v2 = versions
    monkeypatch.setattr(config, "HYBRID_RRF_K", 60)
    before = conditions.collect(PID)
    with conditions.candidate_environment(PID, version=v2, knobs={"HYBRID_RRF_K": 40}):
        inside = conditions.collect(PID)
        assert inside["profile_version"] == v2 and config.HYBRID_RRF_K == 40
        diff = conditions.assert_declared_changes(before, inside, {"profile_version": v2, "knobs": {"HYBRID_RRF_K": 40}})
        assert set(diff) == {"profile_version", "knobs.HYBRID_RRF_K"}
    assert config.HYBRID_RRF_K == 60 and conditions.collect(PID) == before and before["profile_version"] == v1


def test_the_environment_is_restored_when_the_block_raises(versions, monkeypatch):
    _v1, _v2 = versions
    monkeypatch.setattr(config, "HYBRID_RRF_K", 60)
    with pytest.raises(RuntimeError):
        with conditions.candidate_environment(PID, knobs={"HYBRID_RRF_K": 40}):
            raise RuntimeError("boom")
    assert config.HYBRID_RRF_K == 60


def test_unregistered_or_invalid_knobs_and_unknown_versions_are_refused_before_anything_changes(versions, monkeypatch):
    monkeypatch.setattr(config, "HYBRID_RRF_K", 60)
    for knobs in ({"DATABASE_URL": "x"}, {"HYBRID_RRF_K": 0}, {"HYBRID_RRF_K": 40, "HYBRID_DENSE_WEIGHT": 99}):
        with pytest.raises(knob_registry.KnobError):
            with conditions.candidate_environment(PID, knobs=knobs):
                pass
    with pytest.raises(profile_store.ProfileStoreError):
        with conditions.candidate_environment(PID, version="0123456789abcdef", knobs={"HYBRID_RRF_K": 40}):
            pass
    assert config.HYBRID_RRF_K == 60


def test_evaluate_candidate_runs_the_candidate_inside_the_context(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HYBRID_RRF_K", 60)
    cases = [golden_qa.parse_case({"case_id": f"c{i}", "question": f"質問 c{i}", "expected_all": ["正解"]})[0]
             for i in range(5)]
    seen = {"baseline": set(), "candidate": set()}

    def make(label):
        def answer(question):
            seen[label].add(config.HYBRID_RRF_K)
            return {"answer_text": "正解", "has_citation": True, "question_type": "", "guard_reason": None}
        return answer

    evaluate.evaluate_candidate(
        cases, make("baseline"), make("candidate"), proposal_id="p", ledger_file=tmp_path / "l.jsonl",
        candidate_context=conditions.candidate_environment(PID, knobs={"HYBRID_RRF_K": 40}),
    )
    assert seen == {"baseline": {60}, "candidate": {40}} and config.HYBRID_RRF_K == 60
    assert ledger.verify_chain(tmp_path / "l.jsonl") == 1
