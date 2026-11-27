from __future__ import annotations
# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

"""Frozen general retrieval benchmark (general_v1): build, validate and run with the pinned configuration.

  build     build data/bench/general_v1/{eval_set,chunks}.jsonl from the manifest's sources (deterministic, seeded)
            and check every input and output against the sha256 pinned in the manifest.
            --pin: first build on the measurement PC only: write the observed sha256 into the manifest (commit it).
  verify    check already built files against the pinned sha256 (no rebuild)
  run       build (or --skip-build: verify), refuse on any sha256 mismatch or unpinned model revision, run
            eval.bench with configs/bench/general_v1.json and write reports/bench/general_v1/<UTC>_<commit7>_<confighash8>_<runid6>.{md,json}
            (immutable: opened O_EXCL, never overwritten; listed in reports/bench/general_v1/index.json, `latest` is read from it)

  python scripts/run_general_benchmark.py build --pin                      # once, after fetching the data
  python scripts/run_general_benchmark.py run                              # every measurement
  python scripts/run_general_benchmark.py run --config configs/bench/general_v1.json --skip-build

Model revisions in the config must be real commit hashes (no REPLACE_WITH... placeholders); --allow-unpinned-models
exists for the smoke test only and marks the report as not comparable.
"""

import argparse
import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = _ROOT
DEFAULT_CONFIG = ROOT / "configs" / "bench" / "general_v1.json"
REPORT_SCHEMA = "general-benchmark-report-v1"
PLACEHOLDER = "REPLACE_WITH"


def _git(*args: str) -> Optional[str]:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):  # no git / not a checkout: recorded as unknown
        return None


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def unpinned_models(cfg: Dict[str, Any]) -> List[str]:
    """Model entries without a pinned revision (or a local model path)."""
    out = []
    entries = cfg.get("embedding")
    entries = entries if isinstance(entries, list) else [entries]
    for entry in [*entries, *(cfg.get("rerank") or [])]:
        if not entry or not entry.get("model"):
            continue
        revision = str(entry.get("revision") or "")
        if entry.get("local_model_path"):
            continue
        if not revision or PLACEHOLDER in revision:
            out.append(str(entry["model"]))
    return out


def build_dataset(manifest_path: Path, *, out_dir: Optional[Path] = None, pin_now: bool = False) -> Dict[str, Any]:
    from eval.bench import general

    manifest = general.load_manifest(manifest_path)
    inputs = general.check_inputs(manifest, ROOT, allow_unpinned=pin_now)
    built = general.build(manifest, ROOT, out_dir)
    if pin_now:
        general.pin(manifest_path, inputs, built)
    else:
        general.check_outputs(manifest, built["sha256"])
    return dict(built, inputs=inputs, manifest=str(manifest_path))


def verify_dataset(manifest_path: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    from eval.bench import general

    manifest = general.load_manifest(manifest_path)
    target = out_dir or _resolve(manifest["outputs"]["dir"])
    paths = {"eval_set": target / "eval_set.jsonl", "chunks": target / "chunks.jsonl"}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{path} is not built; run `scripts/run_general_benchmark.py build`")
    digests = {name: general.sha256_file(path) for name, path in paths.items()}
    general.check_outputs(manifest, digests)
    return {"paths": {k: str(v) for k, v in paths.items()}, "sha256": digests}


def _mode_of(summary: Dict[str, Any], modes: Dict[str, Dict[str, Any]]) -> Optional[str]:
    config = summary.get("config") or {}
    method = (config.get("fusion") or {}).get("method")
    reranked = bool(config.get("rerank"))
    for name, spec in modes.items():
        if spec.get("fusion") == method and bool(spec.get("rerank")) == reranked:
            return name
    return None


def summarize_run(run_dir: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    from eval.bench import report as bench_report

    primary = str(cfg.get("primary_metric", "ndcg@10"))
    modes_cfg = cfg.get("modes") or {}
    summaries = bench_report.load_summaries(run_dir)
    modes: Dict[str, Dict[str, Any]] = {}
    for summary in summaries:
        name = _mode_of(summary, modes_cfg)
        if name is None:
            continue
        if name in modes:  # two configs matching one mode would silently drop one measurement
            raise ValueError(f"mode '{name}' matches more than one config: {modes[name]['config_id']}, {summary['config_id']}")
        overall = (summary.get("metrics") or {}).get("all", {})
        modes[name] = {
            "config_id": summary["config_id"],
            "metrics": overall,
            "ci": summary.get("ci"),
            "by_source": {
                key.split(":", 2)[-1]: {primary: value.get(primary), "queries": value.get("queries")}
                for key, value in (summary.get("metrics") or {}).items()
                if key.startswith("slice:src:")
            },
            "candidate_miss_rate": summary.get("candidate_miss_rate"),
            "latency_ms": {stage: {"p50": v.get("p50_ms"), "p95": v.get("p95_ms")} for stage, v in (summary.get("latency") or {}).items()},
        }
    paired: Dict[str, Any] = {}
    baseline_mode = "hybrid_weighted_rrf"
    if baseline_mode in modes and len(modes) > 1:
        boot = cfg.get("bootstrap") or {}
        others = [m["config_id"] for name, m in modes.items() if name != baseline_mode]
        result = bench_report.compare(
            run_dir, modes[baseline_mode]["config_id"], metric=primary, candidates=others,
            resamples=int(boot.get("resamples", 2000)), seed=int(boot.get("seed", 0)),
        )
        by_id = {m["config_id"]: name for name, m in modes.items()}
        paired = {"baseline": baseline_mode, "metric": primary,
                  "results": {by_id.get(cid, cid): value for cid, value in (result.get("results") or result).items() if isinstance(value, dict)}}
    return {"primary_metric": primary, "modes": modes, "paired_vs_hybrid": paired}


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{float(value):.4f}"


def to_markdown(report: Dict[str, Any]) -> str:
    primary = report["primary_metric"]
    lines = [
        f"# General benchmark {report['benchmark']['name']} v{report['benchmark']['version']}",
        "",
        f"- date (UTC): {report['generated_at']}",
        f"- git commit: `{report['git_commit'] or 'unknown'}`{' (dirty tree)' if report.get('git_dirty') else ''}",
        f"- dataset sha256: eval_set `{report['dataset']['sha256']['eval_set'][:16]}` / chunks `{report['dataset']['sha256']['chunks'][:16]}`",
        f"- config sha256: `{report['config_sha256'][:16]}`  run: `{report['run_dir']}`",
        f"- comparable with other general_v1 reports: **{report['comparable']}**" + ("" if report["comparable"] else f" ({'; '.join(report['not_comparable_reasons'])})"),
        "",
        f"Primary metric: **{primary}** (see docs/operations/benchmark_governance.md).",
        "",
        f"| mode | {primary} | 95% CI | recall@20 | mrr@10 | hit@1 | candidate miss | p95 total ms |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for name, mode in report["modes"].items():
        m = mode["metrics"]
        ci = (mode.get("ci") or {}).get(primary) or {}
        ci_text = f"[{_fmt(ci.get('low'))}, {_fmt(ci.get('high'))}]" if ci else "-"
        lines.append(
            f"| {name} | {_fmt(m.get(primary))} | {ci_text} | {_fmt(m.get('recall@20'))} | {_fmt(m.get('mrr@10'))} | "
            f"{_fmt(m.get('hit@1'))} | {_fmt(mode.get('candidate_miss_rate'))} | {_fmt((mode['latency_ms'].get('total') or {}).get('p95'))} |"
        )
    sources = sorted({s for mode in report["modes"].values() for s in mode["by_source"]})
    if sources:
        lines += ["", f"## {primary} by source", "", "| mode | " + " | ".join(sources) + " |", "|---|" + "---:|" * len(sources)]
        for name, mode in report["modes"].items():
            lines.append(f"| {name} | " + " | ".join(_fmt(mode["by_source"].get(s, {}).get(primary)) for s in sources) + " |")
    paired = report.get("paired_vs_hybrid") or {}
    if paired.get("results"):
        lines += ["", f"## Paired vs {paired['baseline']} ({paired['metric']})", "", "| mode | diff | 95% CI |", "|---|---:|---|"]
        for name, r in paired["results"].items():
            lines.append(f"| {name} | {_fmt(r.get('diff'))} | [{_fmt(r.get('low'))}, {_fmt(r.get('high'))}] |")
    return "\n".join(lines) + "\n"


def run_benchmark(
    config_path: Path,
    *,
    runs_dir: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    skip_build: bool = False,
    allow_unpinned_models: bool = False,
) -> Dict[str, Any]:
    from eval.bench import general, runner

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    manifest_path = _resolve(cfg["benchmark_manifest"])
    manifest = general.load_manifest(manifest_path)
    unpinned = unpinned_models(cfg)
    if unpinned and not allow_unpinned_models:
        raise SystemExit(f"refusing: model revisions are not pinned for {unpinned} (set 'revision' in {config_path})")
    dataset = verify_dataset(manifest_path, data_dir) if skip_build else build_dataset(manifest_path, out_dir=data_dir)
    run_cfg = dict(cfg)
    run_cfg["eval_set"] = dataset["paths"]["eval_set"]
    run_cfg["corpus"] = dict(cfg.get("corpus") or {}, chunks_jsonl=dataset["paths"]["chunks"])
    run_cfg["output_dir"] = str(runs_dir or ROOT / "runs" / "bench" / "general_v1")
    if cache_dir is not None:
        run_cfg["cache_dir"] = str(cache_dir)
    run_cfg["general_benchmark"] = {"manifest": str(manifest_path), "dataset_sha256": dataset["sha256"]}
    run_dir = runner.run(run_cfg)

    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    reasons = []
    if unpinned:
        reasons.append(f"unpinned models {unpinned}")
    if manifest.get("status") != "pinned":
        reasons.append("manifest not pinned")
    report = {
        "schema": REPORT_SCHEMA,
        "benchmark": {"name": manifest["name"], "version": manifest["version"], "manifest": str(manifest_path.relative_to(ROOT)) if manifest_path.is_relative_to(ROOT) else str(manifest_path)},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "config": str(config_path),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "dataset": {"sha256": dataset["sha256"]},
        "run_dir": str(run_dir),
        "comparable": not reasons,
        "not_comparable_reasons": reasons,
        **summarize_run(run_dir, cfg),
    }
    out_dir = reports_dir or ROOT / "reports" / "bench" / str(manifest["name"])
    out_dir.mkdir(parents=True, exist_ok=True)
    config_sha = report["config_sha256"]
    report["report_paths"] = write_immutable_report(report, to_markdown(report), out_dir, commit=commit, config_sha256=config_sha)
    return report


INDEX_NAME = "index.json"


def _exclusive_write(path: Path, text: str) -> None:
    """Create `path` with O_EXCL: an existing report is never overwritten."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        _exclusive_write(tmp, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_index(out_dir: Path) -> List[Dict[str, Any]]:
    path = out_dir / INDEX_NAME
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("reports") or [])


def latest_report(out_dir: Path) -> Optional[Dict[str, Any]]:
    """The newest index entry (entries are appended in write order); None when nothing is indexed."""
    entries = read_index(out_dir)
    return entries[-1] if entries else None


def write_immutable_report(
    report: Dict[str, Any], markdown: str, out_dir: Path, *, commit: Optional[str], config_sha256: str,
    now: Optional[datetime] = None, run_id: Optional[str] = None,
) -> List[str]:
    """Write <UTC>_<commit7>_<confighash8>_<runid6>.{json,md} (O_EXCL) and append to index.json (atomic)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    rid = run_id or uuid.uuid4().hex[:6]
    stem = f"{stamp}_{(commit or '0000000')[:7]}_{config_sha256[:8]}_{rid}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    _exclusive_write(json_path, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    try:
        _exclusive_write(md_path, markdown)
    except FileExistsError:
        json_path.unlink()  # our own half-written pair; the pre-existing .md stays untouched
        raise
    lock_fd = os.open(str(out_dir / ".index.lock"), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        try:
            import fcntl

            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except ImportError:  # non-POSIX: unique file names still prevent overwrites
            pass
        entries = read_index(out_dir)
        entries.append({
            "stem": stem, "json": json_path.name, "md": md_path.name, "generated_at": report.get("generated_at"),
            "git_commit": commit, "config_sha256": config_sha256, "run_id": rid,
            "comparable": report.get("comparable"),
        })
        _atomic_write_json(out_dir / INDEX_NAME, {"schema": "general-benchmark-index-v1", "reports": entries})
    finally:
        os.close(lock_fd)
    return [str(json_path), str(md_path)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    from eval.bench.general import DatasetMismatch

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify", "run"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        p.add_argument("--data-dir", type=Path, default=None, help="where the built files go (default: manifest outputs.dir)")
        if name == "build":
            p.add_argument("--pin", action="store_true", help="write the observed sha256 into the manifest (first build only)")
        if name == "run":
            p.add_argument("--skip-build", action="store_true")
            p.add_argument("--runs-dir", type=Path, default=None)
            p.add_argument("--reports-dir", type=Path, default=None)
            p.add_argument("--cache-dir", type=Path, default=None)
            p.add_argument("--allow-unpinned-models", action="store_true", help="smoke tests only; the report is marked not comparable")
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    manifest_path = _resolve(cfg["benchmark_manifest"])
    try:
        if args.command == "build":
            result = build_dataset(manifest_path, out_dir=args.data_dir, pin_now=args.pin)
        elif args.command == "verify":
            result = verify_dataset(manifest_path, args.data_dir)
        else:
            report = run_benchmark(
                args.config, runs_dir=args.runs_dir, reports_dir=args.reports_dir, cache_dir=args.cache_dir,
                data_dir=args.data_dir, skip_build=args.skip_build, allow_unpinned_models=args.allow_unpinned_models,
            )
            result = {"report_paths": report["report_paths"], "comparable": report["comparable"]}
    except DatasetMismatch as exc:
        print(f"refusing: dataset does not match the manifest: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
