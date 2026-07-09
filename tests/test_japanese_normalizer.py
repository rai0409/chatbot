from __future__ import annotations

import json

import pytest

from rag_core.japanese_normalizer import (
    compute_business_term_overlap_score,
    compute_synonym_overlap_score,
    detect_negative_mismatch,
    find_synonym_hits,
    load_japanese_business_synonyms,
    normalize_for_match,
    normalize_japanese_text,
    tokenize_lightweight,
)


def test_nfkc_normalization_handles_full_width_alphanumerics():
    assert normalize_japanese_text("ＡＢＣ１２３　ＰＲ") == "abc123 pr"


def test_whitespace_and_punctuation_normalization_for_match():
    assert normalize_for_match(" 保険証！？　住所変更、転居。 ") == "保険証 住所変更 転居"


def test_japanese_text_is_preserved():
    assert normalize_japanese_text("健康保険証の再発行") == "健康保険証の再発行"


def test_tokenize_lightweight_extracts_japanese_and_ascii_terms():
    assert tokenize_lightweight("PR３５の健康保険証について") == ["pr35", "健康保険証について"]


def test_synonym_hits_match_insurance_card_terms():
    cfg = load_japanese_business_synonyms()

    hits = find_synonym_hits(
        "保険証をなくした場合はどうしますか",
        "健康保険被保険者証の再発行手続き",
        cfg,
    )

    assert hits["shared_canonicals"] == ["健康保険証"]
    assert "保険証" in hits["query_terms"]
    assert "健康保険被保険者証" in hits["candidate_terms"]
    assert hits["score"] > 0


def test_synonym_overlap_score_is_positive_for_business_synonyms():
    cfg = load_japanese_business_synonyms()

    score = compute_synonym_overlap_score("育休の申請", "育児休業の手続き", cfg)

    assert score > 0


def test_unrelated_terms_produce_zero_or_low_score():
    cfg = load_japanese_business_synonyms()

    assert compute_synonym_overlap_score("保険証の再発行", "退職後の手続き", cfg) == 0.0
    assert compute_business_term_overlap_score("保険証の再発行", "退職後の手続き", cfg) < 0.5


def test_negative_mismatch_detects_fuyo_in_vs_out():
    cfg = load_japanese_business_synonyms()

    result = detect_negative_mismatch("扶養に入るには？", "扶養から外れる手続き", cfg)

    assert result["matched"] is True
    assert result["query_side_terms"] == ["扶養に入る"]
    assert result["candidate_side_terms"] == ["扶養から外れる"]
    assert result["reason"] == "opposite_intent_terms"


def test_negative_mismatch_detects_lost_reissue_vs_return():
    cfg = load_japanese_business_synonyms()

    result = detect_negative_mismatch("保険証を紛失したので再発行したい", "退職時は保険証を返却します", cfg)

    assert result["matched"] is True
    assert set(result["query_side_terms"]) == {"再発行", "紛失"}
    assert result["candidate_side_terms"] == ["返却"]


def test_missing_synonym_config_is_tolerated_safely(tmp_path):
    cfg = load_japanese_business_synonyms(tmp_path / "missing.json")

    assert cfg["synonym_groups"] == []
    assert cfg["negative_mismatch_pairs"] == []
    assert cfg["_metadata"]["loaded"] is False
    assert compute_synonym_overlap_score("保険証", "被保険者証", cfg) == 0.0


def test_invalid_synonym_config_raises_clear_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid Japanese business synonym config"):
        load_japanese_business_synonyms(path)


def test_returned_lists_are_bounded():
    cfg = {
        "synonym_groups": [
            {
                "canonical": f"用語{i}",
                "terms": [f"用語{i}", f"別名{i}"],
            }
            for i in range(30)
        ],
        "negative_mismatch_pairs": [],
    }
    query = " ".join(f"用語{i}" for i in range(30))
    candidate = " ".join(f"別名{i}" for i in range(30))

    hits = find_synonym_hits(query, candidate, cfg)

    assert len(hits["shared_canonicals"]) == 20
    assert len(hits["query_terms"]) == 20
    assert len(hits["candidate_terms"]) == 20


def test_custom_config_file_loads_expected_shape(tmp_path):
    path = tmp_path / "synonyms.json"
    path.write_text(
        json.dumps(
            {
                "synonym_groups": [{"canonical": "申請", "terms": ["届け出"]}],
                "negative_mismatch_pairs": [{"left": ["追加"], "right": ["削除"]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cfg = load_japanese_business_synonyms(path)

    assert cfg["_metadata"]["loaded"] is True
    assert cfg["synonym_groups"][0]["canonical"] == "申請"
    assert cfg["negative_mismatch_pairs"][0]["left"] == ["追加"]
