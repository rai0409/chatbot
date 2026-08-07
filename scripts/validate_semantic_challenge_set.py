#!/usr/bin/env python3
"""Static, retrieval-free validation for the frozen semantic challenge set."""
from __future__ import annotations
import argparse, hashlib, json, math, re, sys, unicodedata
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
MODES=["bm25_only","dense_only","hybrid","hybrid_rerank"]
CATEGORIES={"lexical_paraphrase":6,"business_jargon_plain_language":6,"procedure_paraphrase":6,"abbreviation_expansion":4,"concept_description":4,"negation_exception":3,"condition_outcome_paraphrase":3,"abstain_missing_fact":4,"abstain_ambiguous_scope":4}
class InputError(ValueError): pass
class PolicyError(ValueError): pass
def digest(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def norm(v:str)->str:return re.sub(r"[\s、。．，,.!?！？「」『』（）()［］\[\]{}:：;；\"']", "", unicodedata.normalize("NFKC",v).lower())
def load_json(path:Path)->dict[str,Any]:
 try: value=json.loads(path.read_text(encoding="utf-8"))
 except (OSError,json.JSONDecodeError) as e: raise InputError("unreadable or invalid JSON") from e
 if not isinstance(value,dict): raise InputError("JSON root must be object")
 return value
def load_lines(path:Path)->list[dict[str,Any]]:
 try: lines=path.read_text(encoding="utf-8").splitlines()
 except OSError as e: raise InputError("unreadable JSONL") from e
 try: rows=[json.loads(x) for x in lines]
 except json.JSONDecodeError as e: raise InputError("invalid JSONL") from e
 if not all(isinstance(x,dict) for x in rows): raise InputError("JSONL objects required")
 return rows
def exact(value:Any, keys:set[str], label:str)->dict[str,Any]:
 if not isinstance(value,dict) or set(value)!=keys: raise InputError("malformed "+label)
 return value
def number(v:Any,label:str)->float:
 if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v): raise InputError("invalid numeric "+label)
 return float(v)
def validate_contract(c:dict[str,Any], cases:Path,chunks:Path)->None:
 exact(c,{"schema_version","profile","inputs","fixed_embedding","category_counts","construction_policy","future_acceptance_thresholds","promotion"},"contract")
 if c["schema_version"]!="semantic_challenge_set_contract.v1": raise InputError("schema")
 p=exact(c["profile"],{"name","purpose","design_status","retrieval_outcomes_observed","real_vector_executed","real_generation","ordinary_ci_required","release_gate_required"},"profile")
 if (p["name"],p["purpose"],p["design_status"],p["retrieval_outcomes_observed"],p["real_vector_executed"],p["real_generation"],p["ordinary_ci_required"],p["release_gate_required"])!=("retrieval_semantic_challenge_v1","precommitted evaluation of incremental semantic retrieval value","frozen_pre_evaluation",False,False,False,False,True): raise PolicyError("profile policy")
 i=exact(c["inputs"],{"cases_path","cases_sha256","chunks_path","chunks_sha256","expected_case_count","expected_answerable_count","expected_abstain_count","expected_chunk_count","expected_gold_chunk_count","expected_distractor_chunk_count","expected_mode_count","expected_row_count","top_k","eval_k","tenant_id","modes"},"inputs")
 expected={"expected_case_count":40,"expected_answerable_count":32,"expected_abstain_count":8,"expected_chunk_count":64,"expected_gold_chunk_count":32,"expected_distractor_chunk_count":32,"expected_mode_count":4,"expected_row_count":160,"top_k":20,"eval_k":5,"tenant_id":"default","modes":MODES}
 if not isinstance(i,dict) or any(i.get(k)!=v or isinstance(i.get(k),bool) and isinstance(v,int) for k,v in expected.items()): raise InputError("inputs")
 if i.get("cases_path")!="eval/cases/semantic_challenge_cases.jsonl" or i.get("chunks_path")!="eval/cases/semantic_challenge_chunks.jsonl" or digest(cases)!=i.get("cases_sha256") or digest(chunks)!=i.get("chunks_sha256"): raise InputError("input hash mismatch")
 if c.get("category_counts")!=CATEGORIES: raise InputError("category counts")
 e=exact(c["fixed_embedding"],{"provider","model","revision","dimension","normalization","asset_files_sha256","runtime_network_allowed","trust_remote_code"},"fixed embedding")
 if (e["provider"],e["model"],e["revision"],e["dimension"],e["normalization"],e["asset_files_sha256"],e["runtime_network_allowed"],e["trust_remote_code"])!=("local","sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2","e8f8c211226b894fcb81acc59f3b34ba3efd5f42",384,"l2","cede7177dd492d9d7776484dce8d030f0cd127eae3297305991de91386394d5a",False,False): raise InputError("fixed embedding")
 policy=c["construction_policy"]
 required={"authored_before_retrieval_execution":True,"model_output_used_for_selection":False,"exact_identifier_queries_allowed":False,"quoted_term_queries_allowed":False,"paired_high_overlap_distractor_required":True,"gold_surface_mismatch_required":True,"abstain_cases_required":True,"proprietary_content_allowed":False,"external_network_allowed":False,"post_observation_case_editing_allowed":False,"plausible_same_domain_distractor_required":True,"trivial_distractor_allowed":False}
 if policy!=required: raise PolicyError("construction policy")
 thresholds=c["future_acceptance_thresholds"]
 threshold_values={"metric_support_count":32,"abstain_expected_count":8,"dense_query_error_max":0,"dense_zero_candidate_max":0,"semantic_gain_case_min_at_5":8,"hybrid_unique_gain_case_min_at_5":6,"hybrid_regression_case_max_at_5":0,"hybrid_gold_hit_min_at_5":28,"hybrid_mean_mrr_min_at_5":.75,"hybrid_mean_ndcg_min_at_5":.8,"hybrid_mrr_delta_over_bm25_min":.1,"hybrid_ndcg_delta_over_bm25_min":.1,"hybrid_rerank_mrr_delta_over_hybrid_min":-.02,"hybrid_rerank_ndcg_delta_over_hybrid_min":-.02,"abstain_pass_min":6,"external_network_attempt_count":0}
 definitions={"semantic_gain_definition":"answerable metric-supported case where BM25 misses all declared gold IDs at eval_k and dense or hybrid hits a declared gold ID at eval_k","hybrid_regression_definition":"answerable metric-supported case where BM25 hits a declared gold ID at eval_k and hybrid misses all declared gold IDs at eval_k"}
 if not isinstance(thresholds,dict) or set(thresholds)!=set(threshold_values)|set(definitions) or any(number(thresholds[k],k)!=v for k,v in threshold_values.items()) or any(thresholds[k]!=v for k,v in definitions.items()): raise InputError("future thresholds")
 promotion=exact(c["promotion"],{"product_promotion_eligible","challenge_set_design_complete","challenge_set_evaluation_complete","semantic_incremental_value_status","next_required_evidence"},"promotion")
 if promotion!={"product_promotion_eligible":False,"challenge_set_design_complete":True,"challenge_set_evaluation_complete":False,"semantic_incremental_value_status":"not_evaluated","next_required_evidence":"real_vector_challenge_evaluation"}: raise PolicyError("promotion")
def validate_rows(c:dict[str,Any], cases:list[dict[str,Any]], chunks:list[dict[str,Any]])->dict[str,Any]:
 if len(cases)!=40 or len(chunks)!=64: raise PolicyError("count")
 if [x["case_id"] for x in cases]!=sorted(x.get("case_id","") for x in cases) or [x["id"] for x in chunks]!=sorted(x.get("id","") for x in chunks): raise PolicyError("sorting")
 if len({x.get("case_id") for x in cases})!=40 or any(not str(x.get("case_id","")).startswith("sc_") for x in cases): raise PolicyError("case identifiers")
 if len({x.get("id") for x in chunks})!=64 or len({x.get("doc_id") for x in chunks})!=64 or any(not str(x.get("id","")).startswith("sc_chunk_") for x in chunks): raise PolicyError("chunk identifiers")
 chunk_keys={"id","text","source_doc","source_pages","doc_id","chunk_index","searchable","type","quality","challenge_pair_id","challenge_role","topic_id","support_fact_id"}
 if any(set(x)!=chunk_keys or any(not isinstance(x.get(k),str) or not x[k] for k in {"id","text","source_doc","doc_id","type","quality","challenge_pair_id","challenge_role","topic_id","support_fact_id"}) or x.get("source_doc")!=x.get("doc_id") or x.get("searchable")!=1 or isinstance(x.get("searchable"),bool) or not isinstance(x.get("chunk_index"),int) or isinstance(x.get("chunk_index"),bool) or x["chunk_index"]<=0 or not isinstance(x.get("source_pages"),list) or len(x["source_pages"])!=1 or not isinstance(x["source_pages"][0],int) or isinstance(x["source_pages"][0],bool) or x["source_pages"][0]<=0 or x.get("challenge_role") not in {"gold","lexical_distractor"} for x in chunks): raise PolicyError("chunk schema")
 categories={k:sum(x.get("category")==k for x in cases) for k in CATEGORIES}
 if categories!=CATEGORIES: raise PolicyError("category distribution")
 by_id={x["id"]:x for x in chunks}; golds=[x for x in chunks if x["challenge_role"]=="gold"]; distractors=[x for x in chunks if x["challenge_role"]=="lexical_distractor"]
 if len(golds)!=32 or len(distractors)!=32: raise PolicyError("role count")
 pairs={x["challenge_pair_id"] for x in chunks}
 if len(pairs)!=32 or any(sum(x["challenge_pair_id"]==pair for x in chunks)!=2 or {x["challenge_role"] for x in chunks if x["challenge_pair_id"]==pair}!={"gold","lexical_distractor"} for pair in pairs): raise PolicyError("corpus pairs")
 query_set=set()
 for case in cases:
  q=norm(str(case.get("query","")))
  if not q or q in query_set or any(token in q for token in ("sc_","chunk_","doc_",".pdf")) or re.search(r"\b[A-Za-z]+[-_]?[0-9]+\b",str(case.get("query",""))) or "\"" in str(case.get("query","")) or "「" in str(case.get("query","")): raise PolicyError("query policy")
  query_set.add(q)
  if case.get("answerable") is True:
   required={"case_id","category","challenge_category","query","query_type","topic_id","pair_id","gold_doc_ids","gold_chunk_ids","distractor_chunk_ids","expected_support_fact_id","answerable","expected_abstain","query_surface_terms","forbidden_gold_terms","required_gold_support_terms","required_distractor_overlap_terms","notes"}
   if set(case)!=required or case["expected_abstain"] is not False or case["challenge_category"]!=case["category"] or not (len(case["gold_doc_ids"])==len(case["gold_chunk_ids"])==len(case["distractor_chunk_ids"])==1): raise PolicyError("answerable schema")
   gold=by_id.get(case["gold_chunk_ids"][0]); dis=by_id.get(case["distractor_chunk_ids"][0])
   if not gold or not dis or gold["challenge_role"]!="gold" or dis["challenge_role"]!="lexical_distractor" or gold["doc_id"]==dis["doc_id"] or gold["challenge_pair_id"]!=dis["challenge_pair_id"] or gold["challenge_pair_id"]!=case["pair_id"] or gold["topic_id"]!=dis["topic_id"] or gold["topic_id"]!=case["topic_id"] or gold["support_fact_id"]!=case["expected_support_fact_id"] or dis["support_fact_id"]==case["expected_support_fact_id"] or case["gold_doc_ids"]!=[gold["doc_id"]]: raise PolicyError("pair integrity")
   surface,forbidden,support,overlap=case["query_surface_terms"],case["forbidden_gold_terms"],case["required_gold_support_terms"],case["required_distractor_overlap_terms"]
   if not (1<=len(surface)<=4 and forbidden and 1<=len(support)<=3 and overlap and set(surface).issubset(set(forbidden))): raise PolicyError("lexical declarations")
   gt,dt=norm(gold["text"]),norm(dis["text"])
   if any(not norm(x) or norm(x) not in q for x in surface) or any(norm(x) in gt for x in forbidden) or any(norm(x) not in gt or norm(x) in q or norm(x) in dt for x in support) or any(norm(x) not in q or norm(x) not in dt for x in overlap) or not any(norm(x) in dt for x in surface): raise PolicyError("lexical mismatch: "+case["case_id"])
  else:
   required={"case_id","category","challenge_category","query","query_type","topic_id","gold_doc_ids","gold_chunk_ids","distractor_chunk_ids","answerable","expected_abstain","notes"}
   if set(case)!=required or case["expected_abstain"] is not True or case["gold_doc_ids"] or case["gold_chunk_ids"] or case["distractor_chunk_ids"]: raise PolicyError("abstain schema")
 answerable=[x for x in cases if x.get("category") not in {"abstain_missing_fact","abstain_ambiguous_scope"}]
 abstain=[x for x in cases if x.get("category") in {"abstain_missing_fact","abstain_ambiguous_scope"}]
 if len(answerable)!=32 or len(abstain)!=8 or any(x.get("answerable") is not True or x.get("expected_abstain") is not False for x in answerable) or any(x.get("answerable") is not False or x.get("expected_abstain") is not True for x in abstain) or any(len({x[k] for x in answerable})!=32 for k in ("pair_id","topic_id","expected_support_fact_id")): raise PolicyError("case global invariants")
 if {x["pair_id"] for x in answerable}!=pairs: raise PolicyError("orphan pair")
 return {"case_count":len(cases),"answerable_count":len(answerable),"abstain_count":len(abstain),"chunk_count":len(chunks),"gold_chunk_count":len(golds),"distractor_chunk_count":len(distractors),"category_counts":categories}
def source_fingerprints()->list[dict[str,str]]:
 return [{"path":p,"sha256":digest(ROOT/p)} for p in ["scripts/validate_semantic_challenge_set.py","tests/test_semantic_challenge_set.py"]]
def report(c:dict[str,Any],cp:Path,cases:Path,chunks:Path,stats:dict[str,Any])->dict[str,Any]:
 return {"schema_version":"semantic_challenge_set_design.v1","profile_name":c["profile"]["name"],"design_status":"frozen_pre_evaluation","retrieval_outcomes_observed":False,"real_vector_executed":False,"model_loaded":False,"real_generation":False,"external_network_attempt_count":0,"contract_sha256":digest(cp),"inputs":{"cases_path":c["inputs"]["cases_path"],"cases_sha256":digest(cases),"chunks_path":c["inputs"]["chunks_path"],"chunks_sha256":digest(chunks)},**stats,"pair_integrity_status":"passed","lexical_mismatch_validation_status":"passed","high_overlap_distractor_validation_status":"passed","fixed_embedding":c["fixed_embedding"],"future_acceptance_thresholds":c["future_acceptance_thresholds"],"promotion":c["promotion"],"implementation_sources":source_fingerprints(),"validation_status":"passed","validation_errors":[]}
def run(contract:Path,cases:Path,chunks:Path,output:Path|None)->dict[str,Any]:
 c=load_json(contract); validate_contract(c,cases,chunks); stats=validate_rows(c,load_lines(cases),load_lines(chunks)); result=report(c,contract,cases,chunks,stats)
 if output: output.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
 return result
def main(argv:list[str]|None=None)->int:
 p=argparse.ArgumentParser();p.add_argument("--contract",type=Path,default=ROOT/"config/evaluation/semantic_challenge_set.contract.json");p.add_argument("--cases",type=Path,default=ROOT/"eval/cases/semantic_challenge_cases.jsonl");p.add_argument("--chunks",type=Path,default=ROOT/"eval/cases/semantic_challenge_chunks.jsonl");p.add_argument("--output",type=Path);p.add_argument("--check-only",action="store_true")
 try:
  a=p.parse_args(argv);run(a.contract,a.cases,a.chunks,None if a.check_only else a.output);return 0
 except SystemExit:return 2
 except InputError as e: print("semantic challenge input error: "+str(e),file=sys.stderr);return 2
 except PolicyError as e: print("semantic challenge policy violation: "+str(e),file=sys.stderr);return 3
 except Exception as e: print("semantic challenge unexpected error: "+type(e).__name__,file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
