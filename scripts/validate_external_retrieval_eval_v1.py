#!/usr/bin/env python3
"""Fail-closed validator for External Retrieval Evaluation v1 artifacts."""
from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.run_external_retrieval_eval_v1 import ARTIFACTS,CONTRACT,DEFAULT_OUT,ExternalEvalError,load_json,sha256
V2=ROOT/'config/evaluation/commercial_retrieval_dev_v1_embedding_comparison_v2.contract.json'
def validate_contract()->dict:
 c=load_json(CONTRACT)
 if c.get('schema_version')!='external_retrieval_eval.contract.v1': raise ExternalEvalError('contract_schema')
 source=c.get('candidate_source',{})
 if source.get('contract_sha256')!=sha256(V2): raise ExternalEvalError('v2_contract_binding')
 if c['datasets']['acord']['forbidden_split']!='test' or c['datasets']['jdocqa']['test_access']!='FORBIDDEN': raise ExternalEvalError('holdout_policy')
 return {'validation_status':'PASS','schema_version':c['schema_version']}
def validate(output:Path=DEFAULT_OUT)->dict:
 c=load_json(CONTRACT); validate_contract(); output=output.resolve(); required=[output/x for x in ARTIFACTS];
 if not all(x.is_file() for x in required): raise ExternalEvalError('artifact_files_missing')
 manifest=load_json(output/'dataset_manifest.json'); results=load_json(output/'candidate_results.json'); receipt=load_json(output/'execution_receipt.json')
 if manifest['dataset_id'] not in ('miracl_ja_lite','acord') or manifest['split']=='test' and manifest['dataset_id']=='acord': raise ExternalEvalError('dataset_policy')
 candidates={x['id']:x for x in load_json(V2)['candidate_embeddings']}
 if set(results.get('candidates',{}))!=set(candidates): raise ExternalEvalError('candidate_set')
 if receipt.get('network_access_count')!=0 or receipt.get('selection_holdout_access_count')!=0 or receipt.get('acord_test_access_count')!=0 or receipt.get('jdocqa_test_access_count')!=0: raise ExternalEvalError('execution_boundary')
 if any(not math.isfinite(v) or not 0<=v<=1 for r in results['candidates'].values() for v in r['metrics'].values()): raise ExternalEvalError('metric_range')
 per=[]; rankings=[]
 for path,target in ((output/'per_query_results.jsonl',per),(output/'rankings.jsonl',rankings)):
  for line in path.read_text(encoding='utf-8').splitlines():
   try: target.append(json.loads(line))
   except json.JSONDecodeError as e: raise ExternalEvalError(f'invalid_jsonl:{path}') from e
 expected=manifest['query_count']*6
 if len(per)!=expected or len(rankings)!=expected: raise ExternalEvalError('per_query_or_ranking_count')
 for candidate_id,candidate in candidates.items():
  rows=[x for x in per if x.get('candidate_id')==candidate_id]
  if len(rows)!=manifest['query_count'] or results['candidates'][candidate_id].get('revision')!=candidate['revision']: raise ExternalEvalError(f'candidate_identity:{candidate_id}')
  if any(not isinstance(x.get('top_corpus_ids'),list) or len(x['top_corpus_ids'])!=len(set(x['top_corpus_ids'])) for x in rows): raise ExternalEvalError('ranking_order')
  means={k:sum(x['metrics'][k] for x in rows)/len(rows) for k in rows[0]['metrics']}
  if any(not math.isclose(means[k],results['candidates'][candidate_id]['metrics'][k],rel_tol=1e-12,abs_tol=1e-12) for k in means): raise ExternalEvalError('aggregate_consistency')
 for name,digest in receipt['artifact_sha256'].items():
  if sha256(output/name)!=digest: raise ExternalEvalError(f'artifact_sha:{name}')
 return {'validation_status':'PASS','candidate_count':6,'dataset_id':manifest['dataset_id']}
def main():
 p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,default=DEFAULT_OUT);p.add_argument('--contract-only',action='store_true');a=p.parse_args()
 try: print(json.dumps(validate_contract() if a.contract_only else validate(a.output_dir),sort_keys=True))
 except ExternalEvalError as e: print(json.dumps({'validation_status':'FAIL','error':str(e)}));return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
