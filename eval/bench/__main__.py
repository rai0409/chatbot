"""python -m eval.bench <command>

plan        count the evaluations a config would run
run         run the grid; results go to runs/bench/<timestamp>_<hash>/
compare     paired tests of every config against a baseline config_id
pareto      configs not dominated on quality, p95 latency and memory
calibrate   abstention thresholds and the rerank-skip margin threshold
samplesize  questions needed for a target CI width or detectable difference
tokens      token-length distribution of chunks for a model (max_seq_length)
from-cases  evaluation set (dev/test split) from a labelled eval/cases/*_cases.jsonl file
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional, Sequence

from eval.bench import jqara, report, runner, stats, synthetic
from eval.bench.corpus import build_chunks
from eval.bench.encoders import EmbeddingSpec, token_length_stats


def _emit(payload: Any, out: Optional[Path]) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.bench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, required=True)
        if name == "run":
            p.add_argument("--final-test", action="store_true", help="the one final run on the held-out split 'test'")
            p.add_argument("--allow-test-reuse", action="store_true", help="record a deliberate second use of the test split")
    p = sub.add_parser("compare")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--metric", default="recall@10")
    p.add_argument("--margin", type=float, default=0.02)
    p.add_argument("--candidates", default="", help="comma-separated config_ids (default: all)")
    p.add_argument("--markdown", action="store_true")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("pareto")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--quality", default="recall@10")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("calibrate")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--config-id", required=True)
    p.add_argument("--base-config-id", default=None, help="un-reranked config, for the rerank-skip analysis")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--cost-wrong", type=float, default=10.0)
    p.add_argument("--cost-abstain", type=float, default=1.0)
    p.add_argument("--max-skip-loss", type=float, default=0.005)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("samplesize")
    p.add_argument("--delta", type=float, default=0.03)
    p.add_argument("--discordant", type=float, default=0.10)
    p.add_argument("--p", type=float, default=0.9)
    p.add_argument("--half-width", type=float, default=0.03)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p = sub.add_parser("tokens")
    p.add_argument("--model", required=True)
    p.add_argument("--chunks", type=Path, required=True, help="canonical chunk JSONL")
    p.add_argument("--documents", type=Path, default=None)
    p.add_argument("--chunking", default="as_is", choices=["as_is", "window"])
    p.add_argument("--chars", type=int, default=400)
    p.add_argument("--overlap", type=float, default=0.1)
    p.add_argument("--prefix", default="auto")
    p.add_argument("--max-seq-length", type=int, default=None)
    p = sub.add_parser("from-cases", help="evaluation set (dev/test split) from a labelled case file")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--test-share", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--match-mode", choices=["auto", "chunk", "doc"], default="auto")
    p = sub.add_parser("jqara-validate", help="validate the candidate-limited JQaRA Parquet contract")
    p.add_argument("--config", type=Path, default=None, help="JSON experiment config (also validates its non-model settings)")
    p.add_argument("--input", type=Path, default=None)
    p.add_argument("--expected-candidates", type=int, default=50)
    p = sub.add_parser("jqara-split", help="write a deterministic JQaRA tuning/validation split manifest")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--tuning-share", type=float, default=0.5)
    p.add_argument("--expected-candidates", type=int, default=50)
    p = sub.add_parser("jqara-run", help="run one candidate-limited JQaRA retrieval configuration")
    p.add_argument("--config", type=Path, default=None, help="JSON experiment config; explicit CLI values override it")
    p.add_argument("--input", type=Path, default=None)
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--model-revision", default=None)
    p.add_argument("--local-model-path", default=None)
    p.add_argument("--prefix", choices=["auto", "none", "custom"], default=None)
    p.add_argument("--query-prefix", default=None)
    p.add_argument("--document-prefix", default=None)
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--sample-size", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--split", choices=["dev", "tuning", "validation"], default=None)
    p.add_argument("--split-manifest", type=Path, default=None)
    p.add_argument("--method", choices=["dense_only", "lexical_only", "weighted_rrf", "dbsf", "dense", "bm25", "rrf"], default=None)
    p.add_argument("--rrf-k", type=int, default=None)
    p.add_argument("--dense-weight", type=float, default=None)
    p.add_argument("--keyword-weight", type=float, default=None)
    p.add_argument("--tokenizer", choices=["heuristic", "sudachi"], default=None)
    p.add_argument("--bm25-k1", type=float, default=None)
    p.add_argument("--bm25-b", type=float, default=None)
    p.add_argument("--ngram", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--metrics-k", default=None, help="comma-separated positive k values")
    p.add_argument("--bootstrap-resamples", type=int, default=None)
    p.add_argument("--reranker-model", default=None)
    p.add_argument("--reranker-revision", default=None)
    p.add_argument("--reranker-local-model-path", default=None)
    p.add_argument("--rerank-depth", type=int, default=None)
    p.add_argument("--reranker-max-length", type=int, default=None)
    p = sub.add_parser("jqara-compare", help="paired JQaRA comparison for complete compatible runs")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="+", required=True)
    p.add_argument("--metric", default="recall@10")
    p.add_argument("--resamples", type=int, default=2000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "plan":
        _emit(runner.plan(json.loads(args.config.read_text(encoding="utf-8"))), None)
    elif args.command == "run":
        cfg = json.loads(args.config.read_text(encoding="utf-8"))
        if args.final_test:
            cfg["final_test"] = True
        if args.allow_test_reuse:
            cfg["allow_test_reuse"] = True
        run_dir = runner.run(cfg)
        _emit({"run_dir": str(run_dir)}, None)
    elif args.command == "compare":
        candidates = [c for c in args.candidates.split(",") if c] or None
        result = report.compare(args.run, args.baseline, metric=args.metric, margin=args.margin, candidates=candidates)
        _emit(report.compare_markdown(result) if args.markdown else result, args.out)
    elif args.command == "pareto":
        _emit(report.pareto(args.run, quality=args.quality), args.out)
    elif args.command == "calibrate":
        _emit(
            report.calibrate(
                args.run, args.config_id, alpha=args.alpha, cost_wrong=args.cost_wrong, cost_abstain=args.cost_abstain,
                base_config_id=args.base_config_id, max_skip_loss=args.max_skip_loss, k=args.k,
            ),
            args.out,
        )
    elif args.command == "samplesize":
        _emit(
            {
                "questions_to_detect_delta": stats.n_for_mcnemar(args.delta, args.discordant, alpha=args.alpha, power=args.power),
                "questions_for_ci_half_width": stats.n_for_proportion_ci(args.p, args.half_width, alpha=args.alpha),
                "inputs": vars(args),
            },
            None,
        )
    elif args.command == "tokens":
        spec = {"mode": args.chunking, "chars": args.chars, "overlap": args.overlap}
        chunks = build_chunks(spec, args.chunks, args.documents)
        emb = EmbeddingSpec(model=args.model, prefix=args.prefix, max_seq_length=args.max_seq_length)
        _emit(token_length_stats(emb, [c.text for c in chunks]), None)
    elif args.command == "from-cases":
        rows, summary = synthetic.build_eval_set(
            synthetic.read_cases(args.cases), test_share=args.test_share, seed=args.seed, match_mode=args.match_mode
        )
        synthetic.write_eval_set(args.out, rows)
        _emit(summary, None)
    elif args.command == "jqara-validate":
        cfg = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
        if args.input is not None:
            cfg["input"] = str(args.input)
        cfg["expected_candidates"] = args.expected_candidates
        if "input" not in cfg:
            raise ValueError("jqara-validate requires --input or input in --config")
        _emit(jqara.validate(cfg), None)
    elif args.command == "jqara-split":
        queries = jqara.load_jqara(args.input, expected_candidates=args.expected_candidates)
        manifest = jqara.create_split(queries, seed=args.seed, tuning_share=args.tuning_share)
        _emit(manifest, args.out)
    elif args.command == "jqara-run":
        cfg = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
        def set_value(key, value):
            if value is not None:
                cfg[key] = str(value) if isinstance(value, Path) else value
        for key in ("input", "output_dir", "sample_size", "seed", "split", "split_manifest", "method", "bootstrap_resamples"):
            set_value(key, getattr(args, key))
        if args.metrics_k is not None:
            cfg["ks"] = [int(x) for x in args.metrics_k.split(",") if x]
        emb = dict(cfg.get("embedding", {}))
        for key, value in (("model", args.model), ("revision", args.model_revision), ("local_model_path", args.local_model_path),
                           ("prefix", args.prefix), ("query_prefix", args.query_prefix), ("document_prefix", args.document_prefix),
                           ("batch_size", args.batch_size)):
            if value is not None:
                emb[key] = value
        if emb:
            cfg["embedding"] = emb
        bm25 = dict(cfg.get("bm25", {}))
        for key, value in (("tokenizer", args.tokenizer), ("k1", args.bm25_k1), ("b", args.bm25_b), ("ngram", args.ngram)):
            if value is not None:
                bm25[key] = value
        if bm25:
            cfg["bm25"] = bm25
        fusion = dict(cfg.get("fusion", {}))
        for key, value in (("k", args.rrf_k), ("dense_weight", args.dense_weight), ("keyword_weight", args.keyword_weight)):
            if value is not None:
                fusion[key] = value
        if fusion:
            cfg["fusion"] = fusion
        rr = dict(cfg.get("reranker", {}))
        for key, value in (("model", args.reranker_model), ("revision", args.reranker_revision),
                           ("local_model_path", args.reranker_local_model_path), ("depth", args.rerank_depth),
                           ("max_length", args.reranker_max_length)):
            if value is not None:
                rr[key] = value
        if args.reranker_model is not None:
            rr["enabled"] = True
        if rr:
            cfg["reranker"] = rr
        if "input" not in cfg:
            raise ValueError("jqara-run requires --input or input in --config")
        jqara.validate(cfg)
        _emit({"run_dir": str(jqara.run(cfg))}, None)
    elif args.command == "jqara-compare":
        _emit(jqara.compare(args.runs, args.baseline, metric=args.metric, resamples=args.resamples, seed=args.seed), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
