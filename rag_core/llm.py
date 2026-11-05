"""Whether an LLM may be used (four layers), and the one way to call it (step 4-1)."""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import config
from rag_core import llm_gateway, llm_profiles

FEATURES = ("generate", "rewrite", "verify", "judge")
LAYERS = ("global", "tenant", "feature", "endpoint")


class LLMNotAllowed(RuntimeError):
    """The policy refused the call; .decision says which layer."""

    def __init__(self, decision: "Decision"):
        super().__init__(f"LLM not allowed for {decision.feature}: {decision.layer}: {decision.reason}")
        self.decision = decision


@dataclass(frozen=True)
class Decision:
    allowed: bool
    feature: str
    tenant_id: str
    layer: str  # the layer that refused, "" when allowed
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _tenant(value: Any) -> str:
    return str(value or "").strip() or "default"


def _names(values: Any) -> List[str]:
    return [str(v).strip().lower() for v in (values or []) if str(v).strip()]


def master_enabled() -> bool:
    """LLM_ENABLED if it was set (a kill switch too), else the historical switch CHAT_GENERATION_MODE=llm."""
    explicit = getattr(config, "LLM_ENABLED", None)
    if explicit is None:
        return config.resolve_chat_generation_mode() == "llm"
    return bool(explicit)


def endpoint_host() -> Optional[str]:
    url = str(getattr(config, "OPENAI_BASE_URL", "") or "").strip()
    if not url:
        return None
    return (urlparse(url if "//" in url else "//" + url).hostname or "").lower() or None


def external_endpoint() -> bool:
    """True when the LLM endpoint is the vendor default or a host that is not loopback, private or listed as internal."""
    host = endpoint_host()
    if host is None:
        return True
    if host in _names(getattr(config, "LLM_INTERNAL_HOSTS", [])) or host == "localhost":
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_private)


def is_production() -> bool:
    return str(os.getenv("APP_ENV", "")).strip().lower() == "production"


def external_blocked() -> bool:
    return is_production() and external_endpoint() and not bool(getattr(config, "LLM_EXTERNAL_ALLOWED", False))


def _endpoint_blocked(feature: str, tenant_id: str) -> bool:
    """Production refuses an external endpoint unless LLM_EXTERNAL_ALLOWED. With model profiles (rag_core/llm_profiles.py)
    the feature is refused only when every profile of its route is blocked; a blocked profile is skipped otherwise."""
    if not llm_profiles.configured():
        return external_blocked()
    return all(llm_profiles.blocked(p) for p in llm_profiles.chain(feature, tenant_id))


def production_problem() -> Optional[str]:
    """The start-up problem for the production guard, or None."""
    if llm_profiles.configured():
        problems = llm_profiles.validate()
        if problems:
            return f"LLM_PROFILES_FILE: {problems[0]}"
    if master_enabled() and _endpoint_blocked("generate", "default"):
        return ("LLM_EXTERNAL_ALLOWED=true, or an internal endpoint (the LLM endpoint is external; "
                "document text would leave the deployment)")
    return None


def check(feature: str, tenant_id: str = "default") -> Decision:
    tenant = _tenant(tenant_id)

    def refuse(layer: str, reason: str) -> Decision:
        return Decision(False, feature, tenant, layer, reason)

    if feature not in FEATURES:
        return refuse("feature", f"unknown feature '{feature}'")
    if not master_enabled():
        return refuse("global", "the LLM is off (LLM_ENABLED / CHAT_GENERATION_MODE)")
    if tenant.lower() in _names(getattr(config, "LLM_TENANT_DENYLIST", [])):
        return refuse("tenant", "the tenant is on LLM_TENANT_DENYLIST")
    allow = _names(getattr(config, "LLM_TENANT_ALLOWLIST", []))
    if allow and tenant.lower() not in allow:
        return refuse("tenant", "the tenant is not on LLM_TENANT_ALLOWLIST")
    if feature not in _names(getattr(config, "LLM_FEATURES", ["generate"])):
        return refuse("feature", f"'{feature}' is not in LLM_FEATURES")
    if _endpoint_blocked(feature, tenant):
        return refuse("endpoint", "the endpoint is external and LLM_EXTERNAL_ALLOWED is not true")
    return Decision(True, feature, tenant, "", "")


def generation_mode(tenant_id: str = "default") -> str:
    """"llm" only when the configured mode is llm and the policy allows "generate" for the tenant; else "extractive"."""
    if config.resolve_chat_generation_mode() != "llm":
        return "extractive"
    return "llm" if check("generate", tenant_id).allowed else "extractive"


# --- calling ----------------------------------------------------------------------------------------

_LOCK = threading.Lock()
_STATS: Dict[str, Dict[str, int]] = {}


def _count(feature: str, key: str) -> None:
    with _LOCK:
        _STATS.setdefault(feature, {"calls": 0, "errors": 0, "refused": 0})[key] += 1


def stats() -> Dict[str, Dict[str, int]]:
    with _LOCK:
        return {feature: dict(values) for feature, values in _STATS.items()}


def reset_stats() -> None:
    with _LOCK:
        _STATS.clear()


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        try:
            code = int(status)
        except (TypeError, ValueError):
            code = None
        if code is not None:
            return code == 429 or 500 <= code <= 599
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


def complete(
    messages: List[Dict[str, str]],
    *,
    feature: str,
    tenant_id: str = "default",
    client: Any = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.0,
) -> str:
    """The text of one chat completion for `feature`, after the policy check. LLMNotAllowed when refused."""
    decision = check(feature, tenant_id)
    if not decision.allowed:
        _count(feature, "refused")
        raise LLMNotAllowed(decision)
    attempts = 1 + max(0, int(getattr(config, "CHAT_COMPLETION_MAX_RETRIES", 1)))
    backoff = float(getattr(config, "CHAT_COMPLETION_RETRY_BACKOFF_SECONDS", 1.0))

    def request(chat_client: Any, profile: llm_profiles.Profile) -> Any:
        limit = int(max_tokens or profile.max_tokens or getattr(config, "LLM_FEATURE_MAX_TOKENS", 512))
        timeout = float(profile.timeout_seconds or getattr(config, "CHAT_COMPLETION_TIMEOUT_SECONDS", 30.0))
        for attempt in range(attempts):
            try:
                return chat_client.chat.completions.create(
                    model=profile.model or config.CHAT_MODEL, messages=messages, temperature=temperature,
                    timeout=timeout, max_tokens=limit,
                )
            except Exception as exc:  # noqa: BLE001 - classified here; the gateway counts what is left after the retries
                if attempt >= attempts - 1 or not _retryable(exc):
                    raise
                time.sleep(backoff * (2 ** attempt))
        raise RuntimeError("unreachable: completion retry loop exhausted")

    try:
        response = llm_gateway.invoke(
            feature, tenant_id, request, client=client,
            estimate_input="".join(str(m.get("content") or "") for m in messages),
            user_visible_text=lambda r: r.choices[0].message.content or "",
        )
    except Exception:
        _count(feature, "errors")
        raise
    _count(feature, "calls")
    return response.choices[0].message.content or ""
