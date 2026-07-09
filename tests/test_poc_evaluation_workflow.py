from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "eval" / "templates" / "poc_question_set_template.jsonl"
WORKFLOW = ROOT / "docs" / "operations" / "real_document_poc_evaluation_workflow.md"
REPORT_TPL = ROOT / "docs" / "reports" / "poc_result_report_template.md"
CHECK = ROOT / "scripts" / "poc_eval_check.py"

_FORBIDDEN = ("sk-", "Bearer ", "X-Api-Key", "OPENAI_API_KEY", "OIDC_CLIENT_SECRET",
              "ADMIN_AUTH_TOKEN", "password")


def test_template_parses_and_covers_categories():
    rows = [json.loads(l) for l in TEMPLATE.read_text(encoding="utf-8").splitlines() if l.strip()]
    cats = " ".join(r["category"] for r in rows)
    for required in ("answerable", "citation", "approved-QA", "abstain", "out-of-corpus"):
        assert required in cats
    # answerable + must-abstain present
    assert any(r.get("expected_used_fallback") is False for r in rows)
    assert any(r.get("expected_used_fallback") is True for r in rows)


def test_template_is_synthetic_placeholder_and_secret_free():
    blob = TEMPLATE.read_text(encoding="utf-8")
    assert "TEMPLATE" in blob and "<" in blob  # placeholders, not real content
    for f in _FORBIDDEN:
        assert f not in blob


def test_check_script_accepts_template():
    proc = subprocess.run([sys.executable, str(CHECK), str(TEMPLATE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_check_script_flags_missing_categories(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"case_id": "x", "query": "q", "category": "answerable"}) + "\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(CHECK), str(bad)], capture_output=True, text=True)
    assert proc.returncode == 1  # missing abstain/out-of-corpus


def test_docs_present_and_data_handling_rules():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "runs/poc/" in wf and "gitignored" in wf and "alias" in wf
    assert "No customer data" in wf or "never committed" in wf
    rpt = REPORT_TPL.read_text(encoding="utf-8")
    assert "do not fabricate" in rpt.lower()
    for blob in (wf, rpt):
        for f in _FORBIDDEN:
            assert f not in blob
