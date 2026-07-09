from __future__ import annotations

from pathlib import Path

from eval.guard_distance_calibration import (
    SCHEMA_VERSION,
    build_report,
    recommend_thresholds,
    render_markdown,
    summarize_distances,
    sweep_thresholds,
    threshold_sweep,
)
from rag_core import store

CURRENT = {"hard": 0.95, "soft_other": 0.65, "soft_reset": 0.8, "soft_change": 0.9}


def test_sweep_thresholds_grid_and_extras():
    grid = sweep_thresholds()
    assert grid[0] == 0.5
    assert grid[-1] == 1.0
    assert 0.75 in grid
    with_extra = sweep_thresholds(extra=[0.65, 0.95])
    assert 0.65 in with_extra and 0.95 in with_extra


def test_threshold_sweep_counts_false_rates():
    answerable = [0.3, 0.4, 0.6]
    abstain = [0.5, 0.8]

    rows = threshold_sweep(answerable, abstain, [0.55])

    row = rows[0]
    assert row["false_abstain_count"] == 1  # 0.6 > 0.55
    assert row["false_answer_count"] == 1  # 0.5 <= 0.55
    assert row["false_abstain_rate"] == round(1 / 3, 4)
    assert row["false_answer_rate"] == 0.5


def test_summarize_distances_is_deterministic_and_handles_empty():
    stats = summarize_distances([0.4, 0.1, 0.3, 0.2])
    assert stats["n"] == 4
    assert stats["min"] == 0.1
    assert stats["max"] == 0.4
    assert stats["p50"] == 0.2  # nearest-rank
    assert summarize_distances([]) == {
        "n": 0, "min": None, "p10": None, "p25": None, "p50": None,
        "p75": None, "p90": None, "p95": None, "max": None,
    }


def test_recommendation_unambiguous_when_classes_separate():
    rec = recommend_thresholds([0.2, 0.3, 0.35], [0.6, 0.7], current=CURRENT)

    assert rec["separated"] is True
    assert rec["unambiguous"] is True
    assert rec["recommended"]["hard"] == 0.5
    assert rec["sweep_at_recommended_hard"]["false_abstain_count"] == 0


def test_recommendation_not_unambiguous_when_overlapping():
    rec = recommend_thresholds([0.2, 0.6], [0.4, 0.5], current=CURRENT)

    assert rec["separated"] is False
    assert rec["unambiguous"] is False


def test_report_schema_and_markdown():
    per_case = [
        {"case_id": "a1", "label": "answerable", "best_distance": 0.3, "gold_distance": 0.3, "top_id": "x"},
        {"case_id": "b1", "label": "abstain", "best_distance": 0.7, "gold_distance": None, "top_id": "y"},
    ]
    report = build_report(
        per_case=per_case,
        collection="test_collection",
        fingerprint={"embed_provider": "local", "embed_model": "m"},
        cases_path="cases.jsonl",
        top_k=5,
        current=CURRENT,
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert set(report) == {
        "schema_version", "collection", "embedding_fingerprint", "cases_path",
        "top_k", "classes", "sweep", "recommendation", "per_case",
    }
    assert report["classes"]["answerable"]["n"] == 1
    md = render_markdown(report)
    assert "# Guard Distance Calibration Report" in md
    assert "Threshold sweep" in md
    assert "test_collection" in md


def test_runtime_code_never_reads_calibration_artifacts():
    # The calibration is measurement-only: production modules must not import
    # it or read its artifacts; behavior with artifacts absent is unchanged.
    for root in ("rag_core", "webapi"):
        for path in Path(root).rglob("*.py"):
            assert "guard_distance_calibration" not in path.read_text(encoding="utf-8"), path


class _FakeCollection:
    name = "fake"

    def __init__(self):
        self.metadata = {"hnsw:space": "cosine", "note": "keep"}
        self.modified_with = None

    def modify(self, metadata):
        # Mirrors chromadb: any hnsw:* key in modify() is rejected.
        if any(str(k).startswith("hnsw:") for k in metadata):
            raise ValueError("Changing the distance function of a collection once it is created is not supported currently.")
        self.modified_with = metadata


def test_stamp_collection_fingerprint_strips_hnsw_keys(monkeypatch):
    monkeypatch.setattr(
        store.embedding_provider,
        "active_fingerprint",
        lambda provider_name=None: {"embed_provider": "local", "embed_model": "m"},
    )
    collection = _FakeCollection()

    fingerprint = store.stamp_collection_fingerprint(collection)

    assert fingerprint == {"embed_provider": "local", "embed_model": "m"}
    assert collection.modified_with == {
        "note": "keep",
        "embed_provider": "local",
        "embed_model": "m",
    }
