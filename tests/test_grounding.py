import json
import types

import pytest

import config
from rag_core import grounding, llm, qa
from rag_grounded import Chunk

CHUNKS = [
    Chunk(id="c1", text="パスワードは90日ごとに変更します。ID-42 の承認は管理者が行います。", source_doc="rules.pdf", source_pages=(1,)),
    Chunk(id="c2", text="サーバーの再起動は夜間に実施します。", source_doc="rules.pdf", source_pages=(2,)),
]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name, value in {"GROUNDING_VERIFY_ENABLED": False, "GROUNDING_MIN_COVERAGE": 0.5, "LLM_ENABLED": None,
                        "LLM_FEATURES": ["generate"], "LLM_TENANT_ALLOWLIST": [], "LLM_TENANT_DENYLIST": [],
                        "CHAT_GENERATION_MODE": "extractive", "CHAT_COMPLETION_MAX_RETRIES": 0}.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    grounding.reset_stats()
    llm.reset_stats()


@pytest.mark.parametrize("claim, rules", [
    ("パスワードは90日ごとに変更します。", []),
    ("パスワードは９０日ごとに変更します。", []),
    ("パスワードは30日ごとに変更します。", ["numbers"]),
    ("ID-43 の承認は管理者が行います。", ["numbers", "identifier"]),
    ("ID-42 と admin の承認は管理者が行います。", ["identifier"]),
    ("パスワードは90日ごとにトークンを変更します。", ["katakana"]),
    ("パスワードは90日ごとに変更します（推奨設定）。", ["new_term"]),
])
def test_the_hard_rules(claim, rules):
    broken, _coverage = grounding.check_claim(claim, CHUNKS[0].text, min_coverage=0.5)
    assert broken == rules


def test_coverage_is_the_share_of_terms_the_evidence_holds():
    assert grounding.check_claim("パスワードは90日ごとに変更します。", CHUNKS[0].text, min_coverage=0.5)[1] == 1.0
    broken, coverage = grounding.check_claim("確認や調査を行う。", CHUNKS[0].text, min_coverage=0.5)
    assert broken == [] and coverage < 0.5
    assert grounding.check_claim("。。。", CHUNKS[0].text, min_coverage=0.5) == ([], 1.0)  # no terms: nothing to miss


RAW = "\n".join([
    "文書内では、以下の記載が確認できます。",
    "- パスワードは90日ごとに変更します。 [S1]",
    "- パスワードは30日ごとに変更します。 [S1]",
    "- サーバーの再起動は夜間に実施します。 [S2]",
    "- 夜間に実施します。",
    "- 再起動は夜間です。 [S9]",
    "不明: 根拠なし",
])


def test_unsupported_bullets_are_dropped_and_everything_else_stays():
    out = grounding.verify_answer(RAW, CHUNKS)
    assert out.raw.splitlines() == ["文書内では、以下の記載が確認できます。", "- パスワードは90日ごとに変更します。 [S1]",
                                    "- サーバーの再起動は夜間に実施します。 [S2]", "不明: 根拠なし"]
    assert (out.kept, out.total) == (2, 5)
    assert [(d["index"], d["reasons"]) for d in out.dropped] == [(2, ["numbers"]), (4, ["no_citation"]), (5, ["no_citation"])]
    assert "30" not in json.dumps(out.dropped) and grounding.stats() == {"checked": 5, "dropped": 3, "rescued": 0}


def test_a_sentence_must_be_supported_by_the_chunk_it_cites():
    raw = "- サーバーの再起動は夜間に実施します。 [S1]"  # true, but not in chunk 1
    out = grounding.verify_answer(raw, CHUNKS)
    assert out.kept == 0 and out.dropped[0]["reasons"] != []
    assert grounding.verify_answer("- サーバーの再起動は夜間に実施します。 [S1] [S2]", CHUNKS).kept == 1  # any cited chunk may carry it


class _Client:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])


SOFT = "- 確認や調査を行います。 [S1]"


def _verify_on(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_FEATURES", ["verify"])


def test_a_soft_failure_is_dropped_without_the_llm_and_can_be_rescued_with_it(monkeypatch):
    client = _Client('{"supported": true}')
    assert grounding.verify_answer(SOFT, CHUNKS, client=client).kept == 0 and client.calls == []  # feature off: no call
    _verify_on(monkeypatch)
    out = grounding.verify_answer(SOFT, CHUNKS, client=client)
    assert out.kept == 1 and out.rescued == 1 and out.dropped == [] and len(client.calls) == 1
    prompt = client.calls[0]["messages"][1]["content"]
    assert "確認や調査を行います。" in prompt and CHUNKS[0].text in prompt and CHUNKS[1].text not in prompt
    assert grounding.stats()["rescued"] == 1


@pytest.mark.parametrize("reply", ['{"supported": false}', "支持されません", "", '{"supported": "true"}', RuntimeError("boom"),
                                   "{", '["supported"]'])
def test_anything_but_an_explicit_yes_drops_the_sentence(monkeypatch, reply):
    _verify_on(monkeypatch)
    out = grounding.verify_answer(SOFT, CHUNKS, client=_Client(reply))
    assert out.kept == 0 and out.dropped[0]["reasons"] == ["coverage"]


def test_a_hard_failure_is_never_sent_to_the_llm(monkeypatch):
    _verify_on(monkeypatch)
    client = _Client('{"supported": true}')
    out = grounding.verify_answer("- パスワードは30日ごとに変更します。 [S1]", CHUNKS, client=client)
    assert out.kept == 0 and client.calls == []


def test_the_threshold_is_the_setting_or_an_argument(monkeypatch):
    claim = "- パスワードは変更します。管理者が確認します。 [S1]"
    monkeypatch.setattr(config, "GROUNDING_MIN_COVERAGE", 0.0)
    assert grounding.verify_answer(claim, CHUNKS).kept == 1
    monkeypatch.setattr(config, "GROUNDING_MIN_COVERAGE", 1.0)
    assert grounding.verify_answer("- 確認や調査を行います。 [S1]", CHUNKS).kept == 0
    assert grounding.verify_answer("- 確認や調査を行います。 [S1]", CHUNKS, min_coverage=0.0).kept == 1


# --- in the answer path ------------------------------------------------------------------------------------------


Q = "パスワードの変更頻度は？"
GEN = "- パスワードは90日ごとに変更します。 [S1]\n- パスワードは30日ごとに変更します。 [S1]"


def test_without_the_switch_the_answer_is_finalized_as_before(monkeypatch):
    raw, fallback = qa._finalize_generated_answer(GEN, Q, CHUNKS, "faq")
    assert "30日" in raw and fallback is False


def test_with_the_switch_unsupported_bullets_go_and_the_rest_keeps_its_citations(monkeypatch):
    monkeypatch.setattr(config, "GROUNDING_VERIFY_ENABLED", True)
    raw, fallback = qa._finalize_generated_answer(GEN, Q, CHUNKS, "faq")
    assert raw == "- パスワードは90日ごとに変更します。 [S1]" and fallback is False


def test_an_answer_with_nothing_supported_becomes_the_extractive_one(monkeypatch):
    monkeypatch.setattr(config, "GROUNDING_VERIFY_ENABLED", True)
    raw, fallback = qa._finalize_generated_answer("- パスワードは30日ごとに変更します。 [S1]", Q, CHUNKS, "faq")
    assert fallback is True and "90日" in raw and "30日" not in raw and "[S1]" in raw


def test_the_verification_is_passed_the_tenant_and_the_client_by_both_generation_paths():
    import inspect

    source = inspect.getsource(qa)
    assert source.count("_finalize_generated_answer(") == 3  # the definition and the two callers
    assert source.count('tenant_id=tenant_id, client=client if hasattr(client, "chat") else None,') == 2


def test_the_knobs_are_registered_and_in_the_cache_key():
    from rag_core import answer_cache, knob_registry

    assert knob_registry.check_value("GROUNDING_MIN_COVERAGE", 0.7) == 0.7
    assert knob_registry.check_value("GROUNDING_VERIFY_ENABLED", True) is True
    with pytest.raises(knob_registry.KnobError):
        knob_registry.check_value("GROUNDING_MIN_COVERAGE", 1.5)
    assert {"GROUNDING_VERIFY_ENABLED", "GROUNDING_MIN_COVERAGE"} <= set(answer_cache._SETTING_NAMES)
