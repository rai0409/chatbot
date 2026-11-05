"""PR H: FAQ layouts, wrapped Japanese lines, circled step numbers. Synthetic data only."""
from __future__ import annotations

import pytest

from rag_core import chunking_ja as cj


def _rows(text, doc_type="faq"):
    return cj.build_ja_chunk_records(doc_id="d", source_doc="s.pdf", text=text, doc_type=doc_type,
                                     title="t", source_pages=[1])


def _faq(text):
    """(question, answer part of the display text) of every FAQ entry."""
    seen, out = set(), []
    for row in _rows(text):
        question = row.get("faq_question")
        if question and question not in seen:
            seen.add(question)
            out.append((question, row["text"]))
    return out


def test_wrapped_japanese_lines_are_joined_without_a_space():
    (question, text), = _faq("Q: 経費精算の\n申請方法を教えてください。\nA: 申請は、システムから\n行います。")
    assert question == "経費精算の申請方法を教えてください。"
    assert "システムから行います。" in text


def test_wrapped_latin_words_keep_their_space():
    (question, _), = _faq("Q: How do I reset\nmy password?\nA: Use the reset page.")
    assert question == "How do I reset my password?"


@pytest.mark.parametrize("text", [
    "Q. パスワードを忘れた場合は？\nA. 再設定画面から手続きします。",
    "Q1. パスワードを忘れた場合は？\nA1. 再設定画面から手続きします。",
    "Q1：パスワードを忘れた場合は？\nA1：再設定画面から手続きします。",
    "Ｑ．パスワードを忘れた場合は？\nＡ．再設定画面から手続きします。",
    "質問1：パスワードを忘れた場合は？\n回答：再設定画面から手続きします。",
    "問1　パスワードを忘れた場合は？\n答　再設定画面から手続きします。",
    "Q:\nパスワードを忘れた場合は？\nA:\n再設定画面から手続きします。",
])
def test_common_faq_layouts_give_a_clean_question_and_answer(text):
    (question, display), = _faq(text)
    assert question == "パスワードを忘れた場合は?" or question == "パスワードを忘れた場合は？"
    assert display.startswith("Q: パスワード")
    assert "A: 再設定画面から手続きします。" in display
    assert "Q1" not in display and "A1" not in display and "質問" not in display and "A. " not in display


def test_consecutive_numbered_pairs_without_a_blank_line_are_separate_entries():
    entries = _faq("Q1. パスワードを忘れた場合は？\nA1. 再設定します。\nQ2. 退会方法は？\nA2. 設定から退会できます。")
    assert [q for q, _ in entries] == ["パスワードを忘れた場合は?", "退会方法は?"]


def test_words_that_only_start_with_q_are_not_questions():
    row = _rows("QRコード: 読み取りに使う二次元コードです。")[0]
    assert row["faq_question"] == "QRコード"  # a term entry: the head is the term, not a "Q" marker


def test_a_question_mark_label_is_not_cut_off_by_a_leading_digit_word():
    (question, _), = _faq("Q: 2段階認証とは？\nA: ログインを二重に確認する仕組みです。")
    assert question.startswith("2段階認証")


# --- circled numbers ----------------------------------------------------------------------------------------------------------------------


def test_circled_step_numbers_survive_in_the_stored_text():
    rows = _rows("手順\n① 電源を入れる。\n② 設定を開く。\n③ 保存する。", "procedure")
    assert all("①" in r["text"] and "②" in r["text"] and "③" in r["text"] for r in rows)


def test_search_text_has_no_circled_numbers_so_queries_with_digits_match():
    rows = _rows("手順\n① 電源を入れる。\n② 設定を開く。", "procedure")
    assert all("①" not in r["searchable_text"] and "②" not in r["searchable_text"] for r in rows)
    assert any("1" in r["searchable_text"] for r in rows)


def test_other_compatibility_characters_are_still_folded():
    rows = _rows("全角ＡＢＣ１２３と半角ｶﾀｶﾅ。", "policy")
    assert "ABC123" in rows[0]["text"] and "カタカナ" in rows[0]["text"]


def test_a_circled_number_between_text_does_not_break_composition_of_kana():
    # half-width kana + dakuten compose across the neighbouring text; protecting ① must not split them
    assert cj._normalize_text("ｶﾞｲﾄﾞ① ﾎﾟｲﾝﾄ") == "ガイド① ポイント"


def test_the_step_pattern_recognizes_circled_numbers():
    assert cj._STEP_RE.match("① 電源を入れる。") and cj._STEP_RE.match("⑩ 終了する。")


def test_join_wrapped_rules():
    assert cj._join_wrapped(["経費精算の", "申請方法"]) == "経費精算の申請方法"
    assert cj._join_wrapped(["how to", "reset"]) == "how to reset"
    assert cj._join_wrapped(["PR2は", "再設定"]) == "PR2は再設定"   # Latin/digit then Japanese: no space
    assert cj._join_wrapped(["", "  ", "a"]) == "a"


def test_numbers_of_four_digits_are_accepted():
    entries = _faq("Q1234. 手続きの期限は？\nA1234. 月末です。\nQ1235. 窓口は？\nA1235. 管理部です。")
    assert [q for q, _ in entries] == ["手続きの期限は?", "窓口は?"]
