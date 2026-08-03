#!/usr/bin/env python3
"""Capture the active retrieval runtime and existing evaluations as a baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from rag_core import embedding_provider, ja_text, reranker, retrieval, store  # noqa: E402
from rag_core.question_normalization import normalize_question_for_exact_match  # noqa: E402
from scripts import (  # noqa: E402
    validate_embedding_asset_contract,
    validate_embedding_source_contract,
)


SCHEMA_VERSION = "current_retrieval_baseline.v2"
INDEX_SCHEMA_VERSION = "canonical-jsonl+chroma.v1"
SECRET_KEY_PARTS = ("api_key", "apikey", "password", "secret", "credential")
CORPUS_METADATA_FIELDS = (
    "aliases",
    "approved_qa_id",
    "chunk_index",
    "chunk_role",
    "doc_type",
    "faq_question",
    "language",
    "parent_id",
    "quality",
    "question_text",
    "section_path",
    "source_doc",
    "source_pages",
    "title",
    "type",
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def deterministic_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_path(path: str | Path, root: Path = ROOT) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.name


def _normalized_searchable(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
    if value is None:
        return True
    return bool(value)


def canonical_corpus_record(record: Mapping[str, Any]) -> dict[str, Any]:
    text = str(record.get("searchable_text") or record.get("text") or record.get("document") or "")
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else record
    metadata = dict(metadata)
    embedding = record.get("embedding")
    return {
        "tenant_id": str(metadata.get("tenant_id") or "default"),
        "document_id": str(metadata.get("doc_id") or metadata.get("document_id") or metadata.get("source_doc") or ""),
        "chunk_id": str(record.get("id") or metadata.get("id") or metadata.get("chunk_id") or ""),
        "searchable_text": text,
        "document_version": str(
            metadata.get("document_version") or metadata.get("doc_version") or metadata.get("version") or ""
        ),
        "searchable": _normalized_searchable(metadata.get("searchable", True)),
        "retrieval_metadata": {
            key: metadata[key]
            for key in CORPUS_METADATA_FIELDS
            if key in metadata and metadata[key] not in (None, "", [])
        },
        "embedding_hash": deterministic_hash(list(embedding)) if embedding is not None else None,
    }


def corpus_fingerprint(records: Iterable[Mapping[str, Any]]) -> str:
    normalized = [canonical_corpus_record(record) for record in records]
    normalized.sort(key=canonical_json)
    return deterministic_hash(normalized)


def _qa_enabled(record: Mapping[str, Any]) -> bool:
    if str(record.get("status") or "").strip().lower() != "approved":
        return False
    if record.get("disabled") is True or record.get("enabled") is False:
        return False
    return True


def canonical_approved_qa_record(record: Mapping[str, Any]) -> dict[str, Any]:
    question = str(record.get("normalized_question") or record.get("question") or "")
    aliases = sorted(
        normalize_question_for_exact_match(str(alias))
        for alias in (record.get("approved_aliases") or [])
        if str(alias).strip()
    )
    citations = [dict(item) for item in (record.get("approved_citations") or []) if isinstance(item, Mapping)]
    citations.sort(key=canonical_json)
    return {
        "qa_id": str(record.get("qa_id") or ""),
        "tenant_id": str(record.get("tenant_id") or "default"),
        "normalized_question": normalize_question_for_exact_match(question),
        "approved_answer": str(record.get("approved_answer") or "").strip(),
        "approved_aliases": aliases,
        "approved_citations": citations,
        "language": str(record.get("language") or "ja"),
        "document_version": str(record.get("doc_version") or record.get("version") or ""),
    }


def approved_qa_fingerprint(records: Iterable[Mapping[str, Any]], *, runtime_enabled: bool = True) -> str:
    normalized = [canonical_approved_qa_record(record) for record in records if runtime_enabled and _qa_enabled(record)]
    normalized.sort(key=canonical_json)
    return deterministic_hash(normalized)


def sanitize_report(value: Any, *, secret_values: Sequence[str] = ()) -> Any:
    secrets = tuple(secret for secret in secret_values if len(str(secret)) >= 4)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                continue
            result[str(key)] = sanitize_report(item, secret_values=secrets)
        return result
    if isinstance(value, list):
        return [sanitize_report(item, secret_values=secrets) for item in value]
    if isinstance(value, tuple):
        return [sanitize_report(item, secret_values=secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            result = result.replace(secret, "[REDACTED]")
        return result
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if any(part in lowered for part in SECRET_KEY_PARTS):
        return True
    segments = [part for part in re.split(r"[^a-z0-9]+", lowered) if part]
    return "token" in segments


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _source_hash(callables: Sequence[Any]) -> str:
    return deterministic_hash([inspect.getsource(item) for item in callables])


def tokenizer_version() -> str:
    digest = _source_hash(
        [retrieval._heuristic_tokenize, retrieval._normalize, ja_text.normalize_japanese_text, ja_text.extract_salient_terms_ja]
    )
    return f"heuristic-ja-tokenizer:{digest}"


def reranker_fingerprint() -> str:
    payload = {
        "heuristic_source_hash": _source_hash([reranker.rerank_chunks]),
        "hybrid_adjustment_source_hash": _source_hash(
            [retrieval._hybrid_bucket_rank_adjustment, retrieval._apply_page_sensitive_adjustments]
        ),
        "heuristic_settings": {
            "boost_faq_short_lookup": config.RERANK_BOOST_FAQ_SHORT_LOOKUP,
            "boost_procedure_doc_type": config.RERANK_BOOST_PROCEDURE_DOC_TYPE,
            "max_lift_strong": config.RERANK_MAX_LIFT_STRONG,
            "max_lift_weak": config.RERANK_MAX_LIFT_WEAK,
            "max_lift_other_weak": config.RERANK_MAX_LIFT_OTHER_WEAK,
            "keyword_boost_enabled": config.KEYWORD_BOOST_ENABLED,
            "keyword_boost_query_types": list(config.KEYWORD_BOOST_QUERY_TYPES),
            "keyword_boost_max_delta": config.KEYWORD_BOOST_MAX_DELTA,
        },
        "cross_encoder": {
            "enabled": config.CROSS_ENCODER_RERANK_ENABLED,
            "model": config.CROSS_ENCODER_MODEL if config.CROSS_ENCODER_RERANK_ENABLED else None,
            "candidate_count": config.CROSS_ENCODER_TOP_N if config.CROSS_ENCODER_RERANK_ENABLED else 0,
        },
    }
    return deterministic_hash(payload)


def _collection_records() -> tuple[list[dict[str, Any]], int | None, int, dict[str, Any]]:
    client = store._get_persistent_client(config.VECTORSTORE_DIR)
    name = config.resolve_chroma_collection_name()
    collection = client.get_collection(name=name)
    payload = collection.get(include=["documents", "metadatas", "embeddings"])
    ids = list(payload.get("ids") or [])
    documents = list(payload.get("documents") or [])
    metadatas = list(payload.get("metadatas") or [])
    embeddings = payload.get("embeddings")
    embeddings_list = list(embeddings) if embeddings is not None else []
    records = []
    for index, item_id in enumerate(ids):
        records.append(
            {
                "id": item_id,
                "document": documents[index] if index < len(documents) else "",
                "metadata": metadatas[index] if index < len(metadatas) else {},
                "embedding": embeddings_list[index] if index < len(embeddings_list) else None,
            }
        )
    dimension = len(embeddings_list[0]) if embeddings_list else None
    return records, dimension, collection.count(), dict(collection.metadata or {})


def _embedding_normalization(provider_name: str) -> str:
    return "l2" if provider_name in {"local", "bge_m3"} else "provider_default"


def _embedding_runtime_metadata(provider: Any) -> dict[str, Any]:
    metadata = getattr(provider, "embedding_asset_metadata", lambda: None)()
    if not metadata:
        return {}
    return {
        "revision": metadata["revision"],
        "asset_files_sha256": metadata["runtime_files_sha256"],
        "asset_file_count": metadata["runtime_file_count"],
        "runtime_network_allowed": False,
        "trust_remote_code": False,
    }


def verify_baseline_runtime() -> dict[str, Any]:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("baseline runtime requires Python 3.12")
    if not Path(sys.executable).is_file():
        raise RuntimeError("current Python interpreter is unavailable")

    source_contract_path = ROOT / "config/embedding_assets/retrieval_baseline.source.json"
    asset_contract_path = ROOT / "config/embedding_assets/retrieval_baseline.asset.json"
    source_contract = validate_embedding_source_contract.load_contract(source_contract_path)
    validate_embedding_source_contract.validate_contract(source_contract)
    provider = embedding_provider.get_embedding_provider()
    if provider.name != "local":
        raise RuntimeError("baseline runtime requires the local embedding provider")
    if str(getattr(provider, "model_name", "")) != source_contract["model_id"]:
        raise RuntimeError("baseline runtime model identity mismatch")
    model_path = getattr(provider, "model_path", None)
    if not model_path:
        raise RuntimeError("baseline runtime requires an external embedding asset")

    try:
        asset = validate_embedding_asset_contract.validate_contract(
            contract_path=asset_contract_path,
            source_contract_path=source_contract_path,
            asset_dir=Path(model_path),
        )
    except validate_embedding_asset_contract.ContractError as exc:
        raise RuntimeError("baseline embedding asset validation failed") from exc
    if asset["model_id"] != provider.model_name:
        raise RuntimeError("baseline runtime asset identity mismatch")

    expected_versions = {
        "sentence-transformers": "5.2.2",
        "torch": "2.10.0+cpu",
    }
    actual_versions = {
        name: importlib.metadata.version(name)
        for name in expected_versions
    }
    if actual_versions != expected_versions:
        raise RuntimeError("baseline runtime dependency version mismatch")
    try:
        import torch
    except Exception as exc:
        raise RuntimeError("baseline runtime torch validation failed") from exc
    if torch.__version__ != "2.10.0+cpu" or torch.version.cuda is not None or torch.cuda.is_available():
        raise RuntimeError("baseline runtime is not CPU-only")
    return {
        "provider": provider.name,
        "model": provider.model_name,
        "revision": asset["revision"],
        "dimension": asset["embedding_dimension"],
        "normalization": "l2",
        "asset_files_sha256": asset["runtime_files_sha256"],
        "asset_file_count": asset["runtime_file_count"],
        "runtime_network_allowed": False,
        "trust_remote_code": False,
    }


def collect_runtime() -> tuple[dict[str, Any], dict[str, Any], str, str]:
    provider = embedding_provider.get_embedding_provider()
    collection_records, dimension, collection_count, collection_metadata = _collection_records()
    keyword_records = _read_jsonl(Path(config.CHUNKS_JSONL_PATH))
    qa_records = _read_jsonl(Path(config.APPROVED_QA_PATH)) if Path(config.APPROVED_QA_PATH).exists() else []
    corpus_records = [dict(row, _source="keyword_jsonl") for row in keyword_records]
    corpus_records.extend(dict(row, _source="vector_collection") for row in collection_records)
    corpus_hash = corpus_fingerprint(corpus_records)
    qa_hash = approved_qa_fingerprint(qa_records, runtime_enabled=config.APPROVED_QA_ENABLED)
    asset_metadata = _embedding_runtime_metadata(provider)
    runtime = {
        "embedding": {
            "provider": provider.name,
            "model": str(getattr(provider, "model_name", "") or ""),
            "dimension": dimension,
            "normalization": _embedding_normalization(provider.name),
            **asset_metadata,
        },
        "vector_collection": {
            "name": config.resolve_chroma_collection_name(),
            "record_count": collection_count,
            "embedding_stamp": {
                "provider": collection_metadata.get("embed_provider"),
                "model": collection_metadata.get("embed_model"),
            },
        },
        "retrieval": {
            "mode": "hybrid" if config.ENABLE_HYBRID_RETRIEVAL else "vector",
            "top_k": config.TOP_K,
            "vector_top_k": config.VECTOR_TOP_K,
            "bm25_top_k": config.BM25_TOP_K,
            "rrf": {"enabled": config.ENABLE_HYBRID_RETRIEVAL, "k": config.HYBRID_RRF_K},
            "keyword_tokenizer": tokenizer_version(),
        },
        "rerank": {
            "heuristic_enabled": True,
            "cross_encoder_enabled": config.CROSS_ENCODER_RERANK_ENABLED,
            "cross_encoder_model": config.CROSS_ENCODER_MODEL if config.CROSS_ENCODER_RERANK_ENABLED else None,
            "candidate_count": config.CROSS_ENCODER_TOP_N if config.CROSS_ENCODER_RERANK_ENABLED else 0,
            "fingerprint": reranker_fingerprint(),
        },
        "answer_guard": {
            "enabled": not config.DISABLE_GUARD,
            "hard_max_distance": config.RAG_HARD_MAX_DIST,
            "hard_max_distance_procedure_delta": config.RAG_HARD_MAX_DIST_PROCEDURE_DELTA,
            "minimum_keyword_evidence_bm25": config.RAG_MIN_KEYWORD_EVIDENCE_BM25,
            "soft_distance_other": config.RAG_SOFT_DIST_OTHER,
            "soft_distance_reset": config.RAG_SOFT_DIST_RESET,
            "soft_distance_change": config.RAG_SOFT_DIST_CHANGE,
            "soft_distance_procedure": config.RAG_SOFT_DIST_PROCEDURE,
            "generation_mode": config.CHAT_GENERATION_MODE,
        },
        "sources": {
            "corpus": normalize_path(config.CHUNKS_JSONL_PATH),
            "vectorstore": normalize_path(config.VECTORSTORE_DIR),
            "approved_qa": normalize_path(config.APPROVED_QA_PATH),
            "approved_qa_enabled": config.APPROVED_QA_ENABLED,
        },
    }
    compatibility = {
        "embedding_provider": provider.name,
        "embedding_model": str(getattr(provider, "model_name", "") or ""),
        "embedding_dimension": dimension,
        "embedding_normalization": _embedding_normalization(provider.name),
        **{
            f"embedding_{key}": value
            for key, value in asset_metadata.items()
            if key in {"revision", "asset_files_sha256"}
        },
        "retrieval_mode": runtime["retrieval"]["mode"],
        "tokenizer_version": runtime["retrieval"]["keyword_tokenizer"],
        "corpus_fingerprint": corpus_hash,
        "approved_qa_fingerprint": qa_hash,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "reranker_fingerprint": runtime["rerank"]["fingerprint"],
    }
    return runtime, compatibility, corpus_hash, qa_hash


def compatibility_fingerprint(fields: Mapping[str, Any]) -> str:
    return deterministic_hash(dict(fields))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()


def _run(
    command: Sequence[str],
    *,
    label: str,
    commands: list[dict[str, Any]],
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        proc = subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "")
    commands.append(
        {
            "label": label,
            "command": " ".join(_display_command_arg(item) for item in command),
            "exit_code": proc.returncode,
            "status": "blocked" if timed_out else ("passed" if proc.returncode == 0 else "failed"),
            "duration_seconds": round(time.monotonic() - started, 3),
            **({"reason": f"timed out after {timeout_seconds:g} seconds"} if timed_out else {}),
        }
    )
    return proc


def _display_command_arg(value: str) -> str:
    text = str(value)
    if text == sys.executable:
        return "<current-python>"
    root_prefix = str(ROOT) + os.sep
    if text.startswith(root_prefix):
        return text[len(root_prefix) :]
    if text.startswith(tempfile.gettempdir() + os.sep):
        return "<temporary-output>/" + Path(text).name
    return text


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _unknown_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    rows = _read_jsonl(path)
    return {
        "total_cases": len(rows),
        "errors": sum(row.get("classification") == "error" for row in rows),
        "abstained_count": sum(row.get("abstained") is True for row in rows),
        "grounded_answer_count": sum(row.get("grounded_answer") is True for row in rows),
        "unsupported_answer_count": sum(row.get("unsupported_answer") is True for row in rows),
        "approved_exact_false_positive_count": sum(
            row.get("approved_exact_false_positive") is True for row in rows
        ),
    }


def _stable_eval_signature(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    payload = json.loads(json.dumps(payload))
    if isinstance(payload.get("summary"), dict):
        payload["summary"].pop("generated_at", None)
    payload.pop("generated_at", None)
    return deterministic_hash(payload)


def _wait_for_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_live_evaluations(
    work: Path,
    commands: list[dict[str, Any]],
    python: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    log_path = work / "uvicorn.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [python, "-m", "uvicorn", "webapi.main:app", "--host", "127.0.0.1", "--port", "8021"],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            if not _wait_for_health("http://127.0.0.1:8021/health"):
                warnings.append({"evaluation": "live_chat", "reason": "local runtime did not become healthy"})
                return {}, warnings
            grounded_dir = work / "grounded"
            unknown_dir = work / "unknown"
            _run(
                [python, "tools/evaluate_grounded_extractive_quality.py", "--chat-url", "http://127.0.0.1:8021/chat", "--output-dir", str(grounded_dir)],
                label="grounded_extractive_answer",
                commands=commands,
            )
            _run(
                [python, "tools/evaluate_unknown_abstention.py", "--chat-url", "http://127.0.0.1:8021/chat", "--output-dir", str(unknown_dir)],
                label="unknown_abstention",
                commands=commands,
            )
            return {
                "grounded_extractive_answer": _load_json(grounded_dir / "grounded_extractive_quality_summary.json"),
                "unknown_abstention": _unknown_summary(unknown_dir / "unknown_abstention_results.jsonl"),
            }, warnings
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def generate(output_path: Path) -> tuple[dict[str, Any], bool]:
    commands: list[dict[str, Any]] = []
    warnings: list[Any] = []
    skipped: list[dict[str, str]] = []
    verified_embedding_runtime = verify_baseline_runtime()
    work = Path(tempfile.mkdtemp(prefix="retrieval_baseline_eval_"))
    python = sys.executable

    runtime_before, fields_before, corpus_hash, qa_hash = collect_runtime()

    smoke_before = work / "smoke_before.json"
    retrieval_before = work / "retrieval_before.json"
    retrieval_rows_before = work / "retrieval_rows_before.jsonl"
    _run([python, "-m", "eval.runner", "--output", str(smoke_before), "--quiet"], label="smoke_before", commands=commands)
    _run(
        [python, "-m", "eval.runner", "--retrieval-aware", "--cases", "eval/cases/retrieval_cases.jsonl", "--chunks-jsonl", "eval/cases/smoke_chunks.jsonl", "--modes", "bm25_only,dense_only,hybrid,hybrid_rerank", "--per-query-output", str(retrieval_rows_before), "--summary-output", str(retrieval_before), "--quiet"],
        label="retrieval_evaluation_before",
        commands=commands,
    )

    exact_path = work / "approved_exact.json"
    alias_dir = work / "approved_alias"
    _run([python, "-m", "eval.approved_qa_runner", "--cases", config.APPROVED_QA_PATH, "--output", str(exact_path)], label="approved_exact_qa", commands=commands)
    _run([python, "-m", "eval.approved_qa_alias_runner", "--output-dir", str(alias_dir)], label="approved_alias", commands=commands)

    live_results, live_warnings = _run_live_evaluations(work, commands, python)
    warnings.extend(live_warnings)
    if not live_results:
        skipped.extend(
            [
                {"evaluation": "grounded_extractive_answer", "reason": "local runtime was unavailable"},
                {"evaluation": "unknown_abstention", "reason": "local runtime was unavailable"},
            ]
        )

    _run(["bash", "scripts/product_readiness_smoke.sh"], label="product_readiness_smoke", commands=commands)
    focused = _run([python, "-m", "pytest", "tests/test_retrieval_baseline.py", "-q"], label="focused_tests", commands=commands)
    full = _run(
        [python, "-m", "pytest", "-q"],
        label="full_pytest",
        commands=commands,
        timeout_seconds=120,
    )
    compile_check = _run(
        [python, "-m", "compileall", "-q", "config.py", "rag_core", "eval", "scripts", "tools", "webapi"],
        label="python_compile_check",
        commands=commands,
    )

    smoke_after = work / "smoke_after.json"
    retrieval_after = work / "retrieval_after.json"
    retrieval_rows_after = work / "retrieval_rows_after.jsonl"
    _run([python, "-m", "eval.runner", "--output", str(smoke_after), "--quiet"], label="smoke_after", commands=commands)
    _run(
        [python, "-m", "eval.runner", "--retrieval-aware", "--cases", "eval/cases/retrieval_cases.jsonl", "--chunks-jsonl", "eval/cases/smoke_chunks.jsonl", "--modes", "bm25_only,dense_only,hybrid,hybrid_rerank", "--per-query-output", str(retrieval_rows_after), "--summary-output", str(retrieval_after), "--quiet"],
        label="retrieval_evaluation_after",
        commands=commands,
    )

    runtime_after, fields_after, _, _ = collect_runtime()
    smoke_before_payload = _load_json(smoke_before)
    smoke_after_payload = _load_json(smoke_after)
    retrieval_before_payload = _load_json(retrieval_before)
    retrieval_after_payload = _load_json(retrieval_after)
    invariance = {
        "runtime_configuration_unchanged": runtime_before == runtime_after,
        "compatibility_fields_unchanged": fields_before == fields_after,
        "smoke_result_signature_before": _stable_eval_signature(smoke_before_payload),
        "smoke_result_signature_after": _stable_eval_signature(smoke_after_payload),
        "smoke_results_unchanged": _stable_eval_signature(smoke_before_payload) == _stable_eval_signature(smoke_after_payload),
        "retrieval_result_signature_before": _stable_eval_signature(retrieval_before_payload),
        "retrieval_result_signature_after": _stable_eval_signature(retrieval_after_payload),
        "retrieval_results_unchanged": _stable_eval_signature(retrieval_before_payload) == _stable_eval_signature(retrieval_after_payload),
    }
    if not all(
        invariance[key]
        for key in (
            "runtime_configuration_unchanged",
            "compatibility_fields_unchanged",
            "smoke_results_unchanged",
            "retrieval_results_unchanged",
        )
    ):
        warnings.append({"type": "runtime_invariance", "reason": "before/after runtime or evaluation signature changed"})

    alias_summary = _load_json(alias_dir / "validation_summary.json") or {}
    alias_summary.pop("existing_gate_summary", None)
    evaluation_results = {
        "approved_exact_qa": (_load_json(exact_path) or {}).get("summary"),
        "approved_alias": alias_summary,
        "retrieval_evaluation": (retrieval_after_payload or {}).get("by_mode"),
        "grounded_smoke": (smoke_after_payload or {}).get("summary"),
        **live_results,
        "product_readiness_smoke": next((item for item in commands if item["label"] == "product_readiness_smoke"), None),
    }
    command_by_label = {item["label"]: item for item in commands}
    tests = {
        "focused_tests": {
            "status": command_by_label["focused_tests"]["status"],
            "exit_code": focused.returncode,
        },
        "full_pytest": {
            "status": command_by_label["full_pytest"]["status"],
            "exit_code": full.returncode,
            **(
                {"reason": command_by_label["full_pytest"]["reason"]}
                if "reason" in command_by_label["full_pytest"]
                else {}
            ),
        },
        "python_compile_check": {
            "status": command_by_label["python_compile_check"]["status"],
            "exit_code": compile_check.returncode,
        },
    }
    dirty = _git("status", "--short").splitlines()
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "branch": _git("branch", "--show-current"),
        "baseline_commit_sha": _git("rev-parse", "HEAD"),
        "dirty_working_tree": bool(dirty),
        "dirty_paths": dirty,
        "executed_commands": commands,
        "runtime_configuration": runtime_before,
        "verified_embedding_runtime": verified_embedding_runtime,
        "compatibility_fingerprint_fields": fields_before,
        "compatibility_fingerprint_hash": compatibility_fingerprint(fields_before),
        "corpus_fingerprint": corpus_hash,
        "approved_qa_fingerprint": qa_hash,
        "evaluation_results": evaluation_results,
        "test_results": tests,
        "runtime_invariance": invariance,
        "warnings": warnings,
        "skipped_evaluations": skipped,
    }
    secret_values = [
        str(value)
        for key, value in os.environ.items()
        if value and _is_secret_key(key)
    ]
    report = sanitize_report(report, secret_values=secret_values)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for secret in secret_values:
        if len(secret) >= 4 and secret in serialized:
            raise RuntimeError("secret value remained in baseline report")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    required_validation_passed = all(item["status"] == "passed" for item in tests.values()) and next(
        item for item in commands if item["label"] == "product_readiness_smoke"
    )["status"] == "passed"
    return report, required_validation_passed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/current_retrieval_baseline.json")
    args = parser.parse_args(argv)
    report, valid = generate(args.output)
    print(
        json.dumps(
            {
                "output": normalize_path(args.output),
                "compatibility_fingerprint_hash": report["compatibility_fingerprint_hash"],
                "required_validation_passed": valid,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
