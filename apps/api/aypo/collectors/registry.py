"""Collector registry.

Builds the set of collectors a run can actually use. A source without its
credential is not an error and does not raise: it is reported unavailable, the
run proceeds on what remains, and the gap is recorded so the UI can say a source
was missing rather than show a silent hole (brief §7, §28).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from aypo.collectors.base import Collector
from aypo.collectors.lazada import LazadaCollector
from aypo.collectors.shopee import ShopeeCollector
from aypo.collectors.tiktok import TikTokCollector
from aypo.collectors.youtube import YouTubeCollector
from aypo.config import get_settings
from aypo.models.provenance import Platform

log = structlog.get_logger(__name__)

SOCIAL_PLATFORMS = (Platform.TIKTOK, Platform.YOUTUBE, Platform.INSTAGRAM, Platform.FACEBOOK)
COMMERCE_PLATFORMS = (Platform.SHOPEE, Platform.LAZADA)


@dataclass
class Availability:
    platform: Platform
    available: bool
    reason: str | None = None
    missing_credentials: list[str] | None = None


def build_collectors() -> tuple[dict[Platform, Collector], list[Availability]]:
    """Every collector, with an availability verdict for each."""
    candidates: dict[Platform, Collector] = {
        Platform.TIKTOK: TikTokCollector(),
        Platform.YOUTUBE: YouTubeCollector(),
        Platform.SHOPEE: ShopeeCollector(),
        Platform.LAZADA: LazadaCollector(),
    }

    usable: dict[Platform, Collector] = {}
    availability: list[Availability] = []
    for platform, collector in candidates.items():
        if collector.available:
            usable[platform] = collector
            availability.append(Availability(platform, True))
        else:
            missing = collector.missing_credentials()
            availability.append(
                Availability(
                    platform,
                    False,
                    reason=f"missing credential: {', '.join(missing)}"
                    if missing
                    else "unavailable",
                    missing_credentials=missing,
                )
            )

    # Instagram and Facebook are explicitly non-critical (brief §14) and have no
    # collector yet; they are reported as such rather than silently absent, so
    # coverage reflects what was actually attempted.
    for platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
        availability.append(
            Availability(
                platform,
                False,
                reason="no collector implemented; brief §14 treats these as non-critical",
                missing_credentials=[] if get_settings().secret("apify_token") else ["APIFY_TOKEN"],
            )
        )

    log.info(
        "collectors.built",
        available=[a.platform.value for a in availability if a.available],
        unavailable=[a.platform.value for a in availability if not a.available],
    )
    return usable, availability


def capability_map(availability: list[Availability]) -> dict[str, bool]:
    """Shape the budget planner expects."""
    return {a.platform.value: a.available for a in availability}
