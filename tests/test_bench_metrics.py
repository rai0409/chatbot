from __future__ import annotations

import json
from math import log2

import pytest

from eval.bench.dataset import EvalQuery, Relevant, evidence_key, load_eval_set
from eval.bench.metrics import RankedChunk, aggregate, query_metrics


def _query(relevant, slices=(), query_id="q1"):
    return EvalQuery(query_id, "質問", "dev", "default", True, tuple(relevant), tuple(slices), "g1")


def _chunk(chunk_id, doc_id, text=""):
    return RankedChunk(chunk_id, doc_id, evidence_key(text))


def test_recall_mrr_ndcg_with_graded_relevance():
    query = _query([Relevant("c2", None, None, 3), Relevant("c4", None, None, 1)])
    ranked = [_chunk("c1", "d"), _chunk("c2", "d"), _chunk("c3", "d"), _chunk("c4", "d")]

    m = query_metrics(ranked, query, "chunk", [1, 2, 4])

    assert (m["recall@1"], m["recall@2"], m["recall@4"]) == (0.0, 0.5, 1.0)
    assert m["mrr@4"] == 0.5
    dcg = 7 / log2(3) + 1 / log2(5)
    idcg = 7 / log2(2) + 1 / log2(3)
    assert m["ndcg@4"] == pytest.approx(dcg / idcg)


def test_doc_mode_credits_a_relevant_document_once():
    query = _query([Relevant(None, "d1", None, 2)])

    m = query_metrics([_chunk("a", "d1"), _chunk("b", "d1")], query, "doc", [2])

    assert m["recall@2"] == 1.0
    assert m["ndcg@2"] == 1.0


def test_evidence_mode_matches_normalized_substring():
    query = _query([Relevant(None, None, "保険証の再発行", 1)])
    ranked = [_chunk("a", "d", "手続き"), _chunk("b", "d", "健康保険証の　再発行は窓口で")]

    m = query_metrics(ranked, query, "evidence", [1, 2])

    assert (m["hit@1"], m["hit@2"]) == (0.0, 1.0)


def test_aggregate_reports_overall_and_slices():
    q1 = _query([Relevant("c1", None, None, 1)], slices=("colloquial",), query_id="q1")
    q2 = _query([Relevant("c1", None, None, 1)], slices=("formal",), query_id="q2")
    per_query = {"q1": {"recall@1": 1.0}, "q2": {"recall@1": 0.0}}

    out = aggregate(per_query, [q1, q2])

    assert out["all"]["recall@1"] == 0.5
    assert out["slice:colloquial"]["recall@1"] == 1.0
    assert out["slice:formal"]["queries"] == 1.0


def test_loader_validates_and_reports_problems(tmp_path):
    path = tmp_path / "eval.jsonl"
    rows = [
        {"query_id": "q1", "query": "質問1", "split": "dev", "relevant": [{"doc_id": "d1", "grade": 2}]},
        {"query_id": "q1", "query": "重複", "split": "dev", "relevant": [{"doc_id": "d1"}]},
        {"query_id": "q3", "query": "質問3", "split": "other"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_eval_set(path)

    message = str(exc_info.value)
    assert "duplicate query_id q1" in message
    assert "split must be one of" in message


def test_loader_defaults_group_to_first_doc(tmp_path):
    path = tmp_path / "eval.jsonl"
    row = {"query_id": "q1", "query": "質問", "split": "test", "relevant": [{"doc_id": "d9"}]}
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    (query,) = load_eval_set(path, split="test")

    assert query.group == "d9"
    assert query.relevant[0].grade == 1
