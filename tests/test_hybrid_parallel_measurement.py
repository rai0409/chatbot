"""HYBRID_PARALLEL measurement script and thread-budget warning (stub lanes, no models)."""
from __future__ import annotations

import json
import logging
import time

import config
from rag_core import retrieval
from scripts import measure_hybrid_parallel as mhp


def _queries(tmp_path, n=6):
    path = tmp_path / "q.jsonl"
    path.write_text("\n".join(json.dumps({"query": f"質問{i}"}, ensure_ascii=False) for i in range(n)) + "\n", encoding="utf-8")
    return path


def _stub(state, off_ms, on_ms):
    def retrieve(question):
        time.sleep((on_ms if state["on"] else off_ms) / 1000.0)
        return []

    return retrieve, lambda on: state.update(on=on)


def test_measure_reports_levels_and_recommends_enable(tmp_path, capsys):
    state = {"on": False}
    retrieve, toggle = _stub(state, 20, 8)  # on is much faster
    out = tmp_path / "r.json"
    code = mhp.main(["--queries", str(_queries(tmp_path)), "--n", "6", "--workers", "2", "--peak", "3",
                     "--output", str(out)], retrieve=retrieve, set_parallel=toggle)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and report["levels"] == [1, 2, 3]
    for level in ("1", "2", "3"):
        for mode in ("off", "on"):
            row = report["results"][level][mode]
            assert row["p50_ms"] > 0 and row["p95_ms"] >= row["p50_ms"] and row["throughput_qps"] > 0
    assert report["recommendation"]["enable"] is True
    assert "RECOMMENDATION" in capsys.readouterr().out


def test_recommendation_rule_thresholds():
    def res(off_p95, on_p95, off_tp, on_tp):
        return {"4": {"off": {"p95_ms": off_p95, "throughput_qps": off_tp}, "on": {"p95_ms": on_p95, "throughput_qps": on_tp}}}

    assert mhp.recommend(res(100, 85, 10, 10), 4)["enable"] is True          # exactly 15 percent
    assert mhp.recommend(res(100, 86, 10, 10), 4)["enable"] is False         # 14 percent
    assert mhp.recommend(res(100, 50, 10, 9.4), 4)["enable"] is False        # throughput -6 percent
    assert mhp.recommend(res(100, 50, 10, 9.5), 4)["enable"] is True         # throughput -5 percent
    assert mhp.recommend(res(100, 50, 10, 10), 8)["enable"] is False         # not measured


def test_measure_restores_parallel_off():
    state = {"on": False}
    retrieve, toggle = _stub(state, 1, 1)
    mhp.measure(["a", "b"], retrieve=retrieve, set_parallel=toggle, levels=[1], warmup=1)
    assert state["on"] is False


def test_thread_budget_warning_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(config, "HYBRID_PARALLEL", True)
    monkeypatch.setattr(config, "EMBED_NUM_THREADS", 6)
    monkeypatch.setenv("HYBRID_PARALLEL_WORKERS", "4")
    monkeypatch.setattr(retrieval, "_THREAD_BUDGET_WARNED", False)
    with caplog.at_level(logging.WARNING, logger=retrieval.logger.name):
        first = retrieval.check_thread_budget(cpu_count=16)  # 8 physical < 10
        second = retrieval.check_thread_budget(cpu_count=16)
    assert first and "exceeds the physical cores" in first and second is None
    assert sum("exceeds the physical cores" in r.message for r in caplog.records) == 1


def test_thread_budget_silent_when_within_budget_or_parallel_off(monkeypatch):
    monkeypatch.setattr(config, "EMBED_NUM_THREADS", 2)
    monkeypatch.setenv("HYBRID_PARALLEL_WORKERS", "4")
    monkeypatch.setattr(config, "HYBRID_PARALLEL", True)
    monkeypatch.setattr(retrieval, "_THREAD_BUDGET_WARNED", False)
    assert retrieval.check_thread_budget(cpu_count=16) is None
    monkeypatch.setattr(config, "HYBRID_PARALLEL", False)
    monkeypatch.setattr(retrieval, "_THREAD_BUDGET_WARNED", False)
    monkeypatch.setattr(config, "EMBED_NUM_THREADS", 64)
    assert retrieval.check_thread_budget(cpu_count=16) is None
