import math
import random

import pytest

from eval.tenant_eval import monitoring
from eval.tenant_eval.monitoring import DriftPolicy


def _events(n, *, seed=0, guard=0.05, fallback=0.1, citations=(2, 5), latency=(800, 200), intents=None, cache=0.0):
    rng = random.Random(seed)
    intents = intents or {"procedure": 0.5, "other": 0.5}
    names, weights = list(intents), list(intents.values())
    events = []
    for _ in range(n):
        events.append({
            "guard_reason": "soft_distance" if rng.random() < guard else None,
            "used_fallback": rng.random() < fallback,
            "citations_count": rng.randint(*citations),
            "latency_ms": max(1.0, rng.gauss(*latency)),
            "intent": rng.choices(names, weights)[0],
            "cache_hit": rng.random() < cache,
        })
    return events


def _signal(result, name):
    return next(s for s in result["signals"] if s["signal"] == name)


def test_two_samples_of_the_same_traffic_are_ok():
    result = monitoring.compare(_events(1000, seed=1), _events(1000, seed=2))
    assert result["status"] == "ok" and {s["status"] for s in result["signals"]} == {"ok"}
    assert [s["signal"] for s in result["signals"]] == ["guard_rate", "fallback_rate", "citations_count", "latency_ms",
                                                         "intent_mix"]


def test_too_few_events_cannot_settle_anything():
    result = monitoring.compare(_events(199), _events(1000))
    assert result["status"] == "insufficient" and result["signals"] == [] and result["n_reference"] == 199
    assert monitoring.compare([], []) ["status"] == "insufficient"


def test_a_jump_in_the_guard_rate_warns_and_a_big_one_alerts():
    warn = monitoring.compare(_events(1000, seed=1, guard=0.05), _events(1000, seed=2, guard=0.12))
    assert _signal(warn, "guard_rate")["status"] == "warn" and warn["status"] == "warn"
    alert = monitoring.compare(_events(1000, seed=1, guard=0.05), _events(1000, seed=2, guard=0.25))
    assert _signal(alert, "guard_rate")["status"] == "alert" and alert["status"] == "alert"
    assert _signal(alert, "guard_rate")["delta"] == pytest.approx(0.2, abs=0.04)
    drop = monitoring.compare(_events(1000, seed=1, guard=0.25), _events(1000, seed=2, guard=0.05))
    assert _signal(drop, "guard_rate")["status"] == "alert"  # a large fall is a change worth a look too


def test_a_small_significant_change_is_not_enough_to_warn():
    result = monitoring.compare(_events(20000, seed=1, guard=0.05), _events(20000, seed=2, guard=0.07))
    assert _signal(result, "guard_rate")["p_value"] < 0.01 and _signal(result, "guard_rate")["status"] == "ok"


def test_a_shift_in_latency_or_citations_or_the_intent_mix_is_found():
    slower = monitoring.compare(_events(1000, seed=1), _events(1000, seed=2, latency=(1600, 400)))
    assert _signal(slower, "latency_ms")["status"] == "alert" and slower["status"] == "alert"
    fewer = monitoring.compare(_events(1000, seed=1, citations=(2, 5)), _events(1000, seed=2, citations=(0, 1)))
    assert _signal(fewer, "citations_count")["status"] == "alert"
    mixed = monitoring.compare(_events(1000, seed=1), _events(1000, seed=2, intents={"procedure": 0.1, "other": 0.2, "reset": 0.7}))
    assert _signal(mixed, "intent_mix")["status"] == "alert"


def test_cache_hits_do_not_move_the_numeric_signals_and_numeric_samples_can_be_too_few():
    reference = _events(1000, seed=1)
    cached = _events(1000, seed=2, cache=0.9)
    result = monitoring.compare(reference, cached)
    assert _signal(result, "latency_ms")["status"] == "insufficient"  # only ~100 live events remain
    assert result["status"] in {"ok", "warn", "alert"}  # insufficient signals do not decide the overall status
    only_live = monitoring.compare(reference, [dict(e, cache_hit=False) for e in cached])
    assert _signal(only_live, "latency_ms")["status"] == "ok"


def test_the_building_blocks():
    assert monitoring.two_proportion_p(10, 100, 10, 100) == pytest.approx(1.0)
    assert monitoring.two_proportion_p(10, 1000, 100, 1000) < 1e-10
    assert monitoring.two_proportion_p(0, 100, 0, 100) == 1.0 and monitoring.two_proportion_p(0, 100, 100, 100) == 0.0
    assert monitoring.two_proportion_p(1, 0, 1, 5) == 1.0
    assert monitoring.categorical_psi(["a", "b"] * 50, ["a", "b"] * 50) == pytest.approx(0.0)
    assert monitoring.categorical_psi(["a"] * 90 + ["b"] * 10, ["a"] * 10 + ["b"] * 90) > 1.0
    assert math.isnan(monitoring.categorical_psi([], ["a"]))
    extracted = monitoring.extract([{"cache_hit": True, "latency_ms": 5, "guard_reason": "x"},
                                    {"latency_ms": 9, "citations_count": 3, "intent": "reset", "used_fallback": True}])
    assert extracted["n"] == 2 and extracted["n_live"] == 1 and extracted["guard"] == 1 and extracted["fallback"] == 1
    assert extracted["latency_ms"] == [9.0] and extracted["citations"] == [3.0] and extracted["intents"] == ["other", "reset"]


def test_the_policy_is_reported_and_adjustable():
    result = monitoring.compare(_events(300, seed=1, guard=0.05), _events(300, seed=2, guard=0.12),
                                DriftPolicy(rate_min_delta=0.2))
    assert result["policy"]["rate_min_delta"] == 0.2 and _signal(result, "guard_rate")["status"] == "ok"
