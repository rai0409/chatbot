from __future__ import annotations

# Stage 2 regression tests: no corpus-specific scoring in runtime code, and
# evaluation metrics that do not hide misses.

import re
from pathlib import Path

import pytest

import config
from eval import runner


# Terms and answers that belonged to one demo corpus. Runtime code must stay
# corpus-neutral; tenant vocabulary lives in profile data (config/rag_profiles).
_DEMO_PATTERNS = (
    "活火山",
    "御嶽",
    "救助活動",
    "噴火警戒",
    "plug-in",
    "インボイス",
    "volcano_demo",
    "pilot_staging_v1",
)
_RUNTIME_DIRS = ("rag_core", "webapi")
_RUNTIME_FILES = ("rag_grounded.py",)


def _runtime_sources():
    base = Path(config.BASE_DIR)
    for name in _RUNTIME_FILES:
        yield base / name
    for directory in _RUNTIME_DIRS:
        yield from sorted((base / directory).rglob("*.py"))


def test_runtime_code_has_no_demo_corpus_terms():
    offenders = []
    for path in _runtime_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in _DEMO_PATTERNS:
            if pattern in text:
                offenders.append(f"{path.relative_to(config.BASE_DIR)}: {pattern}")
    assert offenders == []


def test_runtime_code_has_no_magic_answer_bonus():
    # e.g. `if "111" in sent` style checks that boost a known answer.
    pattern = re.compile(r'if\s+"\d{2,}"\s+in\s+\w+')
    offenders = [
        str(path.relative_to(config.BASE_DIR))
        for path in _runtime_sources()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_staging_collection_does_not_select_demo_profile(monkeypatch):
    from webapi import main

    monkeypatch.delenv("RAG_PROFILE_TENANT_MAP", raising=False)
    req = main.ChatRequest(question="q")
    assert main._resolve_rag_profile_id(req, "pilot_staging_v1", "default") == "default"


def test_labeled_miss_counts_as_zero_mrr_and_false_hit():
    assert runner._mrr_at_k(None, 5, has_gold=True) == 0.0
    assert runner._hit_at_k(None, 5, has_gold=True) is False
    assert runner._mrr_at_k(None, 5) is None
    assert runner._hit_at_k(None, 5) is None
    assert runner._mrr_at_k(7, 5, has_gold=True) == 0.0
    assert runner._mrr_at_k(2, 5, has_gold=True) == pytest.approx(0.5)


def test_case_without_expectations_is_unevaluated_not_passed():
    assert runner._overall_pass({}) is None
    assert runner._overall_pass({"a": {"pass": True}}) is True
    assert runner._overall_pass({"a": {"pass": True}, "b": {"pass": False}}) is False


def test_gold_dropped_by_rerank_is_flagged():
    assert runner._dropped_by_rerank(3, None) is True
    assert runner._dropped_by_rerank(None, None) is False
    assert runner._dropped_by_rerank(3, 1) is False


def test_stubbed_components_are_reported_as_not_evaluated():
    assert runner._not_evaluated(real_vector=False, real_generation=False) == [
        "dense_retrieval",
        "hybrid_fusion",
        "answer_generation",
    ]
    assert runner._not_evaluated(real_vector=True, real_generation=True) == []


def test_readme_no_longer_claims_retracted_figures():
    readme = (Path(config.BASE_DIR) / "README.md").read_text(encoding="utf-8")
    assert "## Accuracy figures (retracted)" in readme
    assert "**22/22, pass_rate=1.0**" not in readme
    assert "- normal retrieval: `hybrid_hit@5=1.0`" not in readme
