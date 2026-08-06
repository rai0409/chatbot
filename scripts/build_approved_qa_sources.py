from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "governed_approved_qa.v1"
GENERATED_BY = "scripts/build_approved_qa_sources.py"
ROOT = Path(__file__).resolve().parents[1]
SOURCE_SPECS = (
    {
        "name": "58887_95105_misc",
        "input": "data/approved_qa/default.jsonl",
        "output": "data/approved_qa/sources/58887_95105_misc.jsonl",
        "source_document": "58887_95105_misc.pdf",
        "approval_provenance": "existing_governed_source",
        "legacy": False,
    },
    {
        "name": "040219e-biscfaq",
        "input": "artifacts/fixed_qa_eval/ingest/040219_canonical_qa_pairs.jsonl",
        "output": "data/approved_qa/sources/040219e-biscfaq.jsonl",
        "source_document": "040219e-biscfaq.pdf",
        "approval_provenance": "legacy_import",
        "legacy": True,
    },
)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: malformed JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_no}: JSONL record must be an object")
        records.append(record)
    return records


def _pdf_fingerprint(source_document: str) -> tuple[str, str]:
    for candidate in (ROOT / "pdfs" / source_document, ROOT / "data/source_pdfs" / source_document):
        if candidate.is_file():
            return _sha256_bytes(candidate.read_bytes()), "sha256_pdf_bytes"
    return _sha256_bytes(f"source_document:{source_document}".encode("utf-8")), "sha256_source_document_identity"


def _citation(record: dict[str, Any]) -> dict[str, Any]:
    citations = record.get("approved_citations")
    if isinstance(citations, list):
        for item in citations:
            if isinstance(item, dict):
                return item
    return {}


def _pages(record: dict[str, Any], citation: dict[str, Any]) -> list[Any]:
    value = citation.get("source_pages") or record.get("source_pages")
    return list(value) if isinstance(value, list) else [value] if value not in (None, "") else []


def _governed_record(record: dict[str, Any], spec: dict[str, Any], source_fingerprint: str) -> dict[str, Any]:
    qa_id = _text(record.get("qa_id") or record.get("approved_qa_id"))
    question = _text(record.get("question") or record.get("question_text") or record.get("normalized_question"))
    answer = _text(record.get("approved_answer") or record.get("answer_text") or record.get("answer"))
    if not qa_id:
        raise ValueError("missing qa_id; no safe deterministic ID derivation is defined")
    if not question:
        raise ValueError(f"qa_id={qa_id}: missing question")
    if not answer:
        raise ValueError(f"qa_id={qa_id}: missing answer")
    citation = _citation(record)
    source_document = _text(citation.get("source_doc") or record.get("source_doc") or record.get("doc_id") or spec["source_document"])
    pages = _pages(record, citation)
    if not source_document or not pages:
        raise ValueError(f"qa_id={qa_id}: missing source document or pages")
    explicit_status = _text(record.get("status"))
    if spec["legacy"]:
        status = explicit_status or "legacy_unreviewed"
        review_required = True
    else:
        status = explicit_status
        review_required = status != "approved"
    governed = {
        "qa_id": qa_id,
        "question": question,
        "answer": answer,
        "status": status,
        "source_document": source_document,
        "source_pages": pages,
        "source_fingerprint": source_fingerprint,
        "schema_version": SCHEMA_VERSION,
        "approval_provenance": spec["approval_provenance"],
        "approval_review_required": review_required,
        "tenant_id": _text(record.get("tenant_id")) or "default",
        "doc_version": _text(record.get("doc_version")),
        "approved_citations": record.get("approved_citations") or [],
    }
    aliases = record.get("approved_aliases") if isinstance(record.get("approved_aliases"), list) else record.get("aliases")
    if isinstance(aliases, list):
        governed["aliases"] = aliases
    provenance = {
        key: record[key]
        for key in ("reviewed_by", "reviewed_at", "review_notes", "created_at", "source_question_no", "question_item")
        if key in record
    }
    if provenance:
        governed["input_provenance"] = provenance
    fingerprint_payload = {key: value for key, value in governed.items() if key != "source_record_fingerprint"}
    governed["source_record_fingerprint"] = _sha256_bytes(_canonical_bytes(fingerprint_payload))
    return governed


def _validate(records: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    qa_answers: dict[str, str] = {}
    question_answers: dict[str, str] = {}
    pairs: set[tuple[str, str]] = set()
    for record in records:
        qa_id, answer = record["qa_id"], record["answer"]
        normalized = _normalized_question(record["question"])
        pair = (normalized, answer)
        if qa_id in ids:
            raise ValueError(f"duplicate qa_id: {qa_id}")
        ids.add(qa_id)
        if qa_id in qa_answers and qa_answers[qa_id] != answer:
            raise ValueError(f"conflicting answer for qa_id: {qa_id}")
        qa_answers[qa_id] = answer
        if normalized in question_answers and question_answers[normalized] != answer:
            raise ValueError(f"conflicting answer for normalized question: {normalized}")
        question_answers[normalized] = answer
        if pair in pairs:
            raise ValueError(f"duplicate normalized question-answer pair: {normalized}")
        pairs.add(pair)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> str:
    payload = b"".join(_canonical_bytes(record) for record in records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return _sha256_bytes(payload)


def _root_relative_path(root: Path, value: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / value).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"source specification path escapes root: {value}") from exc
    return candidate


def build_approved_qa_sources(
    *, root: Path = ROOT, source_specs: tuple[dict[str, Any], ...] | None = None
) -> dict[str, Any]:
    global ROOT
    previous_root, ROOT = ROOT, root
    try:
        manifest_sources = []
        total = approved = review_required = 0
        for spec in source_specs if source_specs is not None else SOURCE_SPECS:
            input_path = _root_relative_path(root, spec["input"])
            output_path = _root_relative_path(root, spec["output"])
            input_bytes = input_path.read_bytes()
            records = _read_jsonl(input_path)
            source_fingerprint, method = _pdf_fingerprint(spec["source_document"])
            governed = sorted((_governed_record(record, spec, source_fingerprint) for record in records), key=lambda item: item["qa_id"])
            _validate(governed)
            output_sha = _write_jsonl(output_path, governed)
            total += len(governed)
            approved += sum(record["status"] == "approved" and not record["approval_review_required"] for record in governed)
            review_required += sum(bool(record["approval_review_required"]) for record in governed)
            manifest_sources.append({"name": spec["name"], "input_path": spec["input"], "input_sha256": _sha256_bytes(input_bytes), "output_path": spec["output"], "source_document": spec["source_document"], "source_fingerprint": source_fingerprint, "source_fingerprint_method": method, "source_jsonl_sha256": output_sha, "record_count": len(governed)})
        manifest = {"schema_version": SCHEMA_VERSION, "generated_by": GENERATED_BY, "sources": manifest_sources, "total_record_count": total, "fully_governed_approved_count": approved, "review_required_count": review_required}
        manifest_path = _root_relative_path(root, "data/approved_qa/manifest.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(_canonical_bytes(manifest))
        return manifest
    finally:
        ROOT = previous_root


if __name__ == "__main__":
    print(json.dumps(build_approved_qa_sources(), ensure_ascii=False, sort_keys=True))
