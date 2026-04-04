from rag_core.ja_text import extract_salient_terms_ja, normalize_japanese_text


def test_normalize_japanese_text_fullwidth_and_spaces():
    raw = "　ＰＲ２　AnswerResult　S１２３４５　"
    assert normalize_japanese_text(raw) == "PR2 AnswerResult S12345"


def test_normalize_japanese_text_preserves_identifier_and_quote_content():
    raw = '「ＡＢＣ-123_4:/x」  “Quoted Term”'
    norm = normalize_japanese_text(raw)
    assert "ABC-123_4:/x" in norm
    assert "Quoted Term" in norm
    assert "-" in norm
    assert "_" in norm
    assert ":" in norm
    assert "/" in norm


def test_extract_salient_terms_ja_order_and_dedup():
    text = '「請求書ID」 PR2 AnswerResult S12345 カタカナ語 漢字複合語 PR2'
    terms = extract_salient_terms_ja(text)
    assert terms == [
        "請求書ID",
        "PR2",
        "AnswerResult",
        "S12345",
        "カタカナ語",
        "漢字複合語",
    ]
