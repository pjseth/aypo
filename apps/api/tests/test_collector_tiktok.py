"""TikTok normalisation, against a fixture whose shape was captured live."""

import json
import pathlib

import pytest

from aypo.collectors.base import SearchFilters
from aypo.collectors.tiktok import TikTokCollector
from aypo.models.provenance import CollectionMethod, Platform, SamplingStrategy
from aypo.providers.apify import ApifyProvider

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "tiktok_items.json").read_text())


@pytest.fixture
def collector():
    return TikTokCollector(provider=ApifyProvider(token="t"))


@pytest.fixture
def filters():
    return SearchFilters(limit=10, sampling_strategy=SamplingStrategy.CATEGORY)


def test_normalizes_live_payload_shape(collector, filters):
    item = collector.normalize(FIXTURE[0], "category query x", filters)
    assert item is not None
    assert item.platform is Platform.TIKTOK
    assert item.platform_id == "7000000000000000001"
    assert item.duration_seconds == 14
    assert item.hashtags == ["themex", "categoryy"]
    assert item.language_hint == "en"


def test_engagement_field_mapping(collector, filters):
    # playCount->views, diggCount->likes, collectCount->saves are not guessable
    # from the names; a wrong mapping would silently corrupt every statistic.
    e = collector.normalize(FIXTURE[0], "q", filters).engagement
    assert (e.views, e.likes, e.comments, e.shares, e.saves) == (123456, 7890, 123, 45, 678)


def test_sponsorship_is_recorded_as_platform_declared(collector, filters):
    # Brief §19/§28: sponsored vs organic must be distinguished. The platform
    # states it, so it is observed rather than inferred -- and the provenance
    # must say which, or a downstream reader cannot tell.
    extras = collector.normalize(FIXTURE[0], "q", filters).provenance.extras
    assert extras["is_sponsored"] is True
    assert extras["sponsorship_source"] == "platform_declared"


def test_provenance_prefers_actor_reported_query(collector, filters):
    # One actor run can cover several handles; the echoed searchQuery is the
    # one that actually found this item.
    item = collector.normalize(FIXTURE[0], "the-query-we-passed", filters)
    assert item.provenance.discovery_query == "category query x"
    assert item.provenance.sampling_strategy is SamplingStrategy.CATEGORY
    assert item.provenance.collection_method is CollectionMethod.MANAGED_COLLECTOR


def test_creator_and_sound_are_mapped(collector, filters):
    item = collector.normalize(FIXTURE[0], "q", filters)
    assert item.creator.handle == "creator_one"
    assert item.creator.follower_count == 4259
    assert item.sound.title == "Sound Z"
    assert item.sound.original is False


def test_malformed_item_is_dropped_not_raised(collector, filters):
    assert collector.normalize(FIXTURE[1], "q", filters) is None


def test_normalize_many_keeps_good_and_drops_bad(collector, filters):
    items = collector.normalize_many(FIXTURE, "q", filters)
    assert len(items) == 1


def test_dedupe_key_is_stable_across_discovery_paths(collector, filters):
    a = collector.normalize(FIXTURE[0], "query one", filters)
    b = collector.normalize(FIXTURE[0], "query two", filters)
    # The same video found by two queries must collapse to one corpus item,
    # or every per-item statistic inflates.
    assert a.dedupe_key == b.dedupe_key == "tiktok:7000000000000000001"


def test_unavailable_without_token_names_the_env_var():
    c = TikTokCollector(provider=ApifyProvider(token=None))
    assert c.available is False
    result = c.unavailable("q")
    assert result.ok is False
    assert "APIFY_TOKEN" in result.error


def test_media_url_absent_is_tolerated(collector, filters):
    # mediaUrls comes back empty without the paid download add-on; retrieval
    # happens separately from the post URL.
    item = collector.normalize(FIXTURE[0], "q", filters)
    assert item.media_url is None
    assert item.thumbnail_url is not None
    assert item.has_retrievable_media is True
