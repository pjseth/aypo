"""Token pricing, used to enforce the per-run LLM budget (brief §26).

Rates are USD per million tokens. Cache reads bill at a fraction of the input
rate and cache writes at a premium; both are modelled so the budget reflects
what is actually spent rather than a raw token count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# USD per 1M tokens: (input, output)
_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5-1": (10.00, 50.00),
}
_FALLBACK_RATE = (5.00, 25.00)

_CACHE_READ_MULTIPLIER = 0.10
_CACHE_WRITE_MULTIPLIER = 1.25

# USD per 1M tokens for the non-Anthropic models in the pipeline.
_EMBEDDING_RATES: dict[str, float] = {
    "text-embedding-3-large": 0.13,
    "text-embedding-3-small": 0.02,
}
# USD per minute of audio.
_TRANSCRIPTION_RATES: dict[str, float] = {"whisper-1": 0.006}


def rates_for(model: str) -> tuple[float, float]:
    if model in _RATES:
        return _RATES[model]
    # Unknown or newly released id: price it at the Opus tier so the budget errs
    # toward stopping early rather than overspending silently.
    for known, rate in _RATES.items():
        if model.startswith(known):
            return rate
    return _FALLBACK_RATE


def message_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    rate_in, rate_out = rates_for(model)
    return (
        input_tokens * rate_in
        + output_tokens * rate_out
        + cache_read_tokens * rate_in * _CACHE_READ_MULTIPLIER
        + cache_write_tokens * rate_in * _CACHE_WRITE_MULTIPLIER
    ) / 1_000_000


def embedding_cost_usd(model: str, tokens: int) -> float:
    return tokens * _EMBEDDING_RATES.get(model, 0.13) / 1_000_000


def transcription_cost_usd(model: str, audio_seconds: float) -> float:
    return (audio_seconds / 60.0) * _TRANSCRIPTION_RATES.get(model, 0.006)


@dataclass
class Spend:
    """One ledger line, attributable to a research run and iteration."""

    kind: str  # "llm" | "embedding" | "transcription" | "collection"
    model: str
    usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    units: int = 0
    label: str = ""


@dataclass
class Ledger:
    """Accumulates spend so the research loop can pace itself and stop cleanly.

    Not thread-safe by design: one ledger per research run, mutated from the
    run's own task group.
    """

    lines: list[Spend] = field(default_factory=list)

    def record(self, spend: Spend) -> Spend:
        self.lines.append(spend)
        return spend

    @property
    def total_usd(self) -> float:
        return sum(line.usd for line in self.lines)

    def usd_by_kind(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for line in self.lines:
            out[line.kind] = out.get(line.kind, 0.0) + line.usd
        return out

    @property
    def collection_units(self) -> int:
        return sum(line.units for line in self.lines if line.kind == "collection")

    def summary(self) -> dict[str, object]:
        return {
            "total_usd": round(self.total_usd, 4),
            "by_kind": {k: round(v, 4) for k, v in self.usd_by_kind().items()},
            "collection_units": self.collection_units,
            "llm_calls": sum(1 for line in self.lines if line.kind == "llm"),
            "input_tokens": sum(line.input_tokens for line in self.lines),
            "output_tokens": sum(line.output_tokens for line in self.lines),
            "cache_read_tokens": sum(line.cache_read_tokens for line in self.lines),
            "cache_write_tokens": sum(line.cache_write_tokens for line in self.lines),
        }
