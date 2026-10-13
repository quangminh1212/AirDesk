"""Regression tests for the LLM gateway's contract with the agent loop.

These deliberately bypass `stub_llm_gateway` (tests/conftest.py): the whole
reason a total tool-calling failure shipped under a green suite is that the
stub returns a canned greeting and never exercises `_to_result`.
"""

from __future__ import annotations

import pytest

from app.core.errors import ProviderError
from app.core.llm import LiteLLMGateway


class _FakeResponse:
    """Minimal stand-in for a litellm ModelResponse."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


def _resp(tool_calls: list[dict], content: str = "") -> _FakeResponse:
    return _FakeResponse(
        {
            "model": "test-model",
            "choices": [{"message": {"content": content, "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


def test_blank_tool_name_raises_instead_of_dispatching_empty_string():
    """A provider that did not resolve returns an empty-but-well-formed call.

    Before the fix this produced name="" and the agent loop ran a tool named "",
    which surfaced as a confusing "Unknown tool ''" and silently escalated every
    request. It must fail loudly instead.
    """
    payload = [{"id": "", "type": "function", "function": {"name": "", "arguments": ""}}]

    with pytest.raises(ProviderError) as exc:
        LiteLLMGateway._to_result(_resp(payload), "openai/big-pickle")

    msg = str(exc.value)
    assert "empty name" in msg
    assert "LLM_MODEL" in msg


def test_whitespace_only_tool_name_also_raises():
    payload = [{"id": "x", "type": "function", "function": {"name": "   ", "arguments": "{}"}}]

    with pytest.raises(ProviderError):
        LiteLLMGateway._to_result(_resp(payload), "some/model")


def test_valid_tool_call_is_flattened_into_the_shape_runner_expects():
    """runner reads tc["name"] / tc["arguments"] off the flattened dict."""
    payload = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search_flights",
                "arguments": '{"origin": "HAN", "destination": "DAD"}',
            },
        }
    ]

    result = LiteLLMGateway._to_result(_resp(payload), "openai/big-pickle")

    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["name"] == "search_flights"
    assert call["arguments"] == {"origin": "HAN", "destination": "DAD"}
    assert call["id"] == "call_1"


def test_malformed_tool_arguments_do_not_crash_the_gateway():
    payload = [
        {
            "id": "c",
            "type": "function",
            "function": {"name": "list_providers", "arguments": "{oops"},
        }
    ]

    result = LiteLLMGateway._to_result(_resp(payload), "m")

    assert result.tool_calls[0]["name"] == "list_providers"
    assert result.tool_calls[0]["arguments"] == {"_raw": "{oops"}


def test_no_tool_calls_is_not_an_error():
    result = LiteLLMGateway._to_result(_resp([], content="Xin chào"), "m")

    assert result.tool_calls == []
    assert result.content == "Xin chào"


def test_configured_primary_model_is_not_a_claude_model():
    """The operator requirement: do not route to claude-* / fable models."""
    from app.core.config import get_settings

    settings = get_settings()
    chosen = [settings.llm_model, *settings.llm_fallback_models_list]
    for model in chosen:
        low = model.lower()
        assert "claude" not in low, f"claude model configured: {model}"
        assert "fable" not in low, f"fable model configured: {model}"
