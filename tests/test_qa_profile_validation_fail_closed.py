from types import SimpleNamespace
import time

import pytest

from rag_core import qa
from schemas import AnswerResult


def _rule(required="必要", forced=True):
    return {"rule_id": "r", "applies_to_question_type": "other",
            "answer_must_contain_all": [required], "fallback_if_failed": forced}


def _apply(monkeypatch, *, answer="不正本文", fallback="", profile=None, guard=None):
    monkeypatch.setattr(qa, "load_rag_profile", lambda _: profile or {"validation_rules": [_rule()]})
    monkeypatch.setattr(qa, "detect_question_type", lambda *a: "other")
    monkeypatch.setattr(qa, "extractive_fallback", lambda *a: fallback)
    state = SimpleNamespace(selected_context=[], retrieved=[], rewritten_query="", augmented_query="")
    result = AnswerResult(answer_text=answer, answer_with_footnotes=answer, intent="other", guard_reason=guard,
                          used_fallback=bool(guard), citations=[])
    trace = {}
    result = qa._apply_profile_validation("質問", result, trace, state, rag_profile_id=None, started_at=time.perf_counter())
    return result, trace


@pytest.mark.parametrize("fallback", ["", "別の不正本文"])
def test_failed_fallback_rejects_without_citations(monkeypatch, fallback):
    result, trace = _apply(monkeypatch, fallback=fallback)
    assert result.guard_reason == "insufficient_evidence" and result.used_fallback
    assert result.citations == [] and "不正本文" not in result.answer_with_footnotes
    assert trace["profile_validation"]["passed"] is False
    assert ("profile_validation_after_fallback" in trace) == bool(fallback)
    assert trace["final_guard_reason"] == result.guard_reason
    assert trace["final_used_fallback"] is True and trace["answer_mode"] == "fallback"
    assert trace["citations_count"] == 0


def test_valid_fallback_and_report_only(monkeypatch):
    result, trace = _apply(monkeypatch, fallback="必要")
    assert result.answer_text == "必要" and result.used_fallback
    assert trace["profile_validation_after_fallback"]["passed"] is True
    result, trace = _apply(monkeypatch, profile={"validation_rules": [_rule(forced=False)]})
    assert result.answer_text == "不正本文" and trace["profile_validation"]["passed"] is False


def test_skips_are_not_passes(monkeypatch):
    for profile, guard in [({"warnings": ["bad"]}, None), ({"validation_rules": [_rule()]}, "soft_distance"),
                           ({"validation_rules": []}, None)]:
        _, trace = _apply(monkeypatch, profile=profile, guard=guard)
        assert trace["profile_validation"]["passed"] is False
        assert trace["profile_validation"]["skipped"] is True


def test_normal_pass(monkeypatch):
    result, trace = _apply(monkeypatch, answer="必要")
    assert result.answer_text == "必要" and trace["profile_validation"]["passed"] is True


def _stream_setup(monkeypatch, forced=True):
    state = SimpleNamespace(intent="other", query_type="other", guard_reason=None, used_fallback=False,
                            normalized_query="質問", selected_context=[], retrieved=[], rewritten_query="",
                            augmented_query="")
    monkeypatch.setattr(qa, "_build_retrieval_trace", lambda *a, **k: ({}, state, time.perf_counter()))
    monkeypatch.setattr(qa, "load_rag_profile", lambda _: {"validation_rules": [_rule(forced=forced)]})
    monkeypatch.setattr(qa, "detect_question_type", lambda *a: "other")
    monkeypatch.setattr(qa.llm, "generation_mode", lambda *a: "llm")
    monkeypatch.setattr(qa, "build_messages", lambda *a: [])
    monkeypatch.setattr(qa, "build_evidence_blocks", lambda *a: [])
    monkeypatch.setattr(qa, "_finalize_generated_answer", lambda raw, *a, **k: (raw, False))
    monkeypatch.setattr(qa, "extractive_fallback", lambda *a: "")


def test_forced_stream_never_discloses_invalid_answer(monkeypatch):
    _stream_setup(monkeypatch)
    monkeypatch.setattr(qa.llm_gateway, "invoke_stream", lambda *a, **k: iter(["不正", "本文"]))
    events = list(qa.answer_query_stream("質問", client=object()))
    assert [name for name, _ in events] == ["meta", "final"]
    result, trace = events[-1][1]
    assert result.guard_reason == "insufficient_evidence" and result.citations == []
    assert trace["profile_validation"]["passed"] is False


def test_forced_stream_valid_answer_matches_final(monkeypatch):
    _stream_setup(monkeypatch)
    monkeypatch.setattr(qa.llm_gateway, "invoke_stream", lambda *a, **k: iter(["必要", "な回答"]))
    events = list(qa.answer_query_stream("質問", client=object()))
    assert [name for name, _ in events] == ["meta", "delta", "final"]
    assert events[1][1]["text"] == events[-1][1][0].answer_text == "必要な回答"


def test_report_only_still_streams_incrementally(monkeypatch):
    _stream_setup(monkeypatch, forced=False)
    monkeypatch.setattr(qa.llm_gateway, "invoke_stream", lambda *a, **k: iter(["不正", "本文"]))
    events = list(qa.answer_query_stream("質問", client=object()))
    assert [name for name, _ in events] == ["meta", "delta", "delta", "final"]
    assert events[-1][1][0].answer_text == "不正本文"


def test_forced_stream_failure_and_size_limit_never_disclose(monkeypatch):
    _stream_setup(monkeypatch)
    def interrupted(*a, **k):
        yield "不正"
        raise RuntimeError("interrupted")
    for generator in (interrupted, lambda *a, **k: iter(["x" * 1_000_001])):
        monkeypatch.setattr(qa.llm_gateway, "invoke_stream", generator)
        events = []
        with pytest.raises((RuntimeError, ValueError)):
            for event in qa.answer_query_stream("質問", client=object()):
                events.append(event)
        assert [name for name, _ in events] == ["meta"]


def test_valid_extractive_fallback_keeps_real_citation(monkeypatch):
    chunk = qa.Chunk(id="1", text="必要な内容", source_doc="real.pdf", source_pages=(7,))
    monkeypatch.setattr(qa, "load_rag_profile", lambda _: {"validation_rules": [_rule()]})
    monkeypatch.setattr(qa, "detect_question_type", lambda *a: "other")
    monkeypatch.setattr(qa, "extractive_fallback", lambda *a: "必要な内容 [S1]")
    state = SimpleNamespace(selected_context=[chunk], retrieved=[], rewritten_query="", augmented_query="")
    answer = AnswerResult(answer_text="不正", answer_with_footnotes="不正", intent="other", guard_reason=None,
                          used_fallback=False, citations=[])
    trace = {}
    result = qa._apply_profile_validation("質問", answer, trace, state, rag_profile_id=None,
                                          started_at=time.perf_counter())
    assert result.used_fallback and len(result.citations) == 1
    assert result.citations[0].source_doc == "real.pdf"
    assert trace["citations_count"] == 1 and trace["profile_validation_after_fallback"]["passed"] is True
