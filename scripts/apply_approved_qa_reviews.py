from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.build_approved_qa_sources import _canonical_bytes, _sha256_bytes


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = "data/approved_qa/sources/040219e-biscfaq.jsonl"
TOURISM_PATH = "data/approved_qa/sources/58887_95105_misc.jsonl"
MANIFEST_PATH = "data/approved_qa/manifest.json"
DECISIONS_PATH = "data/approved_qa/reviews/040219e-biscfaq.decisions.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: malformed JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_no}: JSONL record must be an object")
        rows.append(value)
    return rows


def _validate_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing reviewed_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid reviewed_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("reviewed_at must be timezone-aware")
    return text


def _decisions(path: Path, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    decisions = {}
    required = {"qa_id", "source_record_fingerprint", "decision", "reviewed_by", "reviewed_at", "review_note"}
    for row in _read_jsonl(path):
        missing = required - set(row)
        if missing:
            raise ValueError(f"missing decision fields: {', '.join(sorted(missing))}")
        qa_id = str(row["qa_id"] or "").strip()
        if qa_id not in records:
            raise ValueError(f"unknown qa_id: {qa_id}")
        if qa_id in decisions:
            raise ValueError(f"duplicate decision for qa_id: {qa_id}")
        if row["decision"] not in {"approved", "rejected"}:
            raise ValueError("unsupported decision")
        if not str(row["reviewed_by"] or "").strip():
            raise ValueError("missing reviewed_by")
        _validate_timestamp(row["reviewed_at"])
        if row["source_record_fingerprint"] != records[qa_id].get("source_record_fingerprint"):
            raise ValueError(f"stale source_record_fingerprint for qa_id: {qa_id}")
        decisions[qa_id] = row
    return decisions


def _fingerprint(record: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes({key: value for key, value in record.items() if key != "source_record_fingerprint"}))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def apply_reviews(*, root: Path = ROOT, apply: bool = False, decisions_path: str = DECISIONS_PATH) -> dict[str, Any]:
    legacy_path, tourism_path, manifest_path = root / LEGACY_PATH, root / TOURISM_PATH, root / MANIFEST_PATH
    legacy = _read_jsonl(legacy_path)
    records = {str(row.get("qa_id") or ""): row for row in legacy}
    if len(records) != len(legacy) or not all(records):
        raise ValueError("legacy governed source has duplicate or missing qa_id")
    decisions_file = root / decisions_path
    decisions_bytes = decisions_file.read_bytes()
    decisions = _decisions(decisions_file, records)
    updated = []
    for original in legacy:
        row = dict(original)
        decision = decisions.get(row["qa_id"])
        if decision:
            row.update({"status": decision["decision"], "approval_review_required": False, "approval_provenance": "human_review", "reviewed_by": decision["reviewed_by"], "reviewed_at": decision["reviewed_at"], "review_note": decision["review_note"]})
        updated.append(row)
    updated.sort(key=lambda row: row["qa_id"])
    legacy_bytes = b"".join(_canonical_bytes(row) for row in updated)
    tourism_bytes = tourism_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        if source["output_path"] == LEGACY_PATH:
            source["source_jsonl_sha256"] = _sha256_bytes(legacy_bytes)
        elif source["output_path"] == TOURISM_PATH:
            assert _sha256_bytes(tourism_bytes) == source["source_jsonl_sha256"], "tourism source changed"
    manifest.update({"total_record_count": len(updated) + len(_read_jsonl(tourism_path)), "fully_governed_approved_count": sum(row["status"] == "approved" and not row["approval_review_required"] for row in updated) + len(_read_jsonl(tourism_path)), "rejected_count": sum(row["status"] == "rejected" for row in updated), "review_required_count": sum(bool(row["approval_review_required"]) for row in updated), "review_decisions": {"path": decisions_path, "sha256": _sha256_bytes(decisions_bytes), "decision_count": len(decisions), "approved_decision_count": sum(row["decision"] == "approved" for row in decisions.values()), "rejected_decision_count": sum(row["decision"] == "rejected" for row in decisions.values())}})
    manifest_bytes = _canonical_bytes(manifest)
    result = {"total_record_count": manifest["total_record_count"], "fully_governed_approved_count": manifest["fully_governed_approved_count"], "rejected_count": manifest["rejected_count"], "review_required_count": manifest["review_required_count"], "decision_count": len(decisions)}
    if apply:
        _atomic_write(legacy_path, legacy_bytes)
        _atomic_write(manifest_path, manifest_bytes)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply_reviews(apply=args.apply), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
