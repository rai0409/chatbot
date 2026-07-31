from __future__ import annotations
import argparse, csv, hashlib, json, os
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE="data/approved_qa/sources/040219e-biscfaq.jsonl"
PACKET_MANIFEST="artifacts/approved_qa_review/040219e-biscfaq.review_manifest.json"
COLUMNS=["review_order","qa_id","question","answer","source_document","source_pages","source_record_fingerprint","current_status","reviewer_decision","reviewed_by","reviewed_at","review_note"]
def sha(b): return hashlib.sha256(b).hexdigest()
def rows(path):
    try:
        with path.open(encoding="utf-8-sig",newline="") as f:
            reader=csv.DictReader(f, strict=True)
            if reader.fieldnames != COLUMNS: raise ValueError("missing or invalid CSV columns")
            return list(reader)
    except csv.Error as e: raise ValueError("malformed CSV") from e
def source(root):
    data={}
    for no,line in enumerate((root/SOURCE).read_text(encoding="utf-8").splitlines(),1):
        if not line: continue
        try: r=json.loads(line)
        except json.JSONDecodeError as e: raise ValueError("malformed governed source") from e
        if not isinstance(r,dict): raise ValueError("non-object governed source")
        data[r["qa_id"]]=r
    return data
def timestamp(v):
    try: d=datetime.fromisoformat(v.replace("Z","+00:00"))
    except ValueError as e: raise ValueError("invalid reviewed_at") from e
    if d.tzinfo is None or d.utcoffset() is None: raise ValueError("timezone-aware reviewed_at required")
def import_review_csv(*,input_path:Path,root:Path=ROOT,output_path:Path|None=None):
    manifest=json.loads((root/PACKET_MANIFEST).read_text(encoding="utf-8")); governed=source(root)
    if manifest["source_sha256"]!=sha((root/SOURCE).read_bytes()): raise ValueError("review packet manifest source SHA-256 mismatch")
    decisions=[]; seen_ids=set(); seen_orders=set(); total=approved=rejected=unreviewed=0
    for row in rows(input_path):
        total+=1; qid=row["qa_id"]
        if qid in seen_ids: raise ValueError("duplicate qa_id")
        if row["review_order"] in seen_orders: raise ValueError("duplicate review_order")
        seen_ids.add(qid); seen_orders.add(row["review_order"])
        if qid not in governed: raise ValueError("unknown qa_id")
        g=governed[qid]; immutable={"question":g["question"],"answer":g["answer"],"source_document":g["source_document"],"source_pages":", ".join(str(x) for x in g["source_pages"]),"source_record_fingerprint":g["source_record_fingerprint"],"current_status":g["status"]}
        for key,value in immutable.items():
            if row[key]!=value: raise ValueError(f"immutable CSV value differs: {key}")
        decision=row["reviewer_decision"]
        if decision not in ("","approved","rejected"): raise ValueError("unsupported decision")
        review_values=[row[x] for x in ("reviewed_by","reviewed_at","review_note")]
        if not decision:
            if any(review_values): raise ValueError("review fields supplied with blank decision")
            unreviewed+=1; continue
        if not row["reviewed_by"].strip(): raise ValueError("missing reviewed_by")
        timestamp(row["reviewed_at"])
        decisions.append({"qa_id":qid,"source_record_fingerprint":row["source_record_fingerprint"],"decision":decision,"reviewed_by":row["reviewed_by"],"reviewed_at":row["reviewed_at"],"review_note":row["review_note"]})
        approved+=decision=="approved"; rejected+=decision=="rejected"
    decisions.sort(key=lambda x:x["qa_id"]); payload=b"".join((json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode() for x in decisions)
    result={"total_csv_rows":total,"approved_decisions":approved,"rejected_decisions":rejected,"unreviewed_rows":unreviewed,"output_sha256":sha(payload)}
    if output_path is not None:
        output_path.parent.mkdir(parents=True,exist_ok=True); tmp=output_path.with_name("."+output_path.name+".tmp"); tmp.write_bytes(payload); os.replace(tmp,output_path)
    return result
def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",required=True); g=p.add_mutually_exclusive_group(required=True); g.add_argument("--check",action="store_true"); g.add_argument("--output") ;a=p.parse_args(); print(json.dumps(import_review_csv(input_path=Path(a.input),output_path=Path(a.output) if a.output else None),ensure_ascii=False,sort_keys=True))
if __name__=="__main__": main()
