import json
from pathlib import Path

import pytest

from eval.bench import holdout, runner, synthetic
from eval.bench.__main__ import main as bench_main
from eval.bench.dataset import load_eval_set
from eval.bench.lexical import LexicalHit
from rag_core import retrieval
from rag_core.retrieval import RetrievedChunk


def _bucket(marker, fused, keyword=False):
    chunk = RetrievedChunk(text="", metadata={}, score=0.0)
    return {"marker": marker, "vector": chunk, "keyword": chunk if keyword else None, "fused": fused}


def _order(keys, fused_by_key, keyword_keys=()):
    merged = {k: _bucket(k, fused_by_key[k], keyword=k in keyword_keys) for k in keys}
    return [bucket["marker"] for bucket in retrieval._rank_buckets(merged)]


def test_equal_scores_are_ranked_by_chunk_key_whatever_the_lane_order():
    fused = {"c": 0.5, "a": 0.5, "b": 0.5}
    expected = ["a", "b", "c"]
    for keys in (["c", "a", "b"], ["a", "b", "c"], ["b", "c", "a"], ["c", "b", "a"]):
        assert _order(keys, fused) == expected


def test_score_and_lexical_presence_still_come_before_the_chunk_key():
    fused = {"a": 0.4, "b": 0.5, "c": 0.5}
    assert _order(["a", "b", "c"], fused, keyword_keys={"c"}) == ["c", "b", "a"]


def test_bench_fusion_breaks_ties_by_chunk_id_like_production():
    dense = [(0, 0.9), (1, 0.8)]
    lexical = [LexicalHit(1, 5.0, 0, 0, 0), LexicalHit(0, 3.0, 0, 0, 0)]
    settings = {"method": "weighted_rrf", "dense_weight": 1.0, "keyword_weight": 1.0, "exact_weight": 1.0}
    by_index, scores = runner.fuse_one(dense, lexical, settings, 10)
    assert scores[0] == scores[1] and by_index == [0, 1]
    by_id, _ = runner.fuse_one(dense, lexical, settings, 10, ids=["z-chunk", "a-chunk"])
    assert by_id == [1, 0]


def _cfg(tmp_path, **extra):
    return {"test_ledger": str(tmp_path / "ledger.jsonl"), **extra}


def test_test_split_needs_the_final_test_flag(tmp_path):
    with pytest.raises(holdout.HoldoutError, match="final_test"):
        holdout.claim_test_split(_cfg(tmp_path), "sha1", run_id="r1")
    assert not (tmp_path / "ledger.jsonl").exists()


def test_test_split_is_used_once_per_evaluation_set(tmp_path):
    cfg = _cfg(tmp_path, final_test=True)
    first = holdout.claim_test_split(cfg, "sha1", run_id="r1")
    assert first["reused"] is False and first["uses_before"] == 0
    with pytest.raises(holdout.HoldoutError, match="already used"):
        holdout.claim_test_split(cfg, "sha1", run_id="r2")
    other = holdout.claim_test_split(cfg, "sha2", run_id="r3")
    assert other["uses_before"] == 0
    reused = holdout.claim_test_split({**cfg, "allow_test_reuse": True}, "sha1", run_id="r4")
    assert reused["reused"] is True and reused["uses_before"] == 1
    rows = holdout.read_ledger(tmp_path / "ledger.jsonl")
    assert [row["run_id"] for row in rows] == ["r1", "r3", "r4"]


def test_a_damaged_ledger_is_not_ignored(tmp_path):
    (tmp_path / "ledger.jsonl").write_text("{broken\n", encoding="utf-8")
    with pytest.raises(holdout.HoldoutError, match="not JSON"):
        holdout.claim_test_split(_cfg(tmp_path, final_test=True), "sha1", run_id="r1")


def test_the_runner_refuses_the_test_split_before_any_work(tmp_path):
    eval_set = tmp_path / "eval.jsonl"
    row = {"query_id": "q1", "query": "質問", "split": "test", "relevant": [{"chunk_id": "c1"}]}
    eval_set.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    cfg = {
        "eval_set": str(eval_set),
        "split": "test",
        "match_mode": "chunk",
        "corpus": {"chunks_jsonl": str(tmp_path / "chunks.jsonl")},
        "embedding": [{"model": "unused"}],
        "output_dir": str(tmp_path / "out"),
        "test_ledger": str(tmp_path / "ledger.jsonl"),
    }
    with pytest.raises(holdout.HoldoutError, match="final_test"):
        runner.run(cfg)
    assert not (tmp_path / "out").exists()


def test_auto_slices_tag_the_kinds_of_question_we_want_to_observe():
    assert "year" in synthetic.auto_slices("令和5年度の補助金は？")
    assert "amount" in synthetic.auto_slices("送料は500円ですか")
    assert "negation" in synthetic.auto_slices("返品できない商品は")
    assert "condition" in synthetic.auto_slices("退会する場合の手続き")
    assert "code" in synthetic.auto_slices("ABC123 の仕様")
    assert "katakana" in synthetic.auto_slices("アンケートシステムの利用")
    assert "short" in synthetic.auto_slices("ABC123 の仕様")
    assert synthetic.auto_slices("契約の解除について詳しく教えてください") == []


def _cases():
    return [
        {"case_id": "c1", "category": "Exact ID", "query": "ABC123 の仕様", "gold_chunk_ids": ["k1"], "gold_doc_ids": ["d1"]},
        {"case_id": "c2", "category": "Exact ID", "query": "ABC124 の仕様", "gold_chunk_ids": ["k2"], "gold_doc_ids": ["d1"]},
        {"case_id": "c3", "category": "unknown", "query": "存在しない質問", "expected_abstain": True},
        {"case_id": "c4", "category": "no gold", "query": "正解の無い質問"},
    ]


def test_cases_become_an_evaluation_set_deterministically():
    rows, summary = synthetic.build_eval_set(_cases(), test_share=0.5, seed=3)
    again, _ = synthetic.build_eval_set(_cases(), test_share=0.5, seed=3)
    assert rows == again
    by_id = {row["query_id"]: row for row in rows}
    assert set(by_id) == {"c1", "c2", "c3"} and summary["skipped"] == {"no_gold_label": 1}
    assert by_id["c1"]["split"] == by_id["c2"]["split"] and by_id["c1"]["group"] == "d1"
    assert by_id["c1"]["relevant"] == [{"chunk_id": "k1", "grade": 1}]
    assert by_id["c3"]["answerable"] is False and by_id["c3"]["relevant"] == []
    assert "cat:exact_id" in by_id["c1"]["slices"] and "code" in by_id["c1"]["slices"]
    assert summary["match_mode"] == "chunk" and summary["abstain"] == 1


def test_test_share_bounds_and_mixed_labels():
    assert {r["split"] for r in synthetic.build_eval_set(_cases(), test_share=0.0)[0]} == {"dev"}
    assert {r["split"] for r in synthetic.build_eval_set(_cases(), test_share=1.0)[0]} == {"test"}
    with pytest.raises(ValueError, match="test_share"):
        synthetic.build_eval_set(_cases(), test_share=1.5)
    chunk_only = {"case_id": "m0", "category": "x", "query": "チャンクだけ", "gold_chunk_ids": ["k9"]}
    doc_only = {"case_id": "m1", "category": "x", "query": "文書だけ", "gold_doc_ids": ["d9"]}
    with pytest.raises(ValueError, match="mix chunk-only and doc-only"):
        synthetic.build_eval_set([chunk_only, doc_only])
    # A case with both kinds of label serves either mode: all-doc labels select doc mode.
    rows, summary = synthetic.build_eval_set([_cases()[0], doc_only])
    assert summary["match_mode"] == "doc" and rows[0]["relevant"] == [{"doc_id": "d1", "grade": 1}]
    # An explicit mode skips the cases that cannot be used in it, and says so.
    rows, summary = synthetic.build_eval_set([_cases()[0], doc_only], match_mode="chunk")
    assert [r["query_id"] for r in rows] == ["c1"] and summary["skipped"] == {"no_chunk_label": 1}


@pytest.mark.parametrize("name", ["semantic_challenge_v2_cases", "retrieval_v2_cases"])
def test_committed_case_files_convert_and_load(name, tmp_path):
    cases = synthetic.read_cases(Path("eval/cases") / f"{name}.jsonl")
    rows, summary = synthetic.build_eval_set(cases, test_share=0.3, seed=1)
    path = tmp_path / "eval_set.jsonl"
    synthetic.write_eval_set(path, rows)
    queries = load_eval_set(path)
    assert len(queries) == summary["written"] > 0
    assert {q.split for q in queries} == {"dev", "test"}


def test_from_cases_command(tmp_path, capsys):
    source = tmp_path / "cases.jsonl"
    source.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in _cases()), encoding="utf-8")
    out = tmp_path / "eval_set.jsonl"
    assert bench_main(["from-cases", "--cases", str(source), "--out", str(out), "--test-share", "0.5"]) == 0
    assert len(load_eval_set(out)) == 3
    assert '"written": 3' in capsys.readouterr().out
