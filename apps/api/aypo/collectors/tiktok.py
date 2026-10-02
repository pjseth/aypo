"""TikTok collection (brief §12).

Discovery runs through a managed Apify actor; media retrieval is separate
(see aypo.analysis.retrieval). Field mapping is written against the actor's
real payload, captured from a live run, not from documentation.

Search begins broadly and the research agent generates new searches from what
it discovers. Nothing here encodes what those patterns are (brief §12).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from aypo.collectors.base import CollectionResult, Collector, SearchFilters
from aypo.config import get_settings
from aypo.models.content import (
    Comment,
    ContentItem,
    Creator,
    Engagement,
    MediaKind,
    SoundTrack,
)
from aypo.models.provenance import CollectionMethod, Platform, Provenance
from aypo.providers.apify import ActorFailed, ApifyProvider, ProviderUnavailable

log = structlog.get_logger(__name__)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TikTokCollector(Collector[ContentItem]):
    platform = Platform.TIKTOK
    collection_method = CollectionMethod.MANAGED_COLLECTOR
    provider_name = "apify"

    def __init__(self, provider: ApifyProvider | None = None) -> None:
        self._provider = provider or ApifyProvider()
        self._settings = get_settings()
        # Related search terms surfaced by the actor, accumulated across
        # searches. These are organically discovered query candidates, which is
        # how brief §8D theme-level expansion gets material without any theme
        # being hardcoded.
        self.discovered_search_terms: set[str] = set()

    @property
    def available(self) -> bool:
        return self._provider.available

    def missing_credentials(self) -> list[str]:
        return [] if self.available else ["APIFY_TOKEN"]

    async def search(self, query: str, filters: SearchFilters) -> CollectionResult[ContentItem]:
        if not self.available:
            return self.unavailable(query)

        payload: dict[str, Any] = {
            "resultsPerPage": filters.limit,
            "shouldDownloadCovers": True,
            # Organic query expansion; see discovered_search_terms.
            "scrapeRelatedSearchWords": True,
            "proxyCountryCode": filters.country,
            # Video bytes come from our own retrieval step instead of this
            # actor's paid download add-on.
            "shouldDownloadVideos": False,
            # ALL_TIME on purpose. Filtering collection to recent content would
            # skew the publication-date distribution and manufacture apparent
            # growth for every theme downstream.
            "videoSearchDateFilter": "ALL_TIME",
        }

        # A hashtag or creator handle is a different discovery handle than a
        # keyword, and the actor takes them on different inputs.
        if query.startswith("#"):
            payload["hashtags"] = [query.lstrip("#")]
        elif query.startswith("@"):
            payload["profiles"] = [query.lstrip("@")]
            payload["profileScrapeSections"] = ["videos"]
        else:
            payload["searchQueries"] = [query]
            payload["searchSection"] = "/video"

        if comments := filters.extras.get("comments_per_post"):
            payload["topLevelCommentsPerPost"] = int(comments)

        try:
            run = await self._provider.run_actor(
                self._settings.apify_tiktok_actor, payload, max_items=filters.limit
            )
        except ProviderUnavailable:
            return self.unavailable(query)
        except ActorFailed as exc:
            # A dead actor must not abort the research run (brief §7).
            log.warning("tiktok.actor_failed", query=query, status=exc.status)
            return CollectionResult(
                query=query, available=True, error=f"actor failed: {exc.status}"
            )

        items = self.normalize_many(run.items, query, filters)
        for raw in run.items:
            for term in raw.get("relatedSearchWords") or []:
                if isinstance(term, str) and term.strip():
                    self.discovered_search_terms.add(term.strip())

        return CollectionResult(
            items=items,
            query=query,
            raw_count=len(run.items),
            units_consumed=max(1, round(run.compute_units * 1000)),
        )

    def normalize(
        self, raw_item: dict[str, Any], query: str, filters: SearchFilters
    ) -> ContentItem | None:
        platform_id = str(raw_item.get("id") or "").strip()
        url = raw_item.get("webVideoUrl") or ""
        if not platform_id or not url:
            return None

        author = raw_item.get("authorMeta") or {}
        music = raw_item.get("musicMeta") or {}
        video = raw_item.get("videoMeta") or {}

        creator = (
            Creator(
                platform=Platform.TIKTOK,
                handle=str(author.get("name") or ""),
                display_name=author.get("nickName"),
                platform_user_id=str(author.get("id")) if author.get("id") else None,
                follower_count=_as_int(author.get("fans")),
                following_count=_as_int(author.get("following")),
                content_count=_as_int(author.get("video")),
                verified=author.get("verified"),
                bio=author.get("signature"),
                profile_url=author.get("profileUrl"),
                avatar_url=author.get("avatar"),
            )
            if author.get("name")
            else None
        )

        comments = [
            Comment(
                id=str(c.get("cid")) if c.get("cid") else None,
                text=str(c.get("text") or ""),
                author_handle=(c.get("uniqueId") or c.get("username")),
                like_count=_as_int(c.get("diggCount")),
                published_at=_parse_dt(c.get("createTimeISO") or c.get("createTime")),
            )
            for c in (raw_item.get("comments") or [])
            if isinstance(c, dict) and c.get("text")
        ]

        return ContentItem(
            platform_id=platform_id,
            platform=Platform.TIKTOK,
            url=url,
            media_kind=MediaKind.IMAGE if raw_item.get("isSlideshow") else MediaKind.VIDEO,
            caption=raw_item.get("text"),
            hashtags=[
                h["name"]
                for h in (raw_item.get("hashtags") or [])
                if isinstance(h, dict) and h.get("name")
            ],
            language_hint=raw_item.get("textLanguage"),
            creator=creator,
            published_at=_parse_dt(raw_item.get("createTimeISO") or raw_item.get("createTime")),
            duration_seconds=float(video["duration"]) if video.get("duration") else None,
            # Empty unless the paid download add-on is enabled; retrieval uses
            # the post URL instead.
            media_url=(raw_item.get("mediaUrls") or [None])[0],
            thumbnail_url=video.get("originalCoverUrl") or video.get("coverUrl"),
            engagement=Engagement(
                views=_as_int(raw_item.get("playCount")),
                likes=_as_int(raw_item.get("diggCount")),
                comments=_as_int(raw_item.get("commentCount")),
                shares=_as_int(raw_item.get("shareCount")),
                saves=_as_int(raw_item.get("collectCount")),
                collected_at=datetime.now(UTC),
            ),
            sound=(
                SoundTrack(
                    id=str(music.get("musicId")) if music.get("musicId") else None,
                    title=music.get("musicName"),
                    author=music.get("musicAuthor"),
                    original=music.get("musicOriginal"),
                    url=music.get("playUrl"),
                )
                if music
                else None
            ),
            comments=comments,
            provenance=Provenance(
                platform=Platform.TIKTOK,
                source_url=url,
                # The actor echoes the query that found the item; prefer it over
                # our own, since one run may cover several handles.
                discovery_query=str(raw_item.get("searchQuery") or query),
                sampling_strategy=filters.sampling_strategy,
                collection_provider=self.provider_name,
                collection_method=self.collection_method,
                discovered_at=datetime.now(UTC),
                research_run_id=filters.research_run_id,
                research_query_id=filters.research_query_id,
                hypothesis_id=filters.hypothesis_id,
                extras={
                    # Platform-stated, so sponsored/organic is an OBSERVED fact
                    # here rather than a model inference (brief §19, §28).
                    "is_sponsored": bool(raw_item.get("isSponsored")),
                    "is_ad": bool(raw_item.get("isAd")),
                    "sponsorship_source": "platform_declared",
                    "is_slideshow": bool(raw_item.get("isSlideshow")),
                },
            ),
            raw=raw_item,
        )
