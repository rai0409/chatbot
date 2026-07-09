from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "scripts" / "dr_drill.sh"
DOC = ROOT / "docs" / "operations" / "dr_drill_and_recovery_objectives.md"


def test_dr_drill_runs_green():
    proc = subprocess.run(["bash", str(DRILL)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DR DRILL OK" in proc.stdout
    # the drill uses synthetic content only; no repo vectorstore path touched
    assert "DR DRILL FAIL" not in proc.stdout


def test_dr_drill_is_executable():
    import os
    assert os.access(DRILL, os.X_OK)


def test_dr_doc_has_rpo_rto_as_assumptions():
    doc = DOC.read_text(encoding="utf-8")
    assert "RPO" in doc and "RTO" in doc
    assert "ASSUMPTION" in doc.upper() and ("not guarantees" in doc or "not a guarantee" in doc.lower())
    assert "restore-test report" in doc.lower() or "Restore-test report" in doc
    for forbidden in ("sk-", "Bearer ", "X-Api-Key"):
        assert forbidden not in doc
