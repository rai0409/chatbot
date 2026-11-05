from __future__ import annotations

import pytest

from eval.bench import calibration, stats


def test_mcnemar_exact_known_values():
    assert stats.mcnemar_exact(0, 5) == pytest.approx(0.0625)
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(3, 3) == 1.0


def test_holm_adjustment_and_decisions():
    out = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03}, alpha=0.05)

    assert out["a"]["p_adjusted"] == pytest.approx(0.03)
    assert out["c"]["p_adjusted"] == pytest.approx(0.06)
    assert out["b"]["p_adjusted"] == pytest.approx(0.06)
    assert out["a"]["reject"] == 1.0 and out["b"]["reject"] == 0.0


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    values = [0.0, 1.0] * 50

    first = stats.bootstrap_ci(values, resamples=500, seed=1)
    second = stats.bootstrap_ci(values, resamples=500, seed=1)

    assert first == second
    assert first["low"] <= 0.5 <= first["high"]


def test_cluster_bootstrap_resamples_groups():
    out = stats.bootstrap_ci([1.0, 1.0, 0.0, 0.0], ["g1", "g1", "g2", "g2"], resamples=200)

    assert out["clusters"] == 2.0


def test_paired_difference_detects_consistent_improvement():
    baseline = [0.0] * 40 + [1.0] * 40
    candidate = [1.0] * 80

    out = stats.paired_bootstrap_diff(baseline, candidate, resamples=1000)

    assert out["diff"] == pytest.approx(0.5)
    assert out["low"] > 0
    assert out["p_value"] < 0.05


def test_sample_size_formulas():
    # (1.96 + 0.8416)^2 * 0.10 / 0.03^2 = 872.1 -> 873
    assert stats.n_for_mcnemar(0.03, 0.10) == 873
    # 1.96^2 * 0.9 * 0.1 / 0.03^2 = 384.1 -> 385
    assert stats.n_for_proportion_ci(0.9, 0.03) == 385


def test_pareto_front_drops_dominated_rows():
    rows = [{"q": 0.9, "lat": 100}, {"q": 0.8, "lat": 50}, {"q": 0.7, "lat": 80}]

    front = stats.pareto_front(rows, maximize=["q"], minimize=["lat"])

    assert front == rows[:2]


def test_psi_and_ks_are_zero_for_identical_samples():
    reference = [float(i) for i in range(100)]

    assert stats.psi(reference, reference) == pytest.approx(0.0)
    assert stats.ks_two_sample(reference, reference)["d"] == 0.0


def test_isotonic_fit_is_monotone():
    model = calibration.isotonic_fit([0.1, 0.2, 0.3, 0.4], [1, 0, 1, 1])

    assert model == ([0.2, 0.3, 0.4], [0.5, 1.0, 1.0])
    assert calibration.isotonic_predict(model, 0.15) == 0.5
    assert calibration.isotonic_predict(model, 0.35) == 1.0


def test_cost_and_conformal_thresholds():
    assert calibration.cost_threshold(10, 1) == pytest.approx(0.9)
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05]
    wrong = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]

    out = calibration.conformal_risk_threshold(scores, wrong, alpha=0.2)

    assert out["threshold"] == 0.4
    assert out["coverage"] == pytest.approx(0.6)


def test_margin_skip_analysis_respects_max_loss():
    out = calibration.margin_skip_analysis([0.5, 0.4, 0.1, 0.05], [1, 1, 0, 0], [1, 1, 1, 0], max_loss=0.0)

    assert out["threshold"] == 0.4
    assert out["skip_rate"] == 0.5


def test_risk_coverage_area():
    out = calibration.risk_coverage([0.9, 0.1], [0, 1])

    assert out["aurc"] == pytest.approx(0.25)
