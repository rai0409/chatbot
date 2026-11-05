"""Domain-specific prompt rules live in a data file, not in code. Synthetic data only."""
from __future__ import annotations

import json

import pytest

import rag_grounded as g


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("GROUNDED_DOMAIN_RULES_PATH", raising=False)
    g._DOMAIN_RULES_CACHE.clear()


def _file(tmp_path, monkeypatch, rules, name="rules.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"schema": 1, "rules": rules}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GROUNDED_DOMAIN_RULES_PATH", str(path))
    return path


def test_the_shipped_default_adds_no_domain_rule():
    base = g._rules("コード 12 の許容値は？")
    assert not any("2桁コード" in r or "許容値" in r for r in base)


def test_a_rule_applies_when_the_question_contains_a_trigger(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, [{"id": "tol", "when_contains": ["許容値"], "rule": "許容範囲を示す行を含める。"}])
    assert "許容範囲を示す行を含める。" in g._rules("温度の許容値は？")
    assert "許容範囲を示す行を含める。" not in g._rules("温度は？")


def test_a_regex_trigger_sees_the_normalized_question(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, [{"id": "code", "when_regex": r"(?<!\d)\d{2}(?!\d)", "rule": "コードで始める。"}])
    assert "コードで始める。" in g._rules("コード１２について")  # full-width digits
    assert "コードで始める。" not in g._rules("コード123について")


def test_an_edited_file_is_picked_up(tmp_path, monkeypatch):
    path = _file(tmp_path, monkeypatch, [{"id": "a", "when_contains": ["甲"], "rule": "ルールA"}])
    assert "ルールA" in g._rules("甲")
    import os
    import time

    time.sleep(0.01)
    path.write_text(json.dumps({"schema": 1, "rules": [{"id": "b", "when_contains": ["甲"], "rule": "ルールB"}]},
                               ensure_ascii=False), encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    rules = g._rules("甲")
    assert "ルールB" in rules and "ルールA" not in rules


@pytest.mark.parametrize("payload", [
    "{broken",
    json.dumps({"schema": 2, "rules": []}),
    json.dumps({"schema": 1, "rules": "no"}),
    json.dumps({"schema": 1, "rules": [{"id": "x", "rule": "r"}]}),                                   # no trigger
    json.dumps({"schema": 1, "rules": [{"id": "x", "when_contains": ["a"], "rule": ""}]}),            # empty rule
    json.dumps({"schema": 1, "rules": [{"id": "x", "when_regex": "(", "rule": "r"}]}),                # bad regex
    json.dumps({"schema": 1, "rules": [{"id": "x", "when_contains": ["a"], "rule": "r" * 301}]}),     # too long
])
def test_an_invalid_file_is_refused_loudly_not_silently_ignored(tmp_path, monkeypatch, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("GROUNDED_DOMAIN_RULES_PATH", str(path))
    with pytest.raises(ValueError):
        g._rules("a")


def test_a_missing_configured_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDED_DOMAIN_RULES_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(ValueError):
        g._rules("a")


def test_the_old_two_rules_are_available_as_an_example_file(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("GROUNDED_DOMAIN_RULES_PATH", str(Path(g.__file__).parent / "configs" / "grounded_domain_rules.example.json"))
    rules = g._rules("コード 12 の許容値は？")
    assert any("2桁コード" in r for r in rules) and any("許容値" in r for r in rules)


# --- per tenant -------------------------------------------------------------------------------------------------------------------


def test_a_rule_can_be_limited_to_tenants(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, [
        {"id": "all", "when_contains": ["許容値"], "rule": "全テナントのルール"},
        {"id": "a", "when_contains": ["許容値"], "tenants": ["tenant_a"], "rule": "Aだけのルール"},
    ])
    assert g._rules("許容値は？", "tenant_a") == g._rules("許容値は？", "tenant_a")
    a = g._rules("許容値は？", "tenant_a")
    b = g._rules("許容値は？", "tenant_b")
    assert "Aだけのルール" in a and "全テナントのルール" in a
    assert "Aだけのルール" not in b and "全テナントのルール" in b


def test_without_a_tenant_only_untargeted_rules_apply(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, [{"id": "a", "when_contains": ["許容値"], "tenants": ["tenant_a"], "rule": "Aだけのルール"}])
    assert "Aだけのルール" not in g._rules("許容値は？")
    assert "Aだけのルール" not in g._rules("許容値は？", "default")


def test_the_prompt_builder_passes_the_tenant_on(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, [{"id": "a", "when_contains": ["許容値"], "tenants": ["tenant_a"], "rule": "Aだけのルール"}])
    system_a = g.build_messages("許容値は？", ["[S1] x"], tenant_id="tenant_a")[0]["content"]
    system_b = g.build_messages("許容値は？", ["[S1] x"], tenant_id="tenant_b")[0]["content"]
    assert "Aだけのルール" in system_a and "Aだけのルール" not in system_b


@pytest.mark.parametrize("tenants", ["tenant_a", [], [""], [1], ["a b"], ["x" * 65]])
def test_invalid_tenant_lists_are_refused(tmp_path, monkeypatch, tenants):
    _file(tmp_path, monkeypatch, [{"id": "a", "when_contains": ["x"], "tenants": tenants, "rule": "r"}])
    with pytest.raises(ValueError, match="tenants"):
        g._rules("x")
