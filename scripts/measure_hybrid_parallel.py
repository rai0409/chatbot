from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Measure whether HYBRID_PARALLEL helps on THIS machine (run it on the production PC).

Runs hybrid retrieval for N queries from a JSONL file ({"query": "..."} or {"question": "..."} per line) at
concurrency levels (1, workers, peak) with HYBRID_PARALLEL off and on, and reports p50/p95 latency and throughput.

Recommendation rule (enable only if BOTH hold at the expected concurrency):
  - p95 latency improves by at least 15 percent (on vs off)
  - throughput (queries per second) does not drop by more than 5 percent

  python scripts/measure_hybrid_parallel.py --queries eval/cases/queries.jsonl --n 100 \
      --workers 4 --peak 8 --expected-concurrency 4 --output reports/bench/hybrid_parallel/<run>.json

Inputs: the queries JSONL, the live index (as configured by the environment). Output: JSON on stdout and, with
--output, a file (give a path inside the repository; the report is a measurement, do not commit real-data runs).
The retrieval function is injectable (measure(..., retrieve=..., set_parallel=...)) so tests use stub lanes.
"""

import argparse
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

P95_IMPROVEMENT_MIN = 0.15
THROUGHPUT_DROP_MAX = 0.05


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def load_queries(path: _Path, n: int) -> List[str]:
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        text = str(row.get("query") or row.get("question") or "").strip() if isinstance(row, dict) else str(row)
        if text:
            out.append(text)
    if not out:
        raise ValueError(f"no queries in {path}")
    return [out[i % len(out)] for i in range(n)]  # cycle when the file is shorter than n


def run_level(retrieve: Callable[[str], Any], queries: Sequence[str], concurrency: int) -> Dict[str, Any]:
    latencies: List[float] = []

    def one(question: str) -> float:
        start = time.perf_counter()
        retrieve(question)
        return (time.perf_counter() - start) * 1000.0

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        latencies = list(pool.map(one, queries))
    wall = max(time.perf_counter() - wall_start, 1e-9)
    return {
        "concurrency": concurrency, "queries": len(queries),
        "p50_ms": percentile(latencies, 0.50), "p95_ms": percentile(latencies, 0.95),
        "throughput_qps": len(queries) / wall,
    }


def measure(
    queries: Sequence[str], *, retrieve: Callable[[str], Any], set_parallel: Callable[[bool], None],
    levels: Sequence[int], warmup: int = 3,
) -> Dict[str, Any]:
    results: Dict[str, Dict[str, Any]] = {}
    for level in sorted(set(int(v) for v in levels)):
        for mode in ("off", "on"):
            set_parallel(mode == "on")
            for question in list(queries)[:warmup]:
                retrieve(question)
            results.setdefault(str(level), {})[mode] = run_level(retrieve, queries, level)
    set_parallel(False)
    return results


def recommend(results: Dict[str, Dict[str, Any]], expected_concurrency: int) -> Dict[str, Any]:
    """Enable only if p95 improves >= 15 percent AND throughput does not drop > 5 percent at the expected concurrency."""
    row = results.get(str(expected_concurrency))
    if not row or "off" not in row or "on" not in row:
        return {"enable": False, "reason": f"no measurement at expected concurrency {expected_concurrency}"}
    off, on = row["off"], row["on"]
    p95_gain = (off["p95_ms"] - on["p95_ms"]) / off["p95_ms"] if off["p95_ms"] > 0 else 0.0
    tp_change = (on["throughput_qps"] - off["throughput_qps"]) / off["throughput_qps"] if off["throughput_qps"] > 0 else 0.0
    ok_latency = p95_gain >= P95_IMPROVEMENT_MIN
    ok_throughput = tp_change >= -THROUGHPUT_DROP_MAX
    enable = ok_latency and ok_throughput
    reasons = []
    if not ok_latency:
        reasons.append(f"p95 improves {p95_gain:+.1%} (< {P95_IMPROVEMENT_MIN:.0%} needed)")
    if not ok_throughput:
        reasons.append(f"throughput changes {tp_change:+.1%} (drop > {THROUGHPUT_DROP_MAX:.0%})")
    return {
        "enable": enable, "expected_concurrency": expected_concurrency,
        "p95_improvement": p95_gain, "throughput_change": tp_change,
        "reason": "HYBRID_PARALLEL=true: both criteria met" if enable else "keep HYBRID_PARALLEL=false: " + "; ".join(reasons),
    }


def _default_retrieve_and_toggle(top_k: int):
    import config
    from rag_core import retrieval

    def set_parallel(on: bool) -> None:
        config.HYBRID_PARALLEL = bool(on)

    def retrieve(question: str) -> Any:
        return retrieval.hybrid_retrieve(question, None, top_k=top_k)

    return retrieve, set_parallel


def main(argv: Optional[Sequence[str]] = None, *, retrieve=None, set_parallel=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries", type=_Path, required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--workers", type=int, default=int(os.getenv("HYBRID_PARALLEL_WORKERS", "") or 4))
    parser.add_argument("--peak", type=int, default=None, help="peak concurrency (default 2 x workers)")
    parser.add_argument("--expected-concurrency", type=int, default=None, help="default: workers")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", type=_Path, default=None)
    args = parser.parse_args(argv)
    if args.n < 1 or args.workers < 1:
        parser.error("--n and --workers must be >= 1")
    peak = args.peak or 2 * args.workers
    expected = args.expected_concurrency or args.workers
    levels = sorted({1, args.workers, peak, expected})
    if retrieve is None or set_parallel is None:
        retrieve, set_parallel = _default_retrieve_and_toggle(args.top_k)
    queries = load_queries(args.queries, args.n)
    results = measure(queries, retrieve=retrieve, set_parallel=set_parallel, levels=levels)
    report = {
        "schema": "hybrid-parallel-measurement-v1", "cpu_count": os.cpu_count(), "workers": args.workers,
        "queries": len(queries), "levels": levels, "results": results, "recommendation": recommend(results, expected),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    print("RECOMMENDATION:", report["recommendation"]["reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
