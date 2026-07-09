from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "eval" / "cases" / "manufacturing_pilot"
CHUNKS = PACK / "manufacturing_chunks.jsonl"
CASES = PACK / "manufacturing_cases.jsonl"
DEMO = ROOT / "docs" / "reports" / "pilot_demo_script_manufacturing.md"

_FORBIDDEN = ("sk-", "Bearer ", "X-Api-Key", "OPENAI_API_KEY", "ADMIN_AUTH_TOKEN",
              "OIDC_CLIENT_SECRET", "OIDC_SESSION_SECRET", "ENTERPRISE_AUTH_TRUST_TOKEN",
              "password", "58887_95105")  # last = real-doc marker that must not appear


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- corpus + cases well-formed --------------------------------------------


def test_pack_files_exist():
    for p in (CHUNKS, CASES):
        assert p.is_file(), p


def test_chunks_well_formed():
    chunks = _lines(CHUNKS)
    assert len(chunks) >= 5
    for c in chunks:
        for field in ("id", "text", "source_doc", "source_pages", "doc_id", "chunk_index", "searchable", "type"):
            assert field in c, f"chunk missing {field}: {c.get('id')}"
        assert c["tenant_id"] == "default"
    # at least one approved-Q&A pair chunk for the exact-match case
    assert any(c.get("doc_type") == "approved_qa_pair" for c in chunks)


def test_cases_well_formed_and_cover_required_categories():
    cases = _lines(CASES)
    cats = " ".join(c.get("category", "") for c in cases)
    assert "abstain" in cats and "out-of-corpus" in cats and "approved-QA" in cats
    # must have answerable (no fallback) and must-abstain (fallback) cases
    assert any(c.get("expected_used_fallback") is False for c in cases)
    assert any(c.get("expected_used_fallback") is True for c in cases)
    for c in cases:
        assert c.get("case_id") and c.get("query")


# --- synthetic / no-secret safety ------------------------------------------


def test_pack_is_clearly_synthetic_and_secret_free():
    blob = CHUNKS.read_text(encoding="utf-8") + CASES.read_text(encoding="utf-8")
    assert "架空精機" in blob  # clearly fictional company marker
    for forbidden in _FORBIDDEN:
        assert forbidden not in blob, f"forbidden token in pack: {forbidden}"


def test_demo_script_present_and_secret_free():
    assert DEMO.is_file()
    blob = DEMO.read_text(encoding="utf-8")
    assert "架空精機" in blob and "/chat-ui" in blob
    for forbidden in _FORBIDDEN:
        assert forbidden not in blob


# --- builder reproducibility -----------------------------------------------


def test_builder_reproduces_committed_pack():
    from scripts.build_manufacturing_pilot_pack import build_chunks, CASES as BUILT_CASES
    assert [c["id"] for c in build_chunks()] == [c["id"] for c in _lines(CHUNKS)]
    assert [c["case_id"] for c in BUILT_CASES] == [c["case_id"] for c in _lines(CASES)]


# --- end-to-end eval: abstain/no-answer behavior honored -------------------


def test_manufacturing_eval_runs_green(tmp_path):
    out = tmp_path / "result.json"
    proc = subprocess.run(
        [sys.executable, "-m", "eval.runner",
         "--cases", str(CASES), "--chunks-jsonl", str(CHUNKS), "--output", str(out)],
        cwd=str(ROOT), env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed=11/11" in proc.stdout
    # abstain + out-of-corpus cases must have used the guard/fallback
    assert "[PASS] mfg_abstain_ambiguous" in proc.stdout
    assert "[PASS] mfg_out_of_corpus" in proc.stdout
    assert "[PASS] mfg_approved_inspect" in proc.stdout
