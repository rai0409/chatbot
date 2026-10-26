#!/usr/bin/env python3
from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Production approved-QA exact index, built from approved records only.

tools/build_approved_qa_exact_index.py builds the *evaluation* index from the
evaluation cases (fixed_qa_eval); the runtime refuses that file. This tool
builds the runtime index from the approved-QA file (APPROVED_QA_PATH or
--approved): every tenant's records go through the same validation as the
runtime loader (status=approved, citations required, newest duplicate wins),
and each question and each approved alias becomes a key
(question_normalization.exact_match_key). Items carry tenant_id, so the
lookup returns only the caller's tenant.

  python tools/build_production_exact_index.py --out data/approved_qa/exact_index.json
then set APPROVED_QA_EXACT_INDEX_PATH=data/approved_qa/exact_index.json
"""

import argparse
import dataclasses
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import config
from rag_core.approved_qa import load_approved_qa
from rag_core.question_normalization import exact_match_key

INDEX_VERSION = "approved-exact-v2"


def _tenants(path: Path) -> List[str]:
    tenants = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                tenants.add(str(row.get("tenant_id") or "").strip() or "default")
    return sorted(tenants)


def _item(answer, matched_text: str, match_type: str) -> Dict[str, Any]:
    citations = [dataclasses.asdict(c) for c in answer.approved_citations]
    first = citations[0] if citations else {}
    return {
        "id": f"approved_qa_pair:{answer.qa_id}",
        "text": f"Q: {answer.question}\nA: {answer.approved_answer}",
        "metadata": {
            "id": f"approved_qa_pair:{answer.qa_id}",
            "qa_id": answer.qa_id,
            "tenant_id": answer.tenant_id,
            "question_text": matched_text,
            "canonical_question": answer.question,
            "normalized_question": answer.normalized_question,
            "approved_answer": answer.approved_answer,
            "answer_text": answer.approved_answer,
            "approved_citations": citations,
            "source_doc": first.get("source_doc") or "",
            "source_pages": list(first.get("source_pages") or []),
            "doc_version": answer.doc_version,
            "doc_type": "approved_qa_pair",
            "chunk_type": "qa_pair",
            "exact_key_source": match_type,
        },
        "score": 0.0,
    }


def build_index(approved_path: Path) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    index: Dict[str, List[Dict[str, Any]]] = {}
    report: Dict[str, Any] = {"index_version": INDEX_VERSION, "tenants": {}, "key_collisions": 0}
    for tenant in _tenants(approved_path):
        loaded = load_approved_qa(approved_path, tenant_id=tenant)
        keys = 0
        for answer in loaded.records:
            entries = [(answer.question, "question")] + [(alias, "alias") for alias in answer.approved_aliases]
            for text, kind in entries:
                key = exact_match_key(text)
                if not key:
                    continue
                bucket = index.setdefault(key, [])
                if any(i["metadata"]["tenant_id"] == tenant for i in bucket):
                    # Two answers of one tenant collapse to the same strong key
                    # (e.g. differ only by punctuation): keep the first, report.
                    report["key_collisions"] += 1
                    continue
                bucket.append(_item(answer, text, kind))
                keys += 1
        report["tenants"][tenant] = {
            "answers": len(loaded.records),
            "keys": keys,
            "skipped_lines": loaded.skipped_lines,
            "skipped_records": loaded.skipped_records,
            "replaced_duplicates": loaded.replaced_duplicates,
        }
    return index, report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the runtime approved-QA exact index.")
    parser.add_argument("--approved", default=str(getattr(config, "APPROVED_QA_PATH", "")))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    approved = Path(args.approved)
    if "fixed_qa_eval" in approved.parts:
        print("error: refusing evaluation artifacts as approved-QA source", file=sys.stderr)
        return 1
    index, report = build_index(approved)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"output": str(out), "keys": len(index), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
