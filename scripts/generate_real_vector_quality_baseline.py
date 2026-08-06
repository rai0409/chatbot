#!/usr/bin/env python3
"""Generate the governed offline real-vector retrieval-quality baseline."""
from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts import validate_embedding_asset_contract, validate_embedding_source_contract

SCHEMA_VERSION = "real_vector_quality_baseline.v1"
DEFAULT_CONTRACT = ROOT / "config/evaluation/real_vector_quality_baseline.contract.json"
DEFAULT_OUTPUT = ROOT / "reports/current_real_vector_quality_baseline.json"

class ContractError(ValueError): pass

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ContractError("contract root must be object")
    return value

def _relative(path: Path) -> str:
    try: return path.resolve().relative_to(ROOT).as_posix()
    except ValueError: return path.name

def _safe(value: Any, secrets: tuple[str,...]=()) -> Any:
    if isinstance(value, dict): return {k:_safe(v,secrets) for k,v in value.items() if not any(x in k.lower() for x in ("token","secret","password","credential","api_key"))}
    if isinstance(value, list): return [_safe(v,secrets) for v in value]
    if isinstance(value,str):
        for x in secrets:
            if x: value=value.replace(x,"[REDACTED]")
        return value.replace(str(ROOT),"<repo>") if str(ROOT) in value else value
    return value

def validate_contract(contract: dict[str,Any]) -> None:
    if contract.get("schema_version") != "real_vector_quality_baseline_contract.v1": raise ContractError("unsupported contract schema")
    p=contract.get("profile",{}); i=contract.get("inputs",{}); e=contract.get("embedding",{})
    if p.get("real_vector") is not True or p.get("real_generation") is not False: raise ContractError("invalid evaluation semantics")
    if i.get("modes") != ["bm25_only","dense_only","hybrid","hybrid_rerank"] or i.get("expected_row_count") != 100: raise ContractError("invalid inputs")
    if e.get("provider") != "local" or e.get("dimension") != 384 or e.get("runtime_network_allowed") is not False or e.get("trust_remote_code") is not False: raise ContractError("invalid embedding contract")
    for path_key, hash_key in (("cases_path","cases_sha256"),("chunks_path","chunks_sha256")):
        path=ROOT/i[path_key]
        if not path.is_file() or sha(path)!=i[hash_key]: raise ContractError(f"input hash mismatch: {path_key}")

def _network_guard(path: Path) -> None:
    path.write_text('''import socket, os, json\nfrom pathlib import Path\nL=Path(os.environ["REAL_VECTOR_NETWORK_LOG"])\ndef ok(a):\n h=str(a[0] if isinstance(a,tuple) else a).lower(); return h in {"127.0.0.1","::1","localhost"}\no=socket.socket.connect\ndef c(s,a):\n if s.family==socket.AF_UNIX or ok(a): return o(s,a)\n L.write_text(json.dumps({"attempt":repr(a)})+"\\n",encoding="utf-8"); raise OSError("external network blocked")\nsocket.socket.connect=c\n''',encoding="utf-8")

def build_command(contract: dict[str,Any], rows: Path, summary: Path, collection: str) -> list[str]:
    i=contract["inputs"]
    return [sys.executable,"-m","eval.runner","--retrieval-aware","--real-vector","--cases",i["cases_path"],"--chunks-jsonl",i["chunks_path"],"--modes",','.join(i["modes"]),"--top-k",str(i["top_k"]),"--eval-k",str(i["eval_k"]),"--tenant-id",i["tenant_id"],"--eval-collection",collection,"--per-query-output",str(rows),"--summary-output",str(summary),"--quiet"]

def analyse(contract:dict[str,Any], summary:dict[str,Any], rows:list[dict[str,Any]], collection:str, attempts:int) -> dict[str,Any]:
    i=contract["inputs"]; modes=i["modes"]; by=summary.get("by_mode",{})
    counts={m:sum(r.get("mode")==m for r in rows) for m in modes}
    dense=[r for r in rows if r.get("mode")=="dense_only"]
    d={"query_error_count":sum(bool(r.get("query_error")) for r in dense),"zero_candidate_query_count":sum(not r.get("before_rerank_ids") for r in dense),"nonempty_candidate_query_count":sum(bool(r.get("before_rerank_ids")) for r in dense),"gold_hit_count_at_5":sum(bool(r.get("gold_chunk_hit_at_k") or r.get("gold_doc_hit_at_k")) for r in dense)}
    dense_support=sum(bool(r.get("gold_chunk_ids") or r.get("gold_doc_ids")) for r in dense)
    d["metric_support_count"]=dense_support
    d["gold_miss_count_at_5"]=dense_support-d["gold_hit_count_at_5"] if len(dense)==i["expected_case_count"] else -1
    bm={r["case_id"] for r in rows if r.get("mode")=="bm25_only" and not (r.get("gold_chunk_hit_at_k") or r.get("gold_doc_hit_at_k"))}
    dh={r["case_id"] for r in dense if r.get("gold_chunk_hit_at_k") or r.get("gold_doc_hit_at_k")}
    hy={r["case_id"] for r in rows if r.get("mode")=="hybrid" and not (r.get("gold_chunk_hit_at_k") or r.get("gold_doc_hit_at_k"))}
    gains=sorted(bm & dh); hybrid_gains=sorted(bm - hy); regress=sorted((set(r["case_id"] for r in rows if r.get("mode")=="bm25_only") - bm) & hy)
    inv=contract["release_invariants"]
    errors=[]
    if summary.get("status")!="ok" or len(rows)!=i["expected_row_count"] or any(counts[m]!=i["expected_case_count"] for m in modes): errors.append("row_count")
    if d["query_error_count"] or d["zero_candidate_query_count"] or d["gold_hit_count_at_5"]<inv["dense_gold_hit_min_at_5"] or d["gold_miss_count_at_5"]: errors.append("dense")
    if regress or attempts: errors.append("isolation")
    for m,k1,k2 in (("hybrid","hybrid_mrr_min_at_5","hybrid_ndcg_min_at_5"),("hybrid_rerank","hybrid_rerank_mrr_min_at_5","hybrid_rerank_ndcg_min_at_5")):
        if by.get(m,{}).get("mean_mrr_at_k",0)<inv[k1] or by.get(m,{}).get("mean_ndcg_at_k",0)<inv[k2]: errors.append(m)
    return {"mode_row_counts":counts,"dense_diagnostics":d,"comparison":{"semantic_gain_case_ids":hybrid_gains,"semantic_gain_case_count":len(hybrid_gains),"dense_unique_gain_case_ids":gains,"dense_unique_gain_case_count":len(gains),"hybrid_regression_case_ids":regress,"hybrid_regression_case_count":len(regress),"semantic_contribution_demonstrated":bool(hybrid_gains)},"validation_errors":errors}

def generate(contract_path:Path, output:Path, asset_dir:Path|None, work_dir:Path|None, quiet:bool=False)->dict[str,Any]:
    if sys.version_info[:2] != (3,12): raise ContractError("Python 3.12 required")
    c=load(contract_path); validate_contract(c)
    asset_dir=asset_dir or (Path(os.environ["LOCAL_EMBED_MODEL_PATH"]) if os.environ.get("LOCAL_EMBED_MODEL_PATH") else None)
    if asset_dir is None: raise ContractError("external asset required")
    source=ROOT/"config/embedding_assets/retrieval_baseline.source.json"; asset=ROOT/"config/embedding_assets/retrieval_baseline.asset.json"
    validate_embedding_source_contract.validate_contract(validate_embedding_source_contract.load_contract(source))
    meta=validate_embedding_asset_contract.validate_contract(asset,source,asset_dir)
    if meta["runtime_files_sha256"] != c["embedding"]["asset_files_sha256"]: raise ContractError("asset hash mismatch")
    with tempfile.TemporaryDirectory(dir=work_dir) as temp:
        wd=Path(temp); guard=wd/"guard"; guard.mkdir(); _network_guard(guard/"sitecustomize.py")
        rows_p,summary_p,network=wd/"rows.jsonl",wd/"summary.json",wd/"network.jsonl"; collection="real_vector_quality_"+uuid.uuid4().hex
        env={"EMBED_PROVIDER":"local","LOCAL_EMBED_MODEL":c["embedding"]["model"],"LOCAL_EMBED_MODEL_PATH":str(asset_dir),"VECTORSTORE_DIR":str(wd/"vectorstore"),"CHROMA_COLLECTION":"production_sentinel_not_eval","HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","HF_DATASETS_OFFLINE":"1","ANONYMIZED_TELEMETRY":"False","OPENAI_API_KEY":"","OPENAI_BASE_URL":"","PYTHONPATH":str(guard)+os.pathsep+str(ROOT),"REAL_VECTOR_NETWORK_LOG":str(network)}
        command=build_command(c,rows_p,summary_p,collection); proc=subprocess.run(command,cwd=ROOT,env={**os.environ,**env},text=True,capture_output=True)
        if proc.returncode: raise ContractError("evaluation failed")
        summary=load(summary_p); rows=[json.loads(x) for x in rows_p.read_text(encoding="utf-8").splitlines()]; attempts=len(network.read_text().splitlines()) if network.exists() else 0
        facts=analyse(c,summary,rows,collection,attempts)
        if facts["validation_errors"]: raise ContractError("contract invariants: "+','.join(facts["validation_errors"]))
        report={"schema_version":SCHEMA_VERSION,"profile":c["profile"],"generated_at":datetime.now(timezone.utc).isoformat(),"evaluation_semantics":{"real_vector":True,"real_generation":False},"inputs":c["inputs"],"contract_sha256":sha(contract_path),"embedding":c["embedding"],"evaluation_collection":{"name":"<isolated-evaluation-collection>","record_count":summary["evaluation_collection"]["inserted_record_count"],"corpus_fingerprint":summary["evaluation_collection"]["corpus_fingerprint"]},"per_mode_metrics":summary["by_mode"],**facts,"promotion":c["promotion"],"external_network_attempt_count":attempts,"validation_status":"passed","executed_command":["<current-python>" if x==sys.executable else ("<temporary>" if str(wd) in x else x) for x in command]}
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(_safe(report),ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8"); return report

def main()->int:
 p=argparse.ArgumentParser(); p.add_argument("--contract",type=Path,default=DEFAULT_CONTRACT);p.add_argument("--output",type=Path,default=DEFAULT_OUTPUT);p.add_argument("--asset-dir",type=Path);p.add_argument("--work-dir",type=Path);p.add_argument("--quiet",action="store_true");a=p.parse_args()
 try: generate(a.contract,a.output,a.asset_dir,a.work_dir,a.quiet); return 0
 except ContractError as e: print(f"real-vector baseline contract error: {e}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
