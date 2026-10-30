from __future__ import annotations

import json

import pytest

from eval.bench import jqara


def _write_parquet(path, rows):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("id", pa.string()), ("q_id", pa.string()), ("passage_row_id", pa.string()), ("label", pa.int64()),
        ("text", pa.string()), ("title", pa.string()), ("question", pa.string()), ("answers", pa.list_(pa.string())),
    ])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _rows():
    return [
        {"id": "a", "q_id": "q1", "passage_row_id": "p1", "label": 1, "title": "題", "text": "正解本文", "question": "質問一", "answers": ["答"]},
        {"id": "b", "q_id": "q1", "passage_row_id": "p2", "label": 0, "title": "題2", "text": "不正解本文", "question": "質問一", "answers": ["答"]},
        {"id": "c", "q_id": "q2", "passage_row_id": "p3", "label": 0, "title": "題3", "text": "本文", "question": "質問二", "answers": []},
        {"id": "d", "q_id": "q2", "passage_row_id": "p4", "label": 0, "title": "題4", "text": "別本文", "question": "質問二", "answers": []},
    ]


def test_load_jqara_groups_candidates_and_preserves_ids(tmp_path):
    path = tmp_path / "dev.parquet"
    _write_parquet(path, _rows())

    queries = jqara.load_jqara(path, expected_candidates=2)

    assert [q.q_id for q in queries] == ["q1", "q2"]
    assert queries[0].relevant_ids == ("p1",)
    assert queries[0].candidates[0].searchable_text == "題\n正解本文"


def test_load_jqara_rejects_inconsistent_question_and_duplicate_passage(tmp_path):
    path = tmp_path / "bad.parquet"
    rows = _rows()
    rows[1] = dict(rows[1], passage_row_id="p1", question="別質問")
    _write_parquet(path, rows)

    with pytest.raises(ValueError, match="data contract") as exc:
        jqara.load_jqara(path, expected_candidates=2)

    assert "duplicate within q_id" in str(exc.value)
    assert "inconsistent question" in str(exc.value)


def test_split_keeps_exact_duplicate_questions_together():
    queries = [
        jqara.JQaRAQuery("a", "同じ 質問", ()), jqara.JQaRAQuery("b", "同じ質問", ()),
        jqara.JQaRAQuery("c", "別質問", ()), jqara.JQaRAQuery("d", "さらに別", ()),
    ]

    manifest = jqara.create_split(queries, seed=3, tuning_share=0.5)

    assert ({"a", "b"} <= set(manifest["tuning_q_ids"])) or ({"a", "b"} <= set(manifest["validation_q_ids"]))
    assert manifest["exact_duplicate_question_groups"] == 1


def test_lexical_run_writes_all_candidates_and_keeps_zero_positive_visible(tmp_path):
    path = tmp_path / "dev.parquet"
    _write_parquet(path, _rows())

    run_dir = jqara.run({"input": str(path), "output_dir": str(tmp_path / "out"), "expected_candidates": 2,
                          "method": "lexical_only", "ks": [1, 2], "bootstrap_resamples": 20, "seed": 1})

    result = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "per_query.jsonl").read_text(encoding="utf-8").splitlines()]
    assert result["queries"] == 2 and result["ranking_metric_queries"] == 1 and result["zero_positive_queries"] == 1
    assert len(rows) == 4
    assert {row["query_id"] for row in rows} == {"q1", "q2"}
