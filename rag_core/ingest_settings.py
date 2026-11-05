"""Ingestion parameters that an administrator adjusts from the console (upload limits, conversion limits, OCR, tables,
heading detection, header/footer removal, chunk sizes).

Each parameter is one environment variable the code already reads. Here it gets a definition (type, bounds, group,
documented default) and an override store (rag_core/runtime_settings: a reason and an author for every change, an
audit trail, a shared file, so every worker process sees it):

    value = override (console)  >  environment of the deployment  >  default of the code

sync_environ() makes an override visible to the code that reads os.environ (this process, and, through
worker_environment(), the conversion worker processes): it sets the variable, and a reset gives back the value the
deployment had at start. The check costs one stat of the settings file when nothing changed.

Global only: a tenant cannot have its own upload limit or chunk size here.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import config
from rag_core import runtime_settings as rs

SettingsError = rs.SettingsError
Conflict = rs.Conflict

KEY_PREFIX = "ingest."
GROUPS = ("upload", "conversion", "pdf", "preprocess", "chunking", "review")
MiB, GiB = 1024 * 1024, 1024 ** 3


@dataclass(frozen=True)
class Setting:
    name: str  # the environment variable
    kind: str  # int | float | bool | choice
    default: Any  # what the code does without any setting (kept equal to the code by a test)
    group: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = ()
    unit: str = ""
    restart: str = ""  # "workers": the conversion workers are restarted when it changes
    config_attr: str = ""  # also set on the config module (for a value the code reads from config at call time)


SETTINGS: Tuple[Setting, ...] = (
    # --- upload
    Setting("INGEST_UPLOAD_MAX_BYTES", "int", 50 * MiB, "upload", MiB, 2 * GiB, unit="bytes"),
    Setting("INGEST_UPLOAD_TENANT_QUOTA_BYTES", "int", 5 * GiB, "upload", 0, 10 * 1024 * GiB, unit="bytes"),
    Setting("INGEST_UPLOAD_MAX_CONCURRENT", "int", 4, "upload", 0, 256),
    Setting("INGEST_UPLOAD_RETENTION_DAYS", "int", 30, "upload", 0, 3650, unit="days"),
    Setting("INGEST_UPLOAD_MAX_ZIP_ENTRIES", "int", 5000, "upload", 100, 100000),
    Setting("INGEST_UPLOAD_MAX_UNCOMPRESSED_BYTES", "int", 500 * MiB, "upload", MiB, 10 * GiB, unit="bytes"),
    # --- conversion
    Setting("INGEST_MAX_FILE_BYTES", "int", 50 * MiB, "conversion", MiB, 2 * GiB, unit="bytes"),
    Setting("INGEST_CONVERT_TIMEOUT_SECONDS", "int", 600, "conversion", 0, 86400, unit="seconds"),
    Setting("INGEST_CONVERT_WORKERS", "int", 1, "conversion", 1, 64),
    Setting("INGEST_CONVERT_MAX_MEMORY_MB", "int", 0, "conversion", 0, 262144, unit="MB", restart="workers"),
    # --- PDF / OCR
    Setting("INGEST_PDF_OCR", "choice", "auto", "pdf", choices=("auto", "true", "false")),
    Setting("PDF_OCR_MIN_CONFIDENCE", "float", 60.0, "pdf", 0, 100),
    Setting("PDF_OCR_IMAGE_MIN_AREA", "float", 0.1, "pdf", 0, 1),
    Setting("PDF_TABLES", "bool", True, "pdf"),
    Setting("DOCEXTRACT_MAX_PAGES", "int", 3000, "pdf", 1, 100000),
    Setting("DOCEXTRACT_MAX_PDF_BYTES", "int", 200 * MiB, "pdf", MiB, 4 * GiB, unit="bytes"),
    Setting("DOCEXTRACT_OCR_TIMEOUT_SECONDS", "int", 120, "pdf", 1, 3600, unit="seconds"),
    Setting("DOCEXTRACT_VERTICAL_RATIO", "float", 0.5, "pdf", 0.1, 1.0),
    Setting("DOCEXTRACT_HEADINGS", "bool", True, "pdf"),
    Setting("DOCEXTRACT_HEADING_RATIO", "float", 1.08, "pdf", 1.01, 2.0),
    # --- preprocessing
    Setting("INGEST_PREPROCESS_REPEATED_LINES", "bool", True, "preprocess"),
    Setting("INGEST_PREPROCESS_REPEATED_MIN_PAGES", "int", 3, "preprocess", 2, 50),
    Setting("INGEST_PREPROCESS_REPEATED_MIN_RATIO", "float", 0.6, "preprocess", 0.1, 1.0),
    Setting("INGEST_PREPROCESS_REPEATED_MAX_CHARS", "int", 40, "preprocess", 5, 200),
    Setting("INGEST_PREPROCESS_JOIN_LINES", "bool", True, "preprocess"),
    Setting("INGEST_PREPROCESS_QUANTITIES", "bool", True, "preprocess"),
    # --- chunk sizes (characters) per document type
    Setting("JA_CHUNK_TARGET_FAQ_MIN_CHARS", "int", 80, "chunking", 20, 5000, unit="chars"),
    Setting("JA_CHUNK_TARGET_FAQ_MAX_CHARS", "int", 300, "chunking", 40, 8000, unit="chars"),
    Setting("JA_CHUNK_TARGET_PROCEDURE_MIN_CHARS", "int", 300, "chunking", 20, 5000, unit="chars"),
    Setting("JA_CHUNK_TARGET_PROCEDURE_MAX_CHARS", "int", 900, "chunking", 40, 8000, unit="chars"),
    Setting("JA_CHUNK_TARGET_POLICY_MIN_CHARS", "int", 400, "chunking", 20, 5000, unit="chars"),
    Setting("JA_CHUNK_TARGET_POLICY_MAX_CHARS", "int", 1200, "chunking", 40, 8000, unit="chars"),
    Setting("JA_CHUNK_TARGET_TABLE_MIN_CHARS", "int", 80, "chunking", 20, 5000, unit="chars"),
    Setting("JA_CHUNK_TARGET_TABLE_MAX_CHARS", "int", 500, "chunking", 40, 8000, unit="chars"),
    # --- review and approval
    Setting("APPROVED_QA_ENABLED", "bool", False, "review", config_attr="APPROVED_QA_ENABLED"),
    Setting("APPROVED_QA_REQUIRE_APPROVER", "bool", False, "review"),
    Setting("EXTRACTION_REQUIRE_APPROVAL", "bool", False, "review"),
    Setting("EXTRACTION_REVIEW_MAX_CONFIDENCE", "float", 80.0, "review", 0, 100),
)
_BY_NAME = {s.name: s for s in SETTINGS}

_LOCK = threading.Lock()
_ORIGINAL: Dict[str, Optional[str]] = {}  # the deployment's own value of a name, captured before the first override
_ORIGINAL_ATTR: Dict[str, Any] = {}  # the same for the config module attributes
_APPLIED: Dict[str, str] = {}  # what this process has put into os.environ
_SEEN = {"rev": None}


def definition(name: str) -> Setting:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise SettingsError(f"'{name}' is not a setting that the console changes") from None


def key_for(name: str) -> str:
    return KEY_PREFIX + name.lower()


def _name_for(key: str) -> Optional[str]:
    return next((s.name for s in SETTINGS if key_for(s.name) == key), None)


def parse(setting: Setting, raw: Any) -> Any:
    """The value in its type, or SettingsError (the message says what is allowed)."""
    name = setting.name
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise SettingsError(f"{name}: true or false")
    if setting.kind == "choice":
        text = str(raw).strip().lower()
        if text not in setting.choices:
            raise SettingsError(f"{name}: one of {', '.join(setting.choices)}")
        return text
    if isinstance(raw, bool):
        raise SettingsError(f"{name}: a number")
    try:
        number = float(str(raw).strip()) if not isinstance(raw, (int, float)) else float(raw)
    except ValueError:
        raise SettingsError(f"{name}: a number") from None
    if math.isnan(number) or math.isinf(number):
        raise SettingsError(f"{name}: a number")
    if setting.kind == "int":
        if number != int(number):
            raise SettingsError(f"{name}: a whole number")
        number = int(number)
    if (setting.minimum is not None and number < setting.minimum) or (setting.maximum is not None and number > setting.maximum):
        raise SettingsError(f"{name}: from {setting.minimum:g} to {setting.maximum:g}")
    return number


def _as_env(setting: Setting, value: Any) -> str:
    if setting.kind == "bool":
        return "true" if value else "false"
    return str(value)


def _environment_value(setting: Setting) -> Optional[str]:
    # the deployment's own value: what the process had before this module put an override into os.environ
    return _ORIGINAL.get(setting.name) if setting.name in _APPLIED else os.environ.get(setting.name)


def overrides() -> Dict[str, str]:
    """name -> value (as the environment variable text) for every setting that has an override."""
    found: Dict[str, str] = {}
    for setting in SETTINGS:
        value = rs.get(key_for(setting.name))
        if value is not None:
            try:
                found[setting.name] = _as_env(setting, parse(setting, value))
            except SettingsError:
                continue  # an invalid stored value is ignored, never applied
    return found


def sync_environ() -> None:
    """Make the overrides visible to code that reads os.environ; give back the original value of a reset one."""
    revision = (str(rs._path()), rs.revision())  # the store's location too: another store restarts at revision 1
    with _LOCK:
        if _SEEN["rev"] == revision and not (set(_APPLIED) - set(overrides())):
            return
        wanted = overrides()
        for name, text in wanted.items():
            setting = _BY_NAME[name]
            if name not in _APPLIED:
                _ORIGINAL[name] = os.environ.get(name)
                if setting.config_attr:
                    _ORIGINAL_ATTR[name] = getattr(config, setting.config_attr, None)
            os.environ[name] = text
            _APPLIED[name] = text
            if setting.config_attr:
                setattr(config, setting.config_attr, parse(setting, text))
        for name in [n for n in _APPLIED if n not in wanted]:
            original = _ORIGINAL.pop(name, None)
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
            if _BY_NAME[name].config_attr:
                setattr(config, _BY_NAME[name].config_attr, _ORIGINAL_ATTR.pop(name, None))
            _APPLIED.pop(name, None)
        _SEEN["rev"] = revision


def worker_environment() -> Dict[str, Optional[str]]:
    """For a conversion worker process: every managed name with its override text, or None (use your own environment)."""
    wanted = overrides()
    return {s.name: wanted.get(s.name) for s in SETTINGS}


def effective() -> List[Dict[str, Any]]:
    """One row per setting for the console: the value in force, where it comes from, and who changed it."""
    rows = []
    for setting in SETTINGS:
        stored = rs.entry(key_for(setting.name)) or {}
        override = None
        if "value" in stored:
            try:
                override = parse(setting, stored["value"])
            except SettingsError:
                override = None
        env_text = _environment_value(setting)
        env_value = None
        if env_text not in (None, ""):
            try:
                env_value = parse(setting, env_text)
            except SettingsError:
                env_value = None
        if override is not None:
            value, source = override, "override"
        elif env_value is not None:
            value, source = env_value, "environment"
        else:
            value, source = setting.default, "default"
        rows.append({
            "name": setting.name, "key": key_for(setting.name), "kind": setting.kind, "group": setting.group,
            "default": setting.default, "value": value, "source": source, "minimum": setting.minimum,
            "maximum": setting.maximum, "choices": list(setting.choices), "unit": setting.unit, "restart": setting.restart,
            "by": stored.get("by") if source == "override" else None,
            "reason": stored.get("reason") if source == "override" else None,
            "at": stored.get("at") if source == "override" else None,
        })
    return rows


def _check_pair(setting: Setting, value: Any) -> None:
    """A minimum chunk size must stay below the maximum of the same document type."""
    name = setting.name
    if not (name.startswith("JA_CHUNK_TARGET_") and name.endswith(("_MIN_CHARS", "_MAX_CHARS"))):
        return
    sibling_name = name.replace("_MIN_CHARS", "_MAX_CHARS") if name.endswith("_MIN_CHARS") else name.replace("_MAX_CHARS", "_MIN_CHARS")
    sibling = next(r["value"] for r in effective() if r["name"] == sibling_name)
    low, high = (value, sibling) if name.endswith("_MIN_CHARS") else (sibling, value)
    if low >= high:
        raise SettingsError(f"{name}: the minimum ({low}) must be smaller than the maximum ({high})")


def set_value(name: str, value: Any, *, by: str, reason: str, expected_rev: Optional[int] = None) -> Dict[str, Any]:
    setting = definition(name)
    parsed = parse(setting, value)
    _check_pair(setting, parsed)
    record = rs.set_value(key_for(name), parsed, by=by, reason=reason, expected_rev=expected_rev)
    sync_environ()
    return record


def reset(name: str, *, by: str, reason: str, expected_rev: Optional[int] = None) -> Dict[str, Any]:
    """Remove the override: the deployment's environment (or the code's default) applies again."""
    definition(name)
    record = rs.set_value(key_for(name), None, by=by, reason=reason, expected_rev=expected_rev)
    sync_environ()
    return record


def history(limit: int = 50) -> List[Dict[str, Any]]:
    rows = []
    for item in rs.history(limit=200):
        name = _name_for(str(item.get("key") or ""))
        if name:
            rows.append({**item, "name": name})
    return rows[: max(1, int(limit))]
