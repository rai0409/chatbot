import json
import math

import pytest

from eval.tenant_eval import evaluate, gate, ledger, metrics, runner, stats
from eval.tenant_eval.gate import Policy
from rag_core import golden_qa


# --- stats ---------------------------------------------------------------------------------------


def test_clopper_pearson_matches_known_values():
    assert stats.clopper_pearson_upper(0, 59) == pytest.approx(1 - 0.05 ** (1 / 59), abs=1e-6)
    assert stats.clopper_pearson_upper(1, 10) == pytest.approx(0.3942, abs=1e-3)
    assert stats.clopper_pearson_lower(5, 10) == pytest.approx(0.2224, abs=1e-3)
    assert stats.clopper_pearson_upper(10, 10) == 1.0 and stats.clopper_pearson_lower(0, 10) == 0.0
    assert stats.clopper_pearson_upper(0, 0) == 1.0
    assert stats.clopper_pearson_upper(3, 5000) < 0.002  # a large n does not overflow


@pytest.mark.parametrize("k, n, alpha", [(-1, 5, 0.05), (6, 5, 0.05), (1, 5, 0.0), (1, 5, 1.0)])
def test_binomial_bounds_refuse_bad_input(k, n, alpha):
    with pytest.raises(ValueError):
        stats.clopper_pearson_upper(k, n, alpha)


def test_cases_needed_to_show_a_rate_under_a_limit():
    assert stats.min_cases_for_zero_failures(0.01) == 299
    assert stats.clopper_pearson_upper(0, 299) <= 0.01 < stats.clopper_pearson_upper(0, 298)
    assert stats.rule_of_three_upper(300) == pytest.approx(0.01) and stats.rule_of_three_upper(0) == 1.0
    with pytest.raises(ValueError):
        stats.min_cases_for_zero_failures(0.0)


def test_benjamini_hochberg():
    result = stats.benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5})
    assert result["a"]["p_adjusted"] == pytest.approx(0.04)
    assert result["b"]["p_adjusted"] == pytest.approx(0.04 * 4 / 3) and result["c"]["p_adjusted"] == pytest.approx(0.04 * 4 / 3)
    assert result["d"]["p_adjusted"] == pytest.approx(0.5)
    assert [name for name, r in result.items() if r["reject"]] == ["a"]
    assert stats.benjamini_hochberg({}) == {}


# --- runner and metrics -----------------------------------------------------------------------------


_TYPES = {}  # question -> the question type the fake pipeline reports for it


def _case(case_id, *, qtype="measure", priority=0, group="", expected="正解", forbidden=()):
    _TYPES[f"質問 {case_id}"] = qtype
    return golden_qa.parse_case({
        "case_id": case_id, "question": f"質問 {case_id}", "expected_all": [expected], "forbidden_any": list(forbidden),
        "expected_question_type": qtype, "priority": priority, "group": group,
    })[0]


def _fn(answers):
    """answers: case question -> dict, an exception instance, or a string (the answer text)."""

    def answer(question):
        value = answers.get(question, "正解です")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            return {"answer_text": value, "has_citation": True, "question_type": _TYPES.get(question, "measure"),
                    "guard_reason": None}
        return value

    return answer


def test_run_cases_judges_each_case_and_survives_a_failing_one():
    cases = [_case("a"), _case("b"), _case("c"), _case("d", forbidden=["禁止"])]
    rows = runner.run_cases(cases, _fn({
        "質問 b": "まちがい",
        "質問 c": RuntimeError("/srv/secret/path failed"),
        "質問 d": "正解 禁止",
    }))
    by_id = {row["case_id"]: row for row in rows}
    assert [row["case_id"] for row in rows] == ["a", "b", "c", "d"]
    assert by_id["a"]["passed"] is True and by_id["a"]["answered"] is True and by_id["a"]["slice"] == "measure"
    assert by_id["b"]["failures"] == ["missing_all"] and by_id["b"]["answered"] is True
    assert by_id["c"]["failures"] == ["error"] and by_id["c"]["error"] == "RuntimeError"
    assert "secret" not in json.dumps(by_id["c"]) and by_id["c"]["answered"] is False
    assert by_id["d"]["failures"] == ["forbidden"]
    assert all(row["latency_ms"] >= 0 for row in rows)


def test_a_guarded_or_empty_answer_is_an_abstention_not_a_false_accept():
    cases = [_case("a"), _case("b"), _case("c")]
    rows = runner.run_cases(cases, _fn({
        "質問 a": {"answer_text": "", "guard_reason": "soft_distance", "question_type": "measure"},
        "質問 b": {"answer_text": "正解", "guard_reason": "hard_distance", "question_type": "measure"},
        "質問 c": "別の答え",
    }))
    assert [row["answered"] for row in rows] == [False, False, True]
    summary = metrics.summarize(rows)
    assert summary["n"] == 3 and summary["abstained"] == 2 and summary["false_accepts"] == 1
    assert summary["pass_rate"] == 0.0 and summary["false_accept_rate"] == pytest.approx(1 / 3)
    assert summary["false_accept_upper"] > summary["false_accept_rate"]


def test_summaries_by_slice_and_of_nothing():
    rows = runner.run_cases([_case("a", qtype="x"), _case("b", qtype="y"), _case("c", qtype="y")], _fn({"質問 c": "誤"}))
    parts = metrics.by_slice(rows)
    assert list(parts) == ["x", "y"] and parts["y"]["n"] == 2 and parts["y"]["passed"] == 1
    empty = metrics.summarize([])
    assert empty["pass_rate"] is None and empty["false_accept_upper"] is None and empty["latency_ms"] is None


def test_the_eval_set_digest_ignores_order_and_follows_content():
    a, b = _case("a"), _case("b")
    assert runner.eval_set_digest([a, b]) == runner.eval_set_digest([b, a])
    assert runner.eval_set_digest([a, b]) != runner.eval_set_digest([a, _case("b", expected="別")])


# --- gate ------------------------------------------------------------------------------------------


def _rows(n, *, fail=(), answered=True, protected=(), slices=("x", "y")):
    """n rows; `fail` is a set of indexes that fail; a failing answered row is a false accept."""
    rows = []
    for i in range(n):
        failed = i in fail
        rows.append({
            "case_id": f"c{i:04d}", "passed": not failed, "failures": ["missing_all"] if failed else [],
            "answered": answered, "slice": slices[i % len(slices)], "group": f"g{i}",
            "priority": 100 if i in protected else 0, "latency_ms": 1.0,
        })
    return rows


def _check(result, name):
    return next(check for check in result["checks"] if check["name"] == name)


def test_identical_runs_pass_only_when_enough_cases_prove_the_false_accept_bound():
    small = gate.compare(_rows(100), _rows(100))
    assert small["decision"] == "insufficient" and _check(small, "false_accept")["status"] == "insufficient"
    assert _check(small, "overall_non_inferiority")["status"] == "pass"
    large = gate.compare(_rows(300), _rows(300))
    assert large["decision"] == "pass" and _check(large, "false_accept")["upper"] <= 0.01


def test_too_few_cases_cannot_settle_the_overall_comparison():
    result = gate.compare(_rows(10), _rows(10))
    assert _check(result, "overall_non_inferiority") == {"name": "overall_non_inferiority", "status": "insufficient",
                                                         "n": 10, "needed": 30}
    assert _check(result, "slice_regression")["status"] == "not_applicable"
    assert result["decision"] == "insufficient"


def test_a_clear_regression_fails_overall_and_in_its_slice():
    result = gate.compare(_rows(300), _rows(300, fail=set(range(0, 60, 2))))  # 30 of slice "x" now fail
    assert result["decision"] == "fail"
    assert _check(result, "overall_non_inferiority")["status"] == "fail"
    assert _check(result, "slice_regression")["regressed"] == ["x"]
    assert len(result["changed"]["worse"]) == 30 and result["changed"]["better"] == []


def test_an_improvement_is_not_a_regression():
    result = gate.compare(_rows(300, fail={1, 2, 3, 4, 5}), _rows(300))
    assert _check(result, "overall_non_inferiority")["status"] == "pass"
    assert _check(result, "slice_regression")["status"] == "pass" and result["changed"]["worse"] == []


def test_a_small_drop_inside_the_margin_passes_the_overall_check():
    result = gate.compare(_rows(1000), _rows(1000, fail={0}), Policy())
    assert _check(result, "overall_non_inferiority")["status"] == "pass"


def test_a_protected_case_that_regresses_fails_whatever_the_rest_does():
    result = gate.compare(_rows(300, protected={7}), _rows(300, fail={7}, protected={7}))
    assert _check(result, "protected_cases") == {"name": "protected_cases", "status": "fail", "regressed": ["c0007"]}
    assert result["decision"] == "fail"
    unprotected = gate.compare(_rows(300), _rows(300, fail={7}))
    assert _check(unprotected, "protected_cases")["status"] == "pass"


def test_more_false_accepts_than_the_baseline_fail_even_when_the_pass_rate_holds():
    baseline = _rows(300)
    candidate = _rows(300, fail={5})  # one answered-but-wrong case
    result = gate.compare(baseline, candidate)
    assert _check(result, "false_accept")["status"] == "fail" and result["decision"] == "fail"
    abstains = _rows(300, fail={5})
    abstains[5]["answered"] = False  # the same loss, as an abstention: not a false accept
    assert _check(gate.compare(baseline, abstains), "false_accept")["status"] == "pass"


def test_new_errors_fail():
    candidate = _rows(300)
    candidate[3].update(passed=False, failures=["error"], answered=False)
    result = gate.compare(_rows(300), candidate)
    assert _check(result, "errors") == {"name": "errors", "status": "fail", "new": ["c0003"]}


def test_slices_below_the_minimum_are_reported_not_judged():
    result = gate.compare(_rows(300, slices=("x", "y", "z")), _rows(300, slices=("x", "y", "z")), Policy(min_slice=200))
    assert _check(result, "slice_regression")["status"] == "not_applicable"
    assert _check(result, "slice_regression")["not_judged"] == {"x": 100, "y": 100, "z": 100}


def test_the_two_runs_must_cover_the_same_cases_once():
    with pytest.raises(ValueError):
        gate.compare(_rows(5), _rows(6))
    with pytest.raises(ValueError):
        gate.compare(_rows(5) + _rows(1), _rows(5) + _rows(1))


def test_the_gate_result_is_json_serializable_and_deterministic():
    one = gate.compare(_rows(300), _rows(300, fail={1}))
    two = gate.compare(_rows(300), _rows(300, fail={1}))
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert not any(isinstance(v, float) and math.isnan(v) for v in _check(one, "overall_non_inferiority").values()
                   if isinstance(v, float))


# --- ledger and budget ---------------------------------------------------------------------------------


def test_the_ledger_chains_entries_and_finds_tampering(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = ledger.record({"proposal_id": "p1", "eval_set_sha256": "s1"}, path=path)
    second = ledger.record({"proposal_id": "p2", "eval_set_sha256": "s1"}, path=path)
    assert first["prev"] == ledger.GENESIS and second["prev"] == first["sha256"] and second["seq"] == 2
    assert ledger.verify_chain(path) == 2 and ledger.evaluations_used("s1", path) == 2

    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0].replace('"p1"', '"px"') + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(ledger.LedgerError, match="entry 1"):
        ledger.verify_chain(path)
    with pytest.raises(ledger.LedgerError):
        ledger.record({"proposal_id": "p3"}, path=path)  # nothing is appended to a broken chain

    path.write_text(lines[1] + "\n", encoding="utf-8")  # the first entry removed
    with pytest.raises(ledger.LedgerError):
        ledger.verify_chain(path)
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ledger.LedgerError):
        ledger.read_ledger(path)
    assert ledger.read_ledger(tmp_path / "missing.jsonl") == [] and ledger.verify_chain(tmp_path / "missing.jsonl") == 0


def test_the_budget_counts_uses_of_one_evaluation_set(tmp_path):
    path = tmp_path / "ledger.jsonl"
    for _ in range(3):
        ledger.record({"eval_set_sha256": "s1"}, path=path)
    ledger.record({"eval_set_sha256": "other"}, path=path)
    assert ledger.check_budget("s1", budget=5, path=path) == 2
    with pytest.raises(ledger.BudgetError, match="budget 3"):
        ledger.check_budget("s1", budget=3, path=path)
    assert ledger.check_budget("fresh", budget=3, path=path) == 3


def test_the_default_ledger_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT_EVAL_LEDGER", str(tmp_path / "mine.jsonl"))
    assert ledger.ledger_path() == tmp_path / "mine.jsonl"


# --- evaluate_candidate -----------------------------------------------------------------------------------


def _cases(n):
    return [_case(f"c{i:03d}") for i in range(n)]


def test_evaluate_candidate_runs_gates_and_records(tmp_path):
    path = tmp_path / "ledger.jsonl"
    cases = _cases(60)
    out = evaluate.evaluate_candidate(
        cases, _fn({}), _fn({"質問 c001": "誤"}), proposal_id="prop1", ledger_file=path, run_id="r1",
        conditions={"profile_version": "abc"},
    )
    assert out["gate"]["decision"] in {"fail", "insufficient"} and len(out["baseline_rows"]) == 60
    entry = out["ledger_entry"]
    assert entry["proposal_id"] == "prop1" and entry["run_id"] == "r1" and entry["n_cases"] == 60
    assert entry["conditions"] == {"profile_version": "abc"} and entry["budget_remaining_after"] == 19
    assert ledger.verify_chain(path) == 1 and ledger.read_ledger(path)[0]["decision"] == entry["decision"]


def test_evaluate_candidate_refuses_before_running_when_the_budget_is_spent(tmp_path):
    path = tmp_path / "ledger.jsonl"
    cases = _cases(40)
    calls = []

    def counting(question):
        calls.append(question)
        return {"answer_text": "正解", "has_citation": True, "question_type": "measure", "guard_reason": None}  # type matches

    evaluate.evaluate_candidate(cases, counting, counting, proposal_id="p", ledger_file=path, budget=1)
    before = len(calls)
    with pytest.raises(ledger.BudgetError):
        evaluate.evaluate_candidate(cases, counting, counting, proposal_id="p", ledger_file=path, budget=1)
    assert len(calls) == before and ledger.verify_chain(path) == 1
    with pytest.raises(ValueError):
        evaluate.evaluate_candidate([], counting, counting, proposal_id="p", ledger_file=path)
