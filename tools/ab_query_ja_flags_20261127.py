"""Offline A/B of the Japanese query-normalization changes (2026-11-27), BM25 lane only.

Purpose: measure, without any model, how the retrieval query built by rag_core.query_preprocess changes the BM25
ranking of the committed synthetic labelled cases, for three conditions:
  A  baseline: colloquial rules without the new "rewrite" list, QUERY_ABBREVIATIONS / QUERY_BUSINESS_SYNONYMS off
  B  + the new colloquial rewrites (flags still off; this is the shipped default)
  C1 B + QUERY_ABBREVIATIONS=true;  C2 B + QUERY_BUSINESS_SYNONYMS=true
  C  B + QUERY_ABBREVIATIONS=true + QUERY_BUSINESS_SYNONYMS=true
Each condition runs on the cases as written and on their abbreviate / synonym_swap / casual_ending perturbations
(eval/tenant_eval/perturb.py). Dense retrieval, fusion and rerank are not measured (no model offline).

Usage:   .venv/bin/python tools/ab_query_ja_flags_20261127.py [--tokenizer heuristic] [--output-dir /tmp/ja-eval] [--datasets semantic_challenge_v2]
Input:   eval/cases/{retrieval_v3,retrieval_v4,semantic_challenge_v2}_{cases,chunks}.jsonl (synthetic, committed)
Output:  stdout (a table) and runs/agent_query/ab_query_ja_20261127.json (inside the repo; the inputs are synthetic,
         but do not commit runs/). The rule copy for condition A stays in runs/agent_query/ab_rules_without_rewrite/
         (move it to 即削除/ when no longer needed).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import config  # noqa: E402
from eval.bench.corpus import Chunk  # noqa: E402
from eval.bench.lexical import LexicalIndex, LexicalSpec  # noqa: E402
from eval.tenant_eval import perturb  # noqa: E402
from rag_core import query_preprocess as qp  # noqa: E402

SETS = ("retrieval_v3", "retrieval_v4", "semantic_challenge_v2")
OUT_DIR = REPO / "runs" / "agent_query"
KINDS = ("abbreviate", "synonym_swap", "casual_ending")


def _rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _index(name: str, tokenizer: str = "sudachi") -> tuple:
    rows = _rows(REPO / "eval" / "cases" / f"{name}_chunks.jsonl")
    chunks = [Chunk(r["id"], r.get("doc_id") or r.get("source_doc") or "", r.get("tenant_id") or "default", r["text"], r["id"]) for r in rows]
    spec = LexicalSpec(tokenizer=tokenizer, ngram=True, numeric_tokens=True, k1=float(config.BM25_K1), order="production")
    return chunks, LexicalIndex(chunks, spec)


def _cases(name: str):
    out = []
    for r in _rows(REPO / "eval" / "cases" / f"{name}_cases.jsonl"):
        gold = set(r.get("gold_chunk_ids") or [])
        if gold and r.get("answerable", True) is not False:
            out.append({"case_id": r["case_id"], "category": r.get("category"), "question": r["query"], "gold": gold})
    return out


def _variants(cases):
    out = []
    for case in cases:
        for v in perturb.perturb_question(case["question"], kinds=KINDS, seed="ab"):
            out.append({**case, "case_id": f"{case['case_id']}~{v['kind']}", "category": f"perturb:{v['kind']}",
                        "question": v["text"]})
    return out


def _score(index, chunks, cases, details=None):
    hit1 = hit5 = 0
    rr = 0.0
    changed_queries = 0
    for case in cases:
        query = qp.preprocess_query(case["question"]).retrieval_query or case["question"]
        if query != case["question"]:
            changed_queries += 1
        ranked = [chunks[h.index].chunk_id for h in index.search(query, 10, "default")]
        rank = next((i for i, cid in enumerate(ranked, 1) if cid in case["gold"]), None)
        if details is not None:
            details[case["case_id"]] = {"rank": rank, "reciprocal_rank": 1.0 / rank if rank else 0.0}
        hit1 += 1 if rank == 1 else 0
        hit5 += 1 if rank and rank <= 5 else 0
        rr += 1.0 / rank if rank else 0.0
    n = len(cases) or 1
    return {"n": len(cases), "hit@1": hit1 / n, "hit@5": hit5 / n, "mrr@10": rr / n}


def _condition(name: str, rules_dir: Path, flags):
    qp.RULES_DIR = rules_dir
    qp._load_rules.cache_clear()
    abbreviations, business = (flags, flags) if isinstance(flags, bool) else flags
    config.QUERY_ABBREVIATIONS = abbreviations
    config.QUERY_BUSINESS_SYNONYMS = business


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", choices=("heuristic", "sudachi"), default="sudachi")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--datasets", nargs="+", choices=SETS, default=list(SETS))
    args = parser.parse_args(argv)
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    real_rules = qp.RULES_DIR
    old_rules = out_dir / "ab_rules_without_rewrite"
    old_rules.mkdir(parents=True, exist_ok=True)
    data = json.loads((real_rules / "colloquial_ja.json").read_text(encoding="utf-8"))
    data.pop("rewrite", None)
    (old_rules / "colloquial_ja.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    shutil.copy(real_rules / "abbreviations_ja.json", old_rules / "abbreviations_ja.json")
    conditions = {"A_baseline": (old_rules, False), "B_rewrites": (real_rules, False),
                  "C1_abbreviations": (real_rules, (True, False)), "C2_business_synonyms": (real_rules, (False, True)),
                  "C_rewrites_flags_on": (real_rules, True)}
    results = {}
    ranks = {}
    old_flags = (config.QUERY_ABBREVIATIONS, config.QUERY_BUSINESS_SYNONYMS)
    try:
        for set_name in args.datasets:
            chunks, index = _index(set_name, args.tokenizer)
            cases = _cases(set_name)
            groups = {"as_written": cases}
            for case in cases:
                groups.setdefault(f"cat:{case['category']}", []).append(case)
            for variant in _variants(cases):
                groups.setdefault(variant["category"], []).append(variant)
            for cond, (rules, flags) in conditions.items():
                _condition(cond, rules, flags)
                for group, items in groups.items():
                    details = {}
                    results.setdefault(set_name, {}).setdefault(group, {})[cond] = _score(index, chunks, items, details)
                    ranks.setdefault(set_name, {}).setdefault(group, {})[cond] = details
    finally:
        _condition("restore", real_rules, old_flags)
    (out_dir / "ab_query_ja_20261127.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    comparisons = {}
    for dataset, groups in ranks.items():
        for group, conditions_by_case in groups.items():
            baseline = conditions_by_case["A_baseline"]
            for condition, cases in conditions_by_case.items():
                if condition == "A_baseline":
                    continue
                improved, worsened = [], []
                for case_id, row in cases.items():
                    old_rr, new_rr = baseline[case_id]["reciprocal_rank"], row["reciprocal_rank"]
                    change = {"case_id": case_id, "baseline_rank": baseline[case_id]["rank"], "rank": row["rank"]}
                    if new_rr > old_rr:
                        improved.append(change)
                    elif new_rr < old_rr:
                        worsened.append(change)
                comparisons.setdefault(dataset, {}).setdefault(group, {})[condition] = {
                    "improved": improved, "worsened": worsened,
                }
    (out_dir / "case_changes.json").write_text(json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")
    for set_name, groups in results.items():
        print(f"## {set_name}")
        names = list(conditions)
        print("| group | n | " + " | ".join(f"{n} hit@1/mrr" for n in names) + " |")
        for group, conds in groups.items():
            cells = " | ".join(f"{conds[n]['hit@1']:.3f}/{conds[n]['mrr@10']:.3f}" for n in names)
            print(f"| {group} | {conds[names[0]]['n']} | {cells} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
