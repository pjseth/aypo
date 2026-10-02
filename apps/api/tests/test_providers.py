"""Provider transport behaviour, mocked.

Covers the failure paths that matter operationally: a missing credential must
degrade rather than raise into the research loop, and a failing actor must not
leave a billable run orphaned.
"""

import httpx
import pytest
import respx

from aypo.providers.apify import ActorFailed, ApifyProvider, ProviderUnavailable
from aypo.providers.storage import LocalStorage, content_key
from aypo.providers.websearch import SerperWebSearch, WebSearchUnavailable, classify_platform

API = "https://api.apify.com/v2"


# ── apify ────────────────────────────────────────────────────────────────────


async def test_apify_without_token_reports_unavailable_not_crash():
    provider = ApifyProvider(token=None)
    assert provider.available is False
    with pytest.raises(ProviderUnavailable) as exc:
        await provider.run_actor("any~actor", {})
    # The message must tell an operator what to set and what they lose.
    assert "APIFY_TOKEN" in str(exc.value)


@respx.mock
async def test_apify_run_returns_normalised_dataset_items():
    respx.post(f"{API}/acts/x~y/runs").mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {
                    "id": "run1",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "ds1",
                    "stats": {"computeUnits": 0.25},
                }
            },
        )
    )
    respx.get(f"{API}/datasets/ds1/items").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
            httpx.Response(200, json=[]),
        ]
    )
    run = await ApifyProvider(token="t").run_actor("x~y", {"keywords": ["Theme X"]})
    assert run.succeeded
    assert [i["id"] for i in run.items] == [1, 2]
    # Compute units feed the collection budget alongside LLM spend.
    assert run.compute_units == 0.25


@respx.mock
async def test_apify_failed_actor_raises_with_status():
    respx.post(f"{API}/acts/x~y/runs").mock(
        return_value=httpx.Response(201, json={"data": {"id": "r", "status": "FAILED"}})
    )
    with pytest.raises(ActorFailed) as exc:
        await ApifyProvider(token="t").run_actor("x~y", {})
    assert exc.value.status == "FAILED"


@respx.mock
async def test_apify_dataset_paging_respects_max_items():
    respx.post(f"{API}/acts/a~b/runs").mock(
        return_value=httpx.Response(
            201, json={"data": {"id": "r", "status": "SUCCEEDED", "defaultDatasetId": "d"}}
        )
    )
    respx.get(f"{API}/datasets/d/items").mock(
        return_value=httpx.Response(200, json=[{"id": n} for n in range(500)])
    )
    run = await ApifyProvider(token="t").run_actor("a~b", {}, max_items=3)
    assert len(run.items) == 3


# ── web search ───────────────────────────────────────────────────────────────


async def test_websearch_without_key_is_unavailable():
    ws = SerperWebSearch(api_key=None)
    assert ws.available is False
    with pytest.raises(WebSearchUnavailable):
        await ws.search("Theme X")


@respx.mock
async def test_websearch_classifies_platform_and_records_query():
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {"link": "https://www.tiktok.com/@a/video/1", "title": "A", "position": 1},
                    {"link": "https://example.org/post", "title": "B", "position": 2},
                ]
            },
        )
    )
    results = await SerperWebSearch(api_key="k").search("Theme X", platform="tiktok")
    assert [r.platform for r in results] == ["tiktok", "web"]
    # Provenance: brief §7 requires the discovery query be stored per item.
    assert all(r.discovery_query == "site:tiktok.com Theme X" for r in results)


@respx.mock
async def test_websearch_returns_empty_on_http_error_rather_than_raising():
    # A dead discovery source must not abort the research run (brief §7).
    respx.post("https://google.serper.dev/search").mock(return_value=httpx.Response(500))
    assert await SerperWebSearch(api_key="k").search("Theme X") == []


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.tiktok.com/@x/video/1", "tiktok"),
        ("https://youtube.com/watch?v=1", "youtube"),
        ("https://shopee.ph/product-i.1.2", "shopee"),
        ("https://www.lazada.com.ph/products/x.html", "lazada"),
        ("https://some.blog/post", "web"),
    ],
)
def test_classify_platform(url, expected):
    assert classify_platform(url) == expected


# ── storage ──────────────────────────────────────────────────────────────────


async def test_local_storage_roundtrip(tmp_path):
    store = LocalStorage(tmp_path)
    key = content_key("frames", b"bytes", ".jpg")
    await store.put(key, b"bytes")
    assert await store.get(key) == b"bytes"
    assert await store.exists(key)


async def test_content_key_is_stable_and_deduplicates(tmp_path):
    # Re-analysing an item must overwrite, not accumulate duplicates.
    assert content_key("f", b"same", ".jpg") == content_key("f", b"same", ".jpg")
    assert content_key("f", b"a", ".jpg") != content_key("f", b"b", ".jpg")


async def test_local_storage_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        await LocalStorage(tmp_path).put("../escape.txt", b"x")


async def test_missing_key_returns_none_not_error(tmp_path):
    assert await LocalStorage(tmp_path).get("frames/nope.jpg") is None


# ── listing classification ───────────────────────────────────────────────────


def test_enumerated_listing_is_multi_brand():
    from aypo.collectors.shopee import classify_listing
    from aypo.models.commerce import ListingKind

    # Measured on live Lazada data: one listing covering seven products.
    # Attributing it to a single brand corrupts every share-of-listings figure.
    title = (
        "PILLOW cheetos extra flamin hot crunchy, piattos, nova, honey butter, "
        "vcut , chippy, and chiz curls PMKCODE 268"
    )
    assert classify_listing(title) is ListingKind.MULTI_BRAND


def test_multi_product_bundle_is_multi_brand_not_bundle():
    from aypo.collectors.shopee import classify_listing
    from aypo.models.commerce import ListingKind

    # Which brands it covers matters more than that it is sold together.
    title = (
        "[LAZ EXCLUSIVE] BESTSELLER BUNDLE - Great Taste White, C2 Solo Apple, "
        "Cloud 9 Classic, Wafrets Choco, Cream-O Vanilla, Brand A Cheese"
    )
    assert classify_listing(title) is ListingKind.MULTI_BRAND


def test_many_units_of_one_product_is_a_bundle():
    from aypo.collectors.shopee import classify_listing
    from aypo.models.commerce import ListingKind

    assert classify_listing("[BUNDLE OF 12] Brand A Cheese Crisps 40g") is ListingKind.BUNDLE
    assert classify_listing("Brand A Sour Cream 40g x 3pcs") is ListingKind.BUNDLE


def test_a_size_list_is_not_several_products():
    from aypo.collectors.shopee import classify_listing
    from aypo.models.commerce import ListingKind

    # "40g, 85g, 120g" is one product in three sizes.
    assert classify_listing("Brand A Crisps 40g, 85g, 120g") is not ListingKind.MULTI_BRAND


def test_plain_single_listing():
    from aypo.collectors.shopee import classify_listing
    from aypo.models.commerce import ListingKind

    assert classify_listing("Artisan Potato Chips 180g") is ListingKind.SINGLE
