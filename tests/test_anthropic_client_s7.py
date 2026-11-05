"""S7: Claude through its own API, shaped like the OpenAI client; the profile settings for it; and the gateway in front of it."""
import json
from types import SimpleNamespace

import pytest

import config
from rag_core import anthropic_client, llm, llm_gateway, llm_profiles
from rag_core.anthropic_client import AnthropicChatClient, split_messages


class FakeSdk:
    """The part of the Anthropic SDK the adapter uses: with_options, messages.create, messages.stream."""

    def __init__(self, text="回答です", stop_reason="end_turn", usage=None, fail=None):
        self.calls, self.options, self.text, self.stop_reason, self.fail = [], [], text, stop_reason, fail
        self.usage = usage or SimpleNamespace(input_tokens=120, output_tokens=30, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        self.messages = SimpleNamespace(create=self._create, stream=self._stream)

    def with_options(self, **options):
        self.options.append(options)
        return self

    def _create(self, **params):
        self.calls.append(params)
        if self.fail:
            raise self.fail
        blocks = [SimpleNamespace(type="thinking", thinking="…"), SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(content=blocks, stop_reason=self.stop_reason, usage=self.usage)

    def _stream(self, **params):
        self.calls.append(params)
        sdk = self

        class Manager:
            def __enter__(self_inner):
                if sdk.fail:
                    raise sdk.fail
                return SimpleNamespace(text_stream=iter(["原文", "", "です"]))

            def __exit__(self_inner, *exc):
                return False

        return Manager()


def _client(sdk=None, **kw):
    return AnthropicChatClient(sdk_client=sdk or FakeSdk(), **kw)


def _ask(client, **kw):
    return client.chat.completions.create(model="claude-haiku-5-5", messages=[{"role": "user", "content": "質問"}], **kw)


# --- the request -------------------------------------------------------------------------------------------------------------------------------


def test_system_messages_move_to_the_system_field_and_neighbours_of_one_role_are_joined():
    system, turns = split_messages([
        {"role": "system", "content": "根拠だけで答える"}, {"role": "user", "content": "質問1"}, {"role": "user", "content": "質問2"},
        {"role": "assistant", "content": "答え"}, {"role": "tool", "content": "ignored"}, {"role": "user", "content": "   "},
        {"role": "developer", "content": "丁寧に"}, {"role": "user", "content": "質問3"},
    ])
    assert system == "根拠だけで答える\n\n丁寧に"
    assert turns == [{"role": "user", "content": "質問1\n\n質問2"}, {"role": "assistant", "content": "答え"}, {"role": "user", "content": "質問3"}]


def test_the_request_has_max_tokens_and_never_a_sampling_parameter():
    sdk = FakeSdk()
    _ask(_client(sdk), temperature=0, max_tokens=300, timeout=12.5)
    [params] = sdk.calls
    assert params == {"model": "claude-haiku-5-5", "max_tokens": 300, "messages": [{"role": "user", "content": "質問"}]}
    assert "temperature" not in params and "top_p" not in params and sdk.options == [{"timeout": 12.5}]
    _ask(_client(sdk))
    assert sdk.calls[-1]["max_tokens"] == anthropic_client.DEFAULT_MAX_TOKENS  # the API requires it


def test_effort_and_thinking_come_from_the_profile_and_are_checked():
    sdk = FakeSdk()
    _ask(_client(sdk, effort="low", thinking="disabled"))
    assert sdk.calls[0]["output_config"] == {"effort": "low"} and sdk.calls[0]["thinking"] == {"type": "disabled"}
    _ask(_client(sdk))
    assert "output_config" not in sdk.calls[1] and "thinking" not in sdk.calls[1]  # the model's own defaults
    with pytest.raises(ValueError):
        _client(effort="extreme")
    with pytest.raises(ValueError):
        _client(thinking="always")
    with pytest.raises(ValueError, match="at least one user message"):
        _client().chat.completions.create(model="m", messages=[{"role": "system", "content": "x"}])


# --- the answer ---------------------------------------------------------------------------------------------------------------------------------


def test_the_answer_is_shaped_like_an_openai_one_and_thinking_blocks_are_left_out():
    answer = _ask(_client(FakeSdk(text="3営業日前です")))
    assert answer.choices[0].message.content == "3営業日前です" and answer.choices[0].finish_reason == "stop"
    assert (answer.usage.prompt_tokens, answer.usage.completion_tokens, answer.usage.total_tokens) == (120, 30, 150)


def test_cached_input_counts_as_prompt_and_finish_reasons_are_translated():
    usage = SimpleNamespace(input_tokens=20, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=10)
    answer = _ask(_client(FakeSdk(usage=usage, stop_reason="max_tokens")))
    assert answer.usage.prompt_tokens == 130 and answer.choices[0].finish_reason == "length"


def test_a_refusal_is_an_empty_answer_and_not_a_failure_of_the_endpoint():
    answer = _ask(_client(FakeSdk(text="", stop_reason="refusal")))
    assert answer.choices[0].message.content == "" and answer.choices[0].finish_reason == "refusal"


def test_a_stream_gives_the_pieces_as_openai_chunks_and_skips_empty_ones():
    pieces = [c.choices[0].delta.content for c in _ask(_client(), stream=True)]
    assert pieces == ["原文", "です"]


def test_the_sdks_errors_are_kept_so_the_retry_rules_and_the_breaker_work():
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=request), body=None)
    with pytest.raises(anthropic.RateLimitError) as caught:
        _ask(_client(FakeSdk(fail=rate)))
    assert caught.value.status_code == 429 and llm._retryable(caught.value) is True
    bad = anthropic.BadRequestError("invalid", response=httpx2.Response(400, request=request), body=None)
    assert llm._retryable(bad) is False and llm_gateway._counts_as_failure(bad) is False  # a malformed request is not the endpoint's failure
    assert llm._retryable(anthropic.APITimeoutError(request=request)) and llm._retryable(anthropic.APIConnectionError(request=request))


def test_the_real_sdk_client_is_built_with_its_own_retries_off(monkeypatch):
    client = AnthropicChatClient(api_key="key-not-a-secret")
    assert client.sdk_client.max_retries == 0
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)  # the package is missing
    with pytest.raises(RuntimeError, match="pip install anthropic"):
        AnthropicChatClient(api_key="k")


# --- the profile --------------------------------------------------------------------------------------------------------------------------------


def _file(tmp_path, monkeypatch, profile):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": {"claude": profile, "local": {"base_url": "http://localhost:11434/v1", "model": "m"}},
                                "routes": {"generate": ["claude", "local"]}}), encoding="utf-8")
    monkeypatch.setattr(config, "LLM_PROFILES_FILE", str(path))
    llm_profiles._CACHE.update(key=None, value=None)


def test_an_anthropic_profile_is_parsed_and_checked(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, {"provider": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "model": "claude-haiku-5-5", "effort": "low",
                                  "thinking": "disabled", "cost": {"input_per_million": 0.1, "output_per_million": 0.5}})
    profile = llm_profiles.all_profiles()["claude"]
    assert profile.provider == "anthropic" and profile.effort == "low" and profile.thinking == "disabled"
    assert profile.host == "api.anthropic.com" and profile.location == "external" and profile.external
    assert llm_profiles.validate() == []
    for bad, message in (({"provider": "anthropic", "model": "m"}, "needs 'api_key_env'"),
                         ({"provider": "anthropic", "api_key_env": "K", "model": "m", "batch": True}, "batch API is not supported"),
                         ({"provider": "anthropic", "api_key_env": "K", "model": "m", "effort": "extreme"}, "'effort' is one of"),
                         ({"provider": "anthropic", "api_key_env": "K", "model": "m", "thinking": "always"}, "'thinking' is one of"),
                         ({"provider": "openai", "model": "m", "effort": "low"}, "for provider 'anthropic' only"),
                         ({"provider": "gemini", "model": "m"}, "'provider' is one of")):
        _file(tmp_path, monkeypatch, bad)
        assert message in llm_profiles.validate()[0], bad


def test_make_client_gives_an_anthropic_client_and_needs_the_key(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, {"provider": "anthropic", "api_key_env": "CLAUDE_KEY_FOR_TEST", "model": "claude-haiku-5-5", "effort": "low"})
    profile = llm_profiles.all_profiles()["claude"]
    monkeypatch.delenv("CLAUDE_KEY_FOR_TEST", raising=False)
    with pytest.raises(RuntimeError, match="API key"):
        llm_profiles.make_client(profile)
    monkeypatch.setenv("CLAUDE_KEY_FOR_TEST", "key-not-a-secret")
    client = llm_profiles.make_client(profile)
    assert isinstance(client, AnthropicChatClient) and client.effort == "low"


def test_production_does_not_use_claude_unless_external_endpoints_are_allowed(tmp_path, monkeypatch):
    _file(tmp_path, monkeypatch, {"provider": "anthropic", "api_key_env": "K", "model": "claude-haiku-5-5"})
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(config, "LLM_EXTERNAL_ALLOWED", False)
    assert llm_profiles.blocked(llm_profiles.all_profiles()["claude"]) is True
    assert [p.name for p in llm_gateway._usable("generate", None)] == ["local"]
    monkeypatch.setattr(config, "LLM_EXTERNAL_ALLOWED", True)
    assert [p.name for p in llm_gateway._usable("generate", None)] == ["claude", "local"]


# --- through the gateway ------------------------------------------------------------------------------------------------------------------------------


def test_the_gateway_calls_claude_with_the_profiles_model_and_records_its_usage(tmp_path, monkeypatch):
    from rag_core import llm_usage

    _file(tmp_path, monkeypatch, {"provider": "anthropic", "api_key_env": "K", "model": "claude-haiku-5-5", "effort": "low",
                                  "cost": {"input_per_million": 0.1, "output_per_million": 0.5}})
    sdk = FakeSdk(text="根拠のある回答")
    monkeypatch.setattr(llm_profiles, "make_client", lambda profile: _client(sdk, effort=profile.effort))
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_FEATURES", ["generate", "rewrite", "verify", "judge"])
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "LLM_USAGE_ENABLED", True)
    text = llm.complete([{"role": "system", "content": "指示"}, {"role": "user", "content": "質問"}], feature="generate", tenant_id="tenant_a")
    assert text == "根拠のある回答"
    [params] = sdk.calls
    assert params["model"] == "claude-haiku-5-5" and params["system"] == "指示" and params["output_config"] == {"effort": "low"}
    [row] = [r for r in llm_usage.read(1) if r["event"] == "call"]
    assert row["profile"] == "claude" and row["host"] == "api.anthropic.com" and row["input_tokens"] == 120 and row["estimated"] is False
    assert row["cost"] == pytest.approx((120 * 0.1 + 30 * 0.5) / 1_000_000)


def test_the_answer_pipeline_streams_and_creates_through_the_same_client(monkeypatch):
    from rag_core import qa

    sdk = FakeSdk(text="答え")
    client = _client(sdk, effort="low")
    profile = llm_profiles.Profile(name="claude", base_url="", model="claude-haiku-5-5", provider="anthropic", effort="low", max_tokens=512)
    response = qa._create_chat_completion(client, [{"role": "user", "content": "質問"}], profile)
    assert response.choices[0].message.content == "答え" and sdk.calls[0]["max_tokens"] == 512
    assert list(qa._stream_chat_completion(client, [{"role": "user", "content": "質問"}], profile)) == ["原文", "です"]
    assert all("temperature" not in call for call in sdk.calls)
