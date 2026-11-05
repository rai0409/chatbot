from __future__ import annotations

# N5 / N9: query preprocessing pipeline and its golden cases.

import json

import pytest

from rag_core import query_preprocess as qp

# Independent synthetic cases only. Do not reintroduce the removed source JSONL.
# These are explicit expected outputs, not values calculated from the implementation.
_SYNTHETIC_QUERY_CASES = (
    {"case_id": "colloquial_filler", "category": "colloquial", "input": "あの、退職について教えてください", "expected_query": "退職"},
    {"case_id": "colloquial_expense", "category": "colloquial", "input": "えっと、経費を教えてください", "expected_query": "経費"},
    {"case_id": "colloquial_intro", "category": "colloquial", "input": "質問です、勤怠について教えてください", "expected_query": "勤怠"},
    {"case_id": "colloquial_suffix", "category": "colloquial", "input": "退職なんだけど", "expected_query": "退職"},
    {"case_id": "colloquial_request", "category": "colloquial", "input": "給与について教えてください", "expected_query": "給与"},
    {"case_id": "id_ascii", "category": "id", "input": "ABC-123", "expected_query": "abc-123"},
    {"case_id": "id_mixed", "category": "id", "input": "申請ID PR-2048", "expected_query": "申請id pr-2048"},
    {"case_id": "id_embedded", "category": "id", "input": "ZZ-10を確認", "expected_query": "zz-10を確認"},
    {"case_id": "id_fullwidth", "category": "id", "input": "第３条について教えてください", "expected_query": "第3条"},
    {"case_id": "formal_retirement", "category": "formal", "input": "退職手続き", "expected_query": "退職手続き"},
    {"case_id": "formal_application", "category": "formal", "input": "在宅勤務の申請方法", "expected_query": "在宅勤務の申請方法"},
    {"case_id": "formal_no_request", "category": "formal", "input": "勤怠管理について", "expected_query": "勤怠管理について"},
    {"case_id": "variant_fullwidth", "category": "variant", "input": "ＡＢＣ－１２３", "expected_query": "abc-123"},
    {"case_id": "variant_spaces", "category": "variant", "input": "　入社手続き　", "expected_query": "入社手続き"},
    {"case_id": "variant_space_between", "category": "variant", "input": "退職　手続き", "expected_query": "退職 手続き"},
    {"case_id": "variant_kanji_space", "category": "variant", "input": "社内　規程", "expected_query": "社内 規程"},
    {"case_id": "variant_mixed", "category": "variant", "input": "勤怠ＡＢＣ", "expected_query": "勤怠abc"},
)


def _golden():
    return list(_SYNTHETIC_QUERY_CASES)


@pytest.fixture(autouse=True)
def _legacy_normalization(monkeypatch):
    monkeypatch.setenv("NORMALIZATION_PROFILE", "legacy")
    monkeypatch.setenv("QUERY_TYPO_CORRECTION", "false")
    monkeypatch.setenv("QUERY_SYNONYM_EXPANSION", "false")


@pytest.mark.parametrize("case", _golden(), ids=lambda c: c["case_id"])
def test_golden_query_cases(case):
    result = qp.preprocess_query(case["input"])
    assert result.query == case["expected_query"]


def test_golden_file_covers_required_categories():
    categories = {c["category"] for c in _golden()}
    assert {"colloquial", "id", "formal", "variant"} <= categories


def test_steps_are_traced_in_fixed_order():
    steps = [s["step"] for s in qp.preprocess_query("あの、退職について教えてください").steps]
    assert steps == ["normalize", "colloquial", "numeric_tokens", "reading_lane"]


def test_cleanup_never_empties_the_query():
    assert qp.preprocess_query("教えてください").query == "教えてください"


def test_tenant_rules_are_added_and_tenant_id_is_validated(monkeypatch, tmp_path):
    rules = tmp_path / "query_rules"
    (rules / "tenants").mkdir(parents=True)
    (rules / "colloquial_ja.json").write_text(json.dumps({"strip_prefix": [], "strip_suffix": []}), encoding="utf-8")
    (rules / "tenants" / "tenant_a.json").write_text(
        json.dumps({"strip_prefix": ["^社内用語で"], "strip_suffix": []}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(qp, "RULES_DIR", rules)
    qp._load_rules.cache_clear()
    assert qp.strip_colloquial("社内用語で経費", tenant_id="tenant_a")[0] == "経費"
    assert qp.strip_colloquial("社内用語で経費", tenant_id="tenant_b")[0] == "社内用語で経費"
    assert qp._rule_files("../etc") == [str(rules / "colloquial_ja.json")]
    qp._load_rules.cache_clear()


def test_synonyms_come_only_from_enabled_profile_entries(monkeypatch):
    monkeypatch.setenv("QUERY_SYNONYM_EXPANSION", "true")
    monkeypatch.setattr(
        qp,
        "_synonym_groups",
        lambda rag_profile_id: [["退職", "辞める", "離職"]],
    )
    result = qp.preprocess_query("会社を辞める手続き")
    assert result.query == "会社を辞める手続き"
    assert result.expansions == ["退職", "離職"]
    assert result.retrieval_query.startswith("会社を辞める手続き")


def test_typo_candidates_use_index_vocabulary_and_skip_identifiers():
    vocab = qp._Vocabulary([["有給休暇", "申請", "pr2"], ["休暇申請"]])
    assert vocab.candidates("有給休憩") == ["有給休暇"]  # one substitution
    assert vocab.candidates("有給休") == ["有給休暇"]  # one missing character
    assert vocab.candidates("休憩申込") == []  # two edits are not allowed for a 4-character token
    assert "pr2" in vocab.words


def test_correct_typos_skips_ids_quotes_and_known_words(monkeypatch):
    class _Index:
        tokenizer = "heuristic"
        tokenized_corpus = [["就業規則", "申請書"]]

    monkeypatch.setattr(qp, "_vocabulary", lambda collection_name: (_Index(), qp._Vocabulary(_Index.tokenized_corpus)))
    from rag_core import retrieval

    monkeypatch.setattr(retrieval, "_query_tokens", lambda q, t: ["就業規側", "申請書", "abc12", "「就業規則」"])
    corrections = qp.correct_typos("就業規側 申請書 abc12")
    assert corrections == [{"token": "就業規側", "candidates": ["就業規則"]}]


def test_qa_uses_preprocessed_query_only_when_enabled(monkeypatch):
    from rag_core import qa
    from rag_core.retrieval import RetrievedChunk

    seen = {}

    def _fake(question, augmented, **kwargs):
        seen["question"] = question
        chunk = RetrievedChunk(text="退職の手続きです。", metadata={"id": "c1", "source_doc": "d.pdf"}, score=0.1)
        return [chunk], [chunk], [chunk]

    monkeypatch.setattr(qa, "_retrieve_and_rerank", _fake)
    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", "false")
    qa._build_retrieval_trace("あの、退職について教えてください", client=object(), top_k=3, max_context_chars=1000)
    assert seen["question"] == "あの、退職について教えてください"

    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", "true")
    trace, _state, _ = qa._build_retrieval_trace(
        "あの、退職について教えてください", client=object(), top_k=3, max_context_chars=1000
    )
    assert seen["question"] == "退職"
    assert trace["query_preprocess"]["original"] == "あの、退職について教えてください"
