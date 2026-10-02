"""Test configuration.

Tests must be hermetic: provider behaviour must not depend on which credentials
happen to be present in the developer's .env, or a suite that passes locally
fails in CI (and vice versa).
"""

import pytest

from aypo import config


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch):
    """Run every test against a credential-free configuration by default.

    Tests that need a credential pass it explicitly to the provider under test.
    """
    settings = config.Settings(
        _env_file=None,
        anthropic_api_key=None,
        openai_api_key=None,
        jina_api_key=None,
        gemini_api_key=None,
        apify_token=None,
        youtube_api_key=None,
        serper_api_key=None,
        aypo_storage_backend="local",
    )
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    # Modules that imported the symbol directly still need to see the override.
    for module in (
        "aypo.providers.apify",
        "aypo.providers.websearch",
        "aypo.providers.storage",
        "aypo.providers.embeddings",
        "aypo.providers.transcription",
        "aypo.llm.client",
        "aypo.db.pool",
    ):
        try:
            mod = __import__(module, fromlist=["get_settings"])
        except Exception:
            continue
        if hasattr(mod, "get_settings"):
            monkeypatch.setattr(mod, "get_settings", lambda: settings, raising=False)
    return settings
