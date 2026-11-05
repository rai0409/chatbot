import pytest

from eval.tenant_eval import shadow


def _answer(text="回答", *, guard=None, citation=True, distance=0.4):
    return {"answer_text": text, "guard_reason": guard, "has_citation": citation, "top_distance": distance}


def _fn(table, default=None):
    def fn(question):
        value = table.get(question, default if default is not None else _answer())
        if isinstance(value, Exception):
            raise value
        return value
    return fn


def test_compare_runs_counts_each_kind_of_difference_without_storing_text():
    questions = ["q0", "q1", "q2", "q3", "q4", "q5", "q6"]
    baseline = _fn({"q5": _answer("", guard="soft_distance")})
    candidate = _fn({
        "q1": _answer("別の回答"),
        "q2": _answer("", guard="soft_distance"),          # answered -> abstained
        "q3": _answer("回答", citation=False),             # citation lost
        "q4": _answer("回答", distance=0.55),              # same text, moved distance
        "q5": _answer("新しい回答"),                       # abstained -> answered
        "q6": RuntimeError("/srv/private/path"),           # candidate error
    })
    out = shadow.compare_runs(questions, baseline, candidate)
    s = out["summary"]
    assert (s["n"], s["changed_answer"], s["newly_abstained"], s["newly_answered"]) == (7, 1, 1, 1)
    assert (s["citation_lost"], s["citation_gained"], s["candidate_errors"], s["baseline_errors"]) == (1, 0, 1, 0)
    assert s["disagreement_rate"] == pytest.approx(3 / 7) and s["newly_abstained_rate"] == pytest.approx(1 / 7)
    assert s["distance_delta_abs_median"] == pytest.approx(0.0) and 0.1 < s["distance_delta_abs_p95"] <= 0.15
    text = str(out)
    assert "別の回答" not in text and "q1" not in text and "private" not in text
    assert [row["id"] for row in out["rows"]] == [f"q{i:05d}" for i in range(7)]
    assert out["rows"][6]["candidate_error"] == "RuntimeError" and out["rows"][6]["newly_abstained"] is False


def test_the_same_text_in_another_layout_is_not_a_change():
    out = shadow.compare_runs(["q"], _fn({"q": _answer("ＡＢＣ  の 回答")}), _fn({"q": _answer("abc の回答")}))
    assert out["summary"]["changed_answer"] == 0 and out["summary"]["disagreement_rate"] == 0.0


def test_a_run_of_nothing_and_identical_functions():
    assert shadow.compare_runs([], _fn({}), _fn({}))["summary"]["newly_abstained_rate"] is None
    out = shadow.compare_runs([f"q{i}" for i in range(10)], _fn({}), _fn({}))
    assert out["summary"]["disagreement_rate"] == 0.0 and out["summary"]["distance_delta_abs_median"] == 0.0


def _summary(n=200, **changes):
    base = {"n": n, "candidate_errors": 0, "baseline_errors": 0, "changed_answer": 0, "newly_abstained": 0,
            "newly_abstained_rate": 0.0, "newly_answered": 0, "citation_lost": 0, "disagreement_rate": 0.0}
    base.update(changes)
    return base


def test_the_verdict_needs_enough_questions_and_names_every_reason():
    assert shadow.verdict(_summary(50))["decision"] == "insufficient"
    assert shadow.verdict(_summary()) == {"decision": "ok", "reasons": []}
    bad = shadow.verdict(_summary(candidate_errors=2, newly_abstained=10, newly_abstained_rate=0.05, citation_lost=10,
                                  disagreement_rate=0.5))
    assert bad["decision"] == "review" and len(bad["reasons"]) == 4
    assert shadow.verdict(_summary(baseline_errors=2, candidate_errors=2))["decision"] == "ok"
    assert shadow.verdict(_summary(newly_abstained_rate=0.02))["decision"] == "ok"  # at the limit, not over


def test_the_evidence_line_carries_the_numbers_for_the_shadow_step():
    summary = _summary(newly_abstained=3, newly_abstained_rate=0.015, disagreement_rate=0.1)
    line = shadow.evidence_text(summary, shadow.verdict(summary))
    assert "影子比較 200 件" in line and "判定 ok" in line and "新たに棄権 3 件" in line and "理由" not in line
    review = shadow.verdict(_summary(candidate_errors=1))
    assert "理由: the candidate raised errors" in shadow.evidence_text(_summary(candidate_errors=1), review)
