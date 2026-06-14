from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "release_check.py"
DOC = ROOT / "docs" / "operations" / "onprem_install_upgrade_release.md"


def test_release_check_passes_on_current_env():
    proc = subprocess.run([sys.executable, str(CHECK), "--json"], capture_output=True, text=True)
    report = json.loads(proc.stdout)
    assert report["ok"] is True, [r for r in report["results"] if r["status"] != "ok"]
    assert proc.returncode == 0
    # the OIDC deps added in Prompt046 are pinned and present
    by_name = {r["name"].lower(): r for r in report["results"]}
    assert by_name["authlib"]["status"] == "ok"
    assert by_name["cryptography"]["status"] == "ok"


def test_release_check_flags_pin_mismatch(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi==0.0.1\n", encoding="utf-8")  # impossible pin
    proc = subprocess.run([sys.executable, str(CHECK), "--requirements", str(req), "--json"],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["results"][0]["status"] == "pin_mismatch"


def test_release_check_flags_missing(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("definitely-not-a-real-pkg-xyz==1.0.0\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(CHECK), "--requirements", str(req), "--json"],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["results"][0]["status"] == "missing"


def test_install_upgrade_doc_sections():
    doc = DOC.read_text(encoding="utf-8")
    for section in ("Release bundle checklist", "Install", "Upgrade", "Rollback",
                    "release_check.py", "Back up first", "limited_beta_preflight.sh"):
        assert section in doc
