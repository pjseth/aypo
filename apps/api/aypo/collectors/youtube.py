"""YouTube collection via the official Data API (brief §13).

Uses the official API rather than a managed collector because one exists and
brief §5 prefers official APIs where suitable.

Short-form is preferred where appropriate, but nothing here depends on a
"Shorts" classification: the API exposes no reliable Shorts flag, and inferring
one from duration alone is wrong often enough that brief §13 explicitly forbids
depending on it. Duration is recorded and left for downstream filtering.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from aypo.collectors.base import CollectionResult, Collector, SearchFilters
from aypo.config import get_settings
from aypo.models.content import ContentItem, Creator, Engagement, MediaKind
from aypo.models.provenance import CollectionMethod, Platform, Provenance

log = structlog.get_logger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"

# ISO-8601 duration as returned by contentDetails.duration (e.g. PT1M30S).
_DURATION_RE = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_iso_duration(value: str | None) -> float | None:
    if not value:
        return None
    m = _DURATION_RE.fullmatch(value)
    if not m:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeCollector(Collector[ContentItem]):
    platform = Platform.YOUTUBE
    collection_method = CollectionMethod.OFFICIAL_API
    provider_name = "youtube_data_api"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._key = api_key or settings.secret("youtube_api_key")
        self._region = settings.youtube_region_code
        self._language = settings.youtube_relevance_language

    @property
    def available(self) -> bool:
        return bool(self._key)

    def missing_credentials(self) -> list[str]:
        return [] if self.available else ["YOUTUBE_API_KEY"]

    async def search(self, query: str, filters: SearchFilters) -> CollectionResult[ContentItem]:
        if not self.available:
            return self.unavailable(query)

        params: dict[str, Any] = {
            "key": self._key,
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(filters.limit, 50),
            "regionCode": filters.country or self._region,
            "relevanceLanguage": filters.language or self._language,
            # Relevance, not date. Ordering by date would skew the corpus
            # toward recent uploads and manufacture apparent growth downstream.
            "order": "relevance",
        }
        if filters.published_after:
            params["publishedAfter"] = filters.published_after

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                found = await client.get(f"{API_BASE}/search", params=params)
                if found.status_code >= 400:
                    return self._api_error(query, found)
                ids = [
                    item["id"]["videoId"]
                    for item in found.json().get("items", [])
                    if (item.get("id") or {}).get("videoId")
                ]
                if not ids:
                    return CollectionResult(query=query, raw_count=0)

                # A second call is required: search results carry no statistics
                # or duration, and a corpus without engagement is not analysable.
                detail = await client.get(
                    f"{API_BASE}/videos",
                    params={
                        "key": self._key,
                        "part": "snippet,statistics,contentDetails",
                        "id": ",".join(ids),
                        "maxResults": 50,
                    },
                )
                if detail.status_code >= 400:
                    return self._api_error(query, detail)
                raw_items = detail.json().get("items", [])
        except httpx.HTTPError as exc:
            log.warning("youtube.request_failed", query=query, error=str(exc))
            return CollectionResult(query=query, error=f"request failed: {exc}")

        return CollectionResult(
            items=self.normalize_many(raw_items, query, filters),
            query=query,
            raw_count=len(raw_items),
            units_consumed=len(ids) + 100,  # search costs ~100 quota units, videos ~1 each
        )

    def _api_error(self, query: str, response: httpx.Response) -> CollectionResult[ContentItem]:
        reason = ""
        try:
            reason = (response.json().get("error") or {}).get("message", "")
        except Exception:
            reason = response.text[:200]
        # Quota exhaustion is the common case and is not a bug; the run
        # continues on other sources (brief §7).
        log.warning("youtube.api_error", status=response.status_code, reason=reason[:160])
        return CollectionResult(
            query=query, error=f"youtube api {response.status_code}: {reason[:200]}"
        )

    def normalize(
        self, raw_item: dict[str, Any], query: str, filters: SearchFilters
    ) -> ContentItem | None:
        video_id = raw_item.get("id")
        snippet = raw_item.get("snippet") or {}
        if not video_id or not snippet:
            return None

        stats = raw_item.get("statistics") or {}
        details = raw_item.get("contentDetails") or {}
        thumbnails = snippet.get("thumbnails") or {}
        thumb = (
            thumbnails.get("maxres")
            or thumbnails.get("standard")
            or thumbnails.get("high")
            or thumbnails.get("default")
            or {}
        )

        published = None
        if raw := snippet.get("publishedAt"):
            try:
                published = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                published = None

        return ContentItem(
            platform_id=str(video_id),
            platform=Platform.YOUTUBE,
            url=f"https://www.youtube.com/watch?v={video_id}",
            media_kind=MediaKind.VIDEO,
            caption=snippet.get("title"),
            hashtags=[t for t in (snippet.get("tags") or []) if isinstance(t, str)],
            language_hint=snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
            creator=Creator(
                platform=Platform.YOUTUBE,
                handle=snippet.get("channelTitle") or "",
                display_name=snippet.get("channelTitle"),
                platform_user_id=snippet.get("channelId"),
                profile_url=(
                    f"https://www.youtube.com/channel/{snippet['channelId']}"
                    if snippet.get("channelId")
                    else None
                ),
            )
            if snippet.get("channelTitle")
            else None,
            published_at=published,
            duration_seconds=parse_iso_duration(details.get("duration")),
            thumbnail_url=thumb.get("url"),
            engagement=Engagement(
                views=_as_int(stats.get("viewCount")),
                likes=_as_int(stats.get("likeCount")),
                comments=_as_int(stats.get("commentCount")),
                collected_at=datetime.now(UTC),
            ),
            provenance=Provenance(
                platform=Platform.YOUTUBE,
                source_url=f"https://www.youtube.com/watch?v={video_id}",
                discovery_query=query,
                sampling_strategy=filters.sampling_strategy,
                collection_provider=self.provider_name,
                collection_method=self.collection_method,
                discovered_at=datetime.now(UTC),
                research_run_id=filters.research_run_id,
                research_query_id=filters.research_query_id,
                hypothesis_id=filters.hypothesis_id,
                extras={
                    # The API exposes no reliable Shorts flag. Duration is
                    # recorded as a hint only; nothing may branch on it as if
                    # it were a platform classification (brief §13).
                    "description": (snippet.get("description") or "")[:2000],
                    "category_id": snippet.get("categoryId"),
                },
            ),
            raw=raw_item,
        )
