"""External integrations: one registry of what may use the network, and why it may not (S1).

Three layers decide whether an integration may be used. All of them must allow it:

  1. environment  EXTERNAL_INTEGRATIONS lists the names that may be used at all (default: none). The console cannot widen it.
  2. switch       the console's global switch `integration.<name>.enabled` (default: off, so a person turns it on, with a reason)
  3. tenant       `integration.<name>.tenant_off` set for one tenant takes that tenant out

Gated integrations (the three layers): sharepoint, outlook_draft, smtp, slack. Two more are reported here but keep the
switches they already have: llm (rag_core/llm.py, four layers of its own) and oidc (its own setting).

`require_egress` is the check at the one place where a request really leaves (a transport function): it knows no tenant,
so it applies layers 1 and 2. `require` is the check at the entry of a service, where the tenant is known: all three.
A refusal carries a reason code, never a host name, a key or a URL.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional

import config
from rag_core import runtime_settings

GATED = ("sharepoint", "outlook_draft", "smtp", "slack")
REPORTED = ("llm", "oidc")
INTEGRATIONS = ("llm", "sharepoint", "outlook_draft", "smtp", "slack", "oidc")

# State of one integration.
DISABLED = "disabled"
NOT_CONFIGURED = "not_configured"
FAILING = "failing"  # allowed and set up, but its breaker is open: the answers are extractive meanwhile
OK = "ok"

# Reason codes of a refusal (a layer said no) and the layer that said it.
ENV_NOT_ALLOWED = "env_not_allowed"
SWITCH_OFF = "switch_off"
TENANT_OFF = "tenant_off"

_LABELS = {
    "llm": "LLM（AI による文章生成）",
    "sharepoint": "SharePoint 同期",
    "outlook_draft": "Outlook 下書き",
    "smtp": "メール通知（SMTP）",
    "slack": "Slack 通知",
    "oidc": "シングルサインオン（OIDC）",
}


class IntegrationDisabled(RuntimeError):
    """A layer refused the use of an integration. `.status` says which one and why."""

    def __init__(self, status: "Status"):
        super().__init__(f"integration '{status.name}' is off: {status.reason}")
        self.status = status


@dataclass(frozen=True)
class Status:
    name: str
    label: str
    state: str  # disabled | not_configured | failing | ok
    enabled: bool  # every layer allows it (it may use the network)
    configured: bool  # the settings it needs are present
    layer: str  # the layer that said no: environment | switch | tenant | policy | "" when none did
    reason: str  # a reason code, "" when none

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _switch_key(name: str) -> str:
    return f"integration.{name}.enabled"


def _tenant_key(name: str) -> str:
    return f"integration.{name}.tenant_off"


def env_allowed(name: str) -> bool:
    allowed = [str(n).strip().lower() for n in getattr(config, "EXTERNAL_INTEGRATIONS", []) or []]
    return name in allowed or "all" in allowed


def switch_on(name: str) -> bool:
    return runtime_settings.get(_switch_key(name), False) is True


def tenant_off(name: str, tenant_id: Optional[str]) -> bool:
    if not tenant_id:
        return False
    entry = runtime_settings.entry(_tenant_key(name), tenant_id=str(tenant_id))
    return bool(entry and entry.get("value") is True)


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _graph_configured(prefix: str) -> bool:
    if _env(prefix + "_ACCESS_TOKEN"):
        return True
    return all(_env(f"{prefix}_{part}") for part in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"))


def _configured_checks() -> Dict[str, Callable[[], bool]]:
    return {
        "sharepoint": lambda: _graph_configured("SHAREPOINT"),
        "outlook_draft": lambda: _graph_configured("OUTLOOK"),
        "smtp": lambda: bool(_env("ALERT_SMTP_HOST") and _env("ALERT_EMAIL_TO") and _env("ALERT_EMAIL_FROM")),
        "slack": lambda: bool(_env("ALERT_SLACK_WEBHOOK")),
    }


# Probes of the integrations that keep their own switches: name -> function(tenant_id) -> Status. The web layer registers
# "oidc" at start-up (this module does not import it); "llm" is built in.
_PROBES: Dict[str, Callable[[Optional[str]], Status]] = {}


def register_probe(name: str, probe: Callable[[Optional[str]], Status]) -> None:
    _PROBES[name] = probe


def _llm_status(tenant_id: Optional[str]) -> Status:
    from rag_core import llm

    from rag_core import llm_gateway

    decision = llm.check("generate", tenant_id or "default")
    if not decision.allowed:
        return Status("llm", _LABELS["llm"], DISABLED, False, True, "policy", f"{decision.layer}")
    if not llm_gateway.llm_available("generate", tenant_id):
        # Allowed, but every profile of the route has an open breaker: answers stay extractive until one recovers.
        return Status("llm", _LABELS["llm"], FAILING, True, True, "breaker", "breaker_open")
    return Status("llm", _LABELS["llm"], OK, True, True, "", "")


def status(name: str, tenant_id: Optional[str] = None) -> Status:
    """The state of one integration for `tenant_id` (or installation-wide)."""
    if name not in INTEGRATIONS:
        raise KeyError(name)
    label = _LABELS[name]
    if name == "llm":
        return _llm_status(tenant_id)
    if name in _PROBES:
        return _PROBES[name](tenant_id)
    if name == "oidc":
        return Status(name, label, DISABLED, False, False, "environment", "not_reported")
    configured = _configured_checks()[name]()
    if not env_allowed(name):
        return Status(name, label, DISABLED, False, configured, "environment", ENV_NOT_ALLOWED)
    if not switch_on(name):
        return Status(name, label, DISABLED, False, configured, "switch", SWITCH_OFF)
    if tenant_off(name, tenant_id):
        return Status(name, label, DISABLED, False, configured, "tenant", TENANT_OFF)
    if not configured:
        return Status(name, label, NOT_CONFIGURED, True, False, "", "not_configured")
    return Status(name, label, OK, True, True, "", "")


def all_status(tenant_id: Optional[str] = None) -> List[Status]:
    return [status(name, tenant_id) for name in INTEGRATIONS]


def require(name: str, tenant_id: Optional[str] = None) -> None:
    """Raise IntegrationDisabled unless every layer allows `name` for the tenant. Missing settings are not checked here:
    the caller reports its own configuration error."""
    if name not in GATED:
        raise KeyError(name)
    current = status(name, tenant_id)
    if not current.enabled:
        raise IntegrationDisabled(current)


def require_egress(name: str) -> None:
    """The check at the point where a request really leaves: the environment layer and the global switch."""
    if name not in GATED:
        raise KeyError(name)
    if not env_allowed(name):
        raise IntegrationDisabled(Status(name, _LABELS[name], DISABLED, False, True, "environment", ENV_NOT_ALLOWED))
    if not switch_on(name):
        raise IntegrationDisabled(Status(name, _LABELS[name], DISABLED, False, True, "switch", SWITCH_OFF))


def set_switch(name: str, enabled: bool, *, by: str, reason: str, tenant_id: Optional[str] = None) -> Dict[str, object]:
    """Change a console switch. Without a tenant it is the global switch; with one it is that tenant's restriction
    (`enabled=False` takes the tenant out, `enabled=True` removes the restriction). The environment layer is not changed here."""
    if name not in GATED:
        raise runtime_settings.SettingsError(f"'{name}' has no console switch")
    if tenant_id:
        return runtime_settings.set_value(_tenant_key(name), (True if not enabled else None), by=by, reason=reason, tenant_id=tenant_id)
    return runtime_settings.set_value(_switch_key(name), bool(enabled), by=by, reason=reason)


def startup_warnings() -> List[str]:
    """Integrations whose settings are present but that are off. Before the switches existed, the settings alone were
    enough to use them; this tells the operator what the new default (off) stopped."""
    warnings = []
    for name in GATED:
        current = status(name)
        if current.configured and not current.enabled:
            warnings.append(f"{name}: configured but off ({current.reason}); turn it on with EXTERNAL_INTEGRATIONS and the console switch")
    return warnings
