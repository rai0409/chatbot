from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

runner = importlib.import_module("eval.runner")


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_load_cases_parses_fixture_rows(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "c1",
                "category": "quoted term",
                "query": "「請求書ID」 の確認方法",
                "intent_override": "other",
                "expected_top_chunk_id": "g1",
                "expected_guard_reason": None,
                "expected_used_fallback": False,
                "answer_must_contain": ["請求書"],
                "answer_must_not_contain": ["推測"],
                "gold_doc_ids": ["glossary.pdf"],
                "gold_chunk_ids": ["g1"],
                "should_abstain": False,
            }
        ],
    )

    cases = runner.load_cases(cases_path)
    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "c1"
    assert case.intent_override == "other"
    assert "expected_top_chunk_id" in case.expectation_keys
    assert "expected_guard_reason" in case.expectation_keys
    assert "answer_must_contain" in case.expectation_keys
    assert case.gold_doc_ids == ("glossary.pdf",)
    assert case.gold_chunk_ids == ("g1",)
    assert case.should_abstain is False


def test_evaluate_expectations_checks_core_fields(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "c2",
                "category": "ambiguous",
                "query": "これは？",
                "expected_top_chunk_id": "x1",
                "expected_guard_reason": "too_general",
                "expected_used_fallback": True,
                "answer_must_contain": ["関連情報"],
            }
        ],
    )
    case = runner.load_cases(cases_path)[0]

    checks = runner.evaluate_expectations(
        case,
        after_rerank_top=[{"chunk_id": "x1", "source_doc": "doc.pdf"}],
        guard_reason="too_general",
        used_fallback=True,
        answer_text="関連情報が見つかりませんでした。",
        selected_context_preview=[],
    )

    assert checks["expected_top_chunk_id"]["pass"] is True
    assert checks["expected_guard_reason"]["pass"] is True
    assert checks["expected_used_fallback"]["pass"] is True
    assert checks["answer_must_contain"]["pass"] is True


def test_run_eval_outputs_before_after_and_json_keys(tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "id": "g1",
                "text": "請求書ID は帳票ヘッダーで確認できます。",
                "source_doc": "glossary.pdf",
                "source_pages": [1],
                "doc_id": "glossary.pdf",
                "chunk_index": 1,
                "searchable": 1,
                "type": "pdf",
                "quality": "high",
            },
            {
                "id": "g2",
                "text": "請求書番号の一般説明です。",
                "source_doc": "glossary.pdf",
                "source_pages": [2],
                "doc_id": "glossary.pdf",
                "chunk_index": 2,
                "searchable": 1,
                "type": "pdf",
                "quality": "high",
            },
        ],
    )

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "quoted",
                "category": "quoted term",
                "query": "「請求書ID」 の確認方法",
                "expected_top_chunk_id": "g1",
                "expected_used_fallback": False,
            }
        ],
    )

    out_path = tmp_path / "results.json"
    payload = runner.run_eval(
        cases_path=cases_path,
        chunks_jsonl=chunks_path,
        output_path=out_path,
        top_k=5,
        max_context_chars=2000,
        top_n=3,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    assert out_path.exists()
    assert payload["summary"]["total_cases"] == 1
    assert set(payload["summary"].keys()) >= {
        "schema_version",
        "runner_version",
        "generated_at",
        "total_cases",
        "passed_cases",
        "failed_cases",
        "gold_chunk_cases",
        "gold_chunk_hits",
        "gold_doc_cases",
        "gold_doc_hits",
        "abstain_cases",
        "abstain_passes",
    }

    case = payload["cases"][0]
    assert set(case.keys()) >= {
        "case_id",
        "category",
        "query",
        "intent",
        "before_rerank_top",
        "after_rerank_top",
        "final_guard_reason",
        "final_used_fallback",
        "expectations",
        "checks",
        "overall_pass",
        "evaluation_mode",
        "selected_context_preview",
    }
    assert case["before_rerank_top"]
    assert case["after_rerank_top"]
    assert set(case.keys()) >= {
        "gold_doc_ids",
        "gold_chunk_ids",
        "should_abstain",
        "gold_chunk_hit",
        "gold_chunk_best_rank",
        "gold_chunk_best_rank_before",
        "gold_chunk_best_rank_after",
        "gold_doc_hit",
        "gold_doc_best_rank",
        "gold_doc_best_rank_before",
        "gold_doc_best_rank_after",
        "abstain_check_pass",
        "rerank_top_changed",
        "rerank_gain",
        "before_rerank_ids",
        "after_rerank_ids",
    }


def test_runner_help_explains_deterministic_smoke_positioning():
    parser = runner._build_parser()
    help_text = parser.format_help()
    help_text_l = help_text.lower()
    help_text_flat = " ".join(help_text_l.split())
    assert "Lightweight repo-native smoke evaluator" in help_text
    assert "generation is stubbed" in help_text
    assert "retrieval is stubbed empty" in help_text
    assert "not a full live end-to-end answer quality benchmark" in help_text_flat


def test_repo_smoke_cases_run_and_include_rerank_movement(tmp_path):
    out_path = tmp_path / "smoke_results.json"
    payload = runner.run_eval(
        cases_path=runner._default_cases_path(),
        chunks_jsonl=runner._default_chunks_path(),
        output_path=out_path,
        top_k=20,
        max_context_chars=8000,
        top_n=3,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    assert out_path.exists()
    assert payload["summary"]["total_cases"] >= 13
    assert payload["summary"]["failed_cases"] == 0
    assert payload["summary"]["gold_chunk_cases"] == 0
    assert payload["summary"]["gold_doc_cases"] == 0
    assert payload["summary"]["abstain_cases"] == 0

    case_map = {c["case_id"]: c for c in payload["cases"]}
    assert "rerank_movement_01" in case_map
    assert "guard_trigger_02" in case_map

    movement = case_map["rerank_movement_01"]
    before_ids = [x["chunk_id"] for x in movement["before_rerank_top"]]
    after_ids = [x["chunk_id"] for x in movement["after_rerank_top"]]
    assert before_ids != after_ids
    assert movement["checks"]["expected_top_chunk_id"]["pass"] is True

    guard_case = case_map["guard_trigger_02"]
    assert guard_case["checks"]["expected_guard_reason"]["pass"] is True


def test_run_eval_optional_gold_fields_and_rerank_gain(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "rerank_gold",
                "category": "gold",
                "query": "\"QX12\" の手順",
                "expected_top_chunk_id": "mA",
                "gold_chunk_ids": ["mA"],
                "gold_doc_ids": ["movement.pdf"],
                "should_abstain": False,
            }
        ],
    )

    out_path = tmp_path / "results.json"
    payload = runner.run_eval(
        cases_path=cases_path,
        chunks_jsonl=runner._default_chunks_path(),
        output_path=out_path,
        top_k=20,
        max_context_chars=8000,
        top_n=5,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    case = payload["cases"][0]
    assert case["gold_chunk_hit"] is True
    assert case["gold_doc_hit"] is True
    assert case["gold_chunk_best_rank_before"] is not None
    assert case["gold_chunk_best_rank_after"] is not None
    assert case["gold_chunk_best_rank"] == case["gold_chunk_best_rank_after"]
    assert case["rerank_gain"] is not None
    assert case["rerank_gain"] > 0
    assert case["rerank_top_changed"] is True
    assert case["abstain_check_pass"] is True
    assert case["before_rerank_ids"]
    assert case["after_rerank_ids"]
    assert len(case["before_rerank_ids"]) <= 5
    assert len(case["after_rerank_ids"]) <= 5
    assert payload["summary"]["gold_chunk_cases"] == 1
    assert payload["summary"]["gold_chunk_hits"] == 1
    assert payload["summary"]["gold_doc_cases"] == 1
    assert payload["summary"]["gold_doc_hits"] == 1
    assert payload["summary"]["abstain_cases"] == 1
    assert payload["summary"]["abstain_passes"] == 1


def test_run_eval_should_abstain_optional_check(tmp_path):
    chunks_path = tmp_path / "chunks.jsonl"
    _write_jsonl(chunks_path, [])
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "abstain_case",
                "category": "abstain",
                "query": "テスト",
                "should_abstain": True,
            }
        ],
    )

    out_path = tmp_path / "results.json"
    payload = runner.run_eval(
        cases_path=cases_path,
        chunks_jsonl=chunks_path,
        output_path=out_path,
        top_k=5,
        max_context_chars=2000,
        top_n=3,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    case = payload["cases"][0]
    assert case["should_abstain"] is True
    assert case["abstain_check_pass"] is True
    assert case["rerank_gain"] is None
    assert payload["summary"]["abstain_cases"] == 1
    assert payload["summary"]["abstain_passes"] == 1


def test_rank_semantics_are_1_based_and_no_match_is_null():
    from rag_core.retrieval import RetrievedChunk

    chunks = [
        RetrievedChunk(text="x", metadata={"id": "c2", "source_doc": "d2"}, score=0.1),
        RetrievedChunk(text="x", metadata={"id": "c1", "source_doc": "d1"}, score=0.2),
        RetrievedChunk(text="x", metadata={"id": "c3", "source_doc": "d3"}, score=0.3),
    ]
    assert runner._best_rank_by_chunk_id(chunks, ["c1"]) == 2
    assert runner._best_rank_by_doc_id(chunks, ["d3"]) == 3
    assert runner._best_rank_by_chunk_id(chunks, ["none"]) is None
    assert runner._best_rank_by_doc_id(chunks, ["none"]) is None


def test_compact_ids_top5_and_missing_trace_is_null(tmp_path, monkeypatch):
    from rag_core.retrieval import RetrievedChunk

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "compact_ids",
                "category": "gold",
                "query": "q1",
                "gold_chunk_ids": ["c6"],
            },
            {
                "case_id": "missing_trace",
                "category": "abstain",
                "query": "q2",
                "should_abstain": False,
            },
        ],
    )

    seq_before = [
        RetrievedChunk(text=f"t{i}", metadata={"id": f"c{i}", "source_doc": "doc"}, score=float(i))
        for i in range(1, 8)
    ]
    seq_after = [
        RetrievedChunk(text=f"t{i}", metadata={"id": f"c{i}", "source_doc": "doc"}, score=float(i))
        for i in [2, 1, 3, 4, 5, 6, 7]
    ]
    calls = {"n": 0}

    def _fake_answer_query_with_trace(*args, **kwargs):
        del args, kwargs
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                SimpleNamespace(
                    intent="other",
                    guard_reason=None,
                    used_fallback=False,
                    answer_text="ok",
                    rewritten_query="q1",
                    augmented_query="q1",
                ),
                {
                    "before_rerank": seq_before,
                    "after_rerank": seq_after,
                    "selected_context_preview": [],
                },
            )
        return (
            SimpleNamespace(
                intent="other",
                guard_reason=None,
                used_fallback=True,
                answer_text="ng",
                rewritten_query="q2",
                augmented_query="q2",
            ),
            {
                "selected_context_preview": [],
            },
        )

    monkeypatch.setattr(runner.qa, "answer_query_with_trace", _fake_answer_query_with_trace)

    out_path = tmp_path / "results.json"
    payload = runner.run_eval(
        cases_path=cases_path,
        chunks_jsonl=None,
        output_path=out_path,
        top_k=10,
        max_context_chars=2000,
        top_n=3,
        real_vector=False,
        real_generation=False,
        quiet=True,
    )

    case_map = {c["case_id"]: c for c in payload["cases"]}
    compact = case_map["compact_ids"]
    assert compact["before_rerank_ids"] == ["c1", "c2", "c3", "c4", "c5"]
    assert compact["after_rerank_ids"] == ["c2", "c1", "c3", "c4", "c5"]
    assert compact["rerank_top_changed"] is True
    assert compact["gold_chunk_best_rank_before"] == 6
    assert compact["gold_chunk_best_rank_after"] == 6
    assert compact["rerank_gain"] == 0

    missing = case_map["missing_trace"]
    assert missing["before_rerank_ids"] is None
    assert missing["after_rerank_ids"] is None
    assert missing["rerank_top_changed"] is None
    assert missing["abstain_check_pass"] is False
