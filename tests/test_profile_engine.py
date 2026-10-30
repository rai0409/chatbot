import pytest

import config
from rag_core import profile_engine, profile_validation, qa
from rag_core.retrieval import RetrievedChunk

PROFILE = {
    "domain_terms": [
        {"term_id": "t1", "category": "carry", "term": "ヘルメット", "aliases": ["ヘルメツト"], "enabled": True},
        {"term_id": "t2", "category": "carry", "term": "ゴーグル", "aliases": [], "enabled": True},
        {"term_id": "t3", "category": "carry", "term": "停止中", "aliases": [], "enabled": False},
        {"term_id": "t4", "category": "wrong", "term": "避難路", "aliases": [], "enabled": True},
    ],
    "retrieval_boost_rules": [
        {
            "rule_id": "carry_rule",
            "applies_to_question_type": "list_items",
            "when_question_contains_any": ["持っていく", "持参"],
            "positive_categories": ["carry"],
            "positive_terms": ["ライト"],
            "positive_weight": 50,
            "negative_categories": ["wrong"],
            "negative_terms": [],
            "negative_weight": -50,
            "enabled": True,
        }
    ],
}


def _chunks(*texts):
    return [RetrievedChunk(text=text, metadata={"id": f"c{i}"}, score=0.30 + 0.01 * i) for i, text in enumerate(texts)]


def _boost(chunks, question="持っていくものは", question_type="list_items", profile=PROFILE, max_delta=0.05):
    return profile_engine.apply_profile_boost(chunks, question, question_type, profile, max_delta=max_delta)


def _ids(chunks):
    return [chunk.metadata["id"] for chunk in chunks]


def test_a_positive_term_moves_the_chunk_up_and_a_negative_term_down():
    chunks = _chunks("避難路の説明", "無関係な文", "ヘルメットを持つ")
    out, trace = _boost(chunks)
    assert _ids(out) == ["c2", "c1", "c0"]
    assert trace["rules"] == ["carry_rule"] and trace["boosted_chunks"] == 2
    assert out[0].metadata["score_details"]["profile_boost"] == {"rules": ["carry_rule"], "points": 50, "delta": 0.025}
    assert out[2].score == pytest.approx(0.30 + 0.025)
    assert out[1] is chunks[1]  # a chunk no rule touches is the same object


def test_terms_come_from_categories_aliases_and_explicit_terms_only_when_enabled():
    rule = PROFILE["retrieval_boost_rules"][0]
    assert profile_engine.rule_terms(PROFILE, rule, "positive") == ["ライト", "ヘルメット", "ヘルメツト", "ゴーグル"]
    assert profile_engine.rule_terms(PROFILE, rule, "negative") == ["避難路"]  # "停止中" is a disabled term


def test_matching_ignores_width_and_case():
    profile = {"retrieval_boost_rules": [{
        "applies_to_question_type": "t", "positive_terms": ["ＡＢＣ"], "positive_weight": 100,
    }]}
    out, _ = _boost(_chunks("無関係", "abc を含む"), question="何でも", question_type="t", profile=profile)
    assert _ids(out) == ["c1", "c0"]


def test_a_rule_applies_only_to_its_question_type_and_trigger_words():
    chunks = _chunks("無関係", "ヘルメット")
    assert _boost(chunks, question_type="other")[1]["rules"] == []
    assert _ids(_boost(chunks, question="全く別の質問")[0]) == ["c0", "c1"]  # no trigger word in the question
    assert _boost(chunks, question="持参する物")[1]["rules"] == ["carry_rule"]
    open_rule = {"retrieval_boost_rules": [{"applies_to_question_type": "list_items", "positive_terms": ["x"],
                                            "positive_weight": 10}]}
    assert _boost(chunks, question="何でも", profile=open_rule)[1]["rules"] == ["rule_1"]  # no triggers: always


def test_a_disabled_rule_is_ignored():
    profile = {"retrieval_boost_rules": [{**PROFILE["retrieval_boost_rules"][0], "enabled": False}],
               "domain_terms": PROFILE["domain_terms"]}
    chunks = _chunks("無関係", "ヘルメット")
    out, trace = _boost(chunks, profile=profile)
    assert out == chunks and trace["rules"] == []


def test_the_effect_is_bounded_by_max_delta_however_many_rules_add_up():
    rule = {"applies_to_question_type": "t", "positive_terms": ["a"], "positive_weight": 90}
    profile = {"retrieval_boost_rules": [rule, {**rule, "rule_id": "second"}, {**rule, "rule_id": "third"}]}
    out, _ = _boost(_chunks("zzz", "a"), question="q", question_type="t", profile=profile, max_delta=0.04)
    assert out[0].score == pytest.approx(0.31 - 0.04)


def test_a_zero_limit_or_bad_weights_change_nothing():
    chunks = _chunks("無関係", "ヘルメット")
    assert _boost(chunks, max_delta=0.0)[0] == chunks
    bad = {"retrieval_boost_rules": [{"applies_to_question_type": "t", "positive_terms": ["a"], "positive_weight": True},
                                     {"applies_to_question_type": "t", "positive_terms": ["a"], "positive_weight": "x"}]}
    assert _boost(_chunks("a"), question="q", question_type="t", profile=bad)[0][0].score == pytest.approx(0.30)
    assert _boost([], max_delta=0.05)[0] == []


def test_the_input_is_not_modified_and_ties_keep_their_order():
    chunks = _chunks("ヘルメット", "ゴーグル")
    chunks[1].score = chunks[0].score
    before = [(c.score, dict(c.metadata)) for c in chunks]
    out, _ = _boost(chunks)
    assert _ids(out) == ["c0", "c1"]
    assert [(c.score, dict(c.metadata)) for c in chunks] == before


# --- in the retrieval stage ---------------------------------------------------------------------


@pytest.fixture
def stage(monkeypatch):
    chunks = _chunks("避難路の説明", "無関係な文", "ヘルメットを持つ")
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *a, **k: list(chunks))
    monkeypatch.setattr(qa, "rerank_chunks", lambda question, items, intent=None: list(items))
    monkeypatch.setattr(qa, "_decorate_score_details", lambda question, items, query_type=None: list(items))
    monkeypatch.setattr(qa, "expand_parent_chunks", lambda items, **kwargs: list(items))
    monkeypatch.setattr(qa, "detect_question_type", lambda question, profile: "list_items")
    monkeypatch.setattr(qa, "load_rag_profile", lambda profile_id: {**PROFILE, "warnings": []})
    monkeypatch.setattr(config, "KEYWORD_BOOST_ENABLED", False)
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", False)

    def run():
        stats = {}
        _before, ranked, context = qa._retrieve_and_rerank(
            "持っていくものは", "持っていくものは", scoring_query="持っていくものは", client=None, top_k=5,
            intent="other", query_type="other", stats=stats, rag_profile_id="tenant_a",
        )
        return ranked, context, stats

    return run


def test_the_stage_does_nothing_unless_it_is_switched_on(stage, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_BOOST_ENABLED", False)
    ranked, _context, stats = stage()
    assert _ids(ranked) == ["c0", "c1", "c2"] and "profile_boost" not in stats


def test_the_stage_reorders_and_records_the_trace_when_switched_on(stage, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_BOOST_ENABLED", True)
    monkeypatch.setattr(config, "PROFILE_BOOST_MAX_DELTA", 0.05)
    ranked, context, stats = stage()
    assert _ids(ranked) == ["c2", "c1", "c0"] and _ids(context) == ["c2", "c1", "c0"]
    assert stats["profile_boost"]["rules"] == ["carry_rule"] and stats["profile_boost"]["boosted_chunks"] == 2


def test_a_profile_with_warnings_is_not_applied(stage, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_BOOST_ENABLED", True)
    monkeypatch.setattr(qa, "load_rag_profile", lambda profile_id: {**PROFILE, "warnings": ["x: file not found"]})
    ranked, _context, stats = stage()
    assert _ids(ranked) == ["c0", "c1", "c2"] and "profile_boost" not in stats


def test_the_answer_cache_key_follows_the_boost_settings():
    from rag_core import answer_cache

    assert {"PROFILE_BOOST_ENABLED", "PROFILE_BOOST_MAX_DELTA"} <= set(answer_cache._SETTING_NAMES)


# --- validation rules -----------------------------------------------------------------------------


def test_a_disabled_validation_rule_is_not_applied():
    rule = {"rule_id": "r", "applies_to_question_type": "t", "answer_must_contain_any": ["必須"],
            "fallback_if_failed": True}
    profile = {"validation_rules": [{**rule, "enabled": False}]}
    assert profile_validation.validate_answer_with_profile("q", "本文", "t", profile)["passed"] is True
    profile = {"validation_rules": [{**rule, "enabled": True}]}
    assert profile_validation.validate_answer_with_profile("q", "本文", "t", profile)["passed"] is False
    profile = {"validation_rules": [rule]}  # no flag: enabled, as before
    assert profile_validation.validate_answer_with_profile("q", "本文", "t", profile)["passed"] is False
