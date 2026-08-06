from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = "data/approved_qa/sources/040219e-biscfaq.jsonl"
CSV_PATH = "artifacts/approved_qa_review/040219e-biscfaq.review.csv"
MARKDOWN_PATH = "artifacts/approved_qa_review/040219e-biscfaq.review.md"
MANIFEST_PATH = "artifacts/approved_qa_review/040219e-biscfaq.review_manifest.json"
COLUMNS = ["review_order", "qa_id", "question", "answer", "source_document", "source_pages", "source_record_fingerprint", "current_status", "reviewer_decision", "reviewed_by", "reviewed_at", "review_note"]


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read(path: Path) -> list[dict[str, Any]]:
    rows=[]
    for no,line in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
        if not line.strip(): continue
        try: row=json.loads(line)
        except json.JSONDecodeError as exc: raise ValueError(f"{path}:{no}: malformed JSON") from exc
        if not isinstance(row,dict): raise ValueError(f"{path}:{no}: record must be an object")
        rows.append(row)
    return rows


def build_review_packet(*, root: Path = ROOT) -> dict[str, Any]:
    source=root/SOURCE_PATH; source_bytes=source.read_bytes(); rows=_read(source)
    selected=[]; qa_ids=set(); fingerprints=set()
    for row in rows:
        if not row.get("approval_review_required"): continue
        for field in ("qa_id","question","answer","source_record_fingerprint"):
            if not str(row.get(field) or "").strip(): raise ValueError(f"missing {field}")
        if row["qa_id"] in qa_ids: raise ValueError(f"duplicate qa_id: {row['qa_id']}")
        if row["source_record_fingerprint"] in fingerprints: raise ValueError("duplicate source_record_fingerprint")
        qa_ids.add(row["qa_id"]); fingerprints.add(row["source_record_fingerprint"]); selected.append(row)
    selected.sort(key=lambda row: row["qa_id"])
    buf=io.StringIO(newline=""); writer=csv.DictWriter(buf,fieldnames=COLUMNS,lineterminator="\r\n",quoting=csv.QUOTE_ALL); writer.writeheader()
    markdown=["# 040219e-biscfaq review packet", ""]
    for order,row in enumerate(selected,1):
        pages=", ".join(str(x) for x in row.get("source_pages",[]))
        writer.writerow({"review_order":order,"qa_id":row["qa_id"],"question":row["question"],"answer":row["answer"],"source_document":row.get("source_document",""),"source_pages":pages,"source_record_fingerprint":row["source_record_fingerprint"],"current_status":row.get("status","") ,"reviewer_decision":"","reviewed_by":"","reviewed_at":"","review_note":""})
        markdown += [f"## {order}. {row['qa_id']}", "", f"- Question: {row['question']}", f"- Answer: {row['answer']}", f"- Source document: {row.get('source_document','')}", f"- Source pages: {pages}", f"- Source record fingerprint: {row['source_record_fingerprint']}", "- Reviewer decision: ", "- Reviewed by: ", "- Reviewed at: ", "- Review note: ", ""]
    csv_bytes=b"\xef\xbb\xbf"+buf.getvalue().encode("utf-8"); markdown_bytes=("\n".join(markdown)).encode("utf-8")
    manifest={"schema_version":"approved_qa_review_packet.v1","generated_by":"scripts/build_approved_qa_review_packet.py","source_path":SOURCE_PATH,"source_sha256":_sha(source_bytes),"record_count":len(selected),"review_required_count":len(selected),"csv":{"path":CSV_PATH,"sha256":_sha(csv_bytes)},"markdown":{"path":MARKDOWN_PATH,"sha256":_sha(markdown_bytes)},"ordering_rule":"qa_id ascending"}
    for path,payload in ((root/CSV_PATH,csv_bytes),(root/MARKDOWN_PATH,markdown_bytes),(root/MANIFEST_PATH,_canonical(manifest))):
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(payload)
    return manifest


if __name__ == "__main__": print(json.dumps(build_review_packet(),ensure_ascii=False,sort_keys=True))
