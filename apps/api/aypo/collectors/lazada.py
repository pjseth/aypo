"""Lazada Philippines collection (brief §16).

The actor returns a nested record (``product`` / ``pricing`` / ``metrics`` /
``seller`` / ``media`` / ``offer``) rather than a flat one, and carries its own
``source_context.search_query`` -- which is preferred over our query string
because one run can cover several seeds.

Unlike Shopee's free-plan cache, this actor scrapes live, so it answers
arbitrary discovered-theme queries. That asymmetry matters: a theme with no
Shopee result may simply be a cache miss, while a theme with no Lazada result
is closer to genuine absence.

``metrics.sales_count`` is numeric here but is still a platform-reported
visibility proxy with undefined time semantics, never a sales figure (brief §28).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from aypo.collectors.base import CollectionResult, Collector, SearchFilters
from aypo.collectors.shopee import classify_listing, relevance_score
from aypo.config import get_settings
from aypo.models.commerce import CommerceItem, Marketplace, Seller, Variant
from aypo.models.provenance import CollectionMethod, Platform, Provenance
from aypo.providers.apify import ActorFailed, ApifyProvider, ProviderUnavailable

log = structlog.get_logger(__name__)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("₱", "").strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return int(f) if f is not None else None


class LazadaCollector(Collector[CommerceItem]):
    platform = Platform.LAZADA
    collection_method = CollectionMethod.MANAGED_COLLECTOR
    provider_name = "apify"

    MIN_RELEVANCE = 0.5

    def __init__(self, provider: ApifyProvider | None = None) -> None:
        self._provider = provider or ApifyProvider()
        self._settings = get_settings()

    @property
    def available(self) -> bool:
        return self._provider.available

    def missing_credentials(self) -> list[str]:
        return [] if self.available else ["APIFY_TOKEN"]

    async def search(self, query: str, filters: SearchFilters) -> CollectionResult[CommerceItem]:
        if not self.available:
            return self.unavailable(query)

        # The actor rejects `limit` below 10.
        limit = max(10, filters.limit)
        payload: dict[str, Any] = {
            "queries": [query],
            # Lowercase on purpose: this actor's country enum differs in case
            # from Shopee's, and the wrong case silently targets the wrong
            # marketplace.
            "country": self._settings.apify_lazada_country,
            "limit": limit,
            "enrich_data": bool(filters.extras.get("enrich")),
        }
        # The actor rejects maxReviews=0 outright (HTTP 400), so the review
        # options are omitted entirely unless reviews were actually asked for.
        if filters.extras.get("with_reviews"):
            payload["getReviews"] = True
            payload["maxReviews"] = max(1, int(filters.extras.get("max_reviews", 10) or 10))
        try:
            run = await self._provider.run_actor(
                self._settings.apify_lazada_actor, payload, max_items=limit
            )
        except ProviderUnavailable:
            return self.unavailable(query)
        except ActorFailed as exc:
            return CollectionResult(
                query=query, available=True, error=f"actor failed: {exc.status}"
            )

        products = [
            r
            for r in run.items
            if isinstance(r, dict) and r.get("record_type") in (None, "product")
        ]
        return CollectionResult(
            items=self.normalize_many(products, query, filters),
            query=query,
            raw_count=len(products),
            units_consumed=max(1, round(run.compute_units * 1000)),
        )

    def normalize(
        self, raw_item: dict[str, Any], query: str, filters: SearchFilters
    ) -> CommerceItem | None:
        product = raw_item.get("product") or {}
        pricing = raw_item.get("pricing") or {}
        metrics = raw_item.get("metrics") or {}
        seller = raw_item.get("seller") or {}
        media = raw_item.get("media") or {}
        context = raw_item.get("source_context") or {}
        promotion = (raw_item.get("offer") or {}).get("promotion") or {}

        item_id = raw_item.get("record_id") or product.get("product_id")
        title = raw_item.get("title") or (raw_item.get("entity") or {}).get("title")
        url = raw_item.get("url") or product.get("url") or context.get("canonical_url")
        if not item_id or not title or not url:
            return None

        if relevance_score(str(title), query) < self.MIN_RELEVANCE:
            return None

        brand = (product.get("brand") or "").strip() or None
        price = _as_float(pricing.get("current_price") or pricing.get("price"))
        source_values = pricing.get("source_values") or {}
        original = _as_float(pricing.get("original_price") or source_values.get("origin_price"))

        return CommerceItem(
            platform_id=str(item_id),
            marketplace=Marketplace.LAZADA,
            url=str(url),
            title=str(title),
            brand_surface_form=brand,
            # Marketplace brand fields are frequently a retailer or membership
            # store rather than the manufacturer, so this is a lead for entity
            # resolution, not a settled answer.
            brand_confidence=0.7 if brand else None,
            listing_kind=classify_listing(str(title)),
            seller=Seller(
                name=seller.get("seller_name") or seller.get("name"),
                seller_id=str(seller["seller_id"]) if seller.get("seller_id") else None,
                location=seller.get("seller_location"),
            ),
            category_path=[str(c) for c in (product.get("category_path") or [])],
            price=price,
            original_price=original,
            currency="PHP",
            rating=_as_float(metrics.get("rating")),
            review_count=_as_int(metrics.get("review_count")),
            sold_count=_as_int(metrics.get("sales_count")),
            sold_count_is_proxy=True,
            image_url=media.get("main_image_url") or media.get("primary_image_url"),
            variants=[
                Variant(sku=str(v.get("id")))
                for v in (product.get("variants") or [])
                if isinstance(v, dict) and v.get("id")
            ],
            first_seen_at=datetime.now(UTC),
            provenance=Provenance(
                platform=Platform.LAZADA,
                source_url=str(url),
                # The actor echoes the seed that found this record.
                discovery_query=str(context.get("search_query") or query),
                sampling_strategy=filters.sampling_strategy,
                collection_provider=self.provider_name,
                collection_method=self.collection_method,
                discovered_at=datetime.now(UTC),
                research_run_id=filters.research_run_id,
                research_query_id=filters.research_query_id,
                hypothesis_id=filters.hypothesis_id,
                extras={
                    # Platform-stated, so paid placement is observed rather
                    # than inferred -- a sponsored listing's visibility says
                    # nothing about demand.
                    "is_sponsored": bool(promotion.get("is_sponsored")),
                    "sponsorship_source": "platform_declared",
                    "sales_count_raw": metrics.get("sales_count_text"),
                    "in_stock": (raw_item.get("availability") or {}).get("in_stock"),
                    "sku": product.get("sku"),
                    "relevance": round(relevance_score(str(title), query), 2),
                },
            ),
            raw=raw_item,
        )
