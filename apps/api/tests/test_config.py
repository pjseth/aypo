"""Capability detection decides which sources a research run can use at all."""

import pytest

from aypo.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        anthropic_api_key=None,
        openai_api_key=None,
        jina_api_key=None,
        apify_token=None,
        youtube_api_key=None,
        serper_api_key=None,
        aypo_storage_backend="local",
    )
    base.update(overrides)
    return Settings(**base)


def test_absent_credentials_report_unavailable_rather_than_raising():
    caps = _settings().capabilities()
    assert caps["tiktok"] is False
    assert caps["youtube"] is False
    assert caps["llm"] is False
    # Local storage needs no credential, so it stays usable.
    assert caps["storage"] is True


def test_one_apify_token_enables_all_apify_backed_sources():
    caps = _settings(apify_token="tok").capabilities()
    for source in ("tiktok", "instagram", "facebook", "shopee", "lazada"):
        assert caps[source] is True
    # ...but not the sources that use their own API.
    assert caps["youtube"] is False


def test_missing_for_names_the_env_vars_to_set():
    settings = _settings()
    assert settings.missing_for("tiktok") == ["APIFY_TOKEN"]
    assert settings.missing_for("youtube") == ["YOUTUBE_API_KEY"]
    assert settings.missing_for("llm") == ["ANTHROPIC_API_KEY"]


def test_missing_for_is_empty_once_capability_is_available():
    assert _settings(apify_token="tok").missing_for("shopee") == []


def test_embeddings_fall_back_to_jina_when_openai_absent():
    assert _settings(jina_api_key="k").capabilities()["embeddings"] is True


def test_competitor_seeds_parse_to_a_list():
    # Seeds are an optional hint (brief §23); they must never be required.
    assert _settings().competitor_seeds == []
    s = _settings(aypo_competitor_seeds="Brand A, Brand B ,, Brand C")
    assert s.competitor_seeds == ["Brand A", "Brand B", "Brand C"]


def test_secret_returns_plaintext_or_none():
    assert _settings().secret("anthropic_api_key") is None
    assert _settings(anthropic_api_key="sk-test").secret("anthropic_api_key") == "sk-test"


@pytest.mark.parametrize("capability", ["llm", "embeddings", "tiktok", "youtube", "websearch"])
def test_every_capability_has_a_documented_remedy(capability):
    assert _settings().missing_for(capability), f"{capability} must name its env vars"
