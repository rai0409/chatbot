"""A contract ledger whose values a person confirms, and the deadlines computed from it (step 7-1)."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import config

SCHEMA = 1
EXTRACT_SCHEMA = "contract-extract/v1"
FIELDS: Dict[str, str] = {
    "counterparty": "text", "title": "text", "effective_date": "date", "end_date": "date", "auto_renewal": "bool",
    "renewal_period_months": "int", "renewal_notice_days": "int", "termination_notice_days": "int",
    "amount": "number", "currency": "text", "governing_law": "text", "contact_email": "email",
}
REQUIRED = ("counterparty", "end_date")
DECIDED = ("confirmed", "corrected")
MAX_DAYS = 3650
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_ERAS = {"令和": 2018, "平成": 1988, "昭和": 1925}


class ContractError(ValueError):
    pass


def contracts_dir() -> Path:
    raw = str(os.getenv("CONTRACTS_DIR", "")).strip()
    return Path(raw) if raw else Path(config.BASE_DIR) / "data" / "contracts"


def _tenant(tenant_id: Any) -> str:
    if not isinstance(tenant_id, str) or not _TENANT_RE.fullmatch(tenant_id):
        raise ContractError("invalid tenant id")
    return tenant_id


def _path(tenant_id: str, contract_id: Any) -> Path:
    if not isinstance(contract_id, str) or not _ID_RE.fullmatch(contract_id):
        raise ContractError("invalid contract id (lower-case letters, digits, '_' and '-', at most 64)")
    return contracts_dir() / _tenant(tenant_id) / f"{contract_id}.json"


def _who(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        raise ContractError("who made the change (1 to 64 characters) is required")
    return text


def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


# --- values ------------------------------------------------------------------------------------------------


def parse_date(value: Any) -> date:
    """ISO, 2026/10/27, 2026年10月27日 or 令和8年10月27日 (平成, 昭和; 元年). ValueError when it is not a real date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text) or re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if match:
        year, month, day = (int(g) for g in match.groups())
    else:
        era = re.fullmatch(r"(令和|平成|昭和)(元|\d{1,2})年(\d{1,2})月(\d{1,2})日", text)
        if not era:
            raise ValueError("not a date")
        year = _ERAS[era.group(1)] + (1 if era.group(2) == "元" else int(era.group(2)))
        month, day = int(era.group(3)), int(era.group(4))
    return date(year, month, day)


def normalize(name: str, value: Any) -> Any:
    """The value of a field in its stored form. ValueError when it is not valid for that field."""
    kind = FIELDS[name]
    if kind == "date":
        return parse_date(value).isoformat()
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in {"true", "yes", "はい", "あり"}:
            return True
        if str(value).strip().lower() in {"false", "no", "いいえ", "なし"}:
            return False
        raise ValueError("not true or false")
    if kind == "int":
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError("not a whole number")
        try:
            number = int(value) if isinstance(value, (int, float)) else int(unicodedata.normalize("NFKC", str(value)).strip())
        except ValueError:
            raise ValueError("not a whole number") from None
        if not 0 <= number <= MAX_DAYS:
            raise ValueError(f"outside 0..{MAX_DAYS}")
        return number
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError("not a number")
        try:
            number = float(unicodedata.normalize("NFKC", str(value)).replace(",", "").strip())
        except ValueError:
            raise ValueError("not a number") from None
        if number < 0 or number != number or number == float("inf"):
            raise ValueError("not a non-negative number")
        return number
    text = unicodedata.normalize("NFKC", str(value if value is not None else "")).strip() if kind == "email" else str(value or "").strip()
    if not text or len(text) > 500 or any(ord(c) < 32 for c in text):
        raise ValueError("empty, too long (500) or has control characters")
    if kind == "email" and not _EMAIL_RE.fullmatch(text):
        raise ValueError("not an e-mail address")
    return text


def _empty_field() -> Dict[str, Any]:
    return {"value": None, "state": "missing", "confidence": None, "evidence": None, "updated_by": None, "updated_at": None}


# --- storage -----------------------------------------------------------------------------------------------


def _write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def get(tenant_id: str, contract_id: str) -> Dict[str, Any]:
    path = _path(tenant_id, contract_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractError(f"contract '{contract_id}' not found") from None
    except (OSError, ValueError) as exc:
        raise ContractError("the contract file is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ContractError("the contract file has an unsupported format")
    return data


def _save(contract: Dict[str, Any], *, expected_rev: int) -> Dict[str, Any]:
    path = _path(contract["tenant_id"], contract["contract_id"])
    if path.exists() and get(contract["tenant_id"], contract["contract_id"])["rev"] != expected_rev:
        raise ContractError("the contract was changed by someone else; reload it")
    contract["rev"] = expected_rev + 1
    _write(path, contract)
    return contract


def _new(tenant_id: str, contract_id: str, source_doc: str, who: str, now: Optional[datetime]) -> Dict[str, Any]:
    return {"schema": SCHEMA, "contract_id": contract_id, "tenant_id": tenant_id, "rev": -1, "source_doc": str(source_doc or "")[:512],
            "created_at": _now(now), "created_by": who, "fields": {name: _empty_field() for name in FIELDS}, "history": []}


def _log(contract: Dict[str, Any], who: str, action: str, field: Optional[str], old: Any, new: Any, now: Optional[datetime]) -> None:
    contract["history"].append({"at": _now(now), "by": who, "action": action, "field": field,
                                "old": None if old is None else str(old)[:200], "new": None if new is None else str(new)[:200]})


def list_contracts(tenant_id: str) -> List[Dict[str, Any]]:
    folder = contracts_dir() / _tenant(tenant_id)
    rows = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.startswith("_"):
            continue
        try:
            rows.append(get(tenant_id, path.stem))
        except ContractError:
            continue
    return rows


def create(tenant_id: str, contract_id: str, *, source_doc: str = "", created_by: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    path = _path(tenant_id, contract_id)
    if path.exists():
        raise ContractError(f"contract '{contract_id}' already exists")
    contract = _new(tenant_id, contract_id, source_doc, _who(created_by), now)
    _log(contract, contract["created_by"], "created", None, None, None, now)
    return _save(contract, expected_rev=-1)


def import_extracted(
    tenant_id: str, contract_id: str, extracted: Mapping[str, Any], *, source_doc: str = "", created_by: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Store the output of the contract extraction. Nothing it says is "confirmed". Returns {contract, rejected, kept}."""
    if not isinstance(extracted, Mapping) or extracted.get("schema") != EXTRACT_SCHEMA:
        raise ContractError(f"unsupported extraction schema (expected '{EXTRACT_SCHEMA}')")
    fields = extracted.get("fields")
    if not isinstance(fields, Mapping):
        raise ContractError("the extraction has no fields")
    who = _who(created_by)
    path = _path(tenant_id, contract_id)
    contract = get(tenant_id, contract_id) if path.exists() else _new(tenant_id, contract_id, source_doc, who, now)
    expected = contract["rev"]
    if expected == -1:
        _log(contract, who, "created", None, None, None, now)
    rejected: Dict[str, str] = {}
    kept: List[str] = []
    for name, item in fields.items():
        if name not in FIELDS:
            rejected[str(name)[:64]] = "unknown field"
            continue
        item = item if isinstance(item, Mapping) else {"value": item}
        current = contract["fields"][name]
        raw = item.get("value")
        value = None
        if raw is not None and str(raw).strip() != "":
            try:
                value = normalize(name, raw)
            except (ValueError, TypeError) as exc:
                rejected[name] = f"not stored: {exc}"
                continue
        if current["state"] in DECIDED:
            kept.append(name)
            if value is not None and value != current["value"]:
                _log(contract, who, "extraction_differs", name, current["value"], value, now)
            continue
        confidence = item.get("confidence")
        evidence = None
        if item.get("page") is not None or item.get("text"):
            evidence = {"page": item.get("page") if isinstance(item.get("page"), int) else None,
                        "text": str(item.get("text") or "")[:300]}
        contract["fields"][name] = {
            "value": value, "state": "extracted" if value is not None else "missing",
            "confidence": float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
            "evidence": evidence, "updated_by": who, "updated_at": _now(now)}
        _log(contract, who, "extracted", name, current["value"], value, now)
    if source_doc:
        contract["source_doc"] = str(source_doc)[:512]
    saved = _save(contract, expected_rev=expected)
    return {"contract": saved, "rejected": rejected, "kept": kept}


def set_field(tenant_id: str, contract_id: str, name: str, value: Any, *, by: str, note: str = "",
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """A person's value for a field: state "corrected"."""
    if name not in FIELDS:
        raise ContractError(f"unknown field '{name}'")
    who = _who(by)
    try:
        stored = normalize(name, value)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"{name}: {exc}") from None
    contract = get(tenant_id, contract_id)
    old = contract["fields"][name]["value"]
    contract["fields"][name] = {**contract["fields"][name], "value": stored, "state": "corrected", "updated_by": who,
                                "updated_at": _now(now), "note": str(note or "")[:500]}
    _log(contract, who, "corrected", name, old, stored, now)
    return _save(contract, expected_rev=contract["rev"])


def confirm_field(tenant_id: str, contract_id: str, name: str, *, by: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """A person says the extracted value is right: state "confirmed". A missing value cannot be confirmed."""
    if name not in FIELDS:
        raise ContractError(f"unknown field '{name}'")
    who = _who(by)
    contract = get(tenant_id, contract_id)
    field = contract["fields"][name]
    if field["value"] is None:
        raise ContractError(f"{name} has no value to confirm (set it instead)")
    contract["fields"][name] = {**field, "state": "confirmed", "updated_by": who, "updated_at": _now(now)}
    _log(contract, who, "confirmed", name, field["value"], field["value"], now)
    return _save(contract, expected_rev=contract["rev"])


# --- status and deadlines ------------------------------------------------------------------------------------


def status_of(contract: Mapping[str, Any], today: Optional[date] = None) -> str:
    fields = contract["fields"]
    if any(fields[name]["state"] not in DECIDED or fields[name]["value"] is None for name in REQUIRED):
        return "needs_review"
    end = date.fromisoformat(fields["end_date"]["value"])
    return "expired" if end < (today or date.today()) else "active"


def deadlines(contract: Mapping[str, Any], today: Optional[date] = None) -> List[Dict[str, Any]]:
    """The dates a person has to act on, with whether each rests only on confirmed values."""
    fields = contract["fields"]
    today = today or date.today()
    if fields["end_date"]["value"] is None:
        return []
    end = date.fromisoformat(fields["end_date"]["value"])
    specs = [("end", end, ["end_date"], "契約の終了日")]
    notice = fields["renewal_notice_days"]["value"]
    if fields["auto_renewal"]["value"] is True and notice is not None:
        specs.append(("non_renewal_notice", end - timedelta(days=notice), ["end_date", "auto_renewal", "renewal_notice_days"],
                      "更新しない旨の通知期限"))
    termination = fields["termination_notice_days"]["value"]
    if termination is not None:
        specs.append(("termination_notice", end - timedelta(days=termination), ["end_date", "termination_notice_days"], "解約の通知期限"))
    return [{"kind": kind, "label": label, "date": when.isoformat(), "days_left": (when - today).days, "basis": basis,
             "verified": all(fields[b]["state"] in DECIDED for b in basis)} for kind, when, basis, label in specs]


def upcoming(tenant_id: str, *, within_days: int = 90, today: Optional[date] = None, overdue_days: int = 30) -> List[Dict[str, Any]]:
    """Deadlines of every contract of the tenant from `overdue_days` ago to `within_days` ahead, earliest first."""
    today = today or date.today()
    rows = []
    for contract in list_contracts(tenant_id):
        for item in deadlines(contract, today):
            if -overdue_days <= item["days_left"] <= within_days:
                rows.append({**item, "contract_id": contract["contract_id"], "counterparty": contract["fields"]["counterparty"]["value"],
                             "title": contract["fields"]["title"]["value"], "status": status_of(contract, today)})
    return sorted(rows, key=lambda r: (r["date"], r["contract_id"], r["kind"]))
