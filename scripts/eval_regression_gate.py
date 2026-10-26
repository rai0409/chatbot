from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""CI regression gate on the repository's labeled retrieval cases (6-13).

Runs eval/runner.py's retrieval-aware evaluation on eval/cases/retrieval_cases.jsonl
with the vector search stubbed (no model download in CI), so it guards the
keyword path, rerank heuristics, guards and abstention. It is not an
accuracy figure (that comes from eval.bench with the real evaluation set).

Fails (exit 1) when mean MRR@k or nDCG@k of a mode drops by more than
--margin against eval/baselines/retrieval_gate_baseline.json, or when the
gold hit count or correct abstentions drop.
  python scripts/eval_regression_gate.py                 # check
  python scripts/eval_regression_gate.py --update-baseline   # after an intended change
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import config
from eval import runner

BASELINE = Path(config.BASE_DIR) / "eval" / "baselines" / "retrieval_gate_baseline.json"
MODES = ("bm25_only", "hybrid_rerank")
_FLOAT_KEYS = ("mean_mrr_at_k", "mean_ndcg_at_k")
_COUNT_KEYS = ("gold_chunk_hits", "gold_doc_hits", "abstain_passes")


def current_metrics(eval_k: int = 5) -> Dict[str, Dict[str, Any]]:
    out_dir = Path(config.RUNS_DIR) / "eval_gate"
    payload = runner.run_retrieval_aware_eval(
        cases_path=Path(config.BASE_DIR) / "eval" / "cases" / "retrieval_cases.jsonl",
        chunks_jsonl=runner._default_chunks_path(),
        per_query_output_path=out_dir / "rows.jsonl",
        summary_output_path=out_dir / "summary.json",
        modes=list(MODES),
        top_k=20,
        max_context_chars=8000,
        real_vector=False,
        real_generation=False,
        eval_k=eval_k,
        quiet=True,
    )
    by_mode = payload["summary"]["by_mode"]
    return {mode: {k: by_mode[mode].get(k) for k in (*_FLOAT_KEYS, *_COUNT_KEYS)} for mode in MODES}


def compare(baseline: Dict[str, Dict[str, Any]], current: Dict[str, Dict[str, Any]], margin: float) -> list[str]:
    failures = []
    for mode, metrics in baseline.items():
        now = current.get(mode, {})
        for key in _FLOAT_KEYS:
            before, after = metrics.get(key), now.get(key)
            if isinstance(before, (int, float)) and (not isinstance(after, (int, float)) or after < before - margin):
                failures.append(f"{mode}.{key}: {before} -> {after} (margin {margin})")
        for key in _COUNT_KEYS:
            before, after = metrics.get(key), now.get(key)
            if isinstance(before, int) and (not isinstance(after, int) or after < before):
                failures.append(f"{mode}.{key}: {before} -> {after}")
    return failures


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval regression gate for CI.")
    parser.add_argument("--margin", type=float, default=0.02)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--baseline", default=str(BASELINE))
    args = parser.parse_args(argv)

    current = current_metrics()
    path = Path(args.baseline)
    if args.update_baseline:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"baseline_written": str(path), "metrics": current}, ensure_ascii=False))
        return 0
    if not path.exists():
        print(f"error: no baseline at {path}; run with --update-baseline once and commit it", file=sys.stderr)
        return 1
    failures = compare(json.loads(path.read_text(encoding="utf-8")), current, args.margin)
    print(json.dumps({"current": current, "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
