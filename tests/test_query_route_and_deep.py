import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache, interpretation, qa, query_route, retrieval, search_mode
from rag_core.retrieval import RetrievedChunk
from webapi import main, metrics_registry
from webapi.routers import chat as chat_router


# --- routing ---------------------------------------------------------------------------------------


def test_routes_apply_only_when_enabled_and_only_to_routed_types(monkeypatch):
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", False)
    assert query_route.route_for("identifier") is None
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", True)
    route = query_route.route_for("identifier")
    assert route["name"] == "identifier" and route["scale"]["keyword"] > 1 > route["scale"]["dense"]
    assert query_route.route_for("other") is None and query_route.route_for("procedure") is None
    route["scale"]["dense"] = 99
    assert query_route.ROUTES["identifier"]["dense"] != 99  # the table is not handed out
    for scale in query_route.ROUTES.values():
        assert set(scale) == {"dense", "keyword", "exact"} and all(0 < v < 3 for v in scale.values())


def test_fusion_scale_multiplies_the_lane_weights_inside_the_block_only(monkeypatch):
    for name in ("HYBRID_DENSE_WEIGHT", "HYBRID_KEYWORD_WEIGHT", "HYBRID_EXACT_WEIGHT"):
        monkeypatch.setattr(config, name, 2.0)
    plain = retrieval._fusion_settings()
    assert (plain["dense_weight"], plain["keyword_weight"], plain["exact_weight"]) == (2.0, 2.0, 2.0)
    with retrieval.fusion_scale({"dense": 0.5, "keyword": 1.5}):
        scaled = retrieval._fusion_settings()
        assert (scaled["dense_weight"], scaled["keyword_weight"], scaled["exact_weight"]) == (1.0, 3.0, 2.0)
        with retrieval.fusion_scale(None):
            assert retrieval._fusion_settings()["dense_weight"] == 2.0
        assert retrieval._fusion_settings()["dense_weight"] == 1.0
    assert retrieval._fusion_settings() == plain
    with pytest.raises(RuntimeError):
        with retrieval.fusion_scale({"dense": 0.1}):
            raise RuntimeError("boom")
    assert retrieval._fusion_settings() == plain


def _stage(monkeypatch, seen):
    chunk = RetrievedChunk(text="本文", metadata={"id": "A"}, score=0.3)

    def hybrid(query, *args, **kwargs):
        seen.append(retrieval._fusion_settings()["keyword_weight"])
        return [chunk]

    monkeypatch.setattr(qa, "hybrid_retrieve", hybrid)
    monkeypatch.setattr(qa, "rerank_chunks", lambda question, items, intent=None: list(items))
    monkeypatch.setattr(qa, "_decorate_score_details", lambda question, items, query_type=None: list(items))
    monkeypatch.setattr(qa, "expand_parent_chunks", lambda items, **kwargs: list(items))
    monkeypatch.setattr(config, "KEYWORD_BOOST_ENABLED", False)
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", False)
    monkeypatch.setattr(config, "PROFILE_BOOST_ENABLED", False)
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(config, "HYBRID_KEYWORD_WEIGHT", 1.0)

    def run(query_type):
        stats = {}
        qa._retrieve_and_rerank("ABC-123 の手順", "ABC-123 の手順", scoring_query="ABC-123 の手順", client=None, top_k=5,
                                intent="other", query_type=query_type, stats=stats)
        return stats

    return run


def test_the_retrieval_stage_searches_with_the_routed_weights(monkeypatch):
    seen = []
    run = _stage(monkeypatch, seen)
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", False)
    assert "route" not in run("identifier") and seen == [1.0]
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", True)
    stats = run("identifier")
    assert seen[-1] == pytest.approx(query_route.ROUTES["identifier"]["keyword"])
    assert stats["route"]["name"] == "identifier" and stats["route"]["scale"]["keyword"] == seen[-1]
    assert "route" not in run("procedure") and seen[-1] == 1.0  # an unrouted type uses the configured weights
    assert retrieval._fusion_settings()["keyword_weight"] == 1.0  # nothing leaks out of the stage


# --- interpretation --------------------------------------------------------------------------------------


def test_the_interpretation_names_the_type_and_what_was_searched():
    stats = {"multi_query": {"variants": [{"query": "別の言い方", "kind": "synonym"}], "deep": True},
             "route": {"name": "identifier", "scale": {}}}
    result = interpretation.build(question="元の質問", retrieval_query="整えた質問", query_type="identifier",
                                  intent="faq", expansions=["同義語A", "同義語B"], stats=stats)
    assert result["type_label"] == "識別子・番号の照会" and result["cleaned_query"] == "整えた質問"
    assert result["expansions"] == ["同義語A", "同義語B"] and result["variants"] == ["別の言い方"]
    assert result["route"] == "identifier" and result["deep"] is True
    assert result["summary"] == ("識別子・番号の照会として解釈しました。検索語: 整えた質問。言い換えも検索: 同義語A、同義語B。"
                                 "別の言い方でも検索: 別の言い方")
    json.dumps(result)


def test_a_plain_question_gets_a_short_summary_and_unknown_types_are_general():
    result = interpretation.build(question="質問", retrieval_query="質問", query_type="weird", intent="other",
                                  expansions=[], stats=None)
    assert result["summary"] == "一般的な質問として解釈しました" and result["cleaned_query"] is None
    assert result["variants"] == [] and result["route"] is None and result["deep"] is False
    long = interpretation.build(question="q", retrieval_query="x" * 500, query_type="faq", intent="o", expansions=[], stats={})
    assert len(long["summary"]) == interpretation.SUMMARY_LIMIT


def test_the_trace_of_a_question_carries_the_interpretation(monkeypatch):
    seen = []
    _stage(monkeypatch, seen)
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", False)
    monkeypatch.setattr(qa, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(qa.embedding_provider, "is_local_provider", lambda: True)
    trace, _state, _started = qa._build_retrieval_trace("サーバーの再起動手順は？", client=None, top_k=5, max_context_chars=2000)
    assert trace["interpretation"]["summary"].endswith("として解釈しました") or "解釈しました" in trace["interpretation"]["summary"]
    assert trace["interpretation"]["query_type"] == trace["query_type"]


# --- the API ---------------------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "TOP_K", 20)
    answer_cache.clear()
    metrics_registry.reset()
    seen = {"chat": [], "stream": []}

    def result():
        return SimpleNamespace(intent="faq", guard_reason=None, used_fallback=False, citations=[],
                               to_dict=lambda: {"answer_text": "回答です", "citations": [], "retrieved": []})

    def trace(question):
        return {"request_id": "r1", "normalized_query": question, "intent": "faq", "final_guard_reason": None,
                "final_used_fallback": False, "citations_count": 0, "latency_ms": 1, "after_rerank": [],
                "answer_mode": "grounded",
                "interpretation": {"summary": "手順の質問として解釈しました", "deep": search_mode.is_deep()}}

    def chat(question, **kwargs):
        seen["chat"].append({"deep": search_mode.is_deep(), "top_k": kwargs.get("top_k")})
        return result(), trace(question)

    def stream(question, **kwargs):
        seen["stream"].append({"deep": search_mode.is_deep(), "top_k": kwargs.get("top_k")})
        yield "delta", {"text": "回"}
        seen["stream"].append({"deep": search_mode.is_deep(), "top_k": kwargs.get("top_k")})
        yield "final", (result(), trace(question))

    monkeypatch.setattr(chat_router, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(chat_router, "answer_query_with_trace", chat)
    monkeypatch.setattr(chat_router, "answer_query_stream", stream)
    monkeypatch.setattr(chat_router, "_approved_qa_lookup", lambda *a, **k: None)
    monkeypatch.setattr(chat_router, "append_audit_event", lambda kind, event: None)
    return SimpleNamespace(client=TestClient(main.app), seen=seen)


def test_chat_returns_the_interpretation_and_a_normal_request_is_not_deep(api):
    resp = api.client.post("/chat", json={"question": "質問です"})
    assert resp.status_code == 200
    assert resp.json()["interpretation"] == {"summary": "手順の質問として解釈しました", "deep": False}
    assert api.seen["chat"] == [{"deep": False, "top_k": 20}]
    assert search_mode.is_deep() is False


def test_a_deep_chat_request_doubles_top_k_and_runs_in_deep_mode(api):
    resp = api.client.post("/chat", json={"question": "質問です", "deep": True})
    assert resp.status_code == 200 and resp.json()["interpretation"]["deep"] is True
    assert api.seen["chat"] == [{"deep": True, "top_k": 40}]
    capped = api.client.post("/chat", json={"question": "質問です", "deep": True, "top_k": main._MAX_TOP_K})
    assert capped.status_code == 200 and api.seen["chat"][-1]["top_k"] == main._MAX_TOP_K
    assert search_mode.is_deep() is False
    assert api.client.post("/chat", json={"question": "質問です", "deep": "maybe"}).status_code == 422


def test_a_deep_request_neither_reads_nor_writes_the_answer_cache(api, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(main.answer_cache, "build_key", lambda *a, **k: ("same-key",))
    first = api.client.post("/chat", json={"question": "質問です"})
    again = api.client.post("/chat", json={"question": "質問です"})
    assert first.status_code == again.status_code == 200 and len(api.seen["chat"]) == 1  # the second came from the cache
    deep = api.client.post("/chat", json={"question": "質問です", "deep": True})
    assert deep.status_code == 200 and len(api.seen["chat"]) == 2 and api.seen["chat"][-1]["deep"] is True
    after = api.client.post("/chat", json={"question": "質問です"})
    assert after.json()["interpretation"]["deep"] is False and len(api.seen["chat"]) == 2  # still the normal answer


def _final_event(text):
    for block in text.split("\n\n"):
        if block.startswith("event: final"):
            return json.loads(block.split("data: ", 1)[1])
    raise AssertionError("no final event")


def test_the_stream_returns_the_interpretation_and_deep_applies_while_the_pipeline_runs(api):
    plain = api.client.post("/chat/stream", json={"question": "質問です"})
    assert plain.status_code == 200 and _final_event(plain.text)["interpretation"]["deep"] is False
    assert [s["deep"] for s in api.seen["stream"]] == [False, False]
    api.seen["stream"].clear()
    deep = api.client.post("/chat/stream", json={"question": "質問です", "deep": True})
    assert _final_event(deep.text)["interpretation"] == {"summary": "手順の質問として解釈しました", "deep": True}
    assert [s["deep"] for s in api.seen["stream"]] == [True, True] and api.seen["stream"][0]["top_k"] == 40
    assert search_mode.is_deep() is False


# --- the chat page ---------------------------------------------------------------------------------------


HTML = Path(__file__).resolve().parents[1] / "webapi" / "static" / "chat.html"


def test_the_chat_page_offers_the_deep_search_and_shows_the_interpretation():
    text = HTML.read_text(encoding="utf-8")
    assert "もっと詳しく調べる" in text and "send(ctx.question, { deep: true })" in text
    assert "if (ctx.deep) body.deep = true;" in text and "function send(text, opts)" in text
    assert "data.interpretation.summary" in text and "interp.textContent" in text and ".interp {" in text
    assert text.count("もっと詳しく調べる") == 1  # one button, built once per answer


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_inline_script_of_the_chat_page_is_valid_javascript():
    html = HTML.read_text(encoding="utf-8")
    scripts = [s for s in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S) if s.strip()]
    assert scripts
    for script in scripts:
        done = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=script,
                              text=True, capture_output=True, timeout=60)
        assert done.returncode == 0, done.stderr
