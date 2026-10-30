import json

import pytest

import config
from eval.tenant_eval import gate, ledger, report
from rag_core import golden_qa, profile_loader, profile_store, query_preprocess
from scripts import tenant_eval

PID = "tenant_a"


def _rows(n, *, fail=(), failures=("missing_all",)):
    return [{
        "case_id": f"c{i:03d}", "passed": i not in fail, "failures": list(failures) if i in fail else [], "answered": True,
        "slice": "x" if i % 2 else "y", "group": f"g{i}", "priority": 0, "latency_ms": 1.0,
    } for i in range(n)]


def test_the_report_names_the_decision_the_checks_and_the_changed_cases():
    base, cand = _rows(300), _rows(300, fail={1, 2}, failures=("forbidden", "no_citation"))
    result = gate.compare(base, cand)
    text = report.to_markdown(
        result, title="評価レポート: p1", baseline_rows=base, candidate_rows=cand,
        baseline_conditions={"profile_version": "aaaa", "knobs": {"HYBRID_RRF_K": 60}},
        candidate_conditions={"profile_version": "bbbb", "knobs": {"HYBRID_RRF_K": 40}},
    )
    assert text.startswith("# 評価レポート: p1") and "**判定: 不合格**" in text
    assert "| profile_version | aaaa | bbbb |" in text and "| knobs.HYBRID_RRF_K | 60 | 40 |" in text
    assert "プロファイル版: 基準 `aaaa` / 候補 `bbbb`" in text
    assert "## 悪化したケース（2 件）" in text and "- `c001`: 禁止語を含む、出典がない" in text
    assert "## 改善したケース（0 件）" in text and "| x | 150 |" in text
    assert "誤受理率の上限" in text and "保護ケースの優先度 100 以上" in text


def test_the_report_without_rows_or_conditions_still_renders_and_says_when_nothing_differs():
    result = gate.compare(_rows(10), _rows(10))
    text = report.to_markdown(result)
    assert "判定不能" in text and "## 質問タイプ別" not in text and "## 比較した条件" not in text
    same = {"profile_version": "aaaa", "knobs": {}}
    assert "違いはない" in report.to_markdown(result, baseline_conditions=same, candidate_conditions=same)


# --- the command line -----------------------------------------------------------------------------------


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("TENANT_EVAL_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.delenv("ACTIVE_GENERATION_FILE", raising=False)
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(config, "HYBRID_RRF_K", 60)
    source = tmp_path / "profiles" / PID / "exported"
    source.mkdir(parents=True)

    def write(variants):
        files = {"profile.json": {"project_id": PID}, "question_type_rules.json": {"rules": []},
                 "domain_terms.json": {"terms": []},
                 "synonyms.json": {"synonyms": [{"canonical": "サーバー", "variants": variants}]},
                 "retrieval_boost_rules.json": {"rules": []}, "validation_rules.json": {"rules": []},
                 "answer_templates.json": {"templates": []}}
        for name, value in files.items():
            (source / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        cases = [{"case_id": f"c{i:03d}", "question": f"質問 c{i:03d}", "expected_all": ["正解"], "group": f"g{i // 2}",
                  "origin_change": "chg_old" if i < 3 else ""} for i in range(40)]
        (source / "golden_qa.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                                                encoding="utf-8")

    write(["サーバ"])
    v1 = profile_store.create_version(PID)["version"]
    write(["サーバ", "ｻｰﾊﾞｰ"])
    v2 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v1)
    profile_loader.reload_rag_profile_cache()
    return v1, v2, tmp_path


def _fake_pipeline(monkeypatch, *, wrong_when):
    """A pipeline whose answer is wrong for every question when wrong_when() is true (it sees the live settings)."""
    def factory(**kwargs):
        def answer(question):
            return {"answer_text": "誤り" if wrong_when() else "正解です", "has_citation": True, "question_type": "",
                    "guard_reason": None}
        return answer
    monkeypatch.setattr(tenant_eval, "pipeline_answer_fn", factory)


def _run(capsys, *argv):
    code = tenant_eval.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_evaluate_a_knob_change_that_breaks_answers_fails_and_is_reported(setup, monkeypatch, capsys):
    _v1, _v2, tmp_path = setup
    _fake_pipeline(monkeypatch, wrong_when=lambda: config.HYBRID_RRF_K == 40)
    code, out = _run(capsys, "evaluate", "--profile", PID, "--proposal", "rrf40", "--knob", "HYBRID_RRF_K=40",
                     "--exclude-change", "chg_old", "--report", str(tmp_path / "reports" / "rrf40.md"))
    assert code == 1 and out["decision"] == "fail" and out["n_cases"] == 37 and out["ledger_seq"] == 1
    text = (tmp_path / "reports" / "rrf40.md").read_text(encoding="utf-8")
    assert "**判定: 不合格**" in text and "| knobs.HYBRID_RRF_K | 60 | 40 |" in text
    assert config.HYBRID_RRF_K == 60  # restored
    entry = ledger.read_ledger()[0]
    assert entry["conditions"]["declared"] == {"knobs": {"HYBRID_RRF_K": 40}} and entry["proposal_id"] == "rrf40"


def test_evaluate_a_candidate_version_runs_it_in_the_candidate_run_only(setup, monkeypatch, capsys):
    v1, v2, _tmp = setup
    _fake_pipeline(monkeypatch, wrong_when=lambda: profile_store.pinned_version(PID) == v2)
    code, out = _run(capsys, "evaluate", "--profile", PID, "--proposal", "v2", "--candidate-version", v2)
    assert code == 1 and out["decision"] == "fail"
    assert profile_store.pinned_version(PID) == v1
    entry = ledger.read_ledger()[0]
    assert entry["conditions"]["candidate"]["profile_version"] == v2 and entry["conditions"]["baseline"]["profile_version"] == v1


def test_a_harmless_candidate_is_not_failed(setup, monkeypatch, capsys):
    _v1, v2, _tmp = setup
    _fake_pipeline(monkeypatch, wrong_when=lambda: False)
    code, out = _run(capsys, "evaluate", "--profile", PID, "--proposal", "v2", "--candidate-version", v2)
    assert code == 2 and out["decision"] == "insufficient"  # 40 cases cannot prove a false-accept rate under 1 %


def test_folds_select_the_held_out_cases_of_whole_groups(setup, monkeypatch, capsys):
    _v1, v2, _tmp = setup
    _fake_pipeline(monkeypatch, wrong_when=lambda: False)
    code, out = _run(capsys, "evaluate", "--profile", PID, "--proposal", "f", "--candidate-version", v2,
                     "--folds", "2", "--held-out", "0", "--seed", "s")
    cases, _ = golden_qa.load_cases(profile_loader.load_rag_profile(PID))
    held = golden_qa.split_by_fold(cases, 2, 0, seed="s")[1]
    assert out["n_cases"] == len(held) and 0 < len(held) < 40
    assert tenant_eval.main(["evaluate", "--profile", PID, "--proposal", "f2", "--candidate-version", v2,
                             "--folds", "2"]) == 3


def test_refusals_exit_with_3_and_run_nothing(setup, monkeypatch, capsys):
    v1, v2, _tmp = setup
    calls = []
    monkeypatch.setattr(tenant_eval, "pipeline_answer_fn", lambda **kw: calls.append(kw) or (lambda q: {}))
    base = ("evaluate", "--profile", PID, "--proposal", "x")
    for extra, fragment in (
        ((), "needs --candidate-version"),
        (("--knob", "DATABASE_URL=1"), "not a registered knob"),
        (("--knob", "HYBRID_RRF_K=0"), "outside"),
        (("--knob", "HYBRID_RRF_K=abc"), "expected a number"),
        (("--knob", "HYBRID_RRF_K"), "NAME=VALUE"),
        (("--knob", "HYBRID_RRF_K=60"), "identical in the baseline"),
        (("--candidate-version", v1), "identical in the baseline"),
        (("--candidate-version", "0123456789abcdef"), "not found"),
    ):
        code, out = _run(capsys, *base, *extra)
        assert code == 3 and fragment in out["error"], (extra, out)
    assert calls == [] and ledger.read_ledger() == []


def test_the_budget_refuses_a_further_evaluation_and_the_ledger_command_verifies(setup, monkeypatch, capsys):
    _v1, v2, _tmp = setup
    _fake_pipeline(monkeypatch, wrong_when=lambda: False)
    args = ("evaluate", "--profile", PID, "--proposal", "b", "--candidate-version", v2, "--budget", "1")
    assert _run(capsys, *args)[0] == 2
    code, out = _run(capsys, *args)
    assert code == 3 and "budget 1" in out["error"]
    code, out = _run(capsys, "ledger")
    assert code == 0 and out["chain"] == "ok" and out["entries"] == 1 and out["rows"][0]["proposal_id"] == "b"
    path = ledger.ledger_path()
    path.write_text(path.read_text(encoding="utf-8").replace('"b"', '"z"'), encoding="utf-8")
    assert _run(capsys, "ledger")[0] == 3


def test_a_profile_with_unusable_golden_rows_is_refused(setup, monkeypatch, capsys):
    v1, v2, tmp_path = setup
    path = tmp_path / "profiles" / PID / "exported" / "golden_qa.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + json.dumps({"case_id": "bad"}) + "\n", encoding="utf-8")
    profile_store.create_version(PID)
    profile_store.clear(PID)
    profile_loader.reload_rag_profile_cache()
    code, out = _run(capsys, "evaluate", "--profile", PID, "--proposal", "x", "--candidate-version", v2)
    assert code == 3 and "golden cases have problems" in out["error"]
