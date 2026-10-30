"""The one way an LLM is called: profiles, breaker, usage (S2).

`invoke` runs one request for a feature and a tenant. For each profile of the route, in order:

  1. the profile's breaker must allow a call (open: skipped at once, nothing is waited for)
  2. the request is made with that profile's model and client
  3. the result is recorded (usage line, counters) and the breaker learns the outcome

The first profile that answers wins. When none does, the last error is raised (or LLMUnavailable when every breaker was
open), and the callers keep their existing handling: the answer stays extractive. The retries of one request belong to the
request itself (`do_call`); the breaker counts the outcome after them.

The policy check (rag_core/llm.py `check`) stays outside: it says whether an LLM may be used at all. The gateway says
which one, and whether it is up.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator, List, Optional

import config
from rag_core import audit_log, circuit_breaker, llm_profiles, llm_quota, llm_usage
from rag_core.llm_profiles import Profile

_METRICS: List[Callable[[str, Optional[str]], None]] = []


class LLMUnavailable(RuntimeError):
    """No profile of the route could be called (every breaker is open)."""


def set_metrics_hook(hook: Optional[Callable[[str, Optional[str]], None]]) -> None:
    """hook(name, label): the web layer passes its counter here (this module does not import it)."""
    _METRICS.clear()
    if hook is not None:
        _METRICS.append(hook)


def _count(name: str, label: Optional[str] = None) -> None:
    for hook in _METRICS:
        try:
            hook(name, label)
        except Exception:  # noqa: BLE001 - a counter never breaks a call
            pass


def breaker_enabled() -> bool:
    return bool(getattr(config, "LLM_BREAKER_ENABLED", True))


def breaker_for(profile: Profile) -> circuit_breaker.CircuitBreaker:
    return circuit_breaker.get(
        f"llm:{profile.name}",
        threshold=int(getattr(config, "LLM_BREAKER_FAILURE_THRESHOLD", 3)),
        open_seconds=float(getattr(config, "LLM_BREAKER_OPEN_SECONDS", 60.0)),
        probes=int(getattr(config, "LLM_BREAKER_PROBES", 1)),
    )


def install() -> None:
    """Called once at start-up: the breaker's changes of state go to the usage log."""
    circuit_breaker.add_listener(llm_usage.record_breaker)


def _counts_as_failure(exc: BaseException) -> bool:
    """A request the endpoint refused as malformed (400, 422) says nothing about whether the endpoint is up."""
    status = getattr(exc, "status_code", None)
    try:
        return int(status) not in (400, 422) if status is not None else True
    except (TypeError, ValueError):
        return True


def _usage_of(result: Any) -> Optional[tuple]:
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    try:
        return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None


def _record(profile: Profile, feature: str, tenant_id: str, outcome: str, started: float, *, tokens: Optional[tuple] = None,
            estimate: tuple = (0, 0), error_type: str = "") -> None:
    estimated = tokens is None
    input_tokens, output_tokens = estimate if estimated else tokens
    if outcome != "ok":
        input_tokens = output_tokens = 0
    llm_usage.record_call(
        feature=feature, tenant_id=tenant_id, profile=profile.name, model=profile.model, host=profile.host,
        location=profile.location, outcome=outcome, input_tokens=input_tokens, output_tokens=output_tokens,
        estimated=estimated and outcome == "ok", latency_ms=int((time.perf_counter() - started) * 1000),
        error_type=error_type, cost=profile.cost(input_tokens, output_tokens),
    )
    _count("llm_calls_total", f"{feature}:{outcome}")
    if outcome in ("ok", "error"):  # a call that reached the endpoint counts against the daily limits
        llm_quota.note(tenant_id, _caller().get("key_fingerprint"), tokens=input_tokens + output_tokens,
                       cost=profile.cost(input_tokens, output_tokens))


def _caller() -> dict:
    """Who is asking: the fingerprint of the key (or of the person with single sign-on) and the role, as the web layer set them."""
    return audit_log._ACTOR.get() or {}


def _usable(feature: str, tenant_id: Optional[str]) -> List[Profile]:
    """The route of `feature` for this caller (the tier assigned to them, see llm_assignments.py) without the profiles that
    production does not allow (an external one without LLM_EXTERNAL_ALLOWED)."""
    caller = _caller()
    chain = llm_profiles.chain(feature, tenant_id, caller.get("key_fingerprint"), caller.get("role"))
    return [p for p in chain if not llm_profiles.blocked(p)]


def _check_quota(feature: str, tenant_id: str) -> None:
    """Refuse the call, before anything is made, when a daily limit is reached (rag_core/llm_quota.py). The answer stays extractive."""
    try:
        llm_quota.check(feature, tenant_id, _caller().get("key_fingerprint"))
    except llm_quota.QuotaExceeded as exc:
        llm_usage.record_call(feature=feature, tenant_id=tenant_id, profile="", model="", host="", location="", outcome="refused",
                              error_type=f"QuotaExceeded:{exc.limit}")
        _count("llm_calls_total", f"{feature}:refused")
        _count("llm_quota_exceeded_total", exc.limit)
        raise


def _client_for(profile: Profile, injected: Any, _first: bool) -> Any:
    # A client handed in by the caller (a test, or a request that already made one from the old settings) serves the
    # implicit profile only. A profile of a profiles file is its own endpoint and makes its own client.
    if injected is not None and profile.implicit:
        return injected
    return llm_profiles.make_client(profile)


def invoke(
    feature: str, tenant_id: str, do_call: Callable[[Any, Profile], Any], *, client: Any = None,
    estimate_input: str = "", user_visible_text: Optional[Callable[[Any], str]] = None,
) -> Any:
    """Make one request through the route of `feature` for `tenant_id`.

    `do_call(client, profile)` performs it (with its own retries) and returns the provider's answer.
    `estimate_input` is the prompt text, used to estimate the tokens when the answer carries no usage;
    `user_visible_text(result)` gives the answer text for the same estimate."""
    _check_quota(feature, tenant_id)
    chain = _usable(feature, tenant_id)
    last_error: Optional[BaseException] = None
    tried = 0
    for index, profile in enumerate(chain):
        breaker = breaker_for(profile)
        if breaker_enabled() and not breaker.allow():
            _record(profile, feature, tenant_id, "breaker_open", time.perf_counter())
            _count("llm_breaker_open_total", profile.name)
            continue
        tried += 1
        started = time.perf_counter()
        try:
            result = do_call(_client_for(profile, client, index == 0), profile)
        except Exception as exc:  # noqa: BLE001 - classified; the caller decides what a failure means for the answer
            if breaker_enabled() and _counts_as_failure(exc):
                breaker.record_failure()
            _record(profile, feature, tenant_id, "error", started, error_type=type(exc).__name__)
            last_error = exc
            continue
        if breaker_enabled():
            breaker.record_success()
        try:
            text = user_visible_text(result) if user_visible_text else ""
        except Exception:  # noqa: BLE001 - an odd answer shape only costs the estimate
            text = ""
        _record(profile, feature, tenant_id, "ok", started, tokens=_usage_of(result),
                estimate=(llm_usage.estimate_tokens(estimate_input), llm_usage.estimate_tokens(text)))
        return result
    if last_error is not None:
        raise last_error
    raise LLMUnavailable(f"no LLM profile could be called for '{feature}' ({tried} tried; the breakers are open)")


def invoke_stream(
    feature: str, tenant_id: str, make_stream: Callable[[Any, Profile], Iterator[str]], *, client: Any = None,
    estimate_input: str = "",
) -> Iterator[str]:
    """Like `invoke` for a stream of text. Another profile is tried only while nothing has been sent yet: once the first
    piece is out, a failure ends the stream (a different answer in the middle would be a different text)."""
    _check_quota(feature, tenant_id)
    chain = _usable(feature, tenant_id)
    last_error: Optional[BaseException] = None
    for index, profile in enumerate(chain):
        breaker = breaker_for(profile)
        if breaker_enabled() and not breaker.allow():
            _record(profile, feature, tenant_id, "breaker_open", time.perf_counter())
            _count("llm_breaker_open_total", profile.name)
            continue
        started = time.perf_counter()
        emitted: List[str] = []
        try:
            for piece in make_stream(_client_for(profile, client, index == 0), profile):
                emitted.append(piece)
                yield piece
        except Exception as exc:  # noqa: BLE001
            if breaker_enabled() and _counts_as_failure(exc):
                breaker.record_failure()
            _record(profile, feature, tenant_id, "error", started, error_type=type(exc).__name__)
            last_error = exc
            if emitted:
                raise
            continue
        if breaker_enabled():
            breaker.record_success()
        _record(profile, feature, tenant_id, "ok", started, estimate=(llm_usage.estimate_tokens(estimate_input),
                                                                       llm_usage.estimate_tokens("".join(emitted))))
        return
    if last_error is not None:
        raise last_error
    raise LLMUnavailable(f"no LLM profile could be called for '{feature}' (the breakers are open)")


def status() -> dict:
    """The state of every profile's breaker, for the console and the integration registry."""
    out = {}
    for name, profile in llm_profiles.all_profiles().items():
        out[name] = {"breaker": breaker_for(profile).state() if breaker_enabled() else "disabled", "model": profile.model,
                     "location": profile.location, "host": profile.host}
    return out


def llm_available(feature: str = "generate", tenant_id: Optional[str] = None) -> bool:
    """Would a call for `feature` find a profile whose breaker lets it through? (It does not use up a trial call.)"""
    if not breaker_enabled():
        return True
    return any(breaker_for(p).state() != circuit_breaker.OPEN for p in _usable(feature, tenant_id))
