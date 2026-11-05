from __future__ import annotations

import json
from types import SimpleNamespace

import config
from rag_core import qa
from rag_core.retrieval import RetrievedChunk
from webapi import main
from webapi.routers import chat as chat_router


def _mk_chunk(label: str, text: str, score: float = 0.25, page: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        metadata={
            "id": label,
            "source_doc": "doc.pdf",
            "source_pages": [page],
            "retrieval_source": "hybrid",
        },
        score=score,
    )


def _fake_client(content: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    completions = SimpleNamespace(create=lambda **kwargs: response)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _forbidden_client():
    def _raise(**kwargs):
        raise AssertionError("chat completion must not be called on guard fallback")

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_raise)))


def test_guard_fallback_has_no_citations_and_no_source_tags(monkeypatch):
    hits = [_mk_chunk("A", "一般説明です。")]
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *args, **kwargs: hits)
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: "soft_distance")

    res = qa.answer_query("PR2 を教えて", client=_forbidden_client(), top_k=5)

    assert res.guard_reason == "soft_distance"
    assert res.used_fallback is True
    assert res.citations == []
    assert "[S" not in res.answer_text
    assert "[S" not in res.answer_with_footnotes
    assert "参考資料" not in res.answer_with_footnotes
    assert "関連情報が見つかりませんでした" in res.answer_text


def test_missing_procedure_evidence_fallback_has_no_citations(monkeypatch):
    hits = [_mk_chunk("A", "関連しそうな一般説明です。")]
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *args, **kwargs: hits)

    res = qa.answer_query(
        "暗証番号を再設定したい",
        client=_forbidden_client(),
        top_k=5,
        intent_override="reset",
    )

    assert res.guard_reason == "missing_procedure_evidence"
    assert res.used_fallback is True
    assert res.citations == []
    assert "[S" not in res.answer_text
    assert "参考資料" not in res.answer_with_footnotes
    assert "手順の記載が見つかりません" in res.answer_text


def test_grounded_answer_still_produces_real_citations(monkeypatch):
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "llm")
    hits = [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *args, **kwargs: hits)
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: None)

    res = qa.answer_query(
        "PR2 を教えて",
        client=_fake_client("- PR2 は二段階確認の説明です [S1]\n不足: なし [S1]"),
        top_k=5,
    )

    assert res.guard_reason is None
    assert res.used_fallback is False
    assert len(res.citations) == 1
    assert res.citations[0].source_doc == "doc.pdf"
    assert "参考資料:" in res.answer_with_footnotes
    assert "[1]" in res.answer_with_footnotes


def test_extractive_fallback_still_cites_real_chunks(monkeypatch):
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "llm")
    hits = [_mk_chunk("A", "PR2 は二段階確認の説明です。")]
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *args, **kwargs: hits)
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: None)

    res = qa.answer_query(
        "PR2 を教えて",
        client=_fake_client("箇条書きではない不正な形式の出力"),
        top_k=5,
    )

    assert res.guard_reason is None
    assert res.used_fallback is True
    assert len(res.citations) >= 1
    assert "参考資料:" in res.answer_with_footnotes


def test_approved_exact_match_citations_preserved(monkeypatch, tmp_path):
    record = {
        "qa_id": "qa-cit-1",
        "question": "営業時間を教えてください",
        "approved_answer": "営業時間は9時から17時です。",
        "status": "approved",
        "tenant_id": "default",
        "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [3]}],
    }
    path = tmp_path / "approved.jsonl"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(chat_router, "_approved_qa_cache", {})

    payload = main.chat(main.ChatRequest(question="営業時間を教えてください"))

    assert payload["answer_mode"] == "approved_exact_match"
    assert len(payload["citations"]) == 1
    assert payload["citations"][0]["source_doc"] == "faq.pdf"
    assert payload["citations"][0]["source_pages"] == [3]


def test_a_guard_without_any_grounded_chunk_invents_no_evidence_in_the_trace(monkeypatch):
    # No retrieved chunk at all: the old code put a made-up chunk ("1", "該当なし", source "unknown")
    # into the selected context, which then appeared in traces, audit events and evaluation output.
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *args, **kwargs: [])
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: "soft_distance")

    res, trace = qa.answer_query_with_trace("存在しない話題について", client=_forbidden_client(), top_k=5)

    assert res.used_fallback is True and res.citations == []
    assert trace["selected_context_chunk_ids"] == []
    assert trace["selected_context_preview"] == [] and trace["selected_context_chars"] == 0
    assert "unknown" not in json.dumps(trace, ensure_ascii=False, default=str)


def test_the_missing_procedure_guard_also_has_no_invented_chunk(monkeypatch):
    monkeypatch.setattr(qa, "hybrid_retrieve", lambda *args, **kwargs: [])
    monkeypatch.setattr(qa, "guard_merged_top", lambda *args, **kwargs: None)

    _res, trace = qa.answer_query_with_trace(
        "暗証番号を再設定したい", client=_forbidden_client(), top_k=5, intent_override="reset"
    )
    assert "OCR" not in trace.get("selected_context_preview", [])
    assert trace["selected_context_chunk_ids"] in ([], ["A"])
