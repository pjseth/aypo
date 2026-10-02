"""Normalised marketplace records (brief §15, §16).

Shopee and Lazada expose different field sets with different names, and the
third-party collectors behind them change output without notice. Both normalise
into these objects so the commerce analysis never branches on marketplace.

Every field except identity is optional by design: brief §15 and §16 both state
that missing fields must not break analysis.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from aypo.models.provenance import Platform, Provenance


class Marketplace(StrEnum):
    SHOPEE = "shopee"
    LAZADA = "lazada"


class ListingKind(StrEnum):
    """What a listing actually is.

    Marketplace titles are keyword-stuffed and frequently cover bundles or
    multiple brands. Recording this prevents a bundle being silently attributed
    to one brand, which would corrupt every share-of-listings statistic.
    """

    SINGLE = "single"
    BUNDLE = "bundle"
    MULTI_BRAND = "multi_brand"
    UNCLEAR = "unclear"


class Seller(BaseModel):
    name: str | None = None
    seller_id: str | None = None
    url: str | None = None
    rating: float | None = None
    follower_count: int | None = None
    is_official_store: bool | None = None
    location: str | None = None


class Variant(BaseModel):
    name: str | None = None
    price: float | None = None
    original_price: float | None = None
    stock: int | None = None
    sku: str | None = None


class CommerceReview(BaseModel):
    review_id: str | None = None
    rating: int | None = None
    text: str | None = None
    published_at: datetime | None = None
    author_handle: str | None = None
    variant_name: str | None = None
    helpful_count: int | None = None


class CommerceItem(BaseModel):
    """One marketplace listing."""

    platform_id: str
    marketplace: Marketplace
    url: str
    title: str

    # Extracted from a noisy title rather than supplied by the marketplace, so
    # it is held with its own confidence and may be left unresolved. Brief §28
    # prefers identifying insufficient data over a confident wrong answer.
    brand_surface_form: str | None = None
    brand_confidence: float | None = None
    listing_kind: ListingKind = ListingKind.UNCLEAR

    seller: Seller | None = None
    category_path: list[str] = Field(default_factory=list)

    price: float | None = None
    original_price: float | None = None
    currency: str = "PHP"
    discount_percent: float | None = None

    rating: float | None = None
    review_count: int | None = None

    # Platform-reported and semantically inconsistent between marketplaces:
    # sometimes lifetime, sometimes a rolling window, sometimes bucketed. It is
    # a visibility proxy, never a sales figure, and the UI must say so wherever
    # it appears (brief §28).
    sold_count: int | None = None
    sold_count_is_proxy: bool = True

    image_url: str | None = None
    variants: list[Variant] = Field(default_factory=list)
    reviews: list[CommerceReview] = Field(default_factory=list)

    first_seen_at: datetime | None = None
    provenance: Provenance
    raw: dict = Field(default_factory=dict, repr=False)

    @property
    def dedupe_key(self) -> str:
        return f"{self.marketplace.value}:{self.platform_id}"

    @property
    def effective_discount(self) -> float | None:
        """Prefer the computed discount over the platform's own claim.

        Marketplaces routinely show a discount against an inflated "original"
        price, so when both are present the computed value is used and the
        platform's figure is kept only as a fallback.
        """
        if self.price is not None and self.original_price and self.original_price > 0:
            if self.original_price <= self.price:
                return 0.0
            return round((self.original_price - self.price) / self.original_price * 100, 2)
        return self.discount_percent

    @property
    def platform_enum(self) -> Platform:
        return Platform(self.marketplace.value)
