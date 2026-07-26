from __future__ import annotations
import csv, hashlib, io, json, shutil
from pathlib import Path
import pytest
from scripts.build_approved_qa_review_packet import build_review_packet, SOURCE_PATH, CSV_PATH, MARKDOWN_PATH, MANIFEST_PATH
ROOT=Path(__file__).resolve().parents[1]
def test_packet_is_deterministic_and_preserves_governance_inputs(tmp_path):
    source=tmp_path/SOURCE_PATH; source.parent.mkdir(parents=True); shutil.copy2(ROOT/SOURCE_PATH,source)
    decision=ROOT/'data/approved_qa/reviews/040219e-biscfaq.decisions.jsonl'; before=decision.read_bytes()
    manifest=build_review_packet(root=tmp_path); first=[(tmp_path/p).read_bytes() for p in (CSV_PATH,MARKDOWN_PATH,MANIFEST_PATH)]; assert manifest==build_review_packet(root=tmp_path); assert first==[(tmp_path/p).read_bytes() for p in (CSV_PATH,MARKDOWN_PATH,MANIFEST_PATH)]
    csv_bytes=(tmp_path/CSV_PATH).read_bytes(); assert csv_bytes.startswith(b'\xef\xbb\xbf'); rows=list(csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig')))); assert len(rows)==96; assert all(not item[x] for item in rows for x in ('reviewer_decision','reviewed_by','reviewed_at','review_note')); assert hashlib.sha256(csv_bytes).hexdigest()==manifest['csv']['sha256']; assert decision.read_bytes()==before
def test_invalid_source_fails_closed(tmp_path):
    source=tmp_path/SOURCE_PATH; source.parent.mkdir(parents=True); source.write_text('{bad}\n')
    with pytest.raises(ValueError,match='malformed'): build_review_packet(root=tmp_path)
@pytest.mark.parametrize('field,value,error',[('qa_id','same','duplicate qa_id'),('source_record_fingerprint','same','duplicate source_record_fingerprint')])
def test_duplicate_values_fail_closed(tmp_path,field,value,error):
    source=tmp_path/SOURCE_PATH; source.parent.mkdir(parents=True); rows=[{'qa_id':'a','question':'q','answer':'a','source_record_fingerprint':'f1','approval_review_required':True},{'qa_id':'b','question':'q2','answer':'a2','source_record_fingerprint':'f2','approval_review_required':True}]; rows[1][field]=rows[0][field]; source.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    with pytest.raises(ValueError,match=error): build_review_packet(root=tmp_path)
