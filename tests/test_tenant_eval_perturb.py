import json

import pytest

import config
from eval.tenant_eval import conditions, ledger, perturb
from rag_core import golden_qa, knob_registry, profile_loader, query_preprocess
from scripts import tenant_eval

PID = "tenant_a"
SAMPLE = "ABC123の設定手順を教えてください"


def test_each_kind_changes_the_question_in_its_own_way():
    def one(kind, text=SAMPLE, seed=""):
        variants = perturb.perturb_question(text, kinds=[kind], seed=seed)
        return variants[0]["text"] if variants else None

    assert one("fullwidth") == "ＡＢＣ１２３の設定手順を教えてください"
    assert one("filler").endswith(SAMPLE) and one("filler")[: -len(SAMPLE)] in perturb.FILLERS
    assert one("casual_ending", "設定はどこですか？") in {"設定はどこなの？", "設定はどこかな？", "設定はどこのか教えて"}
    assert one("casual_ending", "ログを確認する") == "ログを確認するんだけど教えて"
    assert one("punctuation") == SAMPLE + "？" and one("punctuation", "設定は？") == "設定は"
    assert one("katakana_hiragana", "サーバーの再起動手順") == "さーばーの再起動手順"
    assert one("katakana_hiragana") is None  # no katakana run
    assert one("fullwidth", "設定手順") is None  # no ASCII letters or digits
    spaced = one("spacing", "再起動手順をおしえてください")
    assert spaced.replace(" ", "") == "再起動手順をおしえてください" and spaced.count(" ") == 1
    swapped, dropped = one("typo_swap", "再起動手順を確認"), one("typo_drop", "再起動手順を確認")
    assert len(swapped) == len("再起動手順を確認") and sorted(swapped) == sorted("再起動手順を確認")
    assert len(dropped) == len("再起動手順を確認") - 1
    assert one("typo_swap", "ab") is None and one("typo_drop", "短い") is None
    quoted = one("keywords_only", "「再起動」の手順を教えてください")
    assert quoted is not None and "再起動" in quoted and "ください" not in quoted
    assert one("keywords_only", "ああ") is None  # nothing salient: no variant


def test_variants_are_deterministic_distinct_and_never_the_question():
    first = perturb.perturb_question(SAMPLE, seed="s")
    assert first == perturb.perturb_question(SAMPLE, seed="s")
    texts = [v["text"] for v in first]
    assert SAMPLE not in texts and len(texts) == len(set(texts))
    assert [v["kind"] for v in first] == [k for k in perturb.KINDS if k in {v["kind"] for v in first}]  # in kind order
    seeds = {tuple(v["text"] for v in perturb.perturb_question("再起動手順を確認する方法", seed=str(i))) for i in range(30)}
    assert len(seeds) > 1  # the seed changes which position or filler is picked
    with pytest.raises(ValueError, match="unknown perturbation kind"):
        perturb.perturb_question(SAMPLE, kinds=["nonsense"])


def _case(case_id, question, **extra):
    return golden_qa.parse_case({"case_id": case_id, "question": question, "expected_all": ["正解"], **extra})[0]


def test_perturbed_cases_keep_the_expectations_and_the_group_and_are_not_protected():
    source = _case("c1", SAMPLE, priority=100, tags=["x"], group="g9", expected_question_type="procedure")
    made = perturb.perturb_cases([source], kinds=["filler", "fullwidth"])
    assert [c["case_id"] for c in made] == ["c1~filler", "c1~fullwidth"]
    for case in made:
        assert case["expected_all"] == ["正解"] and case["group"] == "g9" and case["priority"] == 0
        assert case["expected_question_type"] == "procedure" and case["question"] != SAMPLE
        assert case["tags"][0] == "x" and case["tags"][-1] == "from:c1"
        assert golden_qa.parse_case(case)[1] == []  # still a valid golden case
    ungrouped = perturb.perturb_cases([_case("c2", SAMPLE)], kinds=["filler"])[0]
    assert ungrouped["group"] == "c2"  # a variant stays with its source in the folds
    folds = golden_qa.assign_folds([source, *made], 3, seed="s")
    assert len({folds[c["case_id"]] for c in [source, *made]}) == 1


def _row(case_id, passed):
    return {"case_id": case_id, "passed": passed}


def test_robustness_counts_what_is_lost_per_kind():
    rows = [_row("a", True), _row("a~filler", False), _row("b", True), _row("b~filler", True), _row("c", False),
            _row("c~filler", True), _row("a~typo_swap", True), _row("x~filler", True), _row("a~bogus", False),
            _row("d", True)]
    result = perturb.robustness(rows)
    assert list(result) == ["filler", "typo_swap"]
    filler = result["filler"]
    assert (filler["pairs"], filler["orig_passed"], filler["lost"], filler["gained"]) == (3, 2, 1, 1)
    assert filler["lost_rate"] == pytest.approx(0.5) and filler["lost_upper"] > 0.5
    assert filler["perturbed_pass_rate"] == pytest.approx(2 / 3)
    assert result["typo_swap"]["lost"] == 0 and result["typo_swap"]["lost_rate"] == 0.0
    assert perturb.robustness([_row("a", True)]) == {}
    assert perturb.robustness([_row("a", False), _row("a~filler", True)])["filler"]["lost_rate"] is None


def test_the_robustness_table_compares_baseline_and_candidate():
    base = [_row("a", True), _row("a~filler", False), _row("b", True), _row("b~filler", False)]
    cand = [_row("a", True), _row("a~filler", True), _row("b", True), _row("b~filler", False)]
    text = perturb.robustness_markdown(base, cand)
    assert "## 質問の揺れへの強さ（摂動）" in text and "| filler | 2 | 2（100.0%） | 1（50.0%） | 0.0% | 50.0% |" in text
    assert "含まれていない" in perturb.robustness_markdown([_row("a", True)], [_row("a", True)])


# --- the knob and its plumbing ---------------------------------------------------------------------


def test_the_preprocessing_flag_is_a_registered_env_knob(monkeypatch):
    assert "QUERY_PREPROCESS_ENABLED" in knob_registry.names() and "QUERY_PREPROCESS_ENABLED" in knob_registry.ENV_KNOBS
    monkeypatch.delattr(config, "QUERY_PREPROCESS_ENABLED", raising=False)
    monkeypatch.delenv("QUERY_PREPROCESS_ENABLED", raising=False)
    assert knob_registry.read("QUERY_PREPROCESS_ENABLED") is False
    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", " True ")
    assert knob_registry.read("QUERY_PREPROCESS_ENABLED") is True and knob_registry.snapshot()["QUERY_PREPROCESS_ENABLED"] is True
    monkeypatch.setattr(config, "QUERY_PREPROCESS_ENABLED", False, raising=False)
    assert knob_registry.current("QUERY_PREPROCESS_ENABLED") is False  # the config attribute wins, as in qa._setting
    assert knob_registry.read("DATABASE_URL") is None
    assert knob_registry.check_value("QUERY_PREPROCESS_ENABLED", True) is True
    with pytest.raises(knob_registry.KnobError):
        knob_registry.check_value("QUERY_PREPROCESS_ENABLED", "true")


def test_the_candidate_environment_sets_and_then_removes_an_attribute_that_was_not_there(monkeypatch):
    monkeypatch.delattr(config, "QUERY_PREPROCESS_ENABLED", raising=False)
    from rag_core import qa

    assert qa._setting("QUERY_PREPROCESS_ENABLED", "false") == "false"
    with conditions.candidate_environment(PID, knobs={"QUERY_PREPROCESS_ENABLED": True}):
        assert qa._setting("QUERY_PREPROCESS_ENABLED", "false") == "true"
    assert not hasattr(config, "QUERY_PREPROCESS_ENABLED")
    with pytest.raises(RuntimeError):
        with conditions.candidate_environment(PID, knobs={"QUERY_PREPROCESS_ENABLED": True}):
            raise RuntimeError("boom")
    assert not hasattr(config, "QUERY_PREPROCESS_ENABLED")


def test_the_cache_digest_follows_the_config_attribute_of_an_env_setting(monkeypatch):
    from rag_core import answer_cache

    monkeypatch.delattr(config, "QUERY_PREPROCESS_ENABLED", raising=False)
    monkeypatch.delenv("QUERY_PREPROCESS_ENABLED", raising=False)
    plain = answer_cache._settings_state()
    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", "true")
    from_env = answer_cache._settings_state()
    monkeypatch.setattr(config, "QUERY_PREPROCESS_ENABLED", True, raising=False)
    from_config = answer_cache._settings_state()
    assert len({plain, from_env, from_config}) == 3


# --- the command line --------------------------------------------------------------------------------


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("TENANT_EVAL_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.delenv("ACTIVE_GENERATION_FILE", raising=False)
    monkeypatch.delenv("QUERY_PREPROCESS_ENABLED", raising=False)
    monkeypatch.delattr(config, "QUERY_PREPROCESS_ENABLED", raising=False)
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    source = tmp_path / "profiles" / PID / "exported"
    source.mkdir(parents=True)
    for name, value in {"profile.json": {"project_id": PID}, "question_type_rules.json": {"rules": []},
                        "domain_terms.json": {"terms": []}, "synonyms.json": {"synonyms": []},
                        "retrieval_boost_rules.json": {"rules": []}, "validation_rules.json": {"rules": []},
                        "answer_templates.json": {"templates": []}}.items():
        (source / name).write_text(json.dumps(value), encoding="utf-8")
    cases = [{"case_id": f"c{i:03d}", "question": f"質問 c{i:03d}", "expected_all": ["正解"]} for i in range(40)]
    (source / "golden_qa.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                                            encoding="utf-8")
    profile_loader.reload_rag_profile_cache()
    return tmp_path


def _fake_pipeline(monkeypatch):
    """Wrong on a question that starts with a filler, unless preprocessing is on (it cleans the filler away)."""
    def factory(**kwargs):
        def answer(question):
            filler = question.startswith(perturb.FILLERS)
            wrong = filler and not knob_registry.read("QUERY_PREPROCESS_ENABLED")
            return {"answer_text": "誤り" if wrong else "正解です", "has_citation": True, "question_type": "",
                    "guard_reason": None}
        return answer
    monkeypatch.setattr(tenant_eval, "pipeline_answer_fn", factory)


def test_evaluating_preprocessing_with_perturbations_shows_the_gain_in_the_report(setup, monkeypatch, capsys):
    _fake_pipeline(monkeypatch)
    report = setup / "reports" / "pre.md"
    code = tenant_eval.main(["evaluate", "--profile", PID, "--proposal", "pre", "--knob", "QUERY_PREPROCESS_ENABLED=true",
                             "--perturb", "--perturb-kinds", "filler", "--report", str(report)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["decision"] == "insufficient" and out["n_cases"] == 80  # 40 cases + 40 filler variants
    text = report.read_text(encoding="utf-8")
    assert "## 質問の揺れへの強さ（摂動）" in text and "| filler | 40 | 40（100.0%） | 0（0.0%） | 0.0% | 100.0% |" in text
    assert "| knobs.QUERY_PREPROCESS_ENABLED | False | True |" in text
    entry = ledger.read_ledger()[0]
    assert entry["conditions"]["declared"] == {"knobs": {"QUERY_PREPROCESS_ENABLED": True}}
    assert not hasattr(config, "QUERY_PREPROCESS_ENABLED")  # restored (it had no attribute before)


def test_without_perturb_flags_nothing_is_added_and_a_bad_kind_is_refused(setup, monkeypatch, capsys):
    _fake_pipeline(monkeypatch)
    code = tenant_eval.main(["evaluate", "--profile", PID, "--proposal", "plain", "--knob", "QUERY_PREPROCESS_ENABLED=true"])
    assert json.loads(capsys.readouterr().out)["n_cases"] == 40 and code == 2
    code = tenant_eval.main(["evaluate", "--profile", PID, "--proposal", "bad", "--knob", "QUERY_PREPROCESS_ENABLED=true",
                             "--perturb", "--perturb-kinds", "filler,nonsense"])
    assert code == 3 and "unknown perturbation kind" in json.loads(capsys.readouterr().out)["error"]
    assert len(ledger.read_ledger()) == 1  # the refusal used no budget
