"""Shopee Philippines collection (brief §15).

Two behaviours of the live actor shape this collector:

1. On a free Apify plan it serves from a **shared cache of other users' recent
   searches** rather than scraping live, and signals a miss with a ``_notice``
   record instead of products. A miss is a known, explainable degradation --
   not an error and not an empty market -- so it is reported as such.
2. Cached results are frequently **off-query** (a "snacks" search returned a
   jewellery tray). Brief §6 says not to collect blindly toward a quota, so
   every record is relevance-checked before it enters the corpus.

``historicalSoldEstimated`` arrives as a bucketed string such as ``"<100"`` or
``"1.5k"``. It is a platform-reported visibility proxy with inconsistent
semantics, never a sales figure, so the raw text is preserved alongside any
parsed lower bound (brief §28).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import structlog

from aypo.collectors.base import CollectionResult, Collector, SearchFilters
from aypo.config import get_settings
from aypo.models.commerce import CommerceItem, ListingKind, Marketplace, Seller, Variant
from aypo.models.provenance import CollectionMethod, Platform, Provenance
from aypo.providers.apify import ActorFailed, ApifyProvider, ProviderUnavailable

log = structlog.get_logger(__name__)

_SOLD_RE = re.compile(r"([\d.]+)\s*([km]?)", re.I)

# Tokens that mark a listing as covering several units or several products.
# Attributing a bundle to one brand would corrupt share-of-listing statistics.
_BUNDLE_TOKENS = ("bundle", "set of", "pack of", "assorted", "variety", "combo", "x pcs", "pcs")


def parse_sold_estimate(value: Any) -> tuple[int | None, str | None]:
    """Return (lower_bound, raw_text).

    "<100" means fewer than 100, so the only honest numeric reading is a lower
    bound of 0. "1.5k" means at least 1500. The raw string is always kept so the
    UI can show what the platform actually said.
    """
    if value is None:
        return None, None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value), str(value)
    text = str(value).strip()
    if not text:
        return None, None
    if text.startswith("<"):
        return 0, text
    m = _SOLD_RE.search(text.replace(",", ""))
    if not m:
        return None, text
    number = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    multiplier = {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
    return int(number * multiplier), text


# A title that enumerates several products reads like "A, B, C and D". Detected
# structurally rather than against a brand list, because brief §1 forbids
# knowing the brands in advance -- the whole point is to discover them.
_ENUMERATION_RE = re.compile(r",\s*(?:and\s+)?[a-z0-9][\w'\-]*", re.I)
_MIN_ENUMERATED_ITEMS = 3


def looks_enumerated(title: str) -> bool:
    """True when the title lists several distinct products.

    Measured on live data: a Lazada listing titled "PILLOW cheetos extra flamin
    hot crunchy, piattos, nova, honey butter, vcut, chippy, and chiz curls"
    is one listing covering seven products. Attributing it to a single brand
    would corrupt every share-of-listings statistic that counts it.
    """
    segments = _ENUMERATION_RE.findall(title)
    if len(segments) < _MIN_ENUMERATED_ITEMS:
        return False
    # A size/variant list ("40g, 85g, 120g") is one product in several sizes,
    # not several products.
    measurement = re.compile(r"^\s*,?\s*(?:and\s+)?\d+\s*(?:g|kg|ml|l|pcs?|x)\b", re.I)
    substantive = [seg for seg in segments if not measurement.match(seg)]
    return len(substantive) >= _MIN_ENUMERATED_ITEMS


def classify_listing(title: str) -> ListingKind:
    lowered = title.lower()
    if looks_enumerated(title):
        # An enumeration that also says "bundle" is still multi-brand: which
        # brands it covers matters more than that it is sold together.
        return ListingKind.MULTI_BRAND
    if any(token in lowered for token in _BUNDLE_TOKENS):
        return ListingKind.BUNDLE
    return ListingKind.SINGLE


def _stem(token: str) -> str:
    """Crude singularisation so "snacks" matches "snack pack".

    Not linguistics -- just enough that a trailing plural does not cause a
    genuine listing to be discarded. Under-matching here silently shrinks the
    commerce corpus and would make a brand look absent from a category it is
    actually in.
    """
    lowered = token.lower()
    if len(lowered) > 3 and lowered.endswith("es") and not lowered.endswith("ses"):
        return lowered[:-2]
    if len(lowered) > 3 and lowered.endswith("s"):
        return lowered[:-1]
    return lowered


def relevance_score(title: str, query: str) -> float:
    """Share of query terms matched in the title.

    Deliberately crude and lexical: a cheap gate that removes results with no
    lexical connection to the query at all. Matching is stem-based and
    bidirectional, so "snacks" matches "Snack Pack" and "chip" matches "chips".

    **This gate cannot tell a product from an accessory, and must not be
    trusted to.** Marketplace titles are keyword-stuffed, so a "snacks" query
    legitimately returns storage boxes and serving trays whose titles end
    "...for Coffee/Candy/Snacks" -- both measured on live data, both scoring
    1.0 here. Separating a snack from a snack container is a semantic and
    category judgement made downstream; treating this score as category
    evidence would put serveware into food-category statistics.
    """
    terms = [_stem(t) for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    if not terms:
        return 1.0
    title_tokens = [_stem(t) for t in re.split(r"\W+", title.lower()) if t]
    if not title_tokens:
        return 0.0
    matched = sum(
        1
        for term in terms
        if any(
            term == tok or (len(term) >= 4 and (term in tok or tok in term)) for tok in title_tokens
        )
    )
    return matched / len(terms)


class ShopeeCollector(Collector[CommerceItem]):
    platform = Platform.SHOPEE
    collection_method = CollectionMethod.MANAGED_COLLECTOR
    provider_name = "apify"

    # A listing must contain at least this share of the query's terms. One of
    # two terms is enough, since marketplace titles are keyword-stuffed and
    # requiring all terms would discard genuine matches.
    MIN_RELEVANCE = 0.5

    def __init__(self, provider: ApifyProvider | None = None) -> None:
        self._provider = provider or ApifyProvider()
        self._settings = get_settings()
        self.cache_misses: list[str] = []

    @property
    def available(self) -> bool:
        return self._provider.available

    def missing_credentials(self) -> list[str]:
        return [] if self.available else ["APIFY_TOKEN"]

    async def search(self, query: str, filters: SearchFilters) -> CollectionResult[CommerceItem]:
        if not self.available:
            return self.unavailable(query)

        # The actor rejects maxItems below 10, so that is the floor regardless
        # of what the budget planner asked for.
        payload = {
            "keywords": [query],
            "country": self._settings.apify_shopee_country,
            "maxItems": max(10, filters.limit),
        }
        try:
            run = await self._provider.run_actor(
                self._settings.apify_shopee_actor, payload, max_items=max(10, filters.limit)
            )
        except ProviderUnavailable:
            return self.unavailable(query)
        except ActorFailed as exc:
            return CollectionResult(
                query=query, available=True, error=f"actor failed: {exc.status}"
            )

        notices = [r for r in run.items if isinstance(r, dict) and "_notice" in r]
        products = [r for r in run.items if isinstance(r, dict) and "_notice" not in r]
        if notices and not products:
            notice = str(notices[0].get("_notice", ""))[:300]
            self.cache_misses.append(query)
            log.info("shopee.cache_miss", query=query)
            # Not an error and not evidence of absence: the provider simply had
            # nothing cached for this query. Recorded so the UI can distinguish
            # "no listings found" from "we could not look".
            return CollectionResult(
                query=query, available=True, raw_count=0, error=f"cache_miss: {notice}"
            )

        items = self.normalize_many(products, query, filters)
        return CollectionResult(
            items=items,
            query=query,
            raw_count=len(products),
            units_consumed=max(1, round(run.compute_units * 1000)),
        )

    def normalize(
        self, raw_item: dict[str, Any], query: str, filters: SearchFilters
    ) -> CommerceItem | None:
        item_id = raw_item.get("itemId")
        title = raw_item.get("name")
        url = raw_item.get("url")
        if not item_id or not title or not url:
            return None

        if relevance_score(str(title), query) < self.MIN_RELEVANCE:
            return None

        sold_lower, sold_raw = parse_sold_estimate(raw_item.get("historicalSoldEstimated"))
        brand = (raw_item.get("brand") or "").strip() or None

        return CommerceItem(
            platform_id=str(item_id),
            marketplace=Marketplace.SHOPEE,
            url=str(url),
            title=str(title),
            brand_surface_form=brand,
            # The marketplace states a brand field directly, so it needs no
            # extraction from the title -- but it is routinely a seller's own
            # label rather than a real manufacturer brand, so it is not treated
            # as certain.
            brand_confidence=0.7 if brand else None,
            listing_kind=classify_listing(str(title)),
            seller=Seller(
                name=raw_item.get("shopName"),
                seller_id=str(raw_item["shopId"]) if raw_item.get("shopId") else None,
                location=raw_item.get("location"),
            ),
            price=_as_float(raw_item.get("price")),
            original_price=_as_float(raw_item.get("originalPrice")),
            currency=raw_item.get("currency") or "PHP",
            discount_percent=_as_float(raw_item.get("discountPercent")),
            rating=_as_float(raw_item.get("rating")),
            review_count=_as_int(raw_item.get("reviewCount")),
            sold_count=sold_lower,
            sold_count_is_proxy=True,
            image_url=(raw_item.get("images") or [None])[0],
            variants=[
                Variant(name=str(option))
                for option in (raw_item.get("variationOptions") or [])
                if option
            ],
            first_seen_at=datetime.now(UTC),
            provenance=Provenance(
                platform=Platform.SHOPEE,
                source_url=str(url),
                discovery_query=query,
                sampling_strategy=filters.sampling_strategy,
                collection_provider=self.provider_name,
                collection_method=self.collection_method,
                discovered_at=datetime.now(UTC),
                research_run_id=filters.research_run_id,
                research_query_id=filters.research_query_id,
                hypothesis_id=filters.hypothesis_id,
                extras={
                    # Bucketed platform text, kept verbatim: the UI must show
                    # what the platform said, not a number we invented.
                    "sold_count_raw": sold_raw,
                    "favorites": raw_item.get("favorites"),
                    "price_max": raw_item.get("priceMax"),
                    "is_on_sale": raw_item.get("isOnSale"),
                    "relevance": round(relevance_score(str(title), query), 2),
                },
            ),
            raw=raw_item,
        )


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
