from __future__ import annotations
import csv,json,shutil
from pathlib import Path
import pytest
from scripts.import_approved_qa_review_csv import import_review_csv,COLUMNS,SOURCE,PACKET_MANIFEST
ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT / "tests/fixtures/approved_qa_review"
def setup(tmp):
 for p in (SOURCE,PACKET_MANIFEST):
  d=tmp/p;d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(FIXTURES/("040219e-biscfaq.jsonl" if p==SOURCE else "review_manifest.json"),d)
 src=FIXTURES/'review.csv'; out=tmp/'review.csv';shutil.copy2(src,out);return out
def read(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(p,rows):
 with p.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,COLUMNS);w.writeheader();w.writerows(rows)
def test_blank_and_check_write_nothing(tmp_path):
 inp=setup(tmp_path);before=inp.read_bytes();assert import_review_csv(input_path=inp,root=tmp_path)['unreviewed_rows']==96;assert inp.read_bytes()==before
@pytest.mark.parametrize('decision', ['approved','rejected'])
def test_valid_decision(tmp_path,decision):
 inp=setup(tmp_path);r=read(inp);r[0].update(reviewer_decision=decision,reviewed_by='human',reviewed_at='2026-01-01T00:00:00+00:00',review_note='n');write(inp,r);out=tmp_path/'out.jsonl';x=import_review_csv(input_path=inp,root=tmp_path,output_path=out);assert x[decision+'_decisions']==1 and json.loads(out.read_text())['decision']==decision
@pytest.mark.parametrize('field,value,error',[('question','changed','question'),('answer','changed','answer'),('source_record_fingerprint','stale','source_record_fingerprint'),('qa_id','unknown','unknown'),('reviewed_by','','reviewed_by'),('reviewed_at','2026-01-01T00:00:00','timezone')])
def test_invalid_rows_fail_closed(tmp_path,field,value,error):
 inp=setup(tmp_path);r=read(inp);r[0].update(reviewer_decision='approved',reviewed_by='human',reviewed_at='2026-01-01T00:00:00+00:00');r[0][field]=value;write(inp,r)
 with pytest.raises(ValueError,match=error):import_review_csv(input_path=inp,root=tmp_path)
def test_blank_decision_with_fields_and_duplicate_are_rejected(tmp_path):
 inp=setup(tmp_path);r=read(inp);r[0]['reviewed_by']='x';write(inp,r)
 with pytest.raises(ValueError):import_review_csv(input_path=inp,root=tmp_path)
 r=read(setup(tmp_path/'second'));r.append(r[0]);p=tmp_path/'second/review.csv';write(p,r)
 with pytest.raises(ValueError,match='duplicate'):import_review_csv(input_path=p,root=tmp_path/'second')
