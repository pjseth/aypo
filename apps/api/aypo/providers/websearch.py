"""Public-web discovery (brief §5, tier 3).

Two jobs:

1. Discover candidate URLs on platforms whose own collector is unavailable, so
   a run still produces *something* from a dead source rather than a hole
   (brief §7).
2. Discover category and theme context that lives outside the video platforms
   entirely -- listicles, news, forum threads, retailer pages.

Discovery only. It returns URLs and snippets; retrieval of the actual media or
marketplace record is a collector's job (brief §7 keeps these separate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx
import structlog

from aypo.config import get_settings

log = structlog.get_logger(__name__)

SERPER_ENDPOINT = "https://google.serper.dev/search"

# Site filters used to aim a generic web search at a specific platform.
PLATFORM_SITES = {
    "tiktok": "tiktok.com",
    "youtube": "youtube.com",
    "instagram": "instagram.com",
    "facebook": "facebook.com",
    "shopee": "shopee.ph",
    "lazada": "lazada.com.ph",
}


class WebSearchUnavailable(RuntimeError):
    pass


@dataclass
class DiscoveredUrl:
    url: str
    title: str
    snippet: str
    platform: str
    position: int
    discovery_query: str
    published_at: datetime | None = None
    raw: dict = field(default_factory=dict)


def classify_platform(url: str) -> str:
    lowered = url.lower()
    for platform, domain in PLATFORM_SITES.items():
        if domain in lowered:
            return platform
    return "web"


class SerperWebSearch:
    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or get_settings().secret("serper_api_key")

    @property
    def available(self) -> bool:
        return bool(self._key)

    async def search(
        self,
        query: str,
        *,
        platform: str | None = None,
        limit: int = 20,
        country: str = "ph",
        language: str = "en",
        recency: str | None = None,
    ) -> list[DiscoveredUrl]:
        """Run one discovery query.

        ``country`` and ``language`` localise results to the target market;
        ``recency`` accepts Google's tbs-style windows ("qdr:m", "qdr:y") and is
        left unset by default, because filtering to recent results would bias
        the corpus toward new content and manufacture apparent growth.
        """
        if not self._key:
            raise WebSearchUnavailable(
                "SERPER_API_KEY is not set; public-web discovery is unavailable"
            )
        q = f"site:{PLATFORM_SITES[platform]} {query}" if platform in PLATFORM_SITES else query
        payload: dict[str, object] = {"q": q, "gl": country, "hl": language, "num": min(limit, 100)}
        if recency:
            payload["tbs"] = recency

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                SERPER_ENDPOINT,
                headers={"X-API-KEY": self._key, "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code >= 400:
            log.warning("websearch.failed", status=resp.status_code, query=q)
            return []

        body = resp.json()
        out: list[DiscoveredUrl] = []
        for block in ("organic", "videos", "news"):
            for i, item in enumerate(body.get(block) or []):
                url = item.get("link") or item.get("url")
                if not url:
                    continue
                out.append(
                    DiscoveredUrl(
                        url=url,
                        title=item.get("title") or "",
                        snippet=item.get("snippet") or item.get("description") or "",
                        platform=classify_platform(url),
                        position=int(item.get("position") or i + 1),
                        discovery_query=q,
                        raw=item,
                    )
                )
        log.info("websearch.ok", query=q, results=len(out))
        return out[:limit]


def get_websearch() -> SerperWebSearch:
    return SerperWebSearch()
