"""LiteLLM gateway.

Every LLM call in AirDesk goes through this module so that: provider,
retries, timeouts, cost accounting and structured tracing live in one place.
LiteLLM normalises Anthropic/OpenAI/Bedrock/… behind a single interface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Keep LiteLLM quiet unless we are debugging; we do our own structured logging.
litellm.suppress_debug_info = True
litellm.drop_params = True  # ignore params unsupported by a given provider


@dataclass
class LLMResult:
    """Normalised result of a chat completion."""

    content: str
    model: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class LiteLLMGateway:
    """Thin, testable wrapper around ``litellm.acompletion``."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def _models(self, model: str | None = None) -> list[str]:
        primary = model or self._settings.llm_model
        return [primary, *self._settings.llm_fallback_models_list]

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Run a chat completion with retries and model fallbacks."""
        models = self._models(model)
        last_error: Exception | None = None

        for candidate in models:
            try:
                return await self._call_one(
                    model=candidate,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    metadata=metadata,
                )
            except Exception as exc:  # noqa: BLE001 - fall through to next model
                last_error = exc
                logger.warning("llm_call_failed", model=candidate, error=str(exc))
                continue

        raise ProviderError(
            f"All LLM models failed: {models}",
            details={"last_error": str(last_error)},
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def _call_one(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict | None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict | None,
        metadata: dict[str, Any] | None,
    ) -> LLMResult:
        settings = self._settings
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "timeout": settings.llm_timeout_seconds,
            "num_retries": settings.llm_max_retries,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format
        if settings.llm_api_base:
            kwargs["api_base"] = settings.llm_api_base
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if metadata:
            kwargs["metadata"] = metadata

        response = await litellm.acompletion(**kwargs)
        return self._to_result(response, model)

    @staticmethod
    def _to_result(response: Any, model: str) -> LLMResult:
        data = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        usage = data.get("usage", {}) or {}

        tool_calls: list[dict[str, Any]] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {}) or {}
            name = fn.get("name") or ""
            # A tool call with a blank name is never usable: the caller would
            # dispatch "" and get a confusing "unknown tool" downstream. This
            # happens in practice when a gateway/proxy does not resolve the
            # model's provider and returns a well-formed but empty tool call.
            # Fail loudly instead of poisoning the agent loop.
            if not name.strip():
                raise ProviderError(
                    f"Model '{model}' returned a tool call with an empty name. "
                    "This usually means the provider prefix in LLM_MODEL does not "
                    "match what the gateway at LLM_API_BASE serves."
                )
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            tool_calls.append(
                {
                    "id": call.get("id") or "",
                    "name": name,
                    "arguments": args,
                }
            )

        hidden = data.get("_hidden_params", {}) or {}
        return LLMResult(
            content=message.get("content") or "",
            model=data.get("model", model),
            tool_calls=tool_calls,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd=float(hidden.get("response_cost") or 0.0),
            raw=data,
        )


_gateway: LiteLLMGateway | None = None


def get_llm_gateway() -> LiteLLMGateway:
    """FastAPI dependency returning a process-wide gateway instance."""
    global _gateway
    if _gateway is None:
        _gateway = LiteLLMGateway()
    return _gateway
