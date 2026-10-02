"""Runtime configuration.

Every credential and tunable is read from the environment. Nothing is hard-coded
(brief §11). Providers that lack credentials report themselves unavailable rather
than raising, so a research run degrades gracefully instead of halting (brief §7).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env first so a single file configures web + api; a local
        # apps/api/.env may override it for backend-only work.
        env_file=(_REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── runtime ──────────────────────────────────────────────────────────────
    aypo_env: Literal["development", "staging", "production"] = "development"
    aypo_log_level: str = "INFO"
    aypo_api_port: int = 8000
    aypo_worker_concurrency: int = 4

    # ── LLM ──────────────────────────────────────────────────────────────────
    anthropic_api_key: SecretStr | None = None
    # Reasoning, planning, finding generation and Ask-the-Data.
    aypo_llm_model: str = "claude-opus-5"
    # The high-volume per-item extraction pass runs over ~1500 videos, where the
    # Opus rate dominates the run budget. Operator-selected tier for that stage
    # only; every judgement-heavy stage stays on aypo_llm_model.
    aypo_analysis_model: str = "claude-sonnet-5"
    # Used when the primary model declines a request (stop_reason="refusal") so a
    # long research run is not killed by a single refusal.
    aypo_llm_fallback_model: str = "claude-opus-4-8"
    # The per-video analysis pass runs over ~1500 items, so its effort is tuned
    # separately from the low-volume reasoning passes.
    aypo_analysis_effort: Effort = "medium"
    aypo_reasoning_effort: Effort = "high"

    # ── embeddings ───────────────────────────────────────────────────────────
    openai_api_key: SecretStr | None = None
    aypo_embedding_provider: Literal["openai", "jina"] = "openai"
    aypo_embedding_model: str = "text-embedding-3-large"
    # 1536 keeps vectors inside pgvector's 2000-dim HNSW ceiling.
    aypo_embedding_dimensions: int = 1536

    # ── transcription ────────────────────────────────────────────────────────
    aypo_transcription_provider: Literal["openai"] = "openai"
    aypo_transcription_model: str = "whisper-1"

    # ── alternate providers ──────────────────────────────────────────────────
    gemini_api_key: SecretStr | None = None
    jina_api_key: SecretStr | None = None

    # ── Apify managed collection ─────────────────────────────────────────────
    apify_token: SecretStr | None = None
    apify_tiktok_actor: str = "clockworks~tiktok-scraper"
    apify_instagram_actor: str = "apify~instagram-scraper"
    apify_facebook_actor: str = "apify~facebook-posts-scraper"
    # Verified to exist and to support country PH. Note the case difference:
    # the Shopee actor's country enum is uppercase ("PH"), the Lazada actor's
    # is lowercase ("ph"). Collectors must not share one constant.
    apify_shopee_actor: str = "gio21~shopee-scraper"
    apify_shopee_country: str = "PH"
    # No single Shopee actor does both keyword search and review text, so
    # review retrieval is a second actor applied to selected products
    # (the discovery/retrieval split in brief §7).
    apify_shopee_reviews_actor: str = "zen-studio~shopee-product-reviews-scraper"
    apify_lazada_actor: str = "fatihtahta~lazada-scraper"
    apify_lazada_country: str = "ph"
    # A search actor that has not finished in five minutes is stuck, and a
    # stuck run holds a concurrency slot for the whole timeout -- which
    # starves every other platform queued behind it. Measured: a hung TikTok
    # run blocked Lazada collection entirely for 15 minutes.
    apify_default_timeout_secs: int = 300
    apify_max_concurrent_runs: int = 4

    # ── official APIs ────────────────────────────────────────────────────────
    youtube_api_key: SecretStr | None = None
    youtube_region_code: str = "PH"
    youtube_relevance_language: str = "en"

    # ── web search discovery ─────────────────────────────────────────────────
    serper_api_key: SecretStr | None = None
    aypo_websearch_provider: Literal["serper"] = "serper"

    # ── database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql://postgres:postgres@localhost:5432/aypo"
    next_public_supabase_url: str | None = None
    next_public_supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None
    aypo_db_pool_min: int = 2
    aypo_db_pool_max: int = 10

    # ── storage ──────────────────────────────────────────────────────────────
    aypo_storage_backend: Literal["s3", "local"] = "local"
    s3_bucket: str | None = None
    aws_region: str | None = None
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    aypo_local_storage_root: Path = Path(".media")

    # ── research budget (brief §26) ───────────────────────────────────────────
    aypo_max_collection_units: int = 4000
    aypo_max_llm_usd: float = 120.0
    aypo_max_wallclock_minutes: int = 180
    aypo_max_iterations: int = 8

    # ── media processing ─────────────────────────────────────────────────────
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    aypo_max_video_download_mb: int = 120

    # ── optional competitor seeds (brief §23: a hint, never a limit) ─────────
    aypo_competitor_seeds: str = Field(
        default="",
        description=(
            "Optional comma-separated brand names supplied by the operator. Used only "
            "to widen initial discovery; must never prevent or replace discovery of "
            "other competitors, and seeded brands are flagged as such in the UI."
        ),
    )

    # ── derived helpers ──────────────────────────────────────────────────────
    @property
    def repo_root(self) -> Path:
        return _REPO_ROOT

    @property
    def competitor_seeds(self) -> list[str]:
        return [s.strip() for s in self.aypo_competitor_seeds.split(",") if s.strip()]

    def secret(self, name: str) -> str | None:
        """Read a SecretStr field's plaintext, or None when unset/blank."""
        value = getattr(self, name, None)
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return raw or None

    def capabilities(self) -> dict[str, bool]:
        """Which subsystems are usable with the credentials actually present.

        The research loop reads this to plan around unavailable sources instead of
        discovering the gap mid-run.
        """
        apify = self.secret("apify_token") is not None
        return {
            "llm": self.secret("anthropic_api_key") is not None,
            # Either key suffices: get_embedding_provider() falls back across
            # providers regardless of which one is configured, so this must not
            # under-report availability or the research loop would skip
            # clustering it could actually have run.
            "embeddings": self.secret("openai_api_key") is not None
            or self.secret("jina_api_key") is not None,
            "transcription": self.secret("openai_api_key") is not None,
            "tiktok": apify,
            "instagram": apify,
            "facebook": apify,
            "shopee": apify,
            "lazada": apify,
            "youtube": self.secret("youtube_api_key") is not None,
            "websearch": self.secret("serper_api_key") is not None,
            "storage": self.aypo_storage_backend == "local"
            or (self.s3_bucket is not None and self.secret("aws_access_key_id") is not None),
        }

    def missing_for(self, capability: str) -> list[str]:
        """Env var names an operator must set to switch a capability on."""
        required = {
            "llm": ["ANTHROPIC_API_KEY"],
            "embeddings": ["OPENAI_API_KEY (or JINA_API_KEY)"],
            "transcription": ["OPENAI_API_KEY"],
            "tiktok": ["APIFY_TOKEN"],
            "instagram": ["APIFY_TOKEN"],
            "facebook": ["APIFY_TOKEN"],
            "shopee": ["APIFY_TOKEN"],
            "lazada": ["APIFY_TOKEN"],
            "youtube": ["YOUTUBE_API_KEY"],
            "websearch": ["SERPER_API_KEY"],
            "storage": ["S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        }
        return [] if self.capabilities().get(capability) else required.get(capability, [])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
