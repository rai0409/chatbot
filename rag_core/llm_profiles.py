"""Model profiles: which model answers, where it runs, and what to try when it does not (S2).

A deployment may use a model on its own machine, a low-cost model of a cloud provider, or both. The profiles are data of
the deployment (a JSON file named by LLM_PROFILES_FILE, kept outside the code; see config/deploy/llm_profiles.example.json):

  {"profiles": {"local": {"base_url": "http://localhost:11434/v1", "model": "<model name>", ...}, "cloud": {...}},
   "routes":   {"generate": ["local", "cloud"], "rewrite": ["local"], "verify": ["local"], "judge": ["cloud"]},
   "tenants":  {"tenant_a": {"generate": ["cloud"]}}}

A route is an ordered chain: the first profile is tried first, the next one when it fails or its breaker is open. When the
whole chain fails the answer stays extractive. Without a file there is one implicit profile made of the old settings
(OPENAI_BASE_URL, CHAT_MODEL, OPENAI_API_KEY), so a deployment that sets nothing new behaves as before.

Every profile speaks the OpenAI-compatible chat API (what a local server such as vLLM or Ollama offers, and what the cloud
providers offer next to their own API). A key is never in the file: `api_key_env` names the environment variable that holds it.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import config
from rag_core import runtime_settings

FEATURES = ("generate", "rewrite", "verify", "judge")
PROVIDERS = ("openai", "anthropic")
ANTHROPIC_HOST = "api.anthropic.com"
IMPLICIT = "default"
_NAME_RE_MAX = 40


class ProfileError(ValueError):
    """The profiles file is not valid. The message names the field, never a key."""


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    timeout_seconds: Optional[float] = None
    max_tokens: Optional[int] = None
    location: str = "external"  # local (loopback, private, or a host listed in LLM_INTERNAL_HOSTS) | external
    cost_input_per_million: float = 0.0
    cost_output_per_million: float = 0.0
    implicit: bool = False
    batch: bool = False  # the endpoint offers the provider's batch API (rag_core/llm_batch.py)
    provider: str = "openai"  # openai: an OpenAI-compatible endpoint (also local servers) | anthropic: Claude through its own API
    effort: str = ""  # anthropic only: low | medium | high | xhigh | max (empty: the model's default)
    thinking: str = ""  # anthropic only: adaptive | disabled (empty: the model's default)

    @property
    def host(self) -> str:
        if not self.base_url and self.provider == "anthropic":
            return ANTHROPIC_HOST
        return host_of(self.base_url)

    @property
    def external(self) -> bool:
        return self.location != "local"

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.cost_input_per_million + output_tokens * self.cost_output_per_million) / 1_000_000.0


def host_of(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    return (urlparse(url if "//" in url else "//" + url).hostname or "").lower()


def is_external(url: str) -> bool:
    """True when the endpoint is the vendor default (no URL) or a host that is not loopback, private or listed internal."""
    host = host_of(url)
    if not host:
        return True
    internal = [str(h).strip().lower() for h in getattr(config, "LLM_INTERNAL_HOSTS", ["localhost"]) or []]
    if host in internal or host == "localhost":
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_private)


def _file_path() -> Optional[Path]:
    raw = str(getattr(config, "LLM_PROFILES_FILE", "") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else Path(config.BASE_DIR) / path


_CACHE: Dict[str, Any] = {"key": None, "value": None}


def _read() -> Optional[Dict[str, Any]]:
    path = _file_path()
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError as exc:
        raise ProfileError("LLM_PROFILES_FILE does not name a readable file") from exc
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _CACHE["key"] == key:
        return _CACHE["value"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ProfileError("the profiles file is not valid JSON") from exc
    parsed = _parse(data)
    _CACHE["key"], _CACHE["value"] = key, parsed
    return parsed


def _num(value: Any, field: str, *, minimum: float = 0.0) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ProfileError(f"'{field}' is not a number") from None
    if number < minimum:
        raise ProfileError(f"'{field}' is below {minimum}")
    return number


def _parse(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict) or not data["profiles"]:
        raise ProfileError("the profiles file needs a non-empty 'profiles' object")
    profiles: Dict[str, Profile] = {}
    for name, raw in data["profiles"].items():
        if not isinstance(raw, dict) or not str(name).strip() or len(str(name)) > _NAME_RE_MAX:
            raise ProfileError(f"profile '{str(name)[:_NAME_RE_MAX]}' is not valid")
        model = str(raw.get("model") or "").strip()
        if not model:
            raise ProfileError(f"profile '{name}' needs a 'model'")
        base_url = str(raw.get("base_url") or "").strip()
        provider = str(raw.get("provider") or "openai").strip().lower()
        if provider not in PROVIDERS:
            raise ProfileError(f"profile '{name}': 'provider' is one of: {', '.join(PROVIDERS)}")
        effort, thinking = str(raw.get("effort") or "").strip().lower(), str(raw.get("thinking") or "").strip().lower()
        if provider == "anthropic":
            if not str(raw.get("api_key_env") or "").strip():
                raise ProfileError(f"profile '{name}': a profile with provider 'anthropic' needs 'api_key_env'")
            if bool(raw.get("batch", False)):
                raise ProfileError(f"profile '{name}': the batch API is not supported for provider 'anthropic' (use mode 'sync')")
            if effort and effort not in ("low", "medium", "high", "xhigh", "max"):
                raise ProfileError(f"profile '{name}': 'effort' is one of: low, medium, high, xhigh, max")
            if thinking and thinking not in ("adaptive", "disabled"):
                raise ProfileError(f"profile '{name}': 'thinking' is one of: adaptive, disabled")
        elif effort or thinking:
            raise ProfileError(f"profile '{name}': 'effort' and 'thinking' are for provider 'anthropic' only")
        cost = raw.get("cost") or {}
        profiles[str(name)] = Profile(
            name=str(name), base_url=base_url, model=model, api_key_env=str(raw.get("api_key_env") or "").strip(),
            timeout_seconds=_num(raw.get("timeout_seconds"), f"{name}.timeout_seconds", minimum=1.0),
            max_tokens=int(_num(raw.get("max_tokens"), f"{name}.max_tokens", minimum=1.0) or 0) or None,
            location="local" if not is_external(base_url) else "external", batch=bool(raw.get("batch", False)),
            cost_input_per_million=_num(cost.get("input_per_million"), f"{name}.cost.input_per_million") or 0.0,
            cost_output_per_million=_num(cost.get("output_per_million"), f"{name}.cost.output_per_million") or 0.0,
            provider=provider, effort=effort, thinking=thinking,
        )

    def chains(raw: Any, where: str) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for feature, names in (raw or {}).items():
            if feature not in FEATURES:
                raise ProfileError(f"{where}: '{feature}' is not a feature")
            if not isinstance(names, list) or not names or any(str(n) not in profiles for n in names):
                raise ProfileError(f"{where}.{feature}: every name must be a profile")
            out[feature] = [str(n) for n in names]
        return out

    routes = chains(data.get("routes"), "routes")
    tenants = {str(t): chains(r, f"tenants.{t}") for t, r in (data.get("tenants") or {}).items()}
    # Tiers: named sets of routes ("standard", "premium", ...) that the console assigns to a tenant, a role or one person
    # (rag_core/llm_assignments.py). A tier names only the features it changes; the others use the routes above.
    tiers = {str(n): chains(r, f"tiers.{n}") for n, r in (data.get("tiers") or {}).items()}
    return {"profiles": profiles, "routes": routes, "tenants": tenants, "tiers": tiers}


def validate() -> List[str]:
    """Problems of the profiles file (empty list: none, or no file)."""
    try:
        _read()
    except ProfileError as exc:
        return [str(exc)]
    return []


def configured() -> bool:
    """Is there a profiles file? (Without one, the implicit profile of the old settings is the only one.)"""
    return _file_path() is not None


def implicit_profile() -> Profile:
    base_url = str(getattr(config, "OPENAI_BASE_URL", "") or "").strip()
    timeout = getattr(config, "CHAT_COMPLETION_TIMEOUT_SECONDS", None)
    return Profile(
        name=IMPLICIT, base_url=base_url, model=str(getattr(config, "CHAT_MODEL", "") or ""),
        timeout_seconds=float(timeout) if timeout is not None else None,
        location="local" if not is_external(base_url) else "external", implicit=True,
    )


def all_profiles() -> Dict[str, Profile]:
    parsed = _read()
    return dict(parsed["profiles"]) if parsed else {IMPLICIT: implicit_profile()}


def tiers() -> List[str]:
    """The names of the tiers of the profiles file (none without a file)."""
    parsed = _read()
    return list(parsed["tiers"]) if parsed else []


TIER_KEY = "llm.tier"
ROLE_TIER_PREFIX = "llm.role_tier."
USER_TIER_PREFIX = "llm.user_tier."


def assigned_tier(tenant_id: Optional[str] = None, user: Optional[str] = None, role: Optional[str] = None) -> Optional[str]:
    """The tier that applies to this caller, or None. Most specific first: the person, the role, the tenant, everyone. A name that is
    no longer in the profiles file is ignored (the assignment stays in the console until someone removes it)."""
    parsed = _read()
    if parsed is None or not parsed["tiers"]:
        return None
    candidates = []
    if user:
        candidates.append(runtime_settings.get(USER_TIER_PREFIX + str(user)))
    if role:
        candidates.append(runtime_settings.get(ROLE_TIER_PREFIX + str(role)))
    candidates.append(runtime_settings.get(TIER_KEY, None, tenant_id=tenant_id))
    return next((c for c in candidates if isinstance(c, str) and c in parsed["tiers"]), None)


def assignment_digest() -> str:
    """A digest of every tier assignment, for the answer cache key (an assignment changes which model answers)."""
    data = runtime_settings.load()
    rows = [(k, v.get("value")) for k, v in sorted(data["global"].items()) if k == TIER_KEY or k.startswith((ROLE_TIER_PREFIX, USER_TIER_PREFIX))]
    for tenant, entries in sorted(data["tenants"].items()):
        rows += [(f"{tenant}/{k}", v.get("value")) for k, v in sorted(entries.items()) if k == TIER_KEY]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:12] if rows else ""


def chain(feature: str, tenant_id: Optional[str] = None, user: Optional[str] = None, role: Optional[str] = None) -> List[Profile]:
    """The profiles to try for `feature`, in order. The tier assigned to the caller wins, then a tenant's own route, then the routes of
    the file; a feature without a route uses the first profile of the file (or the implicit one)."""
    parsed = _read()
    if parsed is None:
        return [implicit_profile()]
    tier = assigned_tier(tenant_id, user, role)
    names = (parsed["tiers"].get(tier, {}).get(feature) if tier else None) \
        or (parsed["tenants"].get(str(tenant_id or "")) or {}).get(feature) or parsed["routes"].get(feature)
    if not names:
        names = [next(iter(parsed["profiles"]))]
    return [parsed["profiles"][n] for n in names]


def fingerprint() -> str:
    """A digest of what decides which model answers, for the answer cache key (empty without a file)."""
    path = _file_path()
    if path is None:
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "unreadable"


def make_client(profile: Profile):
    """A client for the profile with the OpenAI client's interface: an OpenAI-compatible one, or (provider "anthropic") Claude through
    its own API (rag_core/anthropic_client.py). The key comes from the environment variable the profile names."""
    if profile.implicit:
        from rag_core.utils import ensure_openai_client

        return ensure_openai_client(base_url=profile.base_url or None)
    key = str(os.getenv(profile.api_key_env, "") or "").strip() if profile.api_key_env else ""
    if profile.provider == "anthropic":
        if not key:
            raise RuntimeError(f"the API key of profile '{profile.name}' is not set")
        from rag_core.anthropic_client import AnthropicChatClient

        return AnthropicChatClient(api_key=key, base_url=profile.base_url, effort=profile.effort, thinking=profile.thinking)
    if not key:
        if profile.external:
            raise RuntimeError(f"the API key of profile '{profile.name}' is not set")
        key = "not-needed"  # a local server that checks no key (the SDK wants a non-empty one)
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("OpenAI SDK is required") from exc
    return OpenAI(base_url=profile.base_url, api_key=key) if profile.base_url else OpenAI(api_key=key)


def blocked(profile: Profile) -> bool:
    """In production an external endpoint needs LLM_EXTERNAL_ALLOWED=true; without it the profile is not used."""
    production = str(os.getenv("APP_ENV", "")).strip().lower() == "production"
    return production and profile.external and not bool(getattr(config, "LLM_EXTERNAL_ALLOWED", False))
