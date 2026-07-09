from __future__ import annotations

from rag_core.keyword_scorer import apply_keyword_boost, classify_query_type, score_keyword_match
from rag_core.retrieval import RetrievedChunk


def test_classify_query_type_quoted_term_is_exact_lookup():
    assert classify_query_type("「請求書ID」の確認方法") == "exact_lookup"


def test_classify_query_type_identifier_tokens():
    assert classify_query_type("PR2 の対処") == "identifier"
    assert classify_query_type("PR20 の対処") == "identifier"
    assert classify_query_type("エラー ABC-1234 を確認") == "identifier"


def test_classify_query_type_procedure_query():
    assert classify_query_type("パスワード再設定の方法") == "procedure"


def test_classify_query_type_short_ambiguous_query():
    assert classify_query_type("返金") == "ambiguous"


def test_score_keyword_match_quoted_exact_term_hit():
    details = score_keyword_match("「請求書ID」の確認", "請求書ID は明細画面で確認できます。")

    assert details["keyword_score"] > 0
    assert "請求書id" in details["matched_terms"]
    assert "text" in details["matched_fields"]
    assert details["signals"]["quoted_term_hit"] is True
    assert details["signals"]["exact_phrase_hit"] is True


def test_score_keyword_match_identifier_hit_is_strict():
    details = score_keyword_match("PR2 の手順", "PR20 ではなく PR2 の手順です。")

    assert details["keyword_score"] > 0
    assert "pr2" in details["matched_terms"]
    assert "identifier" in details["matched_fields"]
    assert details["signals"]["identifier_hit"] is True


def test_score_keyword_match_title_hit():
    details = score_keyword_match(
        "請求書ID の確認方法",
        "明細画面を開きます。",
        metadata={"title": "請求書IDの確認"},
    )

    assert details["keyword_score"] > 0
    assert "title" in details["matched_fields"]
    assert details["signals"]["title_hit"] is True


def test_score_keyword_match_section_path_hit_with_json_string():
    details = score_keyword_match(
        "支払い方法を変更",
        "設定画面を開きます。",
        metadata={"section_path": '["アカウント", "支払い方法"]'},
    )

    assert details["keyword_score"] > 0
    assert "section_path" in details["matched_fields"]
    assert details["signals"]["section_path_hit"] is True


def test_score_keyword_match_katakana_hit():
    details = score_keyword_match("ログインできません", "ログイン画面でメールアドレスを入力します。")

    assert details["keyword_score"] > 0
    assert "ログイン" in details["matched_terms"]
    assert details["signals"]["katakana_hit"] is True


def test_score_keyword_match_kanji_compound_hit():
    details = score_keyword_match("返金条件は？", "返金条件 は契約プランごとに異なります。")

    assert details["keyword_score"] > 0
    assert "返金条件" in details["matched_terms"]
    assert details["signals"]["kanji_compound_hit"] is True


def _chunk(chunk_id: str, text: str, score: float, *, query: str, query_type: str, **metadata) -> RetrievedChunk:
    meta = {"id": chunk_id, **metadata}
    details = score_keyword_match(query, text, meta, query_type=query_type)
    details["query_type"] = query_type
    meta["score_details"] = details
    return RetrievedChunk(text=text, metadata=meta, score=score)


def test_apply_keyword_boost_exact_lookup_promotes_close_exact_hit():
    query = "「返金条件」"
    chunks = [
        _chunk("semantic", "返金についての一般説明です。", 0.25, query=query, query_type="exact_lookup"),
        _chunk("exact", "返金条件 はプランごとに異なります。", 0.27, query=query, query_type="exact_lookup"),
    ]

    boosted = apply_keyword_boost(chunks, query_type="exact_lookup", max_boost=0.05)

    assert [ch.metadata["id"] for ch in boosted] == ["exact", "semantic"]
    details = boosted[0].metadata["score_details"]
    assert details["keyword_boost_applied"] is True
    assert details["keyword_boost_value"] > 0
    assert details["score_before_keyword_boost"] == 0.27
    assert details["score_after_keyword_boost"] < 0.27
    assert details["boost_reason"]


def test_apply_keyword_boost_identifier_promotes_exact_over_superset():
    query = "PR2 の仕様"
    chunks = [
        _chunk("superset", "PR20 の仕様です。", 0.25, query=query, query_type="identifier"),
        _chunk("exact", "PR2 の仕様です。", 0.27, query=query, query_type="identifier"),
    ]

    boosted = apply_keyword_boost(chunks, query_type="identifier", max_boost=0.05)

    assert [ch.metadata["id"] for ch in boosted] == ["exact", "superset"]
    assert boosted[0].metadata["score_details"]["keyword_boost_applied"] is True
    assert "identifier_hit" in boosted[0].metadata["score_details"]["boost_reason"]
    assert boosted[1].metadata["score_details"]["keyword_boost_applied"] is False


def test_apply_keyword_boost_keeps_non_exact_query_type_order():
    query = "パスワード再設定の方法"
    chunks = [
        _chunk("first", "一般説明です。", 0.30, query=query, query_type="procedure"),
        _chunk("second", "パスワード再設定の方法です。", 0.10, query=query, query_type="procedure"),
    ]

    boosted = apply_keyword_boost(chunks, query_type="procedure", max_boost=0.05)

    assert [ch.metadata["id"] for ch in boosted] == ["first", "second"]
    for ch in boosted:
        details = ch.metadata["score_details"]
        assert details["keyword_boost_applied"] is False
        assert details["keyword_boost_value"] == 0.0
        assert details["score_before_keyword_boost"] == ch.score
        assert details["score_after_keyword_boost"] == ch.score
        assert details["boost_reason"] == []


def test_score_keyword_match_includes_keyword_boost_fields_by_default():
    details = score_keyword_match("「請求書ID」", "請求書ID を確認します。")

    for key in [
        "keyword_boost_applied",
        "keyword_boost_value",
        "score_before_keyword_boost",
        "score_after_keyword_boost",
        "boost_reason",
    ]:
        assert key in details
    assert details["keyword_boost_applied"] is False
