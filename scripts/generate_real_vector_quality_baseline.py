#!/usr/bin/env python3
"""Generate the governed, offline real-vector retrieval-quality baseline."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import validate_embedding_asset_contract, validate_embedding_source_contract

SCHEMA_VERSION = "real_vector_quality_baseline.v1"
DEFAULT_CONTRACT = ROOT / "config/evaluation/real_vector_quality_baseline.contract.json"
DEFAULT_OUTPUT = ROOT / "reports/current_real_vector_quality_baseline.json"
PRODUCTION_COLLECTION_SENTINEL = "production_sentinel_not_eval"
MODES = ["bm25_only", "dense_only", "hybrid", "hybrid_rerank"]
IMPLEMENTATION_SOURCES = [
    "scripts/generate_real_vector_quality_baseline.py", "eval/runner.py",
    "rag_core/retrieval.py", "rag_core/embedding_provider.py", "rag_core/store.py",
]


class PreconditionError(ValueError): pass
class QualityViolationError(ValueError): pass
class ContractError(PreconditionError): pass
class AssetError(PreconditionError): pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractError("JSON root must be an object")
    return value


def _exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError(f"malformed {label}")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"invalid integer: {label}")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"invalid numeric value: {label}")
    return float(value)


def validate_contract(contract: dict[str, Any]) -> None:
    _exact_keys(contract, {"schema_version", "profile", "inputs", "embedding", "observed_quality", "release_invariants", "promotion"}, "contract")
    if contract["schema_version"] != "real_vector_quality_baseline_contract.v1":
        raise ContractError("unsupported contract schema")
    p = _exact_keys(contract["profile"], {"name", "purpose", "real_vector", "real_generation", "ordinary_ci_required", "release_gate_required"}, "profile")
    if (p["name"], p["real_vector"], p["real_generation"], p["ordinary_ci_required"], p["release_gate_required"]) != ("retrieval_real_vector_quality", True, False, False, True):
        raise ContractError("invalid profile")
    i = _exact_keys(contract["inputs"], {"cases_path", "cases_sha256", "chunks_path", "chunks_sha256", "modes", "top_k", "eval_k", "tenant_id", "expected_case_count", "expected_mode_count", "expected_row_count"}, "inputs")
    expected_inputs = {"cases_path": "eval/cases/retrieval_cases.jsonl", "chunks_path": "eval/cases/smoke_chunks.jsonl", "modes": MODES, "top_k": 20, "eval_k": 5, "tenant_id": "default", "expected_case_count": 25, "expected_mode_count": 4, "expected_row_count": 100}
    for key, expected in expected_inputs.items():
        if i.get(key) != expected or (key.startswith("expected_") or key in {"top_k", "eval_k"}) and isinstance(i.get(key), bool):
            raise ContractError(f"invalid input: {key}")
    for path_key, hash_key in (("cases_path", "cases_sha256"), ("chunks_path", "chunks_sha256")):
        if not isinstance(i[hash_key], str) or len(i[hash_key]) != 64 or not (ROOT / i[path_key]).is_file() or sha(ROOT / i[path_key]) != i[hash_key]:
            raise ContractError(f"input hash mismatch: {path_key}")
    e = _exact_keys(contract["embedding"], {"provider", "model", "revision", "dimension", "normalization", "asset_files_sha256", "runtime_network_allowed", "trust_remote_code"}, "embedding")
    if e["provider"] != "local" or e["model"] != "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" or e["revision"] != "e8f8c211226b894fcb81acc59f3b34ba3efd5f42" or e["dimension"] != 384 or isinstance(e["dimension"], bool) or e["normalization"] != "l2" or e["asset_files_sha256"] != "cede7177dd492d9d7776484dce8d030f0cd127eae3297305991de91386394d5a" or e["runtime_network_allowed"] is not False or e["trust_remote_code"] is not False:
        raise ContractError("invalid embedding contract")
    inv = _exact_keys(contract["release_invariants"], {"dense_gold_hit_min_at_5", "hybrid_mrr_min_at_5", "hybrid_ndcg_min_at_5", "hybrid_rerank_mrr_min_at_5", "hybrid_rerank_ndcg_min_at_5", "external_network_attempt_count"}, "release invariants")
    for key in inv:
        _number(inv[key], key)
    if inv["dense_gold_hit_min_at_5"] != 20 or inv["external_network_attempt_count"] != 0:
        raise ContractError("invalid release invariants")


def _network_guard(path: Path) -> None:
    path.write_text('''import errno, ipaddress, json, os, socket
from datetime import datetime, timezone
from pathlib import Path
LOG = Path(os.environ["REAL_VECTOR_NETWORK_LOG"])
_getaddrinfo, _socket, _create_connection = socket.getaddrinfo, socket.socket, socket.create_connection
def _host(address): return str(address[0] if isinstance(address, tuple) else address).rstrip(".").lower()
def _allowed(address):
    host = _host(address)
    if host in {"localhost", "ip6-localhost"}: return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False
def _record(operation, address):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as out: out.write(json.dumps({"operation": operation, "address": _host(address), "timestamp": datetime.now(timezone.utc).isoformat(), "pid": os.getpid()}, sort_keys=True) + "\\n")
def _blocked(operation, address): _record(operation, address); raise OSError(errno.EACCES, "external network blocked")
def getaddrinfo(host, *args, **kwargs):
    if not _allowed(host): _blocked("getaddrinfo", host)
    return _getaddrinfo(host, *args, **kwargs)
class GuardedSocket(_socket):
    def connect(self, address):
        if self.family == socket.AF_UNIX or _allowed(address): return super().connect(address)
        _blocked("connect", address)
    def connect_ex(self, address):
        if self.family == socket.AF_UNIX or _allowed(address): return super().connect_ex(address)
        _record("connect_ex", address); return errno.EACCES
def create_connection(address, *args, **kwargs):
    if _allowed(address): return _create_connection(address, *args, **kwargs)
    _blocked("create_connection", address)
socket.getaddrinfo, socket.socket, socket.create_connection = getaddrinfo, GuardedSocket, create_connection
''', encoding="utf-8")


def build_child_env(asset_dir: Path, work_dir: Path, guard: Path, network: Path, contract: dict[str, Any]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR"}}
    env.update({"EMBED_PROVIDER": "local", "LOCAL_EMBED_MODEL": contract["embedding"]["model"], "LOCAL_EMBED_MODEL_PATH": str(asset_dir), "VECTORSTORE_DIR": str(work_dir / "vectorstore"), "CHROMA_COLLECTION": PRODUCTION_COLLECTION_SENTINEL, "PYTHONPATH": str(guard) + os.pathsep + str(ROOT), "REAL_VECTOR_NETWORK_LOG": str(network), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "ANONYMIZED_TELEMETRY": "False", "PYTHONNOUSERSITE": "1", "TOKENIZERS_PARALLELISM": "false", "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1"})
    return env


def build_command(contract: dict[str, Any], rows: Path, summary: Path, collection: str) -> list[str]:
    i = contract["inputs"]
    return [sys.executable, "-m", "eval.runner", "--retrieval-aware", "--real-vector", "--cases", i["cases_path"], "--chunks-jsonl", i["chunks_path"], "--modes", ",".join(i["modes"]), "--top-k", str(i["top_k"]), "--eval-k", str(i["eval_k"]), "--tenant-id", i["tenant_id"], "--eval-collection", collection, "--per-query-output", str(rows), "--summary-output", str(summary), "--quiet"]


def _hit(row: dict[str, Any]) -> bool: return bool(row.get("gold_chunk_hit_at_k") or row.get("gold_doc_hit_at_k"))
def _supported(row: dict[str, Any]) -> bool: return bool(row.get("gold_chunk_ids") or row.get("gold_doc_ids"))


def analyse(contract: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]], collection: str, attempts: int) -> dict[str, Any]:
    i, inv, by = contract["inputs"], contract["release_invariants"], summary.get("by_mode", {})
    errors: list[str] = []
    coll = summary.get("evaluation_collection")
    expected_corpus_count = len((ROOT / i["chunks_path"]).read_text(encoding="utf-8").splitlines())
    if not isinstance(coll, dict) or coll.get("collection_name") != collection or collection == PRODUCTION_COLLECTION_SENTINEL or not isinstance(coll.get("inserted_record_count"), int) or isinstance(coll.get("inserted_record_count"), bool) or coll["inserted_record_count"] <= 0 or coll["inserted_record_count"] != expected_corpus_count: errors.append("collection")
    counts = {m: sum(r.get("mode") == m for r in rows) for m in MODES}
    if summary.get("status") != "ok" or set(by) != set(MODES) or len(rows) != i["expected_row_count"] or any(counts[m] != i["expected_case_count"] for m in MODES): errors.append("rows")
    dense = [r for r in rows if r.get("mode") == "dense_only"]
    support = [r for r in dense if _supported(r)]
    diagnostics = {"query_error_count": sum(bool(r.get("query_error")) for r in dense), "zero_candidate_query_count": sum(not r.get("before_rerank_ids") for r in dense), "nonempty_candidate_query_count": sum(bool(r.get("before_rerank_ids")) for r in dense), "metric_support_count": len(support), "gold_hit_count_at_5": sum(_hit(r) for r in support)}
    diagnostics["gold_miss_count_at_5"] = len(support) - diagnostics["gold_hit_count_at_5"]
    if diagnostics["query_error_count"] or diagnostics["zero_candidate_query_count"] or diagnostics["gold_hit_count_at_5"] < inv["dense_gold_hit_min_at_5"] or diagnostics["gold_miss_count_at_5"]: errors.append("dense")
    indexed = {m: {r.get("case_id"): r for r in rows if r.get("mode") == m and _supported(r)} for m in MODES}
    bm_misses = {case for case, row in indexed["bm25_only"].items() if not _hit(row)}
    dense_gains = sorted(case for case in bm_misses if case in indexed["dense_only"] and _hit(indexed["dense_only"][case]))
    hybrid_gains = sorted(case for case in bm_misses if case in indexed["hybrid"] and _hit(indexed["hybrid"][case]))
    regressions = sorted(case for case, row in indexed["bm25_only"].items() if _hit(row) and case in indexed["hybrid"] and not _hit(indexed["hybrid"][case]))
    if regressions: errors.append("hybrid_regression")
    for mode, mrr, ndcg in (("hybrid", "hybrid_mrr_min_at_5", "hybrid_ndcg_min_at_5"), ("hybrid_rerank", "hybrid_rerank_mrr_min_at_5", "hybrid_rerank_ndcg_min_at_5")):
        values = by.get(mode, {})
        try: valid = _number(values.get("mean_mrr_at_k"), mode + ".mrr") >= inv[mrr] and _number(values.get("mean_ndcg_at_k"), mode + ".ndcg") >= inv[ndcg]
        except ContractError: valid = False
        if not valid: errors.append(mode)
    if attempts != 0: errors.append("external_network")
    return {"mode_row_counts": counts, "dense_diagnostics": diagnostics, "comparison": {"semantic_gain_case_ids": hybrid_gains, "semantic_gain_case_count": len(hybrid_gains), "dense_unique_gain_case_ids": dense_gains, "dense_unique_gain_case_count": len(dense_gains), "hybrid_regression_case_ids": regressions, "hybrid_regression_case_count": len(regressions), "semantic_contribution_demonstrated": bool(hybrid_gains)}, "validation_errors": errors}


def _implementation_sources() -> list[dict[str, str]]:
    result = []
    for relative in IMPLEMENTATION_SOURCES:
        path = ROOT / relative
        if not path.is_file(): raise PreconditionError(f"required implementation source missing: {relative}")
        result.append({"path": relative, "sha256": sha(path)})
    return result


def validate_report(report: dict[str, Any], contract_path: Path = DEFAULT_CONTRACT) -> None:
    contract = load(contract_path); validate_contract(contract)
    if report.get("schema_version") != SCHEMA_VERSION or report.get("contract_sha256") != sha(contract_path) or report.get("external_network_attempt_count") != 0 or report.get("validation_status") != "passed": raise QualityViolationError("invalid governed report")
    if report.get("promotion", {}).get("product_promotion_eligible") is not False or report.get("comparison", {}).get("semantic_contribution_demonstrated") is not False: raise QualityViolationError("invalid promotion status")
    if report.get("runtime_isolation") != {"external_dns_blocked": True, "external_tcp_blocked": True, "loopback_allowed": True, "unix_socket_allowed": True, "telemetry_disabled": True, "offline_model_loading": True}: raise QualityViolationError("invalid isolation report")
    if report.get("implementation_sources") != _implementation_sources(): raise QualityViolationError("implementation source fingerprint mismatch")


def generate(contract_path: Path, output: Path, asset_dir: Path | None, work_dir: Path | None, quiet: bool = False) -> dict[str, Any]:
    if sys.version_info[:2] != (3, 12): raise PreconditionError("Python 3.12 required")
    contract = load(contract_path); validate_contract(contract)
    if asset_dir is None: raise AssetError("external asset required")
    asset_dir = asset_dir.resolve()
    source, asset = ROOT / "config/embedding_assets/retrieval_baseline.source.json", ROOT / "config/embedding_assets/retrieval_baseline.asset.json"
    try:
        validate_embedding_source_contract.validate_contract(validate_embedding_source_contract.load_contract(source))
        metadata = validate_embedding_asset_contract.validate_contract(asset, source, asset_dir)
    except Exception as exc:
        raise AssetError("invalid model asset") from exc
    for key, contract_key in (("model_id", "model"), ("revision", "revision"), ("embedding_dimension", "dimension"), ("runtime_files_sha256", "asset_files_sha256")):
        if metadata.get(key) != contract["embedding"][contract_key]: raise AssetError(f"asset metadata mismatch: {key}")
    with tempfile.TemporaryDirectory(dir=work_dir) as temporary:
        wd = Path(temporary); guard = wd / "guard"; guard.mkdir(); _network_guard(guard / "sitecustomize.py")
        rows_path, summary_path, network = wd / "rows.jsonl", wd / "summary.json", wd / "network.jsonl"; collection = "real_vector_quality_" + uuid.uuid4().hex
        proc = subprocess.run(build_command(contract, rows_path, summary_path, collection), cwd=ROOT, env=build_child_env(asset_dir, wd, guard, network, contract), text=True, capture_output=True)
        if proc.returncode: raise PreconditionError("evaluation runtime failed")
        summary = load(summary_path); rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
        attempts = len(network.read_text(encoding="utf-8").splitlines()) if network.exists() else 0
        facts = analyse(contract, summary, rows, collection, attempts)
        if facts["validation_errors"]: raise QualityViolationError("quality invariants: " + ",".join(facts["validation_errors"]))
        report = {"schema_version": SCHEMA_VERSION, "profile": contract["profile"], "generated_at": datetime.now(timezone.utc).isoformat(), "evaluation_semantics": {"real_vector": True, "real_generation": False}, "inputs": contract["inputs"], "contract_sha256": sha(contract_path), "embedding": contract["embedding"], "evaluation_collection": {"name": "<isolated-evaluation-collection>", "record_count": summary["evaluation_collection"]["inserted_record_count"], "corpus_fingerprint": summary["evaluation_collection"]["corpus_fingerprint"]}, "per_mode_metrics": summary["by_mode"], **facts, "promotion": contract["promotion"], "external_network_attempt_count": attempts, "runtime_isolation": {"external_dns_blocked": True, "external_tcp_blocked": True, "loopback_allowed": True, "unix_socket_allowed": True, "telemetry_disabled": True, "offline_model_loading": True}, "implementation_sources": _implementation_sources(), "validation_status": "passed", "executed_command": ["<current-python>" if x == sys.executable else ("<temporary>" if str(wd) in x else x) for x in build_command(contract, rows_path, summary_path, collection)]}
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT); parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT); parser.add_argument("--asset-dir", type=Path); parser.add_argument("--work-dir", type=Path); parser.add_argument("--quiet", action="store_true")
    try: args = parser.parse_args(argv); generate(args.contract, args.output, args.asset_dir, args.work_dir, args.quiet); return 0
    except SystemExit: return 2
    except (PreconditionError, AssetError) as exc: print(f"real-vector baseline precondition error: {type(exc).__name__}: {exc}", file=sys.stderr); return 2
    except QualityViolationError as exc: print(f"real-vector baseline quality violation: {type(exc).__name__}: {exc}", file=sys.stderr); return 3
    except Exception as exc: print(f"real-vector baseline unexpected error: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
