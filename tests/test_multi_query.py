import pytest

import config
from rag_core import multi_query, qa, query_preprocess, search_mode
from rag_core.retrieval import RetrievedChunk


# --- search_mode -------------------------------------------------------------------------------


def test_the_deep_flag_is_off_by_default_and_restored_after_the_block():
    assert search_mode.is_deep() is False
    with search_mode.deep_mode():
        assert search_mode.is_deep() is True
        with search_mode.deep_mode(False):
            assert search_mode.is_deep() is False
        assert search_mode.is_deep() is True
    assert search_mode.is_deep() is False
    with pytest.raises(RuntimeError):
        with search_mode.deep_mode():
            raise RuntimeError("boom")
    assert search_mode.is_deep() is False


def test_iterate_in_mode_sets_the_mode_only_while_the_iterator_runs():
    seen = []

    def source():
        for i in range(3):
            seen.append(search_mode.is_deep())
            yield i

    out = []
    for item in search_mode.iterate_in_mode(True, source()):
        out.append((item, search_mode.is_deep()))  # the caller's own code runs outside the mode
    assert seen == [True, True, True] and out == [(0, False), (1, False), (2, False)]
    assert list(search_mode.iterate_in_mode(False, iter([]))) == []


# --- variants ----------------------------------------------------------------------------------


@pytest.fixture
def synonyms(monkeypatch):
    monkeypatch.setattr(query_preprocess, "_synonym_groups", lambda profile_id: [["サーバー", "サーバ"]])


def test_the_question_comes_first_then_the_variants_without_duplicates(synonyms):
    variants = multi_query.build_variants("サーバーの再起動手順を教えてください", limit=5)
    kinds = [v["kind"] for v in variants]
    assert variants[0] == {"query": "サーバーの再起動手順を教えてください", "kind": "original"}
    assert kinds[0] == "original" and "synonym" in kinds and len(kinds) == len(set(zip(kinds, [v["query"] for v in variants])))
    assert len({v["query"] for v in variants}) == len(variants)
    synonym = next(v for v in variants if v["kind"] == "synonym")
    assert "サーバ" in synonym["query"] and "サーバー" not in synonym["query"]


def test_the_limit_counts_the_question_and_one_means_no_variants(synonyms):
    assert [v["kind"] for v in multi_query.build_variants("サーバーの再起動手順", limit=1)] == ["original"]
    assert len(multi_query.build_variants("サーバーの再起動手順を教えてください", limit=2)) <= 2
    assert multi_query.build_variants("", limit=3) == [{"query": "", "kind": "original"}]


def test_no_synonym_group_means_no_synonym_variant(monkeypatch):
    monkeypatch.setattr(query_preprocess, "_synonym_groups", lambda profile_id: [])
    assert "synonym" not in [v["kind"] for v in multi_query.build_variants("サーバーの再起動手順", limit=5)]


def test_the_number_of_queries_follows_the_setting_and_deep_raises_it(monkeypatch):
    monkeypatch.setattr(config, "MULTI_QUERY_MAX_VARIANTS", 2)
    assert multi_query.max_queries() == 2
    with search_mode.deep_mode():
        assert multi_query.max_queries() == 3
    monkeypatch.setattr(config, "MULTI_QUERY_MAX_VARIANTS", 9)
    assert multi_query.max_queries() == 5
    monkeypatch.setattr(config, "MULTI_QUERY_MAX_VARIANTS", 0)
    assert multi_query.max_queries() == 1


def test_it_is_enabled_by_the_setting_or_by_a_deep_request(monkeypatch):
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", False)
    assert multi_query.enabled() is False
    with search_mode.deep_mode():
        assert multi_query.enabled() is True
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", True)
    assert multi_query.enabled() is True


# --- fusion --------------------------------------------------------------------------------------


def _chunk(chunk_id, score):
    return RetrievedChunk(text=f"本文 {chunk_id}", metadata={"id": chunk_id}, score=score)


def _ids(chunks):
    return [c.metadata["id"] for c in chunks]


def test_fusion_orders_by_rrf_keeps_trusted_scores_and_penalizes_variant_only_chunks():
    trusted = [[_chunk("A", 0.30), _chunk("B", 0.35)]]
    variants = [[_chunk("C", 0.20), _chunk("A", 0.10)], [_chunk("C", 0.25)]]
    out = multi_query.fuse_rrf(trusted, variants, k=60, variant_penalty=0.05)
    assert _ids(out) == ["C", "A", "B"]  # C: rank 1 in two variant lists; A: ranks 1 and 2; B: rank 2 once
    by_id = {c.metadata["id"]: c for c in out}
    assert by_id["A"].score == pytest.approx(0.30)  # the trusted score, not the variant's 0.10
    assert by_id["C"].score == pytest.approx(0.20 + 0.05)  # the best variant score plus the penalty
    assert by_id["B"].score == pytest.approx(0.35)
    detail = by_id["C"].metadata["score_details"]["multi_query"]
    assert detail["variant_only"] is True and detail["found_by"] == ["variant:0", "variant:1"]
    assert by_id["A"].metadata["score_details"]["multi_query"]["variant_only"] is False
    assert by_id["A"].metadata["score_details"]["multi_query"]["rrf"] == pytest.approx(1 / 61 + 1 / 62, abs=1e-5)
    assert by_id["A"].metadata["score_details"]["multi_query"]["found_by"] == ["question:0", "variant:0"]


def test_fusion_is_deterministic_does_not_modify_its_input_and_handles_empty_lists():
    a, b = _chunk("A", 0.3), _chunk("B", 0.3)
    before = (a.score, dict(a.metadata))
    first = multi_query.fuse_rrf([[a, b]], [[b, a]], k=60)
    assert _ids(first) == _ids(multi_query.fuse_rrf([[a, b]], [[b, a]], k=60))
    assert _ids(first) == ["A", "B"]  # equal fused rank and distance: the first seen first
    assert (a.score, dict(a.metadata)) == before
    assert multi_query.fuse_rrf([[]], [[]]) == [] and _ids(multi_query.fuse_rrf([[a]], [])) == ["A"]


def test_chunks_without_an_id_are_matched_by_their_text_like_the_candidate_union():
    one = RetrievedChunk(text="同じ本文", metadata={"doc_id": "d"}, score=0.3)
    two = RetrievedChunk(text="同じ本文", metadata={"doc_id": "d"}, score=0.4)
    assert len(multi_query.fuse_rrf([[one]], [[two]])) == 1


# --- in the retrieval stage -------------------------------------------------------------------------


@pytest.fixture
def stage(monkeypatch):
    base = [_chunk("A", 0.30), _chunk("B", 0.35)]
    by_query = {"サーバーの再起動手順": base, "サーバの再起動手順": [_chunk("C", 0.20), _chunk("A", 0.28)]}
    calls = []

    def hybrid(query, *args, **kwargs):
        calls.append(query)
        return list(by_query.get(query, []))

    monkeypatch.setattr(qa, "hybrid_retrieve", hybrid)
    monkeypatch.setattr(qa, "rerank_chunks", lambda question, items, intent=None: list(items))
    monkeypatch.setattr(qa, "_decorate_score_details", lambda question, items, query_type=None: list(items))
    monkeypatch.setattr(qa, "expand_parent_chunks", lambda items, **kwargs: list(items))
    monkeypatch.setattr(query_preprocess, "_synonym_groups", lambda profile_id: [["サーバー", "サーバ"]])
    monkeypatch.setattr(config, "KEYWORD_BOOST_ENABLED", False)
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", False)
    monkeypatch.setattr(config, "PROFILE_BOOST_ENABLED", False)
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(config, "MULTI_QUERY_MAX_VARIANTS", 3)
    monkeypatch.setattr(config, "MULTI_QUERY_RRF_K", 60)
    monkeypatch.setattr(config, "MULTI_QUERY_VARIANT_PENALTY", 0.05)

    def run():
        stats = {}
        question = "サーバーの再起動手順"
        _before, ranked, _context = qa._retrieve_and_rerank(
            question, question, scoring_query=question, client=None, top_k=5, intent="other", query_type="other",
            stats=stats, rag_profile_id="tenant_a",
        )
        return ranked, stats

    return run, calls


def test_by_default_only_the_question_is_searched(stage):
    run, calls = stage
    ranked, stats = run()
    assert calls == ["サーバーの再起動手順"] and _ids(ranked) == ["A", "B"] and "multi_query" not in stats


def test_when_enabled_the_variants_are_searched_fused_and_traced(stage, monkeypatch):
    run, calls = stage
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", True)
    ranked, stats = run()
    assert calls[0] == "サーバーの再起動手順" and "サーバの再起動手順" in calls
    assert _ids(ranked)[0] == "A" and set(_ids(ranked)) == {"A", "B", "C"}
    scores = {c.metadata["id"]: c.score for c in ranked}
    assert scores["A"] == pytest.approx(0.30) and scores["C"] == pytest.approx(0.25)
    traced = stats["multi_query"]
    assert traced["deep"] is False and "synonym" in [v["kind"] for v in traced["variants"]]
    assert all(v["kind"] != "original" for v in traced["variants"])


def test_a_deep_request_searches_the_variants_even_when_the_setting_is_off(stage):
    run, calls = stage
    with search_mode.deep_mode():
        ranked, stats = run()
    assert len(calls) > 1 and "C" in _ids(ranked) and stats["multi_query"]["deep"] is True


def test_one_query_in_all_means_no_extra_search(stage, monkeypatch):
    run, calls = stage
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", True)
    monkeypatch.setattr(config, "MULTI_QUERY_MAX_VARIANTS", 1)
    ranked, _stats = run()
    assert calls == ["サーバーの再起動手順"] and _ids(ranked) == ["A", "B"]
