from __future__ import annotations

import json

import pytest

from eval.bench.compare_runs import OVERALL, compare_runs, to_markdown


def _write_run(run_dir, config_id, rows):
    (run_dir / "per_query").mkdir(parents=True)
    with (run_dir / "per_query" / f"{config_id}.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row(qid, recall, slices=("formal",)):
    return {
        "query_id": qid,
        "group": qid,
        "answerable": True,
        "slices": list(slices),
        "metrics": {"recall@10": recall, "hit@10": 1.0 if recall > 0 else 0.0},
    }


def test_identical_runs_are_non_inferior_with_zero_difference(tmp_path):
    rows = [_row(f"q{i}", float(i % 2)) for i in range(40)]
    _write_run(tmp_path / "a", "base", rows)
    _write_run(tmp_path / "b", "cand", rows)

    report = compare_runs(tmp_path / "a", "base", tmp_path / "b", "cand", resamples=200)

    overall = report["results"][OVERALL]
    assert report["paired_queries"] == 40
    assert overall["diff"] == pytest.approx(0.0)
    assert overall["mcnemar"]["baseline_only"] == 0 and overall["mcnemar"]["candidate_only"] == 0
    assert report["verdict"]["non_inferior_overall"] is True


def test_clear_regression_is_reported_per_slice(tmp_path):
    base = [_row(f"c{i}", 1.0, ("colloquial",)) for i in range(20)] + [_row(f"f{i}", 1.0) for i in range(20)]
    cand = [_row(f"c{i}", 0.0, ("colloquial",)) for i in range(20)] + [_row(f"f{i}", 1.0) for i in range(20)]
    _write_run(tmp_path / "a", "base", base)
    _write_run(tmp_path / "b", "cand", cand)

    report = compare_runs(tmp_path / "a", "base", tmp_path / "b", "cand", resamples=200)

    assert report["results"]["colloquial"]["non_inferior"] is False
    assert report["results"]["formal"]["non_inferior"] is True
    assert report["verdict"]["non_inferior_all_slices"] is False
    assert "| colloquial | 20 |" in to_markdown(report)


def test_small_slices_and_unmatched_queries_are_reported(tmp_path):
    base = [_row(f"q{i}", 1.0) for i in range(12)] + [_row("rare", 1.0, ("typo",)), _row("gone", 1.0)]
    cand = [_row(f"q{i}", 1.0) for i in range(12)] + [_row("rare", 1.0, ("typo",)), _row("new", 1.0)]
    _write_run(tmp_path / "a", "base", base)
    _write_run(tmp_path / "b", "cand", cand)

    report = compare_runs(tmp_path / "a", "base", tmp_path / "b", "cand", resamples=100)

    assert report["slices_skipped_small"] == {"typo": 1}
    assert report["only_in_baseline"] == 1 and report["only_in_candidate"] == 1
