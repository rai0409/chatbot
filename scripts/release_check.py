#!/usr/bin/env python3
"""Release dependency-freeze + smoke verification (Prompt061).

Verifies the active environment matches the pinned requirements.txt (a release
gate for on-prem install/upgrade). Reports, for each requirement:
  - pinned `name==ver`: installed version must equal the pin
  - range `name>=ver` etc.: the package must be installed
Exits non-zero on any missing package or pinned-version mismatch. Reads no .env,
no network, no Docker; prints no secrets.

--fingerprint also compares the running embedding profile (provider, model,
prefix scheme, normalisation, distance) with the manifest of the served
generation and reports its verification state: a model / tokenizer / library
upgrade that changes the profile must be re-embedded into a new generation
(docs/operations/onprem_install_upgrade_release.md), never served as is.

Usage: python scripts/release_check.py [--requirements requirements.txt] [--fingerprint] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(==|>=|<=|~=|>|<)?\s*([0-9A-Za-z_.\-]+)?")


def _installed(name: str):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None


def check(requirements: Path) -> dict:
    results = []
    ok = True
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            continue
        name, op, ver = m.group(1), m.group(2), m.group(3)
        installed = _installed(name)
        entry = {"name": name, "op": op, "required": ver, "installed": installed, "status": "ok"}
        if installed is None:
            entry["status"] = "missing"
            ok = False
        elif op == "==" and ver and installed != ver:
            entry["status"] = "pin_mismatch"
            ok = False
        results.append(entry)
    return {"ok": ok, "requirements": str(requirements), "results": results}


def fingerprint_check(provider_name=None) -> dict:
    """The running embedding profile against the manifest of the served generation (rag_core.index_generation).
    A served collection without a manifest fails the release check: register it first."""
    from rag_core import index_generation

    result = index_generation.fingerprint_status(provider_name)
    result["ok"] = bool(result.get("ok"))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the environment matches pinned requirements (release gate).")
    parser.add_argument("--requirements", default=str(ROOT / "requirements.txt"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fingerprint", action="store_true",
                        help="also compare the running embedding profile with the served generation's manifest")
    args = parser.parse_args(argv)
    report = check(Path(args.requirements))
    if args.fingerprint:
        try:
            report["fingerprint"] = fingerprint_check()
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            report["fingerprint"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        report["ok"] = report["ok"] and report["fingerprint"]["ok"]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for r in report["results"]:
            mark = "OK " if r["status"] == "ok" else r["status"].upper()
            print(f"  [{mark}] {r['name']} required={r['op'] or ''}{r['required'] or ''} installed={r['installed']}")
        if "fingerprint" in report:
            fp = report["fingerprint"]
            print(f"  [{'OK ' if fp['ok'] else 'FINGERPRINT_MISMATCH'}] served={fp.get('collection')} "
                  f"differs={fp.get('differs')} manifest={fp.get('manifest_status')} {fp.get('detail') or ''}".rstrip())
        print("RELEASE CHECK:", "OK" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
