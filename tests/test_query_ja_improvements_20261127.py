from __future__ import annotations

# 2026-11-27: Japanese query normalization (business synonyms, abbreviations, colloquial rewrites,
# style routing, perturbation kinds and bench slices). Example values are generic business words only.

import json
from pathlib import Path

import pytest

import config
from eval.bench import synthetic
from eval.tenant_eval import perturb
from rag_core import query_preprocess as qp
from rag_core import query_route

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setenv("NORMALIZATION_PROFILE", "legacy")
    monkeypatch.setenv("QUERY_TYPO_CORRECTION", "false")
    monkeypatch.setenv("QUERY_SYNONYM_EXPANSION", "true")
    monkeypatch.setattr(config, "QUERY_BUSINESS_SYNONYMS", False)
    monkeypatch.setattr(config, "QUERY_ABBREVIATIONS", False)
    monkeypatch.setattr(qp, "_profile_synonym_groups", lambda rag_profile_id: [])
    qp._load_rules.cache_clear()
    yield
    qp._load_rules.cache_clear()


# --- flags ------------------------------------------------------------------------------------------


def test_new_flags_default_off_and_change_nothing_while_off():
    assert config.QUERY_BUSINESS_SYNONYMS is False and config.QUERY_ABBREVIATIONS is False
    result = qp.preprocess_query("育休の申請")
    assert result.expansions == []
    assert [s["step"] for s in result.steps] == ["normalize", "colloquial", "numeric_tokens", "synonyms", "reading_lane"]


# --- 1. business synonyms ------------------------------------------------------------------------


def test_business_synonyms_join_the_main_retrieval_only_when_enabled(monkeypatch):
    assert qp.expand_synonyms("年調の書類") == []
    monkeypatch.setattr(config, "QUERY_BUSINESS_SYNONYMS", True)
    assert qp.expand_synonyms("年調の書類") == ["年末調整"]


def test_profile_groups_win_over_generic_groups_and_identical_groups_are_kept_once():
    merged = qp.merge_synonym_groups([["退職", "退社"]], [("退職", "離職"), ("勤怠", "打刻"), ("勤怠", "打刻")])
    assert merged == [["退職", "退社"], ["勤怠", "打刻"]]


def test_negative_mismatch_pairs_suppress_an_opposite_expansion(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "QUERY_BUSINESS_SYNONYMS", True)
    path = tmp_path / "syn.json"
    path.write_text(json.dumps({
        "synonym_groups": [{"canonical": "入会", "terms": ["加入", "脱退"]}],
        "negative_mismatch_pairs": [{"left": ["加入"], "right": ["脱退"]}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(qp, "BUSINESS_SYNONYMS_PATH", str(path))
    assert qp.expand_synonyms("保険の加入") == ["入会"]  # 脱退 is on the other side
    monkeypatch.setattr(qp, "BUSINESS_SYNONYMS_PATH", None)
    assert "扶養から外れる" not in qp.expand_synonyms("扶養に入る手続き")
    pairs = qp.business_synonyms()[1]
    assert qp.crosses_mismatch("扶養から外れる", "扶養に入る手続き", pairs)
    # the query itself names the term's side: no suppression
    assert not qp.crosses_mismatch("育児休業", "退職と育休", pairs)


def test_short_business_terms_need_a_whole_query_term(monkeypatch):
    monkeypatch.setattr(config, "QUERY_BUSINESS_SYNONYMS", True)
    assert qp.expand_synonyms("npwの設定") == []  # "pw" inside another token
    assert "パスワード" in qp.expand_synonyms("pwを忘れた")


def test_business_dictionary_cache_follows_the_file(monkeypatch, tmp_path):
    path = tmp_path / "syn.json"
    path.write_text(json.dumps({"synonym_groups": [{"canonical": "勤怠", "terms": ["打刻"]}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(qp, "BUSINESS_SYNONYMS_PATH", str(path))
    assert qp.business_synonyms()[0] == (("勤怠", "打刻"),)
    path.write_text(json.dumps({"synonym_groups": [{"canonical": "勤怠", "terms": ["出退勤", "打刻"]}]}, ensure_ascii=False), encoding="utf-8")
    assert qp.business_synonyms()[0] == (("勤怠", "出退勤", "打刻"),)
    path.write_text("{broken", encoding="utf-8")
    assert qp.business_synonyms() == ((), ())  # a broken dictionary never breaks a query


def test_expansion_exclusions_and_narrower_compounds_are_not_added(monkeypatch):
    monkeypatch.setattr(config, "QUERY_BUSINESS_SYNONYMS", True)
    added = qp.expand_synonyms("パスワードの定期変更")
    assert "パスワード再設定" not in added and "パスワードリセット" not in added
    # 窓口 / 連絡先 are expansion exclusions (query_expansion.exclude_terms): they pulled near-duplicate documents
    assert qp.expand_synonyms("本社窓口の受付時間") == []
    data = json.loads((REPO / "configs" / "japanese_business_synonyms.json").read_text(encoding="utf-8"))
    assert "窓口" in data["query_expansion"]["exclude_terms"]


# --- 2. abbreviations ---------------------------------------------------------------------------


def test_abbreviation_dictionary_is_well_formed_and_generic():
    data = json.loads((REPO / "configs" / "query_rules" / "abbreviations_ja.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    entries = data["entries"]
    assert 40 <= len(entries) <= 80
    abbrs = [e["abbr"] for e in entries]
    assert len(abbrs) == len(set(abbrs))
    for entry in entries:
        assert set(entry) == {"abbr", "expansion", "bidirectional", "note"}
        assert len(entry["abbr"]) >= 2 and entry["expansion"] and entry["abbr"] != entry["expansion"]
        assert isinstance(entry["bidirectional"], bool)


@pytest.mark.parametrize(
    "query, expected",
    [
        ("育休の申請", ["育児休業"]),
        ("年調の書類", ["年末調整"]),
        ("pwを忘れた", ["パスワード"]),
        ("pcが壊れた", ["パソコン"]),
        ("時短勤務の条件", ["短時間勤務"]),
        ("育児休業の申請", ["育休"]),  # bidirectional
        ("在宅の申請", ["在宅勤務"]),
    ],
)
def test_abbreviations_expand(query, expected):
    assert qp.expand_abbreviations(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "在宅勤務の申請",  # 在宅 inside the expansion already present
        "源泉徴収票の再発行",  # 源泉 inside 源泉徴収; not bidirectional
        "npcの設定",  # "pc" inside another ASCII token
        "マイナスの金額",  # マイナ inside a longer word
        "契約の解除",
    ],
)
def test_abbreviations_do_not_fire_on_substrings(query):
    assert qp.expand_abbreviations(query) == []


def test_abbreviation_stage_runs_before_synonyms_and_shares_the_cap(monkeypatch):
    monkeypatch.setattr(config, "QUERY_ABBREVIATIONS", True)
    monkeypatch.setattr(qp, "_profile_synonym_groups", lambda rag_profile_id: [["育休", "育児休業", *[f"語{i}" for i in range(20)]]])
    result = qp.preprocess_query("育休の申請")
    steps = [s["step"] for s in result.steps]
    assert steps.index("abbreviations") < steps.index("synonyms")
    assert result.expansions[0] == "育児休業"
    assert result.expansions.count("育児休業") == 1
    assert len(result.expansions) == qp._MAX_SYNONYM_EXPANSIONS


def test_tenant_abbreviations_override_and_disable_global_entries(monkeypatch, tmp_path):
    rules = tmp_path / "query_rules"
    (rules / "tenants").mkdir(parents=True)
    (rules / "colloquial_ja.json").write_text(json.dumps({"strip_prefix": [], "strip_suffix": []}), encoding="utf-8")
    (rules / "abbreviations_ja.json").write_text(json.dumps({"version": 1, "entries": [
        {"abbr": "育休", "expansion": "育児休業", "bidirectional": True},
        {"abbr": "年調", "expansion": "年末調整", "bidirectional": True},
    ]}, ensure_ascii=False), encoding="utf-8")
    (rules / "tenants" / "tenant_a.json").write_text(json.dumps({"strip_prefix": [], "abbreviations": [
        {"abbr": "年調", "enabled": False},
        {"abbr": "定例", "expansion": "定例会議", "bidirectional": False},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(qp, "RULES_DIR", rules)
    assert qp.expand_abbreviations("年調と定例", tenant_id="default") == ["年末調整"]
    assert qp.expand_abbreviations("年調と定例", tenant_id="tenant_a") == ["定例会議"]
    assert qp.expand_abbreviations("育休", tenant_id="tenant_a") == ["育児休業"]
    assert qp.abbreviation_files("../etc") == [str(rules / "abbreviations_ja.json")]


# --- 3. colloquial rewrites ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, expected",
    [
        ("経費精算ってどうやるの？", "経費精算の手順"),
        ("申請するにはどうすればいい", "申請の手順"),
        ("設定をどうやればいいの", "設定の手順"),
        ("経費精算したいんだけど", "経費精算"),
        ("休暇を申請したいんですけど", "休暇を申請"),
        ("トナーってどこにあるの", "トナーの場所"),
        ("申請書ってどこ", "申請書の場所"),
        ("在宅勤務できる？", "在宅勤務できる"),
        ("在宅勤務できるの？", "在宅勤務できる"),
        ("経費精算のやり方", "経費精算の方法"),
        ("経費精算のやり方を教えて", "経費精算の方法"),
        ("多要素認証ってなに", "多要素認証とは"),
    ],
)
def test_colloquial_rewrites_positive(question, expected):
    assert qp.preprocess_query(question).query == expected


@pytest.mark.parametrize(
    "question, expected",
    [
        ("パスワードを忘れたときはどうすればいいですか", "パスワードを忘れたときはどうすればいい"),  # polite: not rewritten
        ("vpnがつながらないんだけどどうしたらいい", "vpnがつながらないんだけどどうしたらいい"),  # trouble report, not how-to
        ("申請したくないんだけど", "申請したくない"),  # negation kept
        ("提出先はどこですか", "提出先はどこ"),  # formal question: no 場所 rewrite
        ("在宅勤務できない？", "在宅勤務できない"),  # negation kept
        ("在宅勤務できますか", "在宅勤務できますか"),  # polite verb ending kept
        ("「申請のやり方」について", "「申請のやり方」について"),  # quoted term untouched
        ("申請のやり方一覧", "申請のやり方一覧"),  # inside a compound
        ("「どうすればいい」とは", "「どうすればいい」とは"),
        ("「どこ」の意味", "「どこ」の意味"),
        ("在宅勤務の申請方法", "在宅勤務の申請方法"),
    ],
)
def test_colloquial_rewrites_negative(question, expected):
    assert qp.preprocess_query(question).query == expected


def test_new_rewrites_live_under_the_rewrite_key_and_do_not_touch_approved_qa_stripping():
    data = json.loads((REPO / "configs" / "query_rules" / "colloquial_ja.json").read_text(encoding="utf-8"))
    assert {r["name"] for r in data["rewrite"]} == {
        "howto_to_procedure", "want_to_do", "where_is", "can_i_plain", "yarikata_to_houhou",
    }
    # strip_colloquial without rewrite (approved QA) keeps the how-to wording
    assert qp.strip_colloquial("経費精算のやり方")[0] == "経費精算のやり方"


# --- 4. routing ----------------------------------------------------------------------------------


def test_style_routes_raise_the_dense_weight_only_when_routing_is_on(monkeypatch):
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", False)
    assert query_route.route_for("other", question="経費精算ってどうやるの") is None
    monkeypatch.setattr(config, "QUERY_ROUTING_ENABLED", True)
    route = query_route.route_for("other", question="経費精算ってどうやるの")
    assert route["name"] == "colloquial" and route["scale"]["dense"] > 1 > route["scale"]["keyword"]
    route = query_route.route_for("procedure", question="家で仕事をするための事前の届出")
    assert route["name"] == "paraphrase" and route["scale"]["dense"] > route["scale"]["keyword"]
    # exact lookups and identifiers keep their term routes
    assert query_route.route_for("identifier", question="abc-123ってどこ")["name"] == "identifier"
    # without the question the historical behaviour holds
    assert query_route.route_for("other") is None and query_route.route_for("faq")["name"] == "faq"
    assert query_route.route_for("other", question="在宅勤務の申請方法") is None


@pytest.mark.parametrize(
    "question, style",
    [
        ("打刻忘れちゃったんだけど", "colloquial"),
        ("研修っていつあるの", "colloquial"),
        ("えっと、経費の締め日", "colloquial"),
        ("印刷機の紙がなくなったときの入れ方", "paraphrase"),
        ("在宅勤務の申請方法", None),
        ("ABC-123の仕様", None),
        ("パスワードを忘れた場合の再設定の確認コードの有効期限は", None),
        ("", None),
    ],
)
def test_detect_style(question, style):
    assert query_route.detect_style(question) == style


# --- 6. perturbation kinds and bench slices ------------------------------------------------------


def test_new_perturb_kinds_are_registered_and_deterministic():
    assert {"abbreviate", "synonym_swap"} <= set(perturb.KINDS)
    for kind in ("abbreviate", "synonym_swap"):
        first = perturb.perturb_question("育児休業の申請方法を教えてください", kinds=[kind], seed="s")
        assert first == perturb.perturb_question("育児休業の申請方法を教えてください", kinds=[kind], seed="s")


def test_abbreviate_replaces_an_expansion_with_its_abbreviation():
    out = perturb.perturb_question("年末調整の書類はいつまでですか", kinds=["abbreviate"])
    assert out == [{"kind": "abbreviate", "text": "年調の書類はいつまでですか"}]
    assert perturb.perturb_question("ABC123の設定手順", kinds=["abbreviate"]) == []


def test_synonym_swap_uses_a_group_member_and_never_crosses_a_mismatch():
    out = perturb.perturb_question("年末調整の書類はいつまでですか", kinds=["synonym_swap"])
    assert out == [{"kind": "synonym_swap", "text": "年調の書類はいつまでですか"}]
    for seed in map(str, range(20)):
        for variant in perturb.perturb_question("扶養に入る手続き", kinds=["synonym_swap"], seed=seed):
            assert "外れる" not in variant["text"]
    assert perturb.perturb_question("ABC123の設定手順", kinds=["synonym_swap"]) == []


def test_perturbed_case_ids_use_the_new_kinds():
    made = perturb.perturb_cases([{"case_id": "c1", "question": "育児休業の申請方法"}], kinds=["abbreviate"])
    assert made[0]["case_id"] == "c1~abbreviate" and made[0]["question"] == "育休の申請方法"
    rows = [{"case_id": "c1", "passed": True}, {"case_id": "c1~abbreviate", "passed": False}]
    assert perturb.robustness(rows)["abbreviate"]["lost"] == 1


@pytest.mark.parametrize(
    "query, tag, present",
    [
        ("経費精算ってどうやるの", "colloquial", True),
        ("打刻忘れちゃったんだけど", "colloquial", True),
        ("経費精算の方法", "colloquial", False),
        ("契約の解除について詳しく教えてください", "colloquial", False),
        ("育休の申請", "abbreviation", True),
        ("VPNの接続手順", "abbreviation", True),
        ("在宅勤務の申請方法", "abbreviation", False),
        ("マイナスの金額", "abbreviation", False),
    ],
)
def test_bench_auto_slices_colloquial_and_abbreviation(query, tag, present):
    assert (tag in synthetic.auto_slices(query)) is present


def test_dictionary_matching_never_calls_morphological_analysis(monkeypatch):
    from rag_core import ja_terms
    monkeypatch.setattr(ja_terms, 'content_terms', lambda *a, **k: pytest.fail('tokenizer called'))
    monkeypatch.setattr(config, 'QUERY_ABBREVIATIONS', True)
    monkeypatch.setattr(config, 'QUERY_BUSINESS_SYNONYMS', True)
    assert '育児休業' in qp.preprocess_query('育休の申請').expansions
    assert '年末調整' in qp.expand_synonyms('年調の書類')
    assert 'abbreviation' in synthetic.auto_slices('育休の申請')
    assert not qp.term_in_query('it', 'edit')


@pytest.mark.parametrize('question, required', [
    ('申請しない場合はどうやるの', 'しない場合'),
    ('申請は禁止だけどどうしたらいい', '禁止'),
    ('4月1日以降はどうやるの', '4月1日以降'),
    ('abc-123を除く場合はどうやるの', 'abc-123を除く場合'),
    ('「申請のやり方」を教えて', '「申請のやり方」'),
])
def test_rewrites_preserve_semantic_constraints(question, required):
    assert required in qp.preprocess_query(question).query


def test_short_matches_are_per_occurrence_and_ascii_identifiers_are_bounded(monkeypatch):
    assert qp.term_in_query('ＰＣ', 'ｐｃの設定')
    assert not qp.term_in_query('pc', 'npcの設定')
    assert not qp.term_in_query('it', 'edit')
    assert qp.expand_abbreviations('マイナスとマイナの手続き') == ['マイナンバー']
    monkeypatch.setattr(qp, '_synonym_groups', lambda _: [['在宅', '在宅勤務'], ['在宅勤務', 'テレワーク']])
    assert '在宅' not in qp._present_surfaces('在宅勤務の申請', ['在宅', '在宅勤務'], None)
