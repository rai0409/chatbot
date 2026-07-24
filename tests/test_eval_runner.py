from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import config
from rag_core import qa
from rag_core.retrieval import RetrievedChunk

runner = importlib.import_module("eval.runner")


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_ndcg_at_k_deduplicates_relevant_chunk_and_document_ids():
    assert runner._ndcg_at_k(["chunk-1", "chunk-1"], ["chunk-1"], 2) == pytest.approx(1.0)
    assert runner._ndcg_at_k(["document-1", "document-1"], ["document-1"], 2) == pytest.approx(1.0)


def test_ndcg_at_k_uses_eval_k_for_dcg_and_idcg():
    assert runner._ndcg_at_k(["chunk-1", "chunk-2"], ["chunk-1", "chunk-2"], 1) == pytest.approx(1.0)


def test_retrieval_aware_eval_deduplicates_document_relevance_for_ndcg(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [{"case_id": "doc-case", "category": "retrieval", "query": "Q", "gold_doc_ids": ["doc-1"]}],
    )
    monkeypatch.setattr(
        runner.qa,
        "answer_query_with_trace",
        lambda *args, **kwargs: (
            SimpleNamespace(intent="other", guard_reason=None, used_fallback=False, answer_text="ok"),
            {
                "before_rerank": [],
                "after_rerank": [
                    RetrievedChunk(text="first", metadata={"id": "chunk-1", "source_doc": "doc-1"}, score=1.0),
                    RetrievedChunk(text="second", metadata={"id": "chunk-2", "source_doc": "doc-1"}, score=0.9),
                ],
            },
        ),
    )

    payload = runner.run_retrieval_aware_eval(
        cases_path=cases_path,
        chunks_jsonl=None,
        per_query_output_path=tmp_path / "rows.jsonl",
        summary_output_path=tmp_path / "summary.json",
        modes=["bm25_only"],
        top_k=2,
        max_context_chars=100,
        real_vector=False,
        real_generation=False,
        eval_k=2,
        quiet=True,
    )

    assert payload["rows"][0]["ndcg_at_k"] == pytest.approx(1.0)
    assert payload["summary"]["by_mode"]["bm25_only"]["mean_ndcg_at_k"] == pytest.approx(1.0)


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


def test_generation_mode_runtime_restores_after_exception(monkeypatch):
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "extractive")

    with pytest.raises(RuntimeError, match="eval failure"):
        with runner._generation_mode_runtime(real_generation=False):
            assert config.CHAT_GENERATION_MODE == "llm"
            raise RuntimeError("eval failure")

    assert config.CHAT_GENERATION_MODE == "extractive"


def test_deterministic_eval_uses_stub_generation_client():
    assert isinstance(runner._build_eval_client(real_vector=False, real_generation=False), runner._StubClient)


def _install_deterministic_rerank_movement(monkeypatch):
    """Make the QX12 case move mA deterministically from rank 2 to 1."""
    original_hybrid_retrieve = qa.hybrid_retrieve

    def deterministic_hybrid_retrieve(question, *args, **kwargs):
        normalized = (
            str(question or "")
            .replace("「", '"')
            .replace("」", '"')
        )

        if "QX12" in normalized and "手順" in normalized:
            return [
                RetrievedChunk(
                    text="QX120 の手順です。",
                    metadata={
                        "id": "mB",
                        "source_doc": "movement.pdf",
                        "source_pages": [18],
                        "retrieval_source": "keyword",
                    },
                    score=0.25,
                ),
                RetrievedChunk(
                    text="QX12 を確認できます。",
                    metadata={
                        "id": "mA",
                        "source_doc": "movement.pdf",
                        "source_pages": [17],
                        "retrieval_source": "keyword",
                    },
                    score=0.26,
                ),
            ]

        return original_hybrid_retrieve(
            question,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        qa,
        "hybrid_retrieve",
        deterministic_hybrid_retrieve,
    )


def test_repo_smoke_cases_run_and_include_rerank_movement(
    monkeypatch,
    tmp_path,
):
    _install_deterministic_rerank_movement(monkeypatch)
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


def test_run_eval_optional_gold_fields_and_rerank_gain(
    monkeypatch,
    tmp_path,
):
    _install_deterministic_rerank_movement(monkeypatch)
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


def test_run_retrieval_aware_eval_writes_jsonl_and_summary(tmp_path, monkeypatch):
    from rag_core.retrieval import RetrievedChunk

    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "ra1",
                "category": "retrieval",
                "query": "QX12",
                "gold_chunk_ids": ["c2"],
                "gold_doc_ids": ["doc2"],
                "expected_abstain": False,
                "query_type": "id_lookup",
            },
            {
                "case_id": "ra2",
                "category": "retrieval",
                "query": "unknown",
                "gold_chunk_ids": ["missing"],
                "answerable": False,
            },
        ],
    )

    def _fake_answer_query_with_trace(question, **kwargs):
        del kwargs
        if question == "QX12":
            return (
                SimpleNamespace(
                    intent="other",
                    guard_reason=None,
                    used_fallback=False,
                    answer_text="ok",
                    rewritten_query=question,
                    augmented_query=question,
                ),
                {
                    "before_rerank": [
                        RetrievedChunk(text="t", metadata={"id": "c1", "source_doc": "doc1"}, score=0.4),
                        RetrievedChunk(text="t", metadata={"id": "c2", "source_doc": "doc2"}, score=0.5),
                    ],
                    "after_rerank": [
                        RetrievedChunk(text="t", metadata={"id": "c2", "source_doc": "doc2"}, score=0.2),
                        RetrievedChunk(text="t", metadata={"id": "c1", "source_doc": "doc1"}, score=0.3),
                    ],
                    "selected_context_preview": [],
                },
            )
        return (
            SimpleNamespace(
                intent="other",
                guard_reason="no_results",
                used_fallback=True,
                answer_text="no",
                rewritten_query=question,
                augmented_query=question,
            ),
            {
                "before_rerank": [],
                "after_rerank": [],
                "selected_context_preview": [],
            },
        )

    monkeypatch.setattr(runner.qa, "answer_query_with_trace", _fake_answer_query_with_trace)

    per_query = tmp_path / "rows.jsonl"
    summary = tmp_path / "summary.json"
    payload = runner.run_retrieval_aware_eval(
        cases_path=cases_path,
        chunks_jsonl=None,
        per_query_output_path=per_query,
        summary_output_path=summary,
        modes=["bm25_only", "dense_only", "hybrid", "hybrid_rerank"],
        top_k=10,
        max_context_chars=2000,
        real_vector=False,
        real_generation=False,
        eval_k=5,
        quiet=True,
    )

    assert per_query.exists()
    assert summary.exists()
    lines = [json.loads(line) for line in per_query.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 8
    first = lines[0]
    assert set(first.keys()) >= {
        "question",
        "mode",
        "gold_doc_hit",
        "gold_chunk_hit",
        "best_rank_before_rerank",
        "best_rank_after_rerank",
        "rerank_gain",
        "guard_reason",
        "used_fallback",
        "expected_abstain",
        "abstain_correct",
        "before_rerank_ids",
        "after_rerank_ids",
        "mrr_at_k",
        "ndcg_at_k",
    }
    assert first["best_rank_before_rerank"] == 2
    assert first["best_rank_after_rerank"] == 1
    assert first["rerank_gain"] == 1
    assert first["gold_chunk_hit"] is True
    assert first["gold_doc_hit"] is True
    assert first["expected_abstain"] is False
    assert first["abstain_correct"] is True
    assert len(first["before_rerank_ids"]) <= 5
    assert len(first["after_rerank_ids"]) <= 5

    second_case_rows = [r for r in lines if r["case_id"] == "ra2"]
    assert all(r["expected_abstain"] is True for r in second_case_rows)
    assert all(r["abstain_correct"] is True for r in second_case_rows)
    assert all(r["gold_chunk_hit"] is None for r in second_case_rows)
    assert payload["summary"]["total_rows"] == 8
    assert set(payload["summary"]["by_mode"].keys()) == {
        "bm25_only",
        "dense_only",
        "hybrid",
        "hybrid_rerank",
    }
    for mode in payload["summary"]["by_mode"].values():
        assert mode["abstain_labeled_cases"] == 2
        assert mode["abstain_expected_cases"] == 1
        assert mode["abstain_passes"] == 2
        assert "abstain_cases" not in mode


def test_run_retrieval_aware_eval_rejects_unknown_mode(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "case_id": "x",
                "category": "c",
                "query": "q",
            }
        ],
    )
    with pytest.raises(ValueError):
        runner.run_retrieval_aware_eval(
            cases_path=cases_path,
            chunks_jsonl=None,
            per_query_output_path=tmp_path / "rows.jsonl",
            summary_output_path=tmp_path / "summary.json",
            modes=["unknown_mode"],
            top_k=5,
            max_context_chars=1000,
            real_vector=False,
            real_generation=False,
            quiet=True,
        )


def test_retrieval_cases_file_has_labels_and_abstain_coverage(tmp_path):
    cases_path = Path("eval/cases/retrieval_cases.jsonl")
    cases = runner.load_cases(cases_path)
    assert 20 <= len(cases) <= 30

    out_rows = tmp_path / "retrieval_rows.jsonl"
    out_summary = tmp_path / "retrieval_summary.json"
    payload = runner.run_retrieval_aware_eval(
        cases_path=cases_path,
        chunks_jsonl=runner._default_chunks_path(),
        per_query_output_path=out_rows,
        summary_output_path=out_summary,
        modes=["hybrid_rerank"],
        top_k=20,
        max_context_chars=8000,
        real_vector=False,
        real_generation=False,
        eval_k=5,
        quiet=True,
    )

    mode_summary = payload["summary"]["by_mode"]["hybrid_rerank"]
    assert mode_summary["cases"] == len(cases)
    assert (mode_summary["gold_chunk_cases"] > 0) or (mode_summary["gold_doc_cases"] > 0)
    assert mode_summary["abstain_labeled_cases"] > 0
    assert mode_summary["abstain_expected_cases"] > 0


def test_real_vector_collection_rebuild_records_metadata_and_uses_document_and_query_embeddings(
    tmp_path, monkeypatch
):
    chunks_path = tmp_path / "chunks.jsonl"
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(chunks_path, [{"id": "c1", "text": "本文", "doc_id": "doc1"}])
    _write_jsonl(cases_path, [{"case_id": "q1", "category": "c", "query": "質問", "gold_chunk_ids": ["c1"], "gold_doc_ids": ["doc1"]}])

    calls = {"documents": 0, "queries": 0, "collections": []}

    class _Collection:
        name = "eval_test_collection"
        metadata = {"embedding_dim": 2, "hnsw:space": "cosine"}

        def count(self):
            return 1

        def get(self, include=None, limit=None):
            return {"embeddings": [[0.1, 0.2]]}

        def modify(self, metadata=None):
            self.metadata = dict(metadata or {})

    collection = _Collection()

    def _fake_ingest(rows, **kwargs):
        row_list = list(rows)
        calls["documents"] += 1
        assert runner.embedder.embed_documents([row["text"] for row in row_list], client=kwargs["client"]) == [[0.1, 0.2]]
        return {"collection": collection.name, "ingested": len(row_list), "skipped": 0}

    monkeypatch.setattr(runner, "ingest_canonical_rows", _fake_ingest)
    monkeypatch.setattr(runner.store, "get_vectorstore", lambda **kwargs: collection)
    monkeypatch.setattr(runner.embedder, "embed_documents", lambda texts, client=None: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(runner.embedder, "embed_queries", lambda texts, client=None: calls.update(queries=calls["queries"] + 1) or [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(runner.embedding_provider, "active_fingerprint", lambda: {"embed_provider": "local", "embed_model": "MiniLM"})

    metadata = runner.build_real_vector_eval_collection(
        chunks_jsonl=chunks_path, cases_path=cases_path, collection_name=collection.name, client=object()
    )

    assert calls["documents"] == 1
    assert calls["queries"] == 1
    assert metadata == {
        "embedding_provider": "local", "embedding_model": "MiniLM", "embedding_dimension": 2,
        "normalization": "l2", "corpus_fingerprint": runner.source_jsonl_sha256(chunks_path),
        "collection_name": "eval_test_collection", "inserted_record_count": 1,
    }
    assert collection.metadata["collection_name"] == "eval_test_collection"


def test_real_vector_collection_rejects_missing_gold_and_production_collection(tmp_path, monkeypatch):
    chunks_path = tmp_path / "chunks.jsonl"
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(chunks_path, [{"id": "c1", "text": "本文", "doc_id": "doc1"}])
    _write_jsonl(cases_path, [{"case_id": "q1", "category": "c", "query": "質問", "gold_chunk_ids": ["missing"]}])

    with pytest.raises(ValueError, match="gold chunk ids missing"):
        runner.build_real_vector_eval_collection(chunks_jsonl=chunks_path, cases_path=cases_path)

    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.setattr(config, "VECTORSTORE_COLLECTION_NAME", "production_collection")
    with pytest.raises(ValueError, match="must not be the production"):
        runner.build_real_vector_eval_collection(
            chunks_jsonl=chunks_path, cases_path=cases_path, collection_name="production_collection"
        )


def test_eval_runtime_uses_stub_by_default_and_isolated_real_collection(monkeypatch):
    class _Collection:
        def query(self, **kwargs):
            return {"documents": [["本文"]], "metadatas": [[{"id": "c1", "tenant_id": "default"}]], "distances": [[0.1]]}

    seen = []
    monkeypatch.setattr(runner.retrieval.embedder, "embed_queries", lambda texts, client=None: [[0.1] for _ in texts])
    monkeypatch.setattr(
        runner.retrieval.store,
        "get_vectorstore",
        lambda **kwargs: seen.append(kwargs) or _Collection(),
    )

    with runner._eval_runtime(chunks_jsonl=None, stub_vector=True):
        assert runner.retrieval.vector_retrieve("質問", None, top_k=1) == []
    with runner._eval_runtime(chunks_jsonl=None, stub_vector=False, eval_collection_name="eval_test_collection"):
        hits = runner.retrieval.vector_retrieve("質問", None, top_k=1)

    assert hits[0].metadata["id"] == "c1"
    assert seen == [{"collection_name": "eval_test_collection", "create_if_missing": False}]


def test_real_dense_only_all_empty_is_recorded_as_error(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(cases_path, [{"case_id": "q1", "category": "c", "query": "質問"}])
    output_rows = tmp_path / "rows.jsonl"
    output_summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        runner,
        "build_real_vector_eval_collection",
        lambda **kwargs: {"collection_name": "eval_test_collection", "inserted_record_count": 1},
    )
    monkeypatch.setattr(runner, "_build_eval_client", lambda **kwargs: object())
    monkeypatch.setattr(
        runner.qa,
        "answer_query_with_trace",
        lambda *args, **kwargs: (
            SimpleNamespace(intent="other", guard_reason="no_results", used_fallback=True, answer_text="", rewritten_query="", augmented_query=""),
            {"before_rerank": [], "after_rerank": []},
        ),
    )

    with pytest.raises(RuntimeError, match="dense_only real-vector retrieval returned no candidates"):
        runner.run_retrieval_aware_eval(
            cases_path=cases_path, chunks_jsonl=tmp_path / "chunks.jsonl",
            per_query_output_path=output_rows, summary_output_path=output_summary,
            modes=["dense_only"], top_k=1, max_context_chars=100, real_vector=True,
            real_generation=False, quiet=True,
        )

    assert json.loads(output_summary.read_text(encoding="utf-8"))["status"] == "error"
