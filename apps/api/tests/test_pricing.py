"""Budget accounting must be exact: the research loop stops on these numbers."""

from aypo.llm.pricing import (
    Ledger,
    Spend,
    embedding_cost_usd,
    message_cost_usd,
    rates_for,
    transcription_cost_usd,
)


def test_known_model_rates():
    assert rates_for("claude-opus-5") == (5.00, 25.00)
    assert rates_for("claude-sonnet-5") == (2.00, 10.00)


def test_unknown_model_prices_at_opus_tier():
    # Erring high makes the budget stop early rather than overspend silently.
    assert rates_for("claude-does-not-exist-9") == (5.00, 25.00)


def test_message_cost_is_exact():
    cost = message_cost_usd("claude-opus-5", input_tokens=1_000_000, output_tokens=0)
    assert cost == 5.00
    cost = message_cost_usd("claude-opus-5", input_tokens=0, output_tokens=1_000_000)
    assert cost == 25.00


def test_cache_reads_are_cheaper_and_writes_dearer_than_input():
    base = message_cost_usd("claude-opus-5", input_tokens=1_000_000, output_tokens=0)
    read = message_cost_usd(
        "claude-opus-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    write = message_cost_usd(
        "claude-opus-5", input_tokens=0, output_tokens=0, cache_write_tokens=1_000_000
    )
    assert read < base < write
    assert read == base * 0.10
    assert write == base * 1.25


def test_ledger_separates_kinds_and_counts_collection_units():
    ledger = Ledger()
    ledger.record(Spend("llm", "claude-sonnet-5", 1.5, input_tokens=10, output_tokens=20))
    ledger.record(Spend("embedding", "text-embedding-3-large", 0.25, input_tokens=100))
    ledger.record(Spend("collection", "apify", 0.75, units=42))
    assert ledger.total_usd == 2.5
    assert ledger.usd_by_kind() == {"llm": 1.5, "embedding": 0.25, "collection": 0.75}
    assert ledger.collection_units == 42
    assert ledger.summary()["llm_calls"] == 1


def test_summary_reports_cache_write_tokens():
    # Omitting this made spend look inconsistent with the token counts.
    ledger = Ledger()
    ledger.record(Spend("llm", "claude-opus-5", 0.0, cache_write_tokens=520))
    assert ledger.summary()["cache_write_tokens"] == 520


def test_transcription_priced_per_minute():
    assert transcription_cost_usd("whisper-1", 60.0) == 0.006


def test_embedding_cost_scales_with_tokens():
    assert embedding_cost_usd("text-embedding-3-large", 1_000_000) == 0.13
