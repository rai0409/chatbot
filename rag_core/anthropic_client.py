"""Claude through Anthropic's own API, shaped like the OpenAI client the rest of the system calls (S7).

The gateway and the answer pipeline call `client.chat.completions.create(model=..., messages=[...], ...)` and read
`choices[0].message.content`, `usage.prompt_tokens`, or, for a stream, `choices[0].delta.content`. A profile with
`"provider": "anthropic"` gets this client instead of an OpenAI one, so nothing else changes: the policy, the breaker, the limits, the
tiers and the usage log apply to Claude in the same way. It uses the official `anthropic` package (Messages API), not the
OpenAI-compatibility layer, which Anthropic describes as meant for testing.

What it does for the caller:
  - `system` messages are moved to the `system` field; consecutive messages of one role are joined (the Messages API wants them alternating).
  - `max_tokens` is always sent (the API requires it).
  - `temperature` is NOT sent: sampling parameters are refused by the current models and API versions, and the answer is made
    from the evidence, not from variety. (`temperature=` is accepted and ignored, as the callers pass it.)
  - the profile's `effort` (low .. max) goes to `output_config`, and its `thinking` ("adaptive" or "disabled") to `thinking`. Leave them
    out to get the model's own defaults.
  - the SDK's own retries are off (`max_retries=0`): the gateway retries, and its breaker counts what is left.
  - a refusal by the model's safety checks comes back as an empty answer with `finish_reason == "refusal"`: the pipeline then falls
    back to the extractive answer, and it is not counted as a failure of the endpoint.
Errors keep the SDK's classes: `anthropic.APIStatusError` has `.status_code` (429 and 5xx are retried, 400 and 422 are not counted
against the breaker), `anthropic.APITimeoutError` and `anthropic.APIConnectionError` are retried by name.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Sequence

DEFAULT_MAX_TOKENS = 1024
EFFORTS = ("low", "medium", "high", "xhigh", "max")
THINKING = ("adaptive", "disabled")


def split_messages(messages: Sequence[Dict[str, Any]]) -> "tuple[str, List[Dict[str, str]]]":
    """(system text, messages for the Messages API): system parts joined, same-role neighbours joined, only user and assistant left."""
    system_parts: List[str] = []
    turns: List[Dict[str, str]] = []
    for message in messages:
        role, text = str(message.get("role") or ""), str(message.get("content") or "")
        if role in ("system", "developer"):
            if text.strip():
                system_parts.append(text)
            continue
        if role not in ("user", "assistant") or not text.strip():
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": text})
    return "\n\n".join(system_parts), turns


def _usage(usage: Any) -> SimpleNamespace:
    """OpenAI-shaped usage: all input-side tokens (fresh, cache writes, cache reads) are the prompt."""
    fresh = int(getattr(usage, "input_tokens", 0) or 0)
    cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0) + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    return SimpleNamespace(prompt_tokens=fresh + cached, completion_tokens=out, total_tokens=fresh + cached + out)


_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "refusal": "refusal", "tool_use": "tool_calls"}


class _Completions:
    def __init__(self, owner: "AnthropicChatClient"):
        self._owner = owner

    def _request(self, model: str, messages: Sequence[Dict[str, Any]], max_tokens: Optional[int]) -> Dict[str, Any]:
        system, turns = split_messages(messages)
        if not turns:
            raise ValueError("a request needs at least one user message")
        params: Dict[str, Any] = {"model": model, "max_tokens": int(max_tokens or DEFAULT_MAX_TOKENS), "messages": turns}
        if system:
            params["system"] = system
        if self._owner.effort:
            params["output_config"] = {"effort": self._owner.effort}
        if self._owner.thinking:
            params["thinking"] = {"type": self._owner.thinking}
        return params

    def create(self, *, model: str, messages: Sequence[Dict[str, Any]], max_tokens: Optional[int] = None,
               timeout: Optional[float] = None, stream: bool = False, temperature: Any = None, **_ignored: Any) -> Any:
        """One answer (an OpenAI-shaped response), or with stream=True an iterator of OpenAI-shaped chunks."""
        del temperature  # never sent: see the module docstring
        params = self._request(model, messages, max_tokens)
        sdk = self._owner.sdk_client
        if timeout:
            sdk = sdk.with_options(timeout=float(timeout))
        if stream:
            return self._stream(sdk, params)
        response = sdk.messages.create(**params)
        text = "".join(block.text for block in getattr(response, "content", []) or [] if getattr(block, "type", "") == "text")
        reason = _FINISH.get(str(getattr(response, "stop_reason", "") or ""), "stop")
        message = SimpleNamespace(role="assistant", content=text)
        return SimpleNamespace(choices=[SimpleNamespace(index=0, message=message, finish_reason=reason)],
                               usage=_usage(getattr(response, "usage", None)), model=model)

    @staticmethod
    def _stream(sdk: Any, params: Dict[str, Any]) -> Iterator[Any]:
        with sdk.messages.stream(**params) as stream:
            for text in stream.text_stream:
                if text:
                    yield SimpleNamespace(choices=[SimpleNamespace(index=0, delta=SimpleNamespace(content=text), finish_reason=None)])


class AnthropicChatClient:
    """`client.chat.completions.create(...)` on top of the Anthropic SDK. `sdk_client` is for tests; normally it is made from the key."""

    def __init__(self, *, api_key: str = "", base_url: str = "", effort: str = "", thinking: str = "", sdk_client: Any = None):
        if effort and effort not in EFFORTS:
            raise ValueError(f"effort is one of: {', '.join(EFFORTS)}")
        if thinking and thinking not in THINKING:
            raise ValueError(f"thinking is one of: {', '.join(THINKING)}")
        self.effort, self.thinking = effort, thinking
        if sdk_client is None:
            try:
                import anthropic
            except ImportError as exc:  # noqa: BLE001
                raise RuntimeError("the 'anthropic' package is required for a profile with provider 'anthropic' (pip install anthropic)") from exc
            options: Dict[str, Any] = {"api_key": api_key, "max_retries": 0}
            if base_url:
                options["base_url"] = base_url
            sdk_client = anthropic.Anthropic(**options)
        self.sdk_client = sdk_client
        self.chat = SimpleNamespace(completions=_Completions(self))
