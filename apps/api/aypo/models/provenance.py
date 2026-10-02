"""Provenance (brief §7).

Every item carries where it came from and why it was collected. This is not
bookkeeping: brief §9 requires the research process be auditable, and brief §27
requires a marketer to be able to click from a finding down to the content
behind it. Without per-item provenance neither is possible.

Discovery and retrieval are separate concepts and are recorded separately --
finding a URL and obtaining its media can succeed and fail independently.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class Platform(StrEnum):
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    WEB = "web"
    SHOPEE = "shopee"
    LAZADA = "lazada"


class SamplingStrategy(StrEnum):
    """Which query family produced an item (brief §8).

    Tracked per item so the corpus composition can be inspected and so the loop
    can keep the sample deliberately mixed rather than letting it collapse into
    mostly brand-owned content.
    """

    BRAND = "brand"
    CATEGORY = "category"
    COMPETITOR = "competitor"
    THEME = "theme"
    HYPOTHESIS = "hypothesis"
    SEED = "seed"


class CollectionMethod(StrEnum):
    OFFICIAL_API = "official_api"
    MANAGED_COLLECTOR = "managed_collector"
    WEB_SEARCH = "web_search"


class Provenance(BaseModel):
    """Where an item came from and why it is in the corpus."""

    platform: Platform
    source_url: str
    discovery_query: str
    sampling_strategy: SamplingStrategy
    collection_provider: str
    collection_method: CollectionMethod
    discovered_at: datetime
    research_run_id: UUID | None = None
    research_query_id: UUID | None = None
    hypothesis_id: UUID | None = None
    # Why this item was judged relevant enough to retain, shown in the UI
    # (brief §31.5 "reason relevant").
    relevance_reason: str | None = None
    actor_run_id: str | None = None
    extras: dict = Field(default_factory=dict)
