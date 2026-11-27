"""Benchmark governance (2026-11-22): frozen general benchmark, tenant evaluation additions, customer review, rerank gate.

No model is downloaded: the bench encoders and the cross-encoder are replaced by deterministic character-bigram stubs.
All example values are synthetic (株式会社サンプル, 03-1234-5678, @example.co.jp)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

from eval.bench import encoders, general
from eval.bench.rerank_cache import CrossEncoderScorer
from eval.rerank_promotion_gate import GatePolicy, evaluate_statistical_gate
from eval.rerank_promotion_gate import main as gate_main
from eval.tenant_eval import customer_review, feedback, gate, metrics, report, runner
from rag_core import golden_qa
from scripts import run_general_benchmark as rgb

ROOT = Path(__file__).resolve().parents[1]
SMOKE_CONFIG = ROOT / "configs" / "bench" / "general_v1_smoke_v2.json"
SMOKE_MANIFEST = ROOT / "eval" / "bench_data" / "general_v1" / "smoke_v2" / "MANIFEST.json"


# --- stubs ----------------------------------------------------------------------------------------------------------


def _bigrams(text: str):
    text = "".join(str(text).split())
    return [text[i:i + 2] for i in range(max(1, len(text) - 1))]


class _StubEncoder:
    def encode(self, texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for row, text in enumerate(texts):
            for gram in _bigrams(text):
                out[row, int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16) % 64] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class _StubCrossEncoder:
    def predict(self, pairs, batch_size=32):
        return [float(len(set(_bigrams(q)) & set(_bigrams(t)))) for q, t in pairs]


@pytest.fixture
def stub_models(monkeypatch):
    monkeypatch.setattr(encoders, "load_model", lambda spec: _StubEncoder())
    monkeypatch.setattr(CrossEncoderScorer, "_load", lambda self: _StubCrossEncoder())


# --- general benchmark dataset ----------------------------------------------------------------------------------------


def test_production_manifest_and_config_are_well_formed_and_refuse_unpinned_models():
    manifest = general.load_manifest(ROOT / "eval" / "bench_data" / "general_v1" / "MANIFEST.json")
    assert set(manifest["sources"]) == {"jqara_dev", "jagovfaqs", "synthetic_multiformat"}
    assert all(spec.get("license") for spec in manifest["sources"].values())
    cfg = json.loads((ROOT / "configs" / "bench" / "general_v1.json").read_text(encoding="utf-8"))
    assert cfg["primary_metric"] == "ndcg@10" and {"ndcg@10", "recall@20", "mrr@10", "hit@1"} <= set(cfg["ci_metrics"])
    assert set(cfg["modes"]) == {"dense_only", "lexical_only_bm25", "hybrid_weighted_rrf", "hybrid_weighted_rrf_cross_encoder"}
    assert rgb.unpinned_models(cfg) == ["cl-nagoya/ruri-v3-310m", "cl-nagoya/ruri-v3-reranker-310m"]
    # the committed synthetic part matches its pinned input hashes
    synthetic = {"synthetic_multiformat": manifest["sources"]["synthetic_multiformat"]}
    general.check_inputs(dict(manifest, sources=synthetic), ROOT)


def test_build_is_deterministic_and_matches_the_pinned_smoke_manifest(tmp_path):
    manifest = general.load_manifest(SMOKE_MANIFEST)
    a = general.build(manifest, ROOT, tmp_path / "a")
    b = general.build(manifest, ROOT, tmp_path / "b")
    assert a["sha256"] == b["sha256"]
    general.check_outputs(manifest, a["sha256"])
    general.check_inputs(manifest, ROOT)
    rows = [json.loads(line) for line in Path(a["paths"]["eval_set"]).read_text(encoding="utf-8").splitlines()]
    assert {r["split"] for r in rows} == {"dev"} and sum(1 for r in rows if not r["answerable"]) == 1
    assert a["composition"]["jagov_like"] == {"queries": 10, "answerable": 10, "chunks": 12}


def test_a_changed_input_or_output_is_refused(tmp_path):
    manifest = general.load_manifest(SMOKE_MANIFEST)
    bad = json.loads(json.dumps(manifest))
    bad["expected_sha256"]["chunks"] = "0" * 64
    built = general.build(manifest, ROOT, tmp_path)
    with pytest.raises(general.DatasetMismatch):
        general.check_outputs(bad, built["sha256"])
    bad = json.loads(json.dumps(manifest))
    bad["sources"]["jagov_like"]["input_sha256"] = {"jagov_like.jsonl": "f" * 64}
    with pytest.raises(general.DatasetMismatch):
        general.check_inputs(bad, ROOT)
    unpinned = json.loads(json.dumps(manifest))
    unpinned["expected_sha256"] = {"eval_set": None, "chunks": None}
    with pytest.raises(general.DatasetMismatch):
        general.check_outputs(unpinned, built["sha256"])


def test_run_general_benchmark_end_to_end_with_stub_models(stub_models, tmp_path):
    with pytest.raises(SystemExit):
        rgb.run_benchmark(SMOKE_CONFIG, runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports",
                          cache_dir=tmp_path / "cache", data_dir=tmp_path / "data")
    result = rgb.run_benchmark(
        SMOKE_CONFIG, runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports", cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data", allow_unpinned_models=True,
    )
    assert result["schema"] == rgb.REPORT_SCHEMA and result["comparable"] is False
    assert set(result["modes"]) == {"dense_only", "lexical_only_bm25", "hybrid_weighted_rrf", "hybrid_weighted_rrf_cross_encoder"}
    for mode in result["modes"].values():
        assert 0.0 <= mode["metrics"]["ndcg@10"] <= 1.0 and "total" in mode["latency_ms"]
        assert set(mode["by_source"]) == {"jagov_like", "synthetic_multiformat"}
    assert result["modes"]["lexical_only_bm25"]["metrics"]["hit@10"] > 0.5
    assert set(result["paired_vs_hybrid"]["results"]) == {"dense_only", "lexical_only_bm25", "hybrid_weighted_rrf_cross_encoder"}
    md = Path(result["report_paths"][1]).read_text(encoding="utf-8")
    assert "| hybrid_weighted_rrf_cross_encoder |" in md and "comparable with other general_v1 reports: **False**" in md
    run_meta = json.loads((Path(result["run_dir"]) / "run.json").read_text(encoding="utf-8"))
    assert run_meta["config"]["general_benchmark"]["dataset_sha256"] == result["dataset"]["sha256"]

    # a tampered built file is refused with --skip-build
    chunks = tmp_path / "data" / "chunks.jsonl"
    chunks.write_text(chunks.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(general.DatasetMismatch):
        rgb.run_benchmark(SMOKE_CONFIG, runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports",
                          cache_dir=tmp_path / "cache", data_dir=tmp_path / "data", skip_build=True, allow_unpinned_models=True)

    # the gate can consume the same run (too few queries for a promotion: insufficient, exit 1)
    modes = result["modes"]
    out = tmp_path / "gate.json"
    code = gate_main([
        "--baseline-run", result["run_dir"], "--baseline-config", modes["hybrid_weighted_rrf"]["config_id"],
        "--candidate-run", result["run_dir"], "--candidate-config", modes["hybrid_weighted_rrf_cross_encoder"]["config_id"],
        "--max-p95-ms", "100000", "--resamples", "1000", "--output", str(out),
    ])
    decision = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and decision["decision"] == "insufficient_data" and "min_cases" in decision["reasons"]


# --- rerank promotion gate --------------------------------------------------------------------------------------------


def _cmp(metric, *, n=150, low=0.01, diff=0.03, baseline_only=5, candidate_only=9, p=0.42, comparable=True):
    return {
        "metric": metric, "baseline": {"run": "", "config_id": "a"}, "candidate": {"run": "", "config_id": "b"},
        "paired_queries": n, "conditions": {"comparable": comparable, "differences": [] if comparable else ["corpus_sha256"], "unknown": []},
        "results": {"(all)": {"n": n, "diff": diff, "low": low, "high": diff + 0.02, "baseline_mean": 0.6, "candidate_mean": 0.6 + diff,
                              "mcnemar": {"baseline_only": baseline_only, "candidate_only": candidate_only, "p_value": p}}},
        "verdict": {"non_inferior_all_slices": True},
    }


def _gate(ndcg=None, hit1=None, policy=GatePolicy(max_candidate_p95_ms=800.0), **kw):
    return evaluate_statistical_gate(ndcg or _cmp("ndcg@10"), hit1 or _cmp("hit@1"), policy,
                                     baseline_p95_ms=kw.get("b", 300.0), candidate_p95_ms=kw.get("c", 600.0))


def test_rerank_gate_promotes_only_a_significant_well_measured_affordable_improvement():
    assert _gate()["decision"] == "promote_candidate"
    small = _gate(ndcg=_cmp("ndcg@10", n=99), hit1=_cmp("hit@1", n=99))
    assert small["decision"] == "insufficient_data" and "min_cases" in small["reasons"]
    flat = _gate(ndcg=_cmp("ndcg@10", low=-0.001))
    assert flat["decision"] == "blocked" and "ndcg_at_10_improves" in flat["reasons"]
    worse = _gate(hit1=_cmp("hit@1", baseline_only=20, candidate_only=5, p=0.004))
    assert worse["decision"] == "blocked" and "hit_at_1_not_worse" in worse["reasons"]
    slow = _gate(c=900.0)
    assert slow["decision"] == "blocked" and "latency_p95" in slow["reasons"]
    jump = _gate(policy=GatePolicy(max_p95_increase_ms=200.0))
    assert jump["decision"] == "blocked"
    unbudgeted = _gate(policy=GatePolicy())
    assert unbudgeted["decision"] == "blocked" and "latency_p95" in unbudgeted["reasons"]
    other_corpus = _gate(ndcg=_cmp("ndcg@10", comparable=False))
    assert other_corpus["decision"] == "blocked" and "same_conditions" in other_corpus["reasons"]
    swapped = _gate(ndcg=_cmp("recall@10"))
    assert swapped["decision"] == "blocked" and "inputs" in swapped["reasons"]


def test_rerank_gate_cli_reads_compare_files_and_needs_allow_small_for_small_minimums(tmp_path):
    (tmp_path / "n.json").write_text(json.dumps(_cmp("ndcg@10")), encoding="utf-8")
    (tmp_path / "h.json").write_text(json.dumps(_cmp("hit@1")), encoding="utf-8")
    args = ["--compare-ndcg", str(tmp_path / "n.json"), "--compare-hit1", str(tmp_path / "h.json"), "--output", str(tmp_path / "o.json")]
    # the runs have no summary.jsonl here, so the latency is unknown: insufficient
    assert gate_main(args + ["--max-p95-ms", "800"]) == 1
    assert json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["decision"] == "insufficient_data"
    with pytest.raises(SystemExit):
        gate_main(args + ["--min-cases", "10"])


# --- tenant evaluation: retrieval metrics, latency, abstention ------------------------------------------------------------


def _cases():
    cases = []
    for i in range(6):
        case, _ = golden_qa.parse_case({"case_id": f"c{i}", "question": f"質問{i}", "expected_all": ["正解"], "group": f"g{i}"})
        case = dict(case, gold_chunk_ids=[f"k{i}"], should_abstain=False)
        cases.append(case)
    case, _ = golden_qa.parse_case({"case_id": "c9", "question": "存在しない制度", "forbidden_any": ["正解"]})
    cases.append(dict(case, should_abstain=True))
    return cases


def _answer_fn(rank_of_gold, abstain_on=()):
    def answer(question):
        i = question[-1]
        if question in abstain_on:
            return {"answer_text": "", "guard_reason": "soft_distance", "retrieved": [], "stage_ms": {"retrieval": 5.0}}
        ids = [f"x{j}" for j in range(5)]
        if i.isdigit() and rank_of_gold is not None:
            ids.insert(rank_of_gold, f"k{i}")
        return {"answer_text": "正解です", "has_citation": True, "retrieved": [{"chunk_id": c, "doc_id": "d"} for c in ids],
                "cited": [{"chunk_id": ids[0], "doc_id": "d"}, {"chunk_id": "x9", "doc_id": "d"}],
                "stage_ms": {"retrieval": 10.0, "generation": 100.0}}
    return answer


def test_tenant_rows_carry_retrieval_metrics_latency_and_the_abstention_matrix():
    rows = runner.run_cases(_cases(), _answer_fn(0))
    assert rows[0]["retrieval"]["hit@1"] == 1.0 and rows[0]["retrieval"]["ndcg@10"] == pytest.approx(1.0)
    assert rows[-1]["retrieval"] is None and rows[-1]["should_abstain"] is True
    summary = metrics.summarize(rows)
    assert summary["retrieval"]["n"] == 6 and summary["retrieval"]["ndcg@10"]["mean"] == pytest.approx(1.0)
    assert summary["citation_precision"] == {"mean": 0.5, "n": 6.0}
    assert summary["latency_p95_ms"] is not None and set(summary["stage_latency_ms"]) == {"retrieval", "generation"}
    confusion = summary["abstention"]
    assert confusion["should_abstain_but_answered"] == 1 and confusion["should_answer_and_answered"] == 6
    assert confusion["recall"] == 0.0 and confusion["precision"] is None

    abstaining = runner.run_cases(_cases(), _answer_fn(2, abstain_on={"存在しない制度", "質問0"}))
    confusion = metrics.summarize(abstaining)["abstention"]
    assert confusion["should_abstain_and_abstained"] == 1 and confusion["should_answer_but_abstained"] == 1
    assert confusion["precision"] == 0.5 and confusion["recall"] == 1.0
    assert abstaining[1]["retrieval"]["hit@1"] == 0.0 and abstaining[1]["retrieval"]["mrr@10"] == pytest.approx(1 / 3)


def test_tenant_gate_checks_latency_budget_and_retrieval_and_the_report_shows_them():
    base = runner.run_cases(_cases(), _answer_fn(0))
    cand = runner.run_cases(_cases(), _answer_fn(3))
    result = gate.compare(base, cand, gate.Policy(min_cases=5, latency_p95_budget_ms=1e-9, resamples=200))
    names = {c["name"]: c for c in result["checks"]}
    assert names["latency_p95"]["status"] == "fail"
    assert names["retrieval_ndcg"]["status"] == "fail" and names["retrieval_ndcg"]["diff"] < 0
    default = gate.compare(base, base, gate.Policy(min_cases=5, resamples=200))
    assert {c["name"]: c["status"] for c in default["checks"]}["latency_p95"] == "not_applicable"
    text = report.to_markdown(result, baseline_rows=base, candidate_rows=cand)
    assert "## 検索精度（正解 ID を持つケース）" in text and "## 棄権の混同行列" in text and "| generation |" in text
    assert "応答時間 p95 が予算内" in text


# --- customer review --------------------------------------------------------------------------------------------------------


def _evaluation():
    cases = []
    for i in range(4):
        case, _ = golden_qa.parse_case({"case_id": f"c{i}", "question": f"質問{i}", "expected_all": ["正解"]})
        cases.append(case)
    cases[0] = dict(cases[0], question="=HYPERLINK(\"http://example.co.jp\",\"株式会社サンプル\")")
    base = [{"case_id": f"c{i}", "passed": i in (0, 2), "answer_text": "-1+2" if i == 1 else f"基準{i}"} for i in range(4)]
    cand = [{"case_id": f"c{i}", "passed": i in (1, 2), "answer_text": "@SUM(A1)" if i == 1 else f"候補{i}"} for i in range(4)]
    g = {"changed": {"worse": ["c0"], "better": ["c1"]}}
    return g, base, cand, cases


def test_customer_review_csv_is_bom_utf8_and_injection_safe_and_round_trips(tmp_path):
    g, base, cand, cases = _evaluation()
    rows = customer_review.review_rows(g, base, cand, cases, sample_unchanged=1, seed=3)
    assert [r["change"] for r in rows] == ["悪化", "改善", "変化なし"]
    path = customer_review.export_review_csv(tmp_path / "review.csv", rows)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    table = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    assert table[0] == list(customer_review.COLUMNS)
    assert table[1][2].startswith("'=HYPERLINK") and table[2][3] == "'-1+2" and table[2][4] == "'@SUM(A1)"
    assert all(not cell.startswith(("=", "+", "-", "@")) for line in table for cell in line)

    # the customer fills in ratings (one row invalid)
    table[1][7:10] = ["2", "reject", "回答が違う"]
    table[2][7:10] = ["5", "accept", "＝良い"]
    table[3][7:10] = ["9", "accept", ""]
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(table)
    path.write_bytes(buffer.getvalue().encode("utf-8-sig"))
    ratings, problems = customer_review.import_review_csv(path)
    assert [r["case_id"] for r in ratings] == ["c0", "c1"] and len(problems) == 1 and "rating must be 1-5" in problems[0]
    agg = customer_review.aggregate_reviews(ratings, cand)
    assert agg["accepted"] == 1 and agg["rejected"] == 1 and agg["mean_rating"] == 3.5
    assert agg["customer_accepted_correct_rate"]["rate"] == 0.5 and agg["customer_accepted_correct_rate"]["low"] < 0.5
    events = [{"tenant_id": "t1", "feedback_type": "bad", "bad_reason": "incorrect", "feedback_token": "a"},
              {"tenant_id": "t1", "feedback_type": "good", "feedback_token": "b"}]
    lines = "\n".join(report.customer_subjective_lines(agg, feedback.aggregate(events)))
    assert "customer-accepted correct answer rate" in lines and "**50.0%**" in lines and "| t1 | 1 | 1 | 50.0% |" in lines


def test_customer_review_cli_export_and_import(tmp_path):
    g, base, cand, cases = _evaluation()
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps({"gate": g, "baseline_rows": base, "candidate_rows": cand, "cases": cases}, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "review.csv"
    assert customer_review.main(["export", "--evaluation", str(evaluation), "--out", str(out)]) == 0
    assert customer_review.main(["import", "--evaluation", str(evaluation), "--csv", str(out), "--out", str(tmp_path / "agg.json")]) == 0
    assert json.loads((tmp_path / "agg.json").read_text(encoding="utf-8"))["aggregate"]["reviewed"] == 0


# --- immutable general benchmark reports ------------------------------------------------------------------------------


def test_general_report_names_are_immutable_and_indexed(tmp_path):
    from datetime import datetime, timezone

    now = datetime(2026, 11, 24, 1, 2, 3, tzinfo=timezone.utc)
    cfg_sha = "ab12cd34" + "0" * 56
    paths = rgb.write_immutable_report(
        {"generated_at": "t1", "comparable": True}, "# md1\n", tmp_path,
        commit="0123456789abcdef", config_sha256=cfg_sha, now=now, run_id="r1r1r1",
    )
    assert Path(paths[0]).name == "20261124T010203Z_0123456_ab12cd34_r1r1r1.json"
    assert Path(paths[1]).name == "20261124T010203Z_0123456_ab12cd34_r1r1r1.md"
    # the same name again refuses to overwrite and leaves the first report intact
    with pytest.raises(FileExistsError):
        rgb.write_immutable_report(
            {"generated_at": "t2"}, "# md2\n", tmp_path,
            commit="0123456789abcdef", config_sha256=cfg_sha, now=now, run_id="r1r1r1",
        )
    assert Path(paths[1]).read_text(encoding="utf-8") == "# md1\n"
    assert json.loads(Path(paths[0]).read_text(encoding="utf-8"))["generated_at"] == "t1"
    assert len(rgb.read_index(tmp_path)) == 1
    # a second run (other run id) is appended; latest comes from the index
    rgb.write_immutable_report(
        {"generated_at": "t3"}, "# md3\n", tmp_path,
        commit="0123456789abcdef", config_sha256=cfg_sha, now=now, run_id="r2r2r2",
    )
    assert [e["run_id"] for e in rgb.read_index(tmp_path)] == ["r1r1r1", "r2r2r2"]
    assert rgb.latest_report(tmp_path)["run_id"] == "r2r2r2"
    assert not list(tmp_path.glob(".*.tmp"))
    assert rgb.latest_report(tmp_path / "empty") is None


# --- statistics guards: resamples floor, one primary comparison -------------------------------------------------------


def test_gate_and_compare_cli_reject_fewer_than_1000_resamples(tmp_path, capsys):
    from eval.bench import compare_runs as cr

    with pytest.raises(SystemExit) as exc:
        gate_main(["--baseline-run", "a", "--baseline-config", "x", "--candidate-run", "a", "--candidate-config", "y",
                   "--resamples", "999", "--output", str(tmp_path / "o.json")])
    assert exc.value.code == 2 and ">= 1000" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cr.main(["--baseline-run", "a", "--baseline-config", "x", "--candidate-run", "a", "--candidate-config", "y", "--resamples", "10"])
    assert exc.value.code == 2


def test_gate_several_candidates_need_primary_and_others_are_exploratory(stub_models, tmp_path):
    result = rgb.run_benchmark(
        SMOKE_CONFIG, runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports", cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data", allow_unpinned_models=True,
    )
    modes = result["modes"]
    base = modes["hybrid_weighted_rrf"]["config_id"]
    cand_a, cand_b = modes["dense_only"]["config_id"], modes["hybrid_weighted_rrf_cross_encoder"]["config_id"]
    common = ["--baseline-run", result["run_dir"], "--baseline-config", base, "--candidate-run", result["run_dir"],
              "--candidate-config", cand_a, "--candidate-config", cand_b, "--max-p95-ms", "100000", "--resamples", "1000"]
    with pytest.raises(SystemExit) as exc:  # no --primary: refused
        gate_main(common + ["--output", str(tmp_path / "x.json")])
    assert exc.value.code == 2
    out = tmp_path / "gate.json"
    gate_main(common + ["--primary", cand_b, "--output", str(out)])
    decision = json.loads(out.read_text(encoding="utf-8"))
    assert decision["primary"] == cand_b and decision["candidate"]["config_id"] == cand_b
    other = decision["exploratory"][cand_a]
    assert other["decision"] == "exploratory" and other["safe_to_promote"] is False
