"""Measurement-driven analysis of too_general guard false-abstains (Prompt021).

Loads a real-vector eval result (Prompt017 baseline by default), splits the
too_general-firing cases into answerable (false abstain) vs abstain-labeled
(correct abstain), extracts safe observable features, and simulates the
proposed evidence-coverage bypass rule. Writes JSON + Markdown reports under
runs/eval/. Pure measurement: no runtime behavior is touched here.

Feature definitions (all computable at guard time, no LLM, no network):
- content terms: katakana runs >=2, kanji runs >=2, alnum runs >=2 from the
  NFKC-normalized query (rag_core.ja_text.normalize_japanese_text).
- coverage: fraction of distinct content terms present in the normalized
  top-1 retrieved chunk text.
- content_len: total length of word-character runs in the normalized query
  (full business questions are long; genuinely vague queries are short).

Proposed bypass (simulated here, implemented in rag_core/qa.py):
  all content terms appear in top-1 evidence AND
  (content_len >= 12  OR  exactly one term of length >= 3)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

from rag_core.ja_text import normalize_japanese_text

SCHEMA_VERSION = "too_general_guard_analysis.v1"

TERM_RE = re.compile(r"[ァ-ヴー]{2,}|[一-龥]{2,}|[A-Za-z0-9]{2,}")
WORD_RUN_RE = re.compile(r"[A-Za-z0-9ぁ-んァ-ヴー一-龥]+")

MIN_FULL_QUESTION_CONTENT_LEN = 12
MIN_SINGLE_TERM_LEN = 3


def content_terms(query: str) -> List[str]:
    norm = normalize_japanese_text(query or "")
    return list(dict.fromkeys(TERM_RE.findall(norm)))


def content_length(query: str) -> int:
    norm = normalize_japanese_text(query or "")
    return sum(len(run) for run in WORD_RUN_RE.findall(norm))


def case_features(query: str, top_text: str) -> Dict[str, object]:
    terms = content_terms(query)
    top_norm = normalize_japanese_text(top_text or "")
    hits = [t for t in terms if t in top_norm]
    return {
        "n_terms": len(terms),
        "terms": terms,
        "hits": len(hits),
        "coverage_top1": round(len(hits) / len(terms), 4) if terms else 0.0,
        "max_hit_len": max((len(t) for t in hits), default=0),
        "content_len": content_length(query),
    }


def proposed_bypass(features: Dict[str, object]) -> bool:
    if not features["n_terms"]:
        return False
    if features["coverage_top1"] < 1.0:
        return False
    if features["content_len"] >= MIN_FULL_QUESTION_CONTENT_LEN:
        return True
    return features["n_terms"] == 1 and features["max_hit_len"] >= MIN_SINGLE_TERM_LEN


def analyze(result_path: Path, chunks_path: Path) -> Dict[str, object]:
    chunk_text: Dict[str, str] = {}
    with chunks_path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                row = json.loads(raw)
                chunk_text[str(row.get("id"))] = str(
                    row.get("searchable_text") or row.get("text") or ""
                )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    rows = []
    for case in result["cases"]:
        if case.get("final_guard_reason") != "too_general":
            continue
        label = "abstain" if case.get("should_abstain") else "answerable"
        top = case.get("after_rerank_top") or []
        top_id = top[0]["chunk_id"] if top else None
        features = case_features(case["query"], chunk_text.get(top_id or "", ""))
        rows.append(
            {
                "case_id": case["case_id"],
                "label": label,
                "query": case["query"],
                "top_chunk_id": top_id,
                **features,
                "proposed_bypass": proposed_bypass(features),
            }
        )

    answerable = [r for r in rows if r["label"] == "answerable"]
    abstain = [r for r in rows if r["label"] == "abstain"]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_result": str(result_path),
        "rule": {
            "description": (
                "bypass too_general when all query content terms appear in the "
                "top-1 evidence AND (content_len >= "
                f"{MIN_FULL_QUESTION_CONTENT_LEN} OR exactly one term of length >= "
                f"{MIN_SINGLE_TERM_LEN})"
            ),
            "min_full_question_content_len": MIN_FULL_QUESTION_CONTENT_LEN,
            "min_single_term_len": MIN_SINGLE_TERM_LEN,
        },
        "summary": {
            "too_general_total": len(rows),
            "false_abstain_cases": len(answerable),
            "correct_abstain_cases": len(abstain),
            "false_abstains_fixed_by_rule": sum(1 for r in answerable if r["proposed_bypass"]),
            "correct_abstains_broken_by_rule": sum(1 for r in abstain if r["proposed_bypass"]),
        },
        "cases": rows,
    }


def render_markdown(report: Dict[str, object]) -> str:
    s = report["summary"]
    lines = [
        "# too_general Guard Analysis",
        "",
        f"- source: `{report['source_result']}`",
        f"- rule: {report['rule']['description']}",
        "",
        f"- too_general firings: {s['too_general_total']} "
        f"(false abstains: {s['false_abstain_cases']}, correct abstains: {s['correct_abstain_cases']})",
        f"- false abstains fixed by proposed rule: **{s['false_abstains_fixed_by_rule']}**",
        f"- correct abstains broken by proposed rule: **{s['correct_abstains_broken_by_rule']}**",
        "",
        "| case | label | coverage_top1 | n_terms | max_hit_len | content_len | bypass |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["cases"]:
        lines.append(
            f"| {r['case_id']} | {r['label']} | {r['coverage_top1']} | {r['n_terms']} "
            f"| {r['max_hit_len']} | {r['content_len']} | {r['proposed_bypass']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze too_general guard behavior from a saved eval result.")
    parser.add_argument("--result", default="runs/eval/prompt017_realvector_before.json")
    parser.add_argument("--chunks-jsonl", default="eval/cases/real_corpus_chunks.jsonl")
    parser.add_argument("--output-prefix", default="runs/eval/too_general_guard_analysis")
    args = parser.parse_args(argv)

    report = analyze(Path(args.result), Path(args.chunks_jsonl))
    out_json = Path(f"{args.output_prefix}.json")
    out_md = Path(f"{args.output_prefix}.md")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
    print("Summary:", json.dumps(report["summary"], ensure_ascii=False), "output=" + str(out_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
