import json
import os
import sys
import types

import pytest

from tools import eval_product_pipeline_jagov as evaluator


def test_rank_metrics_and_missing_dataset(tmp_path, monkeypatch):
    assert evaluator.rank_metrics([1, 3, None])["R@1"] == 33.3
    assert evaluator.rank_metrics([])["MRR@10"] is None
    monkeypatch.setattr(sys, "argv", ["eval", "--data", str(tmp_path / "missing"), "--work", str(tmp_path)])
    with pytest.raises(SystemExit, match="dataset missing"):
        evaluator.main()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("backend", ["chroma", "qdrant"])
def test_isolated_evaluation_smoke_without_services(tmp_path, monkeypatch, backend):
    data = tmp_path / "data.jsonl"
    rows = [{"Question": "架空の受付時間について教えてください", "Answer": "架空の受付時間は午前九時から午後五時までです。"}, {"Question": "架空の申請期限について教えてください", "Answer": "架空の申請期限は毎月の最終日となります。"}]
    data.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    work = tmp_path / "work"
    calls = []
    def ingest_rows(items, **kwargs):
        calls.append(kwargs)
        assert kwargs["reset"] is False and kwargs["replace_documents"] is False
        return {"ingested": len(items)}
    def write_rows(path, items, **kwargs):
        path.write_text("synthetic", encoding="utf-8")
    fake_ingest = types.SimpleNamespace(new_generation_id=lambda: "g", ingest_canonical_rows=ingest_rows, write_keyword_rows=write_rows)
    fake_retrieval = types.SimpleNamespace(hybrid_retrieve=lambda *a, **k: [types.SimpleNamespace(metadata={"source_doc": "jg_00000.pdf"})])
    def answer(query, **kwargs):
        if "EVAL-MISSING-" in query:
            return types.SimpleNamespace(guard_reason="no_results", answer_text="", citations=[]), {}, 0
        return types.SimpleNamespace(guard_reason=None, answer_text="架空の受付時間は午前九時から午後五時までです。", citations=[types.SimpleNamespace(source_doc="jg_00000.pdf")]), {}, 0
    fake_qa = types.SimpleNamespace(answer_query_with_trace=lambda *a, **k: answer(*a, **k)[:2])
    fake_embed = types.SimpleNamespace(is_local_provider=lambda: True, active_fingerprint=lambda: {"embed_model": "synthetic"})
    fake_rag = types.ModuleType("rag_core"); fake_rag.qa = fake_qa; fake_rag.retrieval = fake_retrieval; fake_rag.embedding_provider = fake_embed
    fake_scripts = types.ModuleType("scripts"); fake_scripts.ingest_canonical_jsonl = fake_ingest
    fake_config = types.ModuleType("config")
    for key in ("VECTORSTORE_DIR", "CHUNKS_JSONL_PATH", "VECTORSTORE_COLLECTION_NAME", "HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "CHAT_GENERATION_MODE", "LLM_ENABLED", "QDRANT_PATH", "INDEX_DIR", "RUNS_DIR", "KEYWORD_INDEX_PERSIST", "VECTOR_BACKEND", "CHROMA_COLLECTION"):
        monkeypatch.setenv(key, "restore-me")
    monkeypatch.setitem(sys.modules, "scripts", fake_scripts)
    original_configure = evaluator.configure
    def configure_with_test_modules(*args, **kwargs):
        original_configure(*args, **kwargs)
        monkeypatch.setitem(sys.modules, "rag_core", fake_rag)
        monkeypatch.setitem(sys.modules, "config", fake_config)
    monkeypatch.setattr(evaluator, "configure", configure_with_test_modules)
    monkeypatch.setattr(sys, "argv", ["eval", "--docs", "2", "--queries", "2", "--unanswerable", "1", "--data", str(data), "--work", str(work), "--backend", backend])
    for _ in range(2):
        for name in list(sys.modules):
            if name == "config" or name == "rag_core" or name.startswith("rag_core."):
                monkeypatch.delitem(sys.modules, name)
        assert evaluator.main() == 0
    assert len(calls) == 2 and calls[0]["collection_name"] != calls[1]["collection_name"]
    reports = sorted(work.glob("jagov-run-*/report.json"))
    assert len(reports) == 2
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["settings"]["backend"] == backend and len(report["data_sha256"]) == 64
    assert "not_comparable" in report["evaluation_series"]
    assert report["answer"]["unanswerable"]["wrong_answer_pct"] == 0.0
    assert report["answer"]["full"]["label_status"] == "source_answer_present_no_exact_conflicts"
    assert report["answer"]["keyword"]["false_abstain_pct"] is None
    assert report["answer"]["partial"]["strict_wrong_answer_pct_of_all"] is None
    assert "strict_answer_correct_pct_of_all" in report["answer"]["full"]
    assert "gold_cited_pct_of_all" in report["answer"]["full"]
    assert os.environ["QDRANT_PATH"].startswith(str(work))
    assert all(os.environ[key].startswith(str(work)) for key in ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "XDG_CACHE_HOME"))
    assert os.environ["LLM_ENABLED"] == "false" and os.environ["CHAT_GENERATION_MODE"] == "extractive"


def test_maintenance_actions_fail_closed_without_mutating_files(tmp_path):
    from tools import commercial_fixes_and_qdrant_measure as tool
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    assert tool.run_fixes(tmp_path, True, []) == 2
    assert tool.run_measure(tmp_path, [1, 2, 3, 4], True, "http://127.0.0.1:6333", "qdrant:latest", [1], 8017) == 2
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sentinel"]


def test_maintenance_preflight_failure_never_changes_any_file(tmp_path, monkeypatch):
    from tools import commercial_fixes_and_qdrant_measure as tool
    for nodes in tool._INTEGRATED_CHECKS.values():
        for node in nodes:
            path = tmp_path / node.split("::", 1)[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic", encoding="utf-8")
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        return types.SimpleNamespace(returncode=1 if len(calls) == len(tool._INTEGRATED_CHECKS) else 0)
    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    assert tool.run_fixes(tmp_path, True, []) == 1
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_maintenance_measure_uses_only_fresh_embedded_qdrant(tmp_path, monkeypatch):
    from tools import commercial_fixes_and_qdrant_measure as tool
    calls = []
    monkeypatch.setattr(tool, "_call", lambda command, env, repo, run: calls.append((command, env, run)) or 0)
    assert tool.run_measure(tmp_path, [2, 3], True, "http://127.0.0.1:6333", "", [1], 8017) == 0
    assert len(calls) == 4 and all(run for _, _, run in calls)
    assert all(env["QDRANT_URL"] == "" and env["QDRANT_PATH"].startswith("/tmp/chatbot-qdrant-measure-") for _, env, _ in calls)
    assert "qdrant-local" in calls[1][0] and "qdrant-local" in calls[2][0]
    assert calls[3][0][-4:-2] == ["--backend", "qdrant"]
    before = len(calls)
    assert tool.run_measure(tmp_path, [2], True, "http://shared.example:6333", "", [1], 8017) == 2
    assert tool.run_measure(tmp_path, [1, 2], True, "http://127.0.0.1:6333", "", [1], 8017) == 2
    assert len(calls) == before


def test_evaluation_rejects_preloaded_application_before_writes(tmp_path, monkeypatch):
    data = tmp_path / "data.jsonl"
    data.write_text("{}\n", encoding="utf-8")
    work = tmp_path / "work"
    monkeypatch.setitem(sys.modules, "config", types.ModuleType("config"))
    monkeypatch.setattr(sys, "argv", ["eval", "--data", str(data), "--work", str(work)])
    with pytest.raises(SystemExit, match="fresh process"):
        evaluator.main()
    assert not work.exists()
