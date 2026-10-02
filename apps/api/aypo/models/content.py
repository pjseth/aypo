"""Normalised social/video content (brief §10, §12-14).

Downstream analysis sees only these objects -- never a provider's response
shape. That is what makes the collection layer replaceable (brief §5): swapping
a managed collector for a licensed feed changes a collector, not analytics.

Every engagement field is optional. Platforms expose different metrics, actors
change their output without notice, and a missing field must normalise safely
rather than break analysis (brief §15, §16).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from aypo.models.provenance import Platform, Provenance


class MediaKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    TEXT = "text"


class Creator(BaseModel):
    """A channel/account. Deliberately free of demographic inference: brief §28
    forbids invented demographics, so only platform-stated fields are stored."""

    platform: Platform
    handle: str
    display_name: str | None = None
    platform_user_id: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    content_count: int | None = None
    verified: bool | None = None
    bio: str | None = None
    profile_url: str | None = None
    avatar_url: str | None = None


class Engagement(BaseModel):
    """Platform-reported counts.

    These measure attention, not preference. Brief §28 forbids equating
    engagement with consumer preference, so nothing downstream may read a high
    view count as approval.
    """

    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    collected_at: datetime | None = None


class SoundTrack(BaseModel):
    """Audio a creator attached to a post. A shared sound is a strong signal of
    a format spreading, so it is retained as a first-class discovery handle."""

    id: str | None = None
    title: str | None = None
    author: str | None = None
    original: bool | None = None
    url: str | None = None


class Comment(BaseModel):
    id: str | None = None
    text: str
    author_handle: str | None = None
    like_count: int | None = None
    published_at: datetime | None = None


class ContentItem(BaseModel):
    """One collected social/video item, normalised across platforms."""

    platform_id: str
    platform: Platform
    url: str
    media_kind: MediaKind = MediaKind.VIDEO

    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    language_hint: str | None = None

    creator: Creator | None = None
    published_at: datetime | None = None
    duration_seconds: float | None = None

    media_url: str | None = None
    thumbnail_url: str | None = None

    engagement: Engagement = Field(default_factory=Engagement)
    sound: SoundTrack | None = None
    comments: list[Comment] = Field(default_factory=list)

    # Only location the platform explicitly states. Brief §18 restricts this to
    # explicit data; inferring location from language or content would be an
    # invented demographic.
    stated_location: str | None = None

    provenance: Provenance
    raw: dict = Field(default_factory=dict, repr=False)

    @property
    def dedupe_key(self) -> str:
        """Stable identity across runs and across discovery paths.

        The same video is routinely found by several queries and sometimes by
        two different collectors; without this the corpus double-counts it and
        every per-item statistic inflates.
        """
        return f"{self.platform.value}:{self.platform_id}"

    @property
    def has_retrievable_media(self) -> bool:
        return bool(self.media_url or self.thumbnail_url)


class DiscoveredItem(BaseModel):
    """A discovery-stage hit: a URL believed relevant, not yet retrieved.

    Kept distinct from ContentItem because brief §7 separates discovery from
    retrieval, and conflating them hides the case where discovery succeeded but
    the media could never be obtained.
    """

    url: str
    platform: Platform
    title: str | None = None
    snippet: str | None = None
    discovery_query: str
    position: int | None = None
    published_at: datetime | None = None
