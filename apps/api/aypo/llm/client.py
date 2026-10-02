"""The only module that talks to the Anthropic SDK.

Everything else in the codebase goes through LLMClient, so model ids, thinking
configuration, refusal handling, cost accounting and retry policy live in exactly
one place.

Design notes
------------
* Structured extraction uses ``client.messages.parse(output_format=Model)``, which
  generates a JSON-schema output format from the Pydantic model and validates the
  response. The SDK merges a caller-supplied ``output_config`` with the generated
  format, so effort control and structured output compose.
* ``parse`` lives on the non-beta namespace and therefore cannot take the
  server-side ``fallbacks`` parameter. Refusals are handled client-side instead:
  on ``stop_reason == "refusal"`` the same request is retried once on a fallback
  model, so one declined item cannot kill a long research run.
* Thinking is adaptive on Opus 5 and is never disabled: disabling it is rejected
  above effort "high" and is known to make the model emit tool calls as visible
  text. Depth is controlled with ``output_config.effort`` instead.
"""

from __future__ import annotations

import asyncio
import base64
import random
from dataclasses import dataclass
from typing import Any, TypeVar

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

from aypo.config import Effort, get_settings
from aypo.llm.pricing import Ledger, Spend, message_cost_usd

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)


class LLMUnavailable(RuntimeError):
    """No Anthropic credential is configured."""


class LLMRefused(RuntimeError):
    """Both the primary and fallback model declined the request."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        super().__init__(f"model declined request (category={category}): {explanation}")
        self.category = category
        self.explanation = explanation


class LLMTruncated(RuntimeError):
    """The response hit max_tokens before completing, so it cannot be trusted."""


class LLMSchemaError(RuntimeError):
    """The model could not produce output matching the requested schema."""


@dataclass
class Result[TOut]:
    value: TOut
    model: str
    spend: Spend
    stop_reason: str | None
    used_fallback: bool


def image_block(data: bytes, media_type: str = "image/jpeg") -> dict[str, Any]:
    """A base64 image content block, for frames handed to the vision model."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class LLMClient:
    """Async Anthropic wrapper with budget accounting and graceful failure."""

    def __init__(
        self,
        *,
        ledger: Ledger | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        max_attempts: int = 4,
    ) -> None:
        settings = get_settings()
        key = settings.secret("anthropic_api_key")
        if not key:
            raise LLMUnavailable(
                "ANTHROPIC_API_KEY is not set; the research engine cannot analyse or reason"
            )
        self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=0)
        self.model = model or settings.aypo_llm_model
        self.fallback_model = fallback_model or settings.aypo_llm_fallback_model
        self.ledger = ledger if ledger is not None else Ledger()
        self._max_attempts = max_attempts

    # ── public API ───────────────────────────────────────────────────────────

    async def structured(
        self,
        output_model: type[T],
        *,
        system: str,
        content: list[dict[str, Any]] | str,
        effort: Effort = "high",
        max_tokens: int = 8000,
        cache_system: bool = True,
        label: str = "",
        model: str | None = None,
    ) -> Result[T]:
        """Extract a validated Pydantic object.

        ``cache_system`` marks the system prompt as a cache breakpoint. The
        per-item analysis prompt is large and identical across thousands of
        items, so caching it is the single biggest cost lever in the pipeline;
        it only pays off while the prompt text stays byte-identical.
        """
        response = await self._call(
            system=system,
            content=content,
            effort=effort,
            max_tokens=max_tokens,
            cache_system=cache_system,
            label=label or output_model.__name__,
            model=model,
            output_model=output_model,
        )
        parsed = getattr(response.value, "parsed_output", None)
        if parsed is None:
            raise LLMSchemaError(
                f"{output_model.__name__}: model returned no parseable structured output"
            )
        return Result(
            value=parsed,
            model=response.model,
            spend=response.spend,
            stop_reason=response.stop_reason,
            used_fallback=response.used_fallback,
        )

    async def text(
        self,
        *,
        system: str,
        content: list[dict[str, Any]] | str,
        effort: Effort = "high",
        max_tokens: int = 8000,
        cache_system: bool = True,
        label: str = "text",
        model: str | None = None,
    ) -> Result[str]:
        """Free-form prose. Used for narrative, never for numbers."""
        response = await self._call(
            system=system,
            content=content,
            effort=effort,
            max_tokens=max_tokens,
            cache_system=cache_system,
            label=label,
            model=model,
            output_model=None,
        )
        body = "".join(
            block.text for block in response.value.content if getattr(block, "type", "") == "text"
        )
        return Result(
            value=body,
            model=response.model,
            spend=response.spend,
            stop_reason=response.stop_reason,
            used_fallback=response.used_fallback,
        )

    # ── internals ────────────────────────────────────────────────────────────

    async def _call(
        self,
        *,
        system: str,
        content: list[dict[str, Any]] | str,
        effort: Effort,
        max_tokens: int,
        cache_system: bool,
        label: str,
        model: str | None,
        output_model: type[BaseModel] | None,
    ) -> Result[Any]:
        primary = model or self.model
        for candidate, is_fallback in ((primary, False), (self.fallback_model, True)):
            try:
                response = await self._attempt(
                    model=candidate,
                    system=system,
                    content=content,
                    effort=effort,
                    max_tokens=max_tokens,
                    cache_system=cache_system,
                    output_model=output_model,
                )
            except _Refusal as refusal:
                if is_fallback or (candidate == primary and self.fallback_model == primary):
                    raise LLMRefused(refusal.category, refusal.explanation) from None
                log.warning(
                    "llm.refusal.falling_back",
                    label=label,
                    category=refusal.category,
                    from_model=candidate,
                    to_model=self.fallback_model,
                )
                continue

            usage = response.usage
            spend = self.ledger.record(
                Spend(
                    kind="llm",
                    model=candidate,
                    usd=message_cost_usd(
                        candidate,
                        input_tokens=usage.input_tokens or 0,
                        output_tokens=usage.output_tokens or 0,
                        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    ),
                    input_tokens=usage.input_tokens or 0,
                    output_tokens=usage.output_tokens or 0,
                    cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    label=label,
                )
            )
            if response.stop_reason == "max_tokens":
                raise LLMTruncated(
                    f"{label}: response truncated at max_tokens={max_tokens}; "
                    "raise max_tokens or reduce the request"
                )
            return Result(
                value=response,
                model=candidate,
                spend=spend,
                stop_reason=response.stop_reason,
                used_fallback=is_fallback,
            )
        raise LLMRefused(None, "exhausted primary and fallback models")

    async def _attempt(
        self,
        *,
        model: str,
        system: str,
        content: list[dict[str, Any]] | str,
        effort: Effort,
        max_tokens: int,
        cache_system: bool,
        output_model: type[BaseModel] | None,
    ) -> Any:
        system_param: Any = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if cache_system
            else system
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_param,
            "messages": [{"role": "user", "content": content}],
            # Adaptive thinking; depth governed by effort. Never disabled.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }

        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                if output_model is not None:
                    response = await self._client.messages.parse(
                        output_format=output_model, **kwargs
                    )
                else:
                    response = await self._client.messages.create(**kwargs)
            except _RETRYABLE as exc:
                last_error = exc
                await self._backoff(attempt)
                continue
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500 or exc.status_code == 408:
                    last_error = exc
                    await self._backoff(attempt)
                    continue
                raise
            except ValidationError as exc:
                # The model produced JSON that does not satisfy the schema. One
                # retry is worthwhile; beyond that the schema or prompt is wrong
                # and silently returning partial data would corrupt the dataset.
                last_error = exc
                if attempt >= 1:
                    raise LLMSchemaError(
                        f"{output_model.__name__ if output_model else 'text'}: "
                        f"schema validation failed twice: {exc}"
                    ) from exc
                await self._backoff(attempt)
                continue

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise _Refusal(
                    getattr(details, "category", None), getattr(details, "explanation", None)
                )
            return response

        raise RuntimeError(f"LLM call failed after {self._max_attempts} attempts") from last_error

    @staticmethod
    async def _backoff(attempt: int) -> None:
        # Full jitter: avoids a thundering herd when many analysis workers are
        # rate-limited by the same bucket at the same moment.
        await asyncio.sleep(random.uniform(0, min(2**attempt, 16)))


class _Refusal(Exception):
    def __init__(self, category: str | None, explanation: str | None) -> None:
        super().__init__(category or "refusal")
        self.category = category
        self.explanation = explanation
