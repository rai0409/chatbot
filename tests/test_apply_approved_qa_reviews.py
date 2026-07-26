from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.apply_approved_qa_reviews import DECISIONS_PATH, LEGACY_PATH, MANIFEST_PATH, TOURISM_PATH, apply_reviews

ROOT = Path(__file__).resolve().parents[1]


def _root(tmp_path):
    for path in (LEGACY_PATH, TOURISM_PATH, MANIFEST_PATH):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
    decision = tmp_path / DECISIONS_PATH
    decision.parent.mkdir(parents=True, exist_ok=True)
    decision.write_bytes(b"")
    return tmp_path


def _decision(root, **changes):
    row = json.loads((root / LEGACY_PATH).read_text(encoding="utf-8").splitlines()[0])
    value = {"qa_id": row["qa_id"], "source_record_fingerprint": row["source_record_fingerprint"], "decision": "approved", "reviewed_by": "reviewer", "reviewed_at": "2026-01-01T00:00:00+00:00", "review_note": "ok"}
    value.update(changes)
    (root / DECISIONS_PATH).write_text(json.dumps(value) + "\n", encoding="utf-8")
    return value


def test_empty_decisions_and_check_do_not_modify_outputs(tmp_path):
    root = _root(tmp_path); before = [(root / p).read_bytes() for p in (LEGACY_PATH, MANIFEST_PATH, TOURISM_PATH)]
    assert apply_reviews(root=root) == {"total_record_count":118,"fully_governed_approved_count":22,"rejected_count":0,"review_required_count":96,"decision_count":0}
    assert before == [(root / p).read_bytes() for p in (LEGACY_PATH, MANIFEST_PATH, TOURISM_PATH)]


@pytest.mark.parametrize(("decision", "approved", "rejected", "review"), [("approved",23,0,95),("rejected",22,1,95)])
def test_single_decisions_apply(tmp_path, decision, approved, rejected, review):
    root=_root(tmp_path); _decision(root, decision=decision); tourism=(root/TOURISM_PATH).read_bytes()
    result=apply_reviews(root=root, apply=True)
    assert result["fully_governed_approved_count"]==approved and result["rejected_count"]==rejected and result["review_required_count"]==review
    row=json.loads((root/LEGACY_PATH).read_text().splitlines()[0]); assert row["status"]==decision and not row["approval_review_required"] and row["approval_provenance"]=="human_review"
    assert (root/TOURISM_PATH).read_bytes()==tourism


@pytest.mark.parametrize("field,value,error", [("source_record_fingerprint","stale","stale"),("qa_id","unknown","unknown"),("decision","other","unsupported"),("reviewed_by"," ","missing reviewed_by"),("reviewed_at","2026-01-01T00:00:00","timezone-aware")])
def test_invalid_decisions_fail_closed(tmp_path, field, value, error):
    root=_root(tmp_path); _decision(root, **{field:value})
    with pytest.raises(ValueError, match=error): apply_reviews(root=root, apply=True)


@pytest.mark.parametrize("bad", ["{bad}\n", "[]\n"])
def test_bad_decision_jsonl_fails_closed(tmp_path, bad):
    root=_root(tmp_path); (root/DECISIONS_PATH).write_text(bad)
    with pytest.raises(ValueError): apply_reviews(root=root)


@pytest.mark.parametrize("field", ["reviewed_by", "reviewed_at"])
def test_missing_required_reviewer_fields_fail_closed(tmp_path, field):
    root = _root(tmp_path); row = _decision(root); del row[field]
    (root / DECISIONS_PATH).write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="missing decision fields"):
        apply_reviews(root=root)


def test_duplicate_mixed_and_deterministic_hashes(tmp_path):
    root=_root(tmp_path); first=json.loads((root/LEGACY_PATH).read_text().splitlines()[0]); second=json.loads((root/LEGACY_PATH).read_text().splitlines()[1])
    rows=[{"qa_id":first["qa_id"],"source_record_fingerprint":first["source_record_fingerprint"],"decision":"approved","reviewed_by":"a","reviewed_at":"2026-01-01T00:00:00+00:00","review_note":""},{"qa_id":second["qa_id"],"source_record_fingerprint":second["source_record_fingerprint"],"decision":"rejected","reviewed_by":"b","reviewed_at":"2026-01-01T00:00:00+00:00","review_note":"no"}]
    (root/DECISIONS_PATH).write_text("".join(json.dumps(x)+"\n" for x in rows)); apply_reviews(root=root,apply=True); one=[(root/p).read_bytes() for p in (LEGACY_PATH,MANIFEST_PATH)]; apply_reviews(root=root,apply=True); assert one==[(root/p).read_bytes() for p in (LEGACY_PATH,MANIFEST_PATH)]
    manifest=json.loads((root/MANIFEST_PATH).read_text()); assert manifest["review_decisions"]["decision_count"]==2 and hashlib.sha256((root/LEGACY_PATH).read_bytes()).hexdigest()==next(x["source_jsonl_sha256"] for x in manifest["sources"] if x["output_path"]==LEGACY_PATH)
    (root/DECISIONS_PATH).write_text(json.dumps(rows[0])+"\n"+json.dumps(rows[0])+"\n")
    with pytest.raises(ValueError,match="duplicate"): apply_reviews(root=root)
