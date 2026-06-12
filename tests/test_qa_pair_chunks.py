from __future__ import annotations

import json

import pytest

import config
from rag_core import retrieval
from scripts.approved_qa_to_pair_chunks import convert_approved_qa_to_pair_chunks


def _approved_record(qa_id="qa_pay_01", tenant_id="default", **overrides):
    record = {
        "qa_id": qa_id,
        "question": "支払い方法には何がありますか？",
        "approved_answer": "銀行振込とクレジットカード決済が利用できます。",
        "approved_citations": [
            {"source_doc": "qa_table.pdf", "source_pages": [1], "title": "支払いQ&A"}
        ],
        "status": "approved",
        "tenant_id": tenant_id,
        "language": "ja",
    }
    record.update(overrides)
    return record


def _write_jsonl(path, records):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _convert(tmp_path, records, name="approved.jsonl", out_name="pairs.jsonl"):
    src = tmp_path / name
    out = tmp_path / out_name
    _write_jsonl(src, records)
    summary = convert_approved_qa_to_pair_chunks(input_path=src, output_path=out)
    return summary, _read_jsonl(out), out


# --- converter -------------------------------------------------------------


def test_pair_chunk_contains_question_and_answer_with_expected_metadata(tmp_path):
    _, rows, _ = _convert(tmp_path, [_approved_record(tenant_id="tenant_a")])

    row = rows[0]
    assert "支払い方法には何がありますか？" in row["text"]
    assert "銀行振込とクレジットカード決済が利用できます。" in row["text"]
    assert row["searchable_text"] == row["text"]
    assert row["id"] == "approved_qa_pair:qa_pay_01"
    assert row["doc_type"] == "approved_qa_pair"
    assert row["chunk_type"] == "qa_pair"
    assert row["approved_qa_id"] == "qa_pay_01"
    assert row["tenant_id"] == "tenant_a"
    assert row["source_doc"] == "qa_table.pdf"
    assert row["source_pages"] == [1]
    # Self-contained retrieval unit: ranked like ordinary chunks, never
    # parent-expanded.
    assert row["chunk_role"] == "child"
    assert row["parent_chunk_id"] == ""


def test_rerun_produces_identical_ids(tmp_path):
    _, first, _ = _convert(tmp_path, [_approved_record()], out_name="a.jsonl")
    _, second, _ = _convert(tmp_path, [_approved_record()], out_name="b.jsonl")

    assert [r["id"] for r in first] == [r["id"] for r in second]


def test_non_approved_records_are_skipped_with_reason_counts(tmp_path):
    records = [
        _approved_record(qa_id="qa_one"),
        _approved_record(
            qa_id="qa_two", question="別の質問？", normalized_question="別の質問?", status="draft"
        ),
        _approved_record(
            qa_id="qa_three", question="第三の質問？", normalized_question="第三の質問?", status="rejected"
        ),
    ]
    summary, rows, _ = _convert(tmp_path, records)

    assert summary["written_records"] == 1
    assert summary["skipped_records"] == 2
    assert summary["skipped_by_reason"] == {"status=draft": 1, "status=rejected": 1}
    assert [r["approved_qa_id"] for r in rows] == ["qa_one"]


def test_duplicate_qa_id_fails_deterministically(tmp_path):
    records = [
        _approved_record(qa_id="qa_dup"),
        _approved_record(qa_id="qa_dup", question="別の質問？", normalized_question="別の質問?"),
    ]
    with pytest.raises(ValueError, match="duplicate id"):
        _convert(tmp_path, records)


# --- retrieval -------------------------------------------------------------


def _setup_corpus(monkeypatch, tmp_path, records, extra_rows=()):
    _, rows, out = _convert(tmp_path, records)
    if extra_rows:
        with out.open("a", encoding="utf-8") as f:
            for row in extra_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(out))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    retrieval._KEYWORD_INDEX_WARNED_KEYS.clear()
    return rows


def test_answer_only_query_retrieves_pair_chunk(monkeypatch, tmp_path):
    distractor = {
        "id": "d1",
        "text": "一般的な決済に関する注意事項を説明する章です。",
        "source_doc": "overview.pdf",
        "source_pages": [1],
        "doc_id": "overview.pdf",
        "chunk_index": 1,
        "searchable": 1,
        "type": "pdf",
        "quality": "high",
    }
    _setup_corpus(monkeypatch, tmp_path, [_approved_record()], extra_rows=[distractor])

    # クレジットカード決済 appears only in the approved answer, not in the
    # stored question: a hit proves Q and A are searched together.
    hits = retrieval.keyword_retrieve("クレジットカード決済 は利用できますか", top_k=5)

    assert hits
    assert hits[0].metadata["id"] == "approved_qa_pair:qa_pay_01"
    assert hits[0].metadata["chunk_type"] == "qa_pair"


def test_pair_chunks_for_tenant_a_are_invisible_to_other_tenants(monkeypatch, tmp_path):
    _setup_corpus(monkeypatch, tmp_path, [_approved_record(tenant_id="tenant_a")])

    visible_a = retrieval.keyword_retrieve("支払い方法", top_k=5, tenant_id="tenant_a")
    visible_b = retrieval.keyword_retrieve("支払い方法", top_k=5, tenant_id="tenant_b")
    visible_default = retrieval.keyword_retrieve("支払い方法", top_k=5)

    assert {h.metadata["id"] for h in visible_a} == {"approved_qa_pair:qa_pay_01"}
    assert visible_b == []
    assert visible_default == []


def test_pair_chunk_is_not_parent_expanded(monkeypatch, tmp_path):
    _setup_corpus(monkeypatch, tmp_path, [_approved_record()])

    hits = retrieval.keyword_retrieve("支払い方法", top_k=5)
    expanded = retrieval.expand_parent_chunks(hits)

    assert [c.metadata["id"] for c in expanded] == ["approved_qa_pair:qa_pay_01"]
    assert expanded[0].text == hits[0].text
