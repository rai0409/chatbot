from rag_core import retrieval


def test_heuristic_tokenize_with_normalized_preprocessing():
    tokens = retrieval._heuristic_tokenize('「ＰＲ２」　ＡＢＣ１２３')
    assert "pr2" in tokens
    assert "abc123" in tokens


def test_query_match_terms_uses_shared_salient_extraction():
    exact_terms, quoted_terms, id_terms = retrieval._query_match_terms(
        '「ＡＢＣ１２３」 PR2 カタカナ語 漢字複合語'
    )
    assert "abc123" in quoted_terms
    assert "pr2" in id_terms
    assert "カタカナ語" in exact_terms
    assert "漢字複合語" in exact_terms
