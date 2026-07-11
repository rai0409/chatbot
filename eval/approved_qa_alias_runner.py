from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable

from rag_core.approved_qa import load_approved_qa, lookup_approved_answer, validate_approved_qa_records


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _citation_contract(answer) -> list[dict]:
    return [
        {
            "source_doc": citation.source_doc,
            "source_pages": list(citation.source_pages),
            "chunk_id": citation.chunk_id,
            "title": citation.title,
        }
        for citation in answer.approved_citations
    ]


def _expected_citations(record: dict) -> list[dict]:
    return [
        {
            "source_doc": str(citation.get("source_doc") or ""),
            "source_pages": [int(page) for page in citation.get("source_pages") or []],
            "chunk_id": citation.get("chunk_id"),
            "title": citation.get("title"),
        }
        for citation in record.get("approved_citations") or []
    ]


def run_alias_eval(fixture_path: str | Path, output_dir: str | Path,
                   *, existing_gate_summary: str | Path | None = None) -> dict[str, Any]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    records = fixture["records"]
    validation_errors = validate_approved_qa_records(records)
    if validation_errors:
        raise ValueError("invalid alias fixture: " + "; ".join(validation_errors))

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    conflict_results: list[dict] = []
    indexes = {}
    with tempfile.TemporaryDirectory(prefix="approved_qa_alias_eval_") as temp_dir:
        approved_path = Path(temp_dir) / "approved_alias_fixture.jsonl"
        _write_jsonl(approved_path, records)
        for tenant in sorted({str(row.get("tenant_id") or "default") for row in records} | {"tenant-c"}):
            indexes[tenant] = load_approved_qa(approved_path, tenant_id=tenant)

        for record in records:
            tenant = str(record.get("tenant_id") or "default")
            answer = lookup_approved_answer(indexes[tenant], record["question"], tenant_id=tenant)
            failures = []
            if answer is None or answer.qa_id != record["qa_id"]:
                failures.append("canonical qa_id mismatch")
            elif answer.match_type != "canonical":
                failures.append("canonical question did not take priority")
            results.append({"case_type": "exact", "case_id": record["qa_id"], "tenant_id": tenant,
                            "query": record["question"], "passed": not failures, "failures": failures,
                            "answer_mode": "approved_exact_match" if answer else None,
                            "approved_qa_id": answer.qa_id if answer else None})

            for alias_index, alias in enumerate(record.get("approved_aliases") or [], start=1):
                answer = lookup_approved_answer(indexes[tenant], alias, tenant_id=tenant)
                failures = []
                if answer is None:
                    failures.append("alias did not match")
                else:
                    if answer.qa_id != record["qa_id"]:
                        failures.append("qa_id mismatch")
                    if answer.approved_answer != record["approved_answer"]:
                        failures.append("canonical answer mismatch")
                    if _citation_contract(answer) != _expected_citations(record):
                        failures.append("citation mismatch")
                    if answer.match_type != "alias" or answer.matched_alias != alias:
                        failures.append("alias trace mismatch")
                results.append({"case_type": "alias", "case_id": f"{record['qa_id']}:alias:{alias_index}",
                                "tenant_id": tenant, "query": alias, "passed": not failures,
                                "failures": failures, "answer_mode": "approved_alias_match" if answer else None,
                                "approved_qa_id": answer.qa_id if answer else None,
                                "matched_alias": answer.matched_alias if answer else None,
                                "retrieval_used": False, "llm_used": False})

        for case in fixture.get("false_positive_queries") or []:
            answer = lookup_approved_answer(indexes.get(case["tenant_id"], indexes["tenant-c"]),
                                            case["query"], tenant_id=case["tenant_id"])
            results.append({"case_type": "false_positive", **case, "passed": answer is None,
                            "failures": [] if answer is None else [f"unexpected match: {answer.qa_id}"],
                            "approved_qa_id": answer.qa_id if answer else None})

        for case in fixture.get("tenant_checks") or []:
            answer = lookup_approved_answer(indexes.get(case["tenant_id"], indexes["tenant-c"]),
                                            case["query"], tenant_id=case["tenant_id"])
            actual = answer.qa_id if answer else None
            results.append({"case_type": "tenant_isolation", **case, "actual_qa_id": actual,
                            "passed": actual == case.get("expected_qa_id"),
                            "failures": [] if actual == case.get("expected_qa_id") else ["tenant isolation mismatch"]})

    for conflict_set in fixture.get("conflict_sets") or []:
        errors = validate_approved_qa_records(conflict_set["records"])
        conflict_results.append({"case_id": conflict_set["case_id"], "passed": bool(errors), "errors": errors})

    exact = [row for row in results if row["case_type"] == "exact"]
    aliases = [row for row in results if row["case_type"] == "alias"]
    false_positives = [row for row in results if row["case_type"] == "false_positive"]
    tenants = [row for row in results if row["case_type"] == "tenant_isolation"]
    canonical_mismatches = sum("canonical answer mismatch" in row.get("failures", []) for row in aliases)
    citation_mismatches = sum("citation mismatch" in row.get("failures", []) for row in aliases)
    existing_gate = {}
    if existing_gate_summary and Path(existing_gate_summary).exists():
        existing_gate = json.loads(Path(existing_gate_summary).read_text(encoding="utf-8"))
    summary = {
        "exact_qa": {"total": len(exact), "passed": sum(row["passed"] for row in exact),
                     "failed": sum(not row["passed"] for row in exact)},
        "alias_qa": {"total": len(aliases), "passed": sum(row["passed"] for row in aliases),
                     "failed": sum(not row["passed"] for row in aliases),
                     "pass_rate": sum(row["passed"] for row in aliases) / len(aliases) if aliases else 0.0},
        "false_positive_total": sum(not row["passed"] for row in false_positives),
        "false_positive_queries": len(false_positives),
        "collision_count": sum(bool(row["errors"]) for row in conflict_results),
        "collision_cases_rejected": sum(row["passed"] for row in conflict_results),
        "collision_validation_failures": sum(not row["passed"] for row in conflict_results),
        "tenant_isolation_failures": sum(not row["passed"] for row in tenants),
        "canonical_answer_mismatches": canonical_mismatches,
        "citation_mismatches": citation_mismatches,
        "existing_gate_regressions": list(existing_gate.get("failed_checks") or []),
        "existing_gate_summary": existing_gate,
    }
    passed = (
        summary["alias_qa"]["failed"] == 0
        and summary["false_positive_total"] == 0
        and summary["collision_validation_failures"] == 0
        and summary["tenant_isolation_failures"] == 0
        and canonical_mismatches == 0
        and citation_mismatches == 0
    )
    summary["status"] = "passed" if passed else "failed"
    summary["recommendation"] = "keep_candidate_only_until_explicit_review" if passed else "block_alias_promotion"
    (output / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_jsonl(output / "results.jsonl", results)
    _write_jsonl(output / "conflicts.jsonl", conflict_results)
    report = ["# Approved QA Alias Evaluation", "", f"- Status: **{summary['status']}**",
              f"- Exact fixture: {summary['exact_qa']['passed']}/{summary['exact_qa']['total']}",
              f"- Alias: {summary['alias_qa']['passed']}/{summary['alias_qa']['total']}",
              f"- Alias false positives: {summary['false_positive_total']}",
              f"- Collision cases rejected: {summary['collision_cases_rejected']}/{len(conflict_results)}",
              f"- Tenant isolation failures: {summary['tenant_isolation_failures']}",
              f"- Canonical answer mismatches: {canonical_mismatches}",
              f"- Citation mismatches: {citation_mismatches}", "",
              "Aliases are operator-approved exact equivalents only; semantic similarity is not evaluated or routed."]
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate explicit approved-QA aliases independently of exact QA.")
    parser.add_argument("--fixture", default="eval/cases/approved_qa_alias_fixture.json")
    parser.add_argument("--output-dir", default="artifacts/approved_qa_alias")
    parser.add_argument("--existing-gate-summary", default="artifacts/free_extractive_chat_mode/validation_summary.json")
    args = parser.parse_args(argv)
    summary = run_alias_eval(args.fixture, args.output_dir, existing_gate_summary=args.existing_gate_summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
