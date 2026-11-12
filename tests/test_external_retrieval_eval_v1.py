from __future__ import annotations
import numpy as np
import pytest
from scripts import run_external_retrieval_eval_v1 as runner
from scripts import validate_external_retrieval_eval_v1 as validator

def qrel(query, corpus, score):
    return {"dataset_id":"x","split":"train","query_id":query,"corpus_id":corpus,"relevance_score":score}

def test_exact_ranking_tie_break_recall_hit_mrr_and_graded_ndcg():
    corpus=np.asarray([[1.,0.],[1.,0.],[0.,1.]],dtype=np.float32)
    ranked=runner.rank_exact(corpus,np.asarray([1.,0.],dtype=np.float32),["b","a","c"],3)
    assert ranked==["a","b","c"]
    metrics=runner.metric_row(ranked,[qrel("q","a",4),qrel("q","z",1)])
    assert metrics["recall_at_5"]==0.5 and metrics["hit_at_5"]==1.0 and metrics["mrr_at_10"]==1.0
    assert metrics["ndcg_at_10"] < 1.0

def test_records_reject_missing_duplicate_empty_and_nonfinite():
    docs=[{"corpus_id":"d","text":"text"}]; queries=[{"query_id":"q","text":"query","split":"train"}]
    runner.validate_records(docs,queries,[qrel("q","d",1)],"train")
    with pytest.raises(runner.ExternalEvalError,match="duplicate_qrel"):
        runner.validate_records(docs,queries,[qrel("q","d",1),qrel("q","d",1)],"train")
    with pytest.raises(runner.ExternalEvalError,match="empty_corpus_text"):
        runner.validate_records([{"corpus_id":"d","text":""}],queries,[],"train")
    with pytest.raises(runner.ExternalEvalError,match="invalid_relevance"):
        runner.validate_records(docs,queries,[qrel("q","d",float("nan"))],"train")

def test_acord_test_is_refused_without_opening_zip(tmp_path):
    contract=runner.load_json(runner.CONTRACT)
    with pytest.raises(runner.ExternalEvalError,match="acord_test_forbidden"):
        runner.load_acord(tmp_path,contract,"test")

def test_invalid_vectors_and_empty_aggregate_fail():
    with pytest.raises(runner.ExternalEvalError,match="invalid_vectors"):
        runner.rank_exact(np.asarray([[np.nan]],dtype=np.float32),np.asarray([1.],dtype=np.float32),["d"])
    with pytest.raises(runner.ExternalEvalError,match="no_evaluable_queries"):
        runner.aggregate([])

def test_metric_aggregate_is_per_query_mean():
    rows=[{"metrics":{"recall_at_5":1.,"hit_at_5":1.}},{"metrics":{"recall_at_5":0.,"hit_at_5":0.}}]
    assert runner.aggregate(rows)=={"hit_at_5":0.5,"recall_at_5":0.5}

def test_jdocqa_requires_known_schema_and_hash(tmp_path):
    contract=runner.load_json(runner.CONTRACT)
    with pytest.raises(runner.ExternalEvalError,match="jdocqa_hash"):
        runner.inspect_jdocqa(tmp_path,contract)

def test_external_contract_binds_frozen_v2_and_protects_test_splits():
    assert validator.validate_contract()["validation_status"] == "PASS"
