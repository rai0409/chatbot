from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "audit_export.py"
from webapi.audit_export import safe_export


def _events():
    return [
        {"timestamp": "2026-06-14T01:00:00Z", "kind": "chat", "tenant_id": "tenant_a",
         "answer_mode": "grounded", "guard_reason": None, "used_fallback": False,
         "citations_count": 2, "question": "機密の質問 OPENAI_KEY_REDACTED_EXAMPLE alice@example.com",
         "top_source_docs": ["secret_doc.pdf"]},
        {"timestamp": "2026-06-14T01:05:00Z", "kind": "chat", "tenant_id": "tenant_a",
         "answer_mode": "fallback", "guard_reason": "too_general", "used_fallback": True,
         "citations_count": 0, "question": "another raw question text"},
        {"timestamp": "2026-06-14T02:00:00Z", "kind": "chat", "tenant_id": "tenant_b",
         "answer_mode": "grounded", "guard_reason": None, "used_fallback": False, "citations_count": 1,
         "question": "別テナントの質問"},
    ]


def test_export_drops_raw_question_and_secrets():
    rows = safe_export(_events())
    blob = json.dumps(rows, ensure_ascii=False)
    for forbidden in ("機密の質問", "OPENAI_KEY_REDACTED_EXAMPLE", "alice@example.com",
                      "another raw question", "secret_doc.pdf", "別テナントの質問"):
        assert forbidden not in blob
    # aggregate fields only
    keys = set().union(*[set(r) for r in rows])
    assert "question" not in keys and "top_source_docs" not in keys


def test_export_aggregates_counts_and_tenants():
    rows = safe_export(_events())
    # 3 distinct groups (2 for tenant_a, 1 for tenant_b)
    assert len(rows) == 3
    assert sum(r["count"] for r in rows) == 3
    tenants = {r["tenant_id"] for r in rows}
    assert tenants == {"tenant_a", "tenant_b"}
    grounded = [r for r in rows if r["answer_mode"] == "grounded" and r["tenant_id"] == "tenant_a"][0]
    assert grounded["avg_citations"] == 2.0


def test_cli_redacts(tmp_path):
    f = tmp_path / "audit.jsonl"
    f.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in _events()) + "\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(CLI), str(f)], capture_output=True, text=True)
    assert proc.returncode == 0
    for forbidden in ("機密の質問", "OPENAI_KEY_REDACTED_EXAMPLE", "alice@example.com"):
        assert forbidden not in proc.stdout
