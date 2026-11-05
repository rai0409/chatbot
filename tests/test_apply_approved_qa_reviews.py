from __future__ import annotations

import hashlib
import json

import pytest

from scripts.apply_approved_qa_reviews import DECISIONS_PATH, LEGACY_PATH, MANIFEST_PATH, TOURISM_PATH, apply_reviews

# Deterministic, invented records. No production PDF/JSONL content is copied.
# Files are written only into pytest's temporary directory.
def _synthetic_jsonl(rows):
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _synthetic_record(qa_id, status, question):
    return {
        "qa_id": qa_id,
        "question": question,
        "answer": "合成テスト専用の回答です。",
        "source_document": "synthetic_source.txt",
        "source_pages": [1],
        "source_record_fingerprint": hashlib.sha256(("synthetic:" + qa_id).encode()).hexdigest(),
        "status": status,
        "approval_review_required": status != "approved",
        "approval_provenance": "synthetic_test",
        "tenant_id": "synthetic_tenant",
    }


def _root(tmp_path):
    legacy = [
        _synthetic_record("synthetic-legacy-001", "legacy_unreviewed", "合成ルールAの申請先はどこですか"),
        _synthetic_record("synthetic-legacy-002", "legacy_unreviewed", "合成ルールBの期限はいつですか"),
    ]
    tourism = [
        _synthetic_record("synthetic-approved-001", "approved", "合成イベントCの概要は何ですか"),
    ]
    legacy_bytes = _synthetic_jsonl(legacy)
    tourism_bytes = _synthetic_jsonl(tourism)
    manifest = {
        "schema_version": "synthetic_review_test.v1",
        "sources": [
            {"output_path": LEGACY_PATH, "source_jsonl_sha256": hashlib.sha256(legacy_bytes).hexdigest()},
            {"output_path": TOURISM_PATH, "source_jsonl_sha256": hashlib.sha256(tourism_bytes).hexdigest()},
        ],
        "total_record_count": 3,
        "fully_governed_approved_count": 1,
        "rejected_count": 0,
        "review_required_count": 2,
        "review_decisions": {"decision_count": 0},
    }
    contents = {
        LEGACY_PATH: legacy_bytes,
        TOURISM_PATH: tourism_bytes,
        MANIFEST_PATH: (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
        DECISIONS_PATH: b"",
    }
    for relative, payload in contents.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return tmp_path


def _decision(root, **changes):
    row = json.loads((root / LEGACY_PATH).read_text(encoding="utf-8").splitlines()[0])
    value = {"qa_id": row["qa_id"], "source_record_fingerprint": row["source_record_fingerprint"], "decision": "approved", "reviewed_by": "reviewer", "reviewed_at": "2026-01-01T00:00:00+00:00", "review_note": "ok"}
    value.update(changes)
    (root / DECISIONS_PATH).write_text(json.dumps(value) + "\n", encoding="utf-8")
    return value


def test_empty_decisions_and_check_do_not_modify_outputs(tmp_path):
    root = _root(tmp_path); before = [(root / p).read_bytes() for p in (LEGACY_PATH, MANIFEST_PATH, TOURISM_PATH)]
    assert apply_reviews(root=root) == {"total_record_count":3,"fully_governed_approved_count":1,"rejected_count":0,"review_required_count":2,"decision_count":0}
    assert before == [(root / p).read_bytes() for p in (LEGACY_PATH, MANIFEST_PATH, TOURISM_PATH)]


@pytest.mark.parametrize(("decision", "approved", "rejected", "review"), [("approved",2,0,1),("rejected",1,1,1)])
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
