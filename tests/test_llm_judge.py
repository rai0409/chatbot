import types

import pytest

import config
from eval.tenant_eval import llm_judge
from rag_core import golden_qa, llm


class _Client:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_FEATURES", ["judge"])
    monkeypatch.setattr(config, "LLM_TENANT_ALLOWLIST", [])
    monkeypatch.setattr(config, "LLM_TENANT_DENYLIST", [])
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 0)
    monkeypatch.delenv("APP_ENV", raising=False)
    llm.reset_stats()


def _case(**extra):
    return golden_qa.parse_case({"case_id": "c1", "question": "パスワードの変更頻度は？", **extra})[0]


def test_a_golden_case_may_carry_a_reference(tmp_path):
    case = _case(reference="90 日ごとに変更する。")
    assert case["reference"] == "90 日ごとに変更する。"
    assert _case(expected_all=["90"])["reference"] == ""
    only_reference, errors = golden_qa.parse_case({"case_id": "c", "question": "q", "reference": "答え"})
    assert errors == [] and only_reference["reference"] == "答え"  # a reference alone checks something
    _none, errors = golden_qa.parse_case({"case_id": "c", "question": "q", "reference": 5})
    assert any("'reference' must be a non-empty string" in e for e in errors)
    assert golden_qa.parse_case({"case_id": "c", "question": "q"})[0] is None  # still checks nothing


def test_a_clear_verdict_is_returned_and_the_prompt_carries_question_reference_and_answer(on):
    client = _Client('{"verdict": "pass", "reason": "同じ内容"}')
    out = llm_judge.judge_case(_case(reference="90 日ごとに変更する。"), "パスワードは三か月ごとに変更します。", client=client)
    assert out == {"verdict": "pass", "reason": "同じ内容"}
    prompt = client.calls[0]["messages"][1]["content"]
    assert "パスワードの変更頻度は？" in prompt and "90 日ごとに変更する。" in prompt and "三か月ごと" in prompt
    assert client.calls[0]["max_tokens"] == 120 and client.calls[0]["temperature"] == 0.0


def test_without_a_reference_the_expected_terms_are_the_basis(on):
    client = _Client('{"verdict": "fail", "reason": "語が無い"}')
    llm_judge.judge_case(_case(expected_all=["90"], expected_any=["日", "週"], forbidden_any=["無期限"]), "答え", client=client)
    prompt = client.calls[0]["messages"][1]["content"]
    assert "すべて含む語: 90" in prompt and "いずれかを含む語: 日、週" in prompt and "含んではいけない語: 無期限" in prompt


@pytest.mark.parametrize("reply, reason", [
    ("pass", "bad_output"), ('{"verdict": "maybe"}', "bad_output"), ('{"verdict": true}', "bad_output"), ("{", "bad_output"),
    ('["pass"]', "bad_output"), ("", "bad_output"), (RuntimeError("boom"), "error:RuntimeError"),
])
def test_anything_but_a_clear_pass_or_fail_is_an_error_never_a_pass(on, reply, reason):
    out = llm_judge.judge_case(_case(reference="x"), "回答", client=_Client(reply))
    assert out == {"verdict": "error", "reason": reason}


def test_the_judge_makes_no_call_when_the_policy_refuses(monkeypatch):
    client = _Client('{"verdict": "pass"}')
    assert llm_judge.judge_case(_case(reference="x"), "回答", client=client) == {"verdict": "error", "reason": "not_allowed:global"}
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    assert llm_judge.judge_case(_case(reference="x"), "回答", client=client)["reason"] == "not_allowed:feature"
    assert client.calls == []


def test_only_the_rows_with_an_answer_are_judged(on):
    cases = [golden_qa.parse_case({"case_id": f"c{i}", "question": "q", "reference": "r"})[0] for i in range(3)]
    rows = [{"case_id": "c0", "answered": True, "answer_text": "a"}, {"case_id": "c1", "answered": False, "answer_text": ""},
            {"case_id": "zz", "answered": True, "answer_text": "a"}]
    out = llm_judge.judge_rows(cases, rows, client=_Client('{"verdict": "pass", "reason": ""}'))
    assert list(out) == ["c0"] and out["c0"]["verdict"] == "pass"


def _scores(tp, fp, fn, tn):
    human, judged, n = {}, {}, 0
    for count, h, j in ((tp, True, "pass"), (fp, False, "pass"), (fn, True, "fail"), (tn, False, "fail")):
        for _ in range(count):
            human[f"c{n}"], judged[f"c{n}"], n = h, {"verdict": j}, n + 1
    return human, judged


def test_agreement_gives_accuracy_kappa_and_the_false_pass_bound():
    report = llm_judge.agreement(*_scores(20, 5, 10, 15))
    assert report["n"] == 50 and report["accuracy"] == pytest.approx(0.7) and report["kappa"] == pytest.approx(0.4)
    assert report["confusion"] == {"tp": 20, "fp": 5, "fn": 10, "tn": 15}
    assert report["false_pass_rate"] == pytest.approx(0.25) and report["false_pass_upper"] > 0.25
    perfect = llm_judge.agreement(*_scores(25, 0, 0, 25))
    assert perfect["kappa"] == 1.0 and perfect["false_pass_rate"] == 0.0 and perfect["false_pass_upper"] < 0.12  # 25 failures: ~11 %
    chance = llm_judge.agreement(*_scores(10, 10, 10, 10))
    assert chance["kappa"] == pytest.approx(0.0)


def test_agreement_counts_errors_and_ignores_cases_only_one_side_scored():
    human, judged = _scores(5, 0, 0, 5)
    judged["c0"] = {"verdict": "error"}
    judged["extra"] = {"verdict": "pass"}  # a person did not score this one
    human["unjudged"] = True
    report = llm_judge.agreement(human, judged)
    assert report["n"] == 9 and report["errors"] == 1
    empty = llm_judge.agreement({}, {})
    assert empty["n"] == 0 and empty["kappa"] is None and llm_judge.usable(empty)["usable"] is False


def test_the_judge_is_usable_only_with_enough_cases_agreement_and_a_bounded_false_pass_rate():
    good = llm_judge.agreement(*_scores(120, 1, 3, 120))
    assert llm_judge.usable(good) == {"usable": True, "reasons": []}
    few = llm_judge.usable(llm_judge.agreement(*_scores(10, 0, 0, 10)))
    assert few["usable"] is False and "at least 50" in few["reasons"][0]
    weak = llm_judge.usable(llm_judge.agreement(*_scores(30, 20, 20, 30)))
    assert weak["usable"] is False and any("kappa" in r for r in weak["reasons"])
    lenient = llm_judge.usable(llm_judge.agreement(*_scores(60, 12, 0, 48)))
    assert lenient["usable"] is False and any("false-pass" in r for r in lenient["reasons"])
    assert llm_judge.usable(llm_judge.agreement(*_scores(40, 0, 0, 40)), min_cases=20)["usable"] is True  # 29+ failures bound it


@pytest.mark.parametrize("rule, verdict, trusted, expected", [
    (True, "pass", True, True), (True, "fail", True, False), (True, "error", True, True), (True, None, True, True),
    (True, "fail", False, True),  # an untrusted judge cannot veto
    (False, "pass", True, False), (False, "pass", False, False), (False, None, True, False),
])
def test_a_usable_judge_can_only_veto_never_rescue(rule, verdict, trusted, expected):
    assert llm_judge.conservative_pass(rule, verdict, trusted) is expected
