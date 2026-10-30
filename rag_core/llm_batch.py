"""Many LLM requests that nobody waits for: scoring a whole evaluation, a nightly re-run, enriching documents (S6).

A person asking a question needs the answer now: that is the gateway (llm_gateway.py). Work that can wait is cheaper to hand to the
provider's batch API: the requests go in one file, the provider answers within a day, and the price is lower. Two ways to run a set
of requests:

  run(..., mode="sync")    one request after the other through the gateway: the policy, the breaker, the limits and the usage
                           log apply to each. Works with every profile, including a local model. The result is here when it returns.
  submit(...) / poll / collect   the provider's batch API (the OpenAI-compatible /v1/files and /v1/batches). Only for a profile whose
                           file says `"batch": true`. `submit` returns at once with a job; `poll` asks how far it is; `collect`
                           reads the answers when it is done and writes them to the usage log, with the batch price factor.

A job is a small file (<RUNS_DIR>/llm_batch/<job id>.json): what was asked (ids only, not the texts), which profile, the provider's batch
id and the state. The texts live in the provider's files for as long as the provider keeps them: use a batch only for data that may go to
that provider, like any other call (the policy check is the same). Not verified against a provider yet: it is written from the
provider's documented API and tested with a fake one.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import config
from rag_core import audit_log, llm, llm_profiles, llm_quota, llm_usage

ENDPOINT = "/v1/chat/completions"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DONE = {"completed"}
_FAILED = {"failed", "expired", "cancelled", "canceled"}


class BatchError(RuntimeError):
    """The batch cannot be made or read (not allowed, no batch-capable profile, an unknown job, the provider's refusal)."""


@dataclass
class BatchRequest:
    custom_id: str
    messages: List[Dict[str, str]]
    max_tokens: int = 256
    temperature: float = 0.0


@dataclass
class BatchResult:
    custom_id: str
    text: str = ""
    error: str = ""  # empty when there is a text
    input_tokens: int = 0
    output_tokens: int = 0


def _dir() -> Path:
    return Path(config.RUNS_DIR) / "llm_batch"


def _job_path(job_id: str) -> Path:
    if not _ID_RE.fullmatch(job_id or ""):
        raise BatchError("the job id is not valid")
    return _dir() / f"{job_id}.json"


def _save(job: Dict[str, Any]) -> None:
    path = _job_path(job["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def get_job(job_id: str) -> Dict[str, Any]:
    try:
        return json.loads(_job_path(job_id).read_text(encoding="utf-8"))
    except OSError:
        raise BatchError(f"job '{job_id}' not found") from None
    except ValueError:
        raise BatchError(f"job '{job_id}' is unreadable") from None


def list_jobs() -> List[Dict[str, Any]]:
    directory = _dir()
    rows: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda j: str(j.get("created_at")), reverse=True)


def _check(requests: Sequence[BatchRequest]) -> None:
    ids = [r.custom_id for r in requests]
    if not requests:
        raise BatchError("there is nothing to ask")
    if len(set(ids)) != len(ids) or any(not _ID_RE.fullmatch(i or "") for i in ids):
        raise BatchError("every request needs its own id (letters, digits, '_' and '-', at most 64)")


# --- one after the other, through the gateway ------------------------------------------------------------------------------------


def run(requests: Sequence[BatchRequest], *, feature: str = "judge", tenant_id: str = "default", client: Any = None) -> Dict[str, BatchResult]:
    """Ask every request now, one after the other, through the gateway. A request that fails gives a result with an `error`."""
    _check(requests)
    out: Dict[str, BatchResult] = {}
    for request in requests:
        try:
            text = llm.complete(request.messages, feature=feature, tenant_id=tenant_id, client=client,
                                max_tokens=request.max_tokens, temperature=request.temperature)
            out[request.custom_id] = BatchResult(request.custom_id, text=text)
        except llm.LLMNotAllowed as exc:
            out[request.custom_id] = BatchResult(request.custom_id, error=f"not_allowed:{exc.decision.layer}")
        except llm_quota.QuotaExceeded as exc:
            out[request.custom_id] = BatchResult(request.custom_id, error=f"quota:{exc.limit}")
        except Exception as exc:  # noqa: BLE001 - one failed request does not stop the others
            out[request.custom_id] = BatchResult(request.custom_id, error=f"error:{type(exc).__name__}")
    return out


# --- the provider's batch API -----------------------------------------------------------------------------------------------------


def _profile_for(feature: str, tenant_id: str, profile_name: Optional[str]) -> llm_profiles.Profile:
    candidates = llm_profiles.chain(feature, tenant_id)
    if profile_name:
        candidates = [p for p in llm_profiles.all_profiles().values() if p.name == profile_name]
    for profile in candidates:
        if profile.batch and not llm_profiles.blocked(profile):
            return profile
    raise BatchError("no profile of this feature offers a batch API (set \"batch\": true on a profile that does), or production does not allow it")


def submit(requests: Sequence[BatchRequest], *, feature: str = "judge", tenant_id: str = "default", profile: Optional[str] = None,
           client: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Hand the requests to the provider's batch API and return the job at once."""
    _check(requests)
    decision = llm.check(feature, tenant_id)
    if not decision.allowed:
        raise BatchError(f"the LLM is not allowed for '{feature}': {decision.layer}")
    llm_quota.check(feature, tenant_id, (audit_log._ACTOR.get() or {}).get("key_fingerprint"))
    chosen = _profile_for(feature, tenant_id, profile)
    chat_client = client if client is not None else llm_profiles.make_client(chosen)
    lines = [json.dumps({"custom_id": r.custom_id, "method": "POST", "url": ENDPOINT,
                         "body": {"model": chosen.model, "messages": r.messages, "max_tokens": r.max_tokens, "temperature": r.temperature}},
                        ensure_ascii=False) for r in requests]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    try:
        uploaded = chat_client.files.create(file=("batch.jsonl", payload), purpose="batch")
        batch = chat_client.batches.create(input_file_id=uploaded.id, endpoint=ENDPOINT, completion_window="24h")
    except Exception as exc:  # noqa: BLE001
        raise BatchError(f"the provider refused the batch: {type(exc).__name__}") from None
    job = {"job_id": "job-" + uuid.uuid4().hex[:16], "feature": feature, "tenant_id": tenant_id, "profile": chosen.name,
           "provider_batch_id": batch.id, "input_file_id": uploaded.id, "status": str(getattr(batch, "status", "validating")),
           "request_ids": [r.custom_id for r in requests], "created_at": (now or datetime.now(timezone.utc)).isoformat(),
           "output_file_id": None, "collected": False,
           "caller": {k: (audit_log._ACTOR.get() or {}).get(k) for k in ("key_fingerprint", "role")}}
    _save(job)
    return job


def _client_of(job: Dict[str, Any], client: Any) -> Any:
    if client is not None:
        return client
    profile = llm_profiles.all_profiles().get(job["profile"])
    if profile is None:
        raise BatchError("the profile of this job is no longer defined")
    return llm_profiles.make_client(profile)


def poll(job_id: str, *, client: Any = None) -> Dict[str, Any]:
    """Ask the provider how far the batch is and keep the answer in the job."""
    job = get_job(job_id)
    if job["status"] in _DONE | _FAILED:
        return job
    try:
        batch = _client_of(job, client).batches.retrieve(job["provider_batch_id"])
    except BatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BatchError(f"the provider did not answer: {type(exc).__name__}") from None
    job["status"] = str(getattr(batch, "status", job["status"]))
    job["output_file_id"] = getattr(batch, "output_file_id", None) or job.get("output_file_id")
    counts = getattr(batch, "request_counts", None)
    if counts is not None:
        job["request_counts"] = {k: getattr(counts, k, None) for k in ("total", "completed", "failed")}
    _save(job)
    return job


def collect(job_id: str, *, client: Any = None, now: Optional[datetime] = None) -> Dict[str, BatchResult]:
    """Read the answers of a finished batch and write them to the usage log (once). An unfinished job is refused."""
    job = poll(job_id, client=client)
    if job["status"] in _FAILED:
        raise BatchError(f"the provider's batch is {job['status']}")
    if job["status"] not in _DONE or not job.get("output_file_id"):
        raise BatchError(f"the batch is not finished ({job['status']})")
    try:
        content = _client_of(job, client).files.content(job["output_file_id"])
        text = content.text if hasattr(content, "text") else str(content)
    except BatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BatchError(f"the answers could not be read: {type(exc).__name__}") from None
    results: Dict[str, BatchResult] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        custom_id = str(row.get("custom_id") or "")
        response = (row.get("response") or {})
        body = response.get("body") or {}
        if row.get("error") or response.get("status_code") != 200 or not body.get("choices"):
            results[custom_id] = BatchResult(custom_id, error="provider_error")
            continue
        usage = body.get("usage") or {}
        results[custom_id] = BatchResult(custom_id, text=str(body["choices"][0].get("message", {}).get("content") or ""),
                                         input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0))
    for missing in set(job["request_ids"]) - set(results):
        results[missing] = BatchResult(missing, error="no_answer")
    if not job.get("collected"):
        _record_usage(job, results, now)
        job["collected"] = True
        _save(job)
    return results


def _record_usage(job: Dict[str, Any], results: Dict[str, BatchResult], now: Optional[datetime]) -> None:
    profile = llm_profiles.all_profiles().get(job["profile"])
    factor = float(getattr(config, "LLM_BATCH_COST_FACTOR", 0.5))
    for result in results.values():
        if profile is None:
            break
        llm_usage.record_call(
            feature=job["feature"], tenant_id=job["tenant_id"], profile=profile.name, model=profile.model, host=profile.host,
            location=profile.location, outcome="ok" if not result.error else "error", input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, error_type=result.error, cost=profile.cost(result.input_tokens, result.output_tokens) * factor,
            now=now, batch=True,
        )
        if not result.error:
            llm_quota.note(job["tenant_id"], (job.get("caller") or {}).get("key_fingerprint"),
                           tokens=result.input_tokens + result.output_tokens,
                           cost=profile.cost(result.input_tokens, result.output_tokens) * factor)
