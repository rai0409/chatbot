from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.runner import load_cases
from scripts.build_eval_cases_from_approved_qa import (
    build_cases,
    extract_answer_only_term,
    validate_case_ids,
)


def _approved_record(qa_id="qa_pay_01", **overrides):
    record = {
        "qa_id": qa_id,
        "question": "支払い方法には何がありますか？",
        "approved_answer": "銀行振込とクレジットカード決済が利用できます。",
        "approved_citations": [{"source_doc": "qa_table.pdf", "source_pages": [1]}],
        "status": "approved",
        "tenant_id": "default",
    }
    record.update(overrides)
    return record


def _pair_chunk(qa_id="qa_pay_01", question="支払い方法には何がありますか？", answer="銀行振込とクレジットカード決済が利用できます。"):
    text = f"Q: {question}\nA: {answer}"
    return {
        "id": f"approved_qa_pair:{qa_id}",
        "text": text,
        "searchable_text": text,
        "source_doc": "qa_table.pdf",
    }


def test_one_approved_record_yields_stored_question_and_answer_term_cases():
    cases, skipped = build_cases(
        approved_records=[_approved_record()],
        pair_chunks=[_pair_chunk()],
    )

    assert [c["case_id"] for c in cases] == ["real_q_qa_pay_01", "real_a_qa_pay_01"]
    assert cases[0]["query"] == "支払い方法には何がありますか？"
    assert cases[0]["gold_chunk_ids"] == ["approved_qa_pair:qa_pay_01"]
    assert cases[0]["gold_doc_ids"] == ["qa_table.pdf"]
    # The answer-term probe uses a term absent from the stored question
    # (longest run wins; katakana and kanji runs are separate candidates).
    assert "クレジットカード" in cases[1]["query"]
    assert cases[1]["gold_chunk_ids"] == ["approved_qa_pair:qa_pay_01"]
    assert skipped == {}
    # Strict expectations are never auto-written.
    for case in cases:
        assert "expected_guard_reason" not in case
        assert "expected_used_fallback" not in case


def test_rerun_is_deterministic():
    args = dict(approved_records=[_approved_record()], pair_chunks=[_pair_chunk()])
    first, _ = build_cases(**args)
    second, _ = build_cases(**args)

    assert first == second


def test_answer_without_answer_only_term_is_skipped_with_reason():
    record = _approved_record(
        question="フリーアンサーも含まれるという認識で良いでしょうか。",
        approved_answer="フリーアンサーも含みます。",
    )
    pair = _pair_chunk(
        question=record["question"], answer=record["approved_answer"]
    )

    cases, skipped = build_cases(approved_records=[record], pair_chunks=[pair])

    assert [c["case_id"] for c in cases] == ["real_q_qa_pay_01"]
    assert skipped == {"no_answer_only_term": 1}
    assert extract_answer_only_term(record["question"], record["approved_answer"]) is None


def test_answer_term_gold_includes_every_pair_chunk_containing_the_term():
    records = [
        _approved_record(qa_id="qa_one"),
        _approved_record(
            qa_id="qa_two",
            question="オンライン決済の手数料はかかりますか？",
            approved_answer="クレジットカード決済の手数料はかかりません。",
        ),
    ]
    pairs = [
        _pair_chunk(qa_id="qa_one"),
        _pair_chunk(
            qa_id="qa_two",
            question=records[1]["question"],
            answer=records[1]["approved_answer"],
        ),
    ]

    cases, _ = build_cases(approved_records=records, pair_chunks=pairs)

    case_one = next(c for c in cases if c["case_id"] == "real_a_qa_one")
    # クレジットカード決済 appears in both pair chunks: honest gold labels list both.
    assert case_one["gold_chunk_ids"] == [
        "approved_qa_pair:qa_one",
        "approved_qa_pair:qa_two",
    ]


def test_missing_pair_chunk_is_skipped_with_reason():
    cases, skipped = build_cases(
        approved_records=[_approved_record(qa_id="qa_orphan")],
        pair_chunks=[],
    )

    assert cases == []
    assert skipped == {"missing_pair_chunk": 1}


def test_duplicate_case_ids_are_rejected():
    rows = [{"case_id": "dup"}, {"case_id": "dup"}]
    with pytest.raises(ValueError, match="duplicate case_id"):
        validate_case_ids(rows)


def test_generated_cases_load_through_eval_runner(tmp_path):
    cases, _ = build_cases(
        approved_records=[_approved_record()],
        pair_chunks=[_pair_chunk()],
    )
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
        encoding="utf-8",
    )

    loaded = load_cases(path)

    assert [c.case_id for c in loaded] == ["real_q_qa_pay_01", "real_a_qa_pay_01"]
    assert loaded[0].gold_chunk_ids == ("approved_qa_pair:qa_pay_01",)


def test_committed_real_corpus_case_file_loads_and_is_unique():
    path = Path("eval/cases/real_corpus_cases.jsonl")
    cases = load_cases(path)

    assert len(cases) >= 50
    case_ids = [c.case_id for c in cases]
    assert len(case_ids) == len(set(case_ids))
    abstain = [c for c in cases if c.expected_abstain]
    paraphrase = [c for c in cases if c.case_id.startswith("real_p_")]
    assert len(abstain) >= 10
    assert len(paraphrase) >= 5
