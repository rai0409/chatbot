from __future__ import annotations

from rag_core.keyword_scorer import classify_query_type, score_keyword_match


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
