"""The provider-independent collector interface (brief §10).

Three responsibilities, deliberately separated:

* ``search``    -- discovery. Finds candidate items for a query.
* ``fetch``     -- retrieval. Obtains the full record for a discovered item.
* ``normalize`` -- maps a provider payload to an internal object.

Analysis only ever consumes the normalised objects, so replacing a provider is
a change to one collector and nothing else (brief §5).

Collectors never raise into the research loop over a missing credential or a
dead provider. They report themselves unavailable and the run continues on the
sources that work (brief §7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from aypo.models.provenance import CollectionMethod, Platform, SamplingStrategy

log = structlog.get_logger(__name__)


@dataclass
class SearchFilters:
    """Constraints on one discovery query.

    ``published_after`` is deliberately optional and unset by default: filtering
    collection to recent content biases the corpus toward new material and
    manufactures apparent growth for every theme (see the temporal-claim
    guardrails in brief §22).
    """

    limit: int = 50
    country: str = "PH"
    language: str | None = None
    published_after: str | None = None
    sampling_strategy: SamplingStrategy = SamplingStrategy.CATEGORY
    research_run_id: str | None = None
    research_query_id: str | None = None
    hypothesis_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionResult[TItem]:
    """Outcome of one collection attempt.

    Carries the failure explicitly rather than raising, so a partial result is
    still usable and the run view can show which sources were degraded and why
    (brief §7, §28).
    """

    items: list[TItem] = field(default_factory=list)
    query: str = ""
    available: bool = True
    error: str | None = None
    units_consumed: int = 0
    raw_count: int = 0

    @property
    def ok(self) -> bool:
        return self.available and self.error is None

    @property
    def kept_ratio(self) -> float:
        """Share of returned records that survived normalisation.

        A collapsing ratio is the earliest signal that a third-party actor
        changed its output shape.
        """
        return len(self.items) / self.raw_count if self.raw_count else 0.0


class Collector[TItem](ABC):
    platform: Platform
    collection_method: CollectionMethod
    provider_name: str

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the credentials this collector needs are present."""

    @abstractmethod
    def missing_credentials(self) -> list[str]:
        """Env var names an operator must set to enable this source."""

    @abstractmethod
    async def search(self, query: str, filters: SearchFilters) -> CollectionResult[TItem]:
        """Discover items for a query."""

    async def fetch(self, item: TItem) -> TItem:
        """Retrieve the full record. Defaults to identity for collectors whose
        search already returns complete records."""
        return item

    @abstractmethod
    def normalize(
        self, raw_item: dict[str, Any], query: str, filters: SearchFilters
    ) -> TItem | None:
        """Map one provider payload to an internal object.

        Returns None when the payload cannot be normalised, so one malformed
        record is dropped rather than failing the batch.
        """

    def unavailable(self, query: str) -> CollectionResult[TItem]:
        missing = self.missing_credentials()
        message = (
            f"{self.platform.value} unavailable: set {', '.join(missing)}"
            if missing
            else f"{self.platform.value} unavailable"
        )
        log.info("collector.unavailable", platform=self.platform.value, missing=missing)
        return CollectionResult(query=query, available=False, error=message)

    def normalize_many(
        self, raw_items: list[dict[str, Any]], query: str, filters: SearchFilters
    ) -> list[TItem]:
        out: list[TItem] = []
        failures = 0
        for raw in raw_items:
            try:
                item = self.normalize(raw, query, filters)
            except Exception as exc:
                failures += 1
                log.debug(
                    "collector.normalize_failed",
                    platform=self.platform.value,
                    error=str(exc),
                )
                continue
            if item is not None:
                out.append(item)
            else:
                failures += 1
        if failures and raw_items:
            log.info(
                "collector.normalize_summary",
                platform=self.platform.value,
                kept=len(out),
                dropped=failures,
            )
        return out
