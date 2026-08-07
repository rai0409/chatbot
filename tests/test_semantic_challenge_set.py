from __future__ import annotations
import copy,json
from pathlib import Path
import pytest
from scripts import validate_semantic_challenge_set as v
ROOT=Path(__file__).resolve().parents[1]; C=ROOT/"config/evaluation/semantic_challenge_set.contract.json"; CASES=ROOT/"eval/cases/semantic_challenge_cases.jsonl"; CHUNKS=ROOT/"eval/cases/semantic_challenge_chunks.jsonl"
def data(): return v.load_json(C),v.load_lines(CASES),v.load_lines(CHUNKS)
def invalid(change):
 c,cases,chunks=data(); change(c,cases,chunks)
 with pytest.raises((v.InputError,v.PolicyError)): v.validate_contract(c,CASES,CHUNKS); v.validate_rows(c,cases,chunks)
def test_current_design_validates(): c,a,b=data();v.validate_contract(c,CASES,CHUNKS);assert v.validate_rows(c,a,b)["case_count"]==40
@pytest.mark.parametrize("change",[
 lambda c,a,b:a.append(copy.deepcopy(a[0])),lambda c,a,b:a.__setitem__(1,{**a[1],"query":a[0]["query"]}),lambda c,a,b:b.append(copy.deepcopy(b[0])),lambda c,a,b:b.__setitem__(1,{**b[1],"doc_id":b[0]["doc_id"]}),lambda c,a,b:a.pop(),lambda c,a,b:a.__setitem__(0,{**a[0],"answerable":False,"expected_abstain":True,"gold_doc_ids":[],"gold_chunk_ids":[],"distractor_chunk_ids":[]}),lambda c,a,b:a.__setitem__(32,{**a[32],"answerable":True,"expected_abstain":False}),lambda c,a,b:b.pop(),lambda c,a,b:a.__setitem__(0,{**a[0],"category":"concept_description"}),lambda c,a,b:a.__setitem__(0,{k:v for k,v in a[0].items() if k!="gold_chunk_ids"}),lambda c,a,b:a.__setitem__(0,{k:v for k,v in a[0].items() if k!="distractor_chunk_ids"}),lambda c,a,b:a.__setitem__(0,{**a[0],"gold_chunk_ids":[a[0]["gold_chunk_ids"][0],"x"]}),lambda c,a,b:b.__setitem__(1,{**b[1],"doc_id":b[0]["doc_id"],"source_doc":b[0]["doc_id"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"gold_chunk_ids":["missing"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"distractor_chunk_ids":["missing"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"expected_support_fact_id":"wrong"}),lambda c,a,b:a.__setitem__(0,{**a[0],"pair_id":"wrong"}),lambda c,a,b:a.__setitem__(0,{**a[0],"topic_id":"wrong"}),lambda c,a,b:b.__setitem__(0,{**b[0],"challenge_role":"wrong"}),lambda c,a,b:a.__setitem__(32,{**a[32],"gold_doc_ids":["x"]}),lambda c,a,b:a.__setitem__(32,{**a[32],"distractor_chunk_ids":["x"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"query_surface_terms":["不存在"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"forbidden_gold_terms":[]}),lambda c,a,b:a.__setitem__(0,{**a[0],"required_gold_support_terms":["不存在"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"required_distractor_overlap_terms":["不存在"]}),lambda c,a,b:a.__setitem__(0,{**a[0],"query":"sc_chunk_01_gold を教えて"}),lambda c,a,b:a.__setitem__(0,{**a[0],"query":"「用語」を教えて"}),])
def test_policy_mutations_fail(change): invalid(change)
def test_boolean_threshold_fails():
 c,a,b=data();c["future_acceptance_thresholds"]["metric_support_count"]=True
 with pytest.raises(v.InputError):v.validate_contract(c,CASES,CHUNKS)
def test_missing_threshold_fails():
 c,a,b=data();del c["future_acceptance_thresholds"]["metric_support_count"]
 with pytest.raises(v.InputError):v.validate_contract(c,CASES,CHUNKS)
def test_report_is_deterministic(tmp_path):
 p1,p2=tmp_path/"a",tmp_path/"b";v.run(C,CASES,CHUNKS,p1);v.run(C,CASES,CHUNKS,p2);assert p1.read_bytes()==p2.read_bytes()
def test_report_has_no_retrieval_metrics(tmp_path):
 p=tmp_path/"r";v.run(C,CASES,CHUNKS,p);s=p.read_text();assert "observed_hit" not in s and "observed_semantic" not in s and "/home/" not in s
def test_implementation_fingerprints(): assert all(x["sha256"]==v.digest(ROOT/x["path"]) for x in v.source_fingerprints())
def test_baselines_unchanged(): assert v.digest(ROOT/"reports/current_retrieval_baseline.json")=="24d160979b05f2383db11033f1d830ca5e943bb75629a341fe9832ce2fc5672d"
def test_roadmap_mentions_frozen_challenge(): assert "semantic challenge" in (ROOT/"docs/roadmaps/commercial-product-roadmap.md").read_text().lower()

@pytest.mark.parametrize("query",["ABC123 の仕様を教えて","INV-12345 を確認したい","PR20 の扱いは","QX12 を探して","ID_987 を確認","「用語」を教えて","sc_chunk_01_gold を教えて"])
def test_general_identifier_and_quoted_queries_fail(query):
 c,a,b=data();a[0]={**a[0],"query":query}
 with pytest.raises(v.PolicyError):v.validate_rows(c,a,b)

@pytest.mark.parametrize("section,key,value",[("profile","extra",True),("inputs","extra",1),("fixed_embedding","extra",1),("construction_policy","extra",True),("promotion","extra",True),("future_acceptance_thresholds","extra",1),("future_acceptance_thresholds","metric_support_count",float("nan")),("future_acceptance_thresholds","metric_support_count",float("inf")),("future_acceptance_thresholds","metric_support_count",True)])
def test_nested_contract_fail_closed(section,key,value):
 c,a,b=data();c[section][key]=value
 with pytest.raises((v.InputError,v.PolicyError)):v.validate_contract(c,CASES,CHUNKS)

@pytest.mark.parametrize("field,value",[("source_pages",[True]),("source_pages",[0]),("chunk_index",True),("chunk_index",0),("searchable",True),("text","")])
def test_chunk_schema_values_fail(field,value):
 c,a,b=data();b[0]={**b[0],field:value}
 with pytest.raises(v.PolicyError):v.validate_rows(c,a,b)
