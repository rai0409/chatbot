import pytest

from rag_core import golden_qa, profile_loader


def _row(case_id="c1", **extra):
    return {"case_id": case_id, "question": "質問です", "expected_any": ["回答"], **extra}


def test_a_valid_row_is_normalized_with_defaults():
    case, errors = golden_qa.parse_case(_row())
    assert errors == []
    assert case == {
        "case_id": "c1", "question": "質問です", "expected_all": [], "expected_any": ["回答"], "forbidden_any": [],
        "expected_question_type": "", "group": "", "origin_change": "", "reference": "", "tags": [],
        "expected_citation": False, "enabled": True, "priority": 0,
    }


@pytest.mark.parametrize(
    "row, fragment",
    [
        ("text", "not an object"),
        ({"question": "q", "expected_any": ["a"]}, "'case_id' is required"),
        (_row(question=""), "'question' is required"),
        (_row(expected_all="回答"), "'expected_all' must be a list of strings"),
        (_row(expected_any=[1]), "'expected_any' must be a list of strings"),
        (_row(expected_citation="yes"), "'expected_citation' must be true or false"),
        (_row(enabled=1), "'enabled' must be true or false"),
        (_row(priority=True), "'priority' must be an integer"),
        (_row(priority="9"), "'priority' must be an integer"),
        (_row(group=5), "'group' must be a non-empty string"),
        ({"case_id": "c2", "question": "q"}, "the case checks nothing"),
    ],
)
def test_a_bad_row_gives_no_case_and_says_why(row, fragment):
    case, errors = golden_qa.parse_case(row)
    assert case is None and any(fragment in message for message in errors)


def test_a_case_may_check_only_the_citation_or_the_question_type():
    assert golden_qa.parse_case({"case_id": "c", "question": "q", "expected_citation": True})[0] is not None
    assert golden_qa.parse_case({"case_id": "c", "question": "q", "expected_question_type": "measure"})[0] is not None


def test_load_cases_orders_by_priority_and_reports_every_unused_row():
    profile = {"golden_qa": [
        _row("low", priority=1),
        _row("high", priority=9),
        _row("off", enabled=False),
        _row("high"),  # duplicate id
        {"case_id": "broken"},
        _row("same_priority"),
    ]}
    cases, problems = golden_qa.load_cases(profile)
    assert [c["case_id"] for c in cases] == ["high", "low", "same_priority"]
    assert len(problems) == 2
    assert "row 4: duplicate case_id 'high'" in problems and any(p.startswith("row 5: broken:") for p in problems)
    with_disabled, _ = golden_qa.load_cases(profile, include_disabled=True)
    assert "off" in [c["case_id"] for c in with_disabled]
    assert golden_qa.load_cases({}) == ([], [])


def _case(**extra):
    return golden_qa.parse_case({"case_id": "c", "question": "q", **extra})[0]


def test_evaluate_case_reports_each_reason():
    case = _case(expected_all=["111", "活火山"], expected_any=["ヘルメット", "マスク"], forbidden_any=["避難路"],
                 expected_citation=True, expected_question_type="list_items")
    ok = golden_qa.evaluate_case(case, answer_text="１１１の活火山。ヘルメットを持つ。", has_citation=True,
                                 question_type="list_items")
    assert ok["passed"] is True and ok["failures"] == []  # full-width digits match

    bad = golden_qa.evaluate_case(case, answer_text="避難路の話。", has_citation=False, question_type="other")
    assert bad["failures"] == ["missing_all", "missing_any", "forbidden", "no_citation", "question_type"]
    assert bad["missing_all"] == ["111", "活火山"] and bad["missing_any"] == ["ヘルメット", "マスク"]
    assert bad["forbidden_hits"] == ["避難路"]

    only_one = golden_qa.evaluate_case(case, answer_text="111 活火山 マスク", has_citation=True,
                                       question_type="list_items")
    assert only_one["passed"] is True  # expected_any needs one term, not all


def test_an_empty_expectation_does_not_fail():
    case = _case(forbidden_any=["禁止"])
    assert golden_qa.evaluate_case(case, answer_text="何でも", has_citation=False, question_type="")["passed"] is True


def _cases(n, group_size=1):
    return [_case_with(f"c{i:03d}", f"g{i // group_size:03d}" if group_size > 1 else "") for i in range(n)]


def _case_with(case_id, group):
    return golden_qa.parse_case({"case_id": case_id, "question": "q", "expected_citation": True, "group": group})[0]


def test_cases_of_one_group_share_a_fold_and_every_fold_is_used():
    cases = _cases(120, group_size=3)
    folds = golden_qa.assign_folds(cases, 4, seed="s")
    by_group = {}
    for case in cases:
        by_group.setdefault(golden_qa.group_of(case), set()).add(folds[case["case_id"]])
    assert all(len(found) == 1 for found in by_group.values())
    assert set(folds.values()) == {0, 1, 2, 3}


def test_a_fold_depends_only_on_the_seed_and_the_group():
    cases = _cases(60)
    folds = golden_qa.assign_folds(cases, 5, seed="s")
    assert golden_qa.assign_folds(list(reversed(cases)), 5, seed="s") == folds  # order does not matter
    more = golden_qa.assign_folds(cases + _cases(200)[60:], 5, seed="s")
    assert all(more[case_id] == fold for case_id, fold in folds.items())  # added cases move nobody
    assert golden_qa.assign_folds(cases, 5, seed="other") != folds


def test_split_by_fold_is_a_partition_without_a_shared_group():
    cases = _cases(90, group_size=3)
    rest, held = golden_qa.split_by_fold(cases, 3, 1, seed="s")
    assert len(rest) + len(held) == 90 and held
    assert {golden_qa.group_of(c) for c in rest}.isdisjoint({golden_qa.group_of(c) for c in held})
    assert sorted(c["case_id"] for c in rest + held) == sorted(c["case_id"] for c in cases)


@pytest.mark.parametrize("k", [0, 1, -3, 2.5, True, "3"])
def test_k_must_be_an_integer_of_at_least_two(k):
    with pytest.raises(ValueError):
        golden_qa.assign_folds(_cases(3), k)


@pytest.mark.parametrize("held_out", [-1, 3, True, "0"])
def test_the_held_out_fold_must_exist(held_out):
    with pytest.raises(ValueError):
        golden_qa.split_by_fold(_cases(3), 3, held_out)


def test_the_cases_written_for_a_change_are_left_out_of_its_judgement():
    cases = [
        golden_qa.parse_case({"case_id": f"c{i}", "question": "q", "expected_citation": True,
                              "origin_change": "chg1" if i < 2 else ""})[0]
        for i in range(5)
    ]
    kept, left_out = golden_qa.exclude_motivating(cases, "chg1")
    assert [c["case_id"] for c in left_out] == ["c0", "c1"] and [c["case_id"] for c in kept] == ["c2", "c3", "c4"]
    assert golden_qa.exclude_motivating(cases, "other")[1] == []


def test_the_demo_profile_cases_all_load(tmp_path, monkeypatch):
    # The former real golden_qa.jsonl was deliberately removed from the
    # public repository. Exercise the real profile-loading path with a
    # self-contained, explicitly synthetic QA dataset instead.
    import json
    from pathlib import Path

    exported = tmp_path / "volcano_demo" / "exported"
    exported.mkdir(parents=True)
    source_profile = (
        Path(__file__).resolve().parents[1]
        / "config/rag_profiles/volcano_demo/exported/profile.json"
    )
    assert source_profile.is_file(), "the non-sensitive demo profile must exist"
    (exported / "profile.json").write_bytes(source_profile.read_bytes())
    synthetic_rows = [
        {"case_id": "synthetic-volcano-a", "question": "架空の火山訓練について教えて",
         "expected_all": ["訓練"], "priority": 5},
        {"case_id": "synthetic-volcano-b", "question": "架空の避難訓練に根拠はありますか",
         "expected_citation": True, "priority": 1},
    ]
    (exported / "golden_qa.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in synthetic_rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        profile_loader, "_resolve",
        lambda project_id: (
            (exported if project_id == "volcano_demo"
             else tmp_path / "default" / "exported"),
            "",
        ),
    )
    profile_loader.reload_rag_profile_cache()
    try:
        profile = profile_loader.load_rag_profile("volcano_demo")
        cases, problems = golden_qa.load_cases(profile)
        assert profile["profile"]["project_id"] == "volcano_demo"
        assert problems == []
        assert len(cases) == len(synthetic_rows)
        assert [case["case_id"] for case in cases] == [
            "synthetic-volcano-a", "synthetic-volcano-b"
        ]
    finally:
        profile_loader.reload_rag_profile_cache()
