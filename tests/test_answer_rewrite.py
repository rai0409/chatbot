import json
import types
from types import SimpleNamespace

import pytest

import config
from rag_core import answer_rewrite, llm, qa
from rag_grounded import Chunk

RAW = "文書内では、以下の記載が確認できます。\n- パスワードは90日ごとに変更します。 [S1]\n- 管理者は ID-42 を承認しない。 [S1] [S2]"


def test_bullets_are_found_with_their_tags_and_other_lines_are_left_out():
    found = answer_rewrite.parse_bullets(RAW)
    assert found == [(1, "- ", "パスワードは90日ごとに変更します。", "[S1]"),
                     (2, "- ", "管理者は ID-42 を承認しない。", "[S1] [S2]")]
    assert answer_rewrite.parse_bullets("不明: 根拠不足\n- 本文のみ") == []


@pytest.mark.parametrize("original, rewritten, rules", [
    ("パスワードは90日ごとに変更します。", "パスワードは90日ごとに変更してください。", []),
    ("パスワードは90日ごとに変更します。", "パスワードは９０日ごとに変更します。", []),  # full-width digits are the same number
    ("パスワードは90日ごとに変更します。", "パスワードは30日ごとに変更します。", ["numbers"]),
    ("パスワードは90日ごとに変更します。", "パスワードは変更します。", ["numbers"]),
    ("パスワードは90日ごとに変更します。", "パスワードは90日ごとに、1回変更します。", ["numbers", "new_term"]),  # "回変更" is a new 3-kanji run
    ("管理者は ID-42 を承認する。", "管理者は ID-43 を承認する。", ["numbers", "identifier"]),
    ("管理者は ID-42 を承認する。", "管理者は ID-42 と admin を承認する。", ["identifier"]),
    ("アカウントを作成する。", "アカウントとトークンを作成する。", ["katakana"]),
    ("申請書類を期限までに提出する。", "申請書類を期限までに提出する（推奨設定）。", ["new_term"]),
    ("申請書を提出しない。", "申請書を提出する。", ["negation"]),
    ("申請書を提出する。", "申請書を提出しないで。", ["negation"]),
    ("してはいけません。", "してはなりません。", []),  # a prohibition stays one
    ("申請書を提出する。", "申請書を提出するための手続きをここで丁寧に詳しく説明します。", ["length"]),
    ("申請書を提出する。", "", ["empty"]),
    ("申請書を提出する。", "   ", ["empty"]),
])
def test_a_rewritten_sentence_is_accepted_only_when_it_breaks_no_rule(original, rewritten, rules):
    assert answer_rewrite.check_rewrite(original, rewritten) == rules


def test_terms_of_the_question_may_appear_in_the_rewrite():
    assert answer_rewrite.check_rewrite("申請を期限までに提出する。", "申請を期限までに提出する承認フロー。") == ["katakana"]
    assert answer_rewrite.check_rewrite("申請を期限までに提出する。", "申請を期限までに提出する承認フロー。", question="承認フローは？") == []


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
    monkeypatch.setattr(config, "LLM_FEATURES", ["rewrite"])
    monkeypatch.setattr(config, "LLM_TENANT_ALLOWLIST", [])
    monkeypatch.setattr(config, "LLM_TENANT_DENYLIST", [])
    monkeypatch.setattr(config, "CHAT_COMPLETION_MAX_RETRIES", 0)
    monkeypatch.delenv("APP_ENV", raising=False)
    llm.reset_stats()


def test_every_good_sentence_is_replaced_and_the_tags_stay(on):
    client = _Client(json.dumps(["パスワードは90日ごとに変更してください。", "管理者は ID-42 を承認できません。"], ensure_ascii=False))
    out = answer_rewrite.rewrite_answer(RAW, "パスワードは？", client=client)
    assert out.reason == "ok" and (out.rewritten, out.kept) == (2, 0)
    assert out.raw.splitlines() == ["文書内では、以下の記載が確認できます。", "- パスワードは90日ごとに変更してください。 [S1]",
                                    "- 管理者は ID-42 を承認できません。 [S1] [S2]"]
    sent = json.loads(client.calls[0]["messages"][1]["content"])
    assert sent == ["パスワードは90日ごとに変更します。", "管理者は ID-42 を承認しない。"]  # only the sentences, no tags


def test_a_sentence_that_breaks_a_rule_keeps_its_original_wording(on):
    client = _Client(json.dumps(["パスワードは30日ごとに変更してください。", "管理者は ID-42 を承認できません。"], ensure_ascii=False))
    out = answer_rewrite.rewrite_answer(RAW, "", client=client)
    assert (out.rewritten, out.kept, out.violations) == (1, 1, {"numbers": 1})
    assert out.raw.splitlines()[1] == "- パスワードは90日ごとに変更します。 [S1]" and "承認できません" in out.raw
    assert "30" not in json.dumps(out.summary()) and set(out.summary()) == {"rewritten", "kept_original", "reason", "violations"}


@pytest.mark.parametrize("reply, reason", [
    ("書き直せません", "bad_output"), ("[\"一つだけ\"]", "bad_output"), ("{\"a\": 1}", "bad_output"), ("[1, 2]", "bad_output"),
    ("[", "bad_output"), (RuntimeError("boom"), "error:RuntimeError"),
])
def test_an_unusable_reply_or_a_failure_leaves_the_original_answer(on, reply, reason):
    out = answer_rewrite.rewrite_answer(RAW, "", client=_Client(reply))
    assert out.raw == RAW and out.rewritten == 0 and out.reason == reason


def test_the_reply_may_be_wrapped_in_text(on):
    client = _Client("結果です:\n```json\n[\"パスワードは90日ごとに変更してください。\", \"管理者は ID-42 を承認できません。\"]\n```")
    assert answer_rewrite.rewrite_answer(RAW, "", client=client).rewritten == 2


def test_nothing_is_sent_when_the_policy_refuses_or_there_is_nothing_to_rewrite(monkeypatch, on):
    client = _Client("[]")
    monkeypatch.setattr(config, "LLM_FEATURES", ["verify"])
    out = answer_rewrite.rewrite_answer(RAW, "", client=client)
    assert out.reason == "not_allowed:feature" and out.raw == RAW and client.calls == []
    monkeypatch.setattr(config, "LLM_FEATURES", ["rewrite"])
    assert answer_rewrite.rewrite_answer("不明: 根拠不足", "", client=client).reason == "no_bullets"
    many = "\n".join(f"- 文{i} [S1]" for i in range(answer_rewrite.MAX_LINES + 1))
    assert answer_rewrite.rewrite_answer(many, "", client=client).reason == "too_many_lines" and client.calls == []


# --- in the answer path ------------------------------------------------------------------------------------


def _state():
    chunks = [
        Chunk(id="c1", text="パスワードは90日ごとに変更します。", source_doc="rules.pdf", source_pages=(1,)),
        Chunk(id="c2", text="管理者は ID-42 を承認しない。", source_doc="rules.pdf", source_pages=(2,)),
    ]
    return SimpleNamespace(selected_context=chunks, intent="faq", rewritten_query="", augmented_query="", retrieved=[])


def _result(state, raw):
    return qa._build_answer_result(raw, state.selected_context, intent="faq", guard_reason=None, used_fallback=False,
                                   retrieved=[], rewritten_query="", augmented_query="")


def test_the_answer_path_replaces_the_text_and_keeps_the_citations(monkeypatch, on):
    state = _state()
    monkeypatch.setattr(qa, "_extractive_answer_raw", lambda question, grounded: RAW)
    original = _result(state, RAW)
    client = _Client(json.dumps(["パスワードは90日ごとに変更してください。", "管理者は ID-42 を承認できません。"], ensure_ascii=False))
    trace = {}
    out = qa._apply_answer_rewrite("パスワードは？", state, original, trace, tenant_id="t1", client=client)
    assert "変更してください" in out.answer_text and "変更します" not in out.answer_text
    assert out.citations == original.citations and len(out.citations) == 2
    assert out.guard_reason is None and out.used_fallback is False
    assert trace["answer_rewrite"]["rewritten"] == 2 and trace["answer_rewrite"]["reason"] == "ok"


def test_the_answer_path_returns_the_same_result_when_nothing_was_rewritten_or_it_is_not_allowed(monkeypatch, on):
    state = _state()
    monkeypatch.setattr(qa, "_extractive_answer_raw", lambda question, grounded: RAW)
    original = _result(state, RAW)
    trace = {}
    bad = _Client("not json")
    assert qa._apply_answer_rewrite("q", state, original, trace, tenant_id="t1", client=bad) is original
    assert trace["answer_rewrite"]["reason"] == "bad_output"
    monkeypatch.setattr(config, "LLM_TENANT_DENYLIST", ["t1"])
    trace = {}
    refused = _Client("[]")
    assert qa._apply_answer_rewrite("q", state, original, trace, tenant_id="t1", client=refused) is original
    assert trace == {} and refused.calls == []  # not even the trace: the feature is off for this tenant


def test_the_answer_path_is_wired_at_both_extractive_branches():
    import inspect

    assert inspect.getsource(qa).count("_apply_answer_rewrite(q, state, result, trace, tenant_id=tenant_id, client=client)") == 2


def test_the_llm_settings_are_part_of_the_cache_key(monkeypatch):
    from rag_core import answer_cache

    base = answer_cache._settings_state()
    monkeypatch.setattr(config, "LLM_FEATURES", ["generate", "rewrite"])
    assert answer_cache._settings_state() != base
