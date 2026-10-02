"""Apify managed-collection provider (brief §11).

This is deliberately a thin transport: run an actor, wait, return raw dataset
items. It performs no normalisation, because brief §10 requires that downstream
analysis never couple to Apify response structures -- collectors own that
mapping, so Apify can later be swapped for a licensed data provider or a direct
API without touching analysis.

Actor ids are configuration, not constants (brief §11): third-party actors are
renamed, deprecated and have their input schemas changed without notice.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from aypo.config import get_settings

log = structlog.get_logger(__name__)

API_BASE = "https://api.apify.com/v2"


def auth_headers(token: str) -> dict[str, str]:
    """Bearer header rather than ?token=.

    A token in the query string is logged verbatim by every HTTP client and
    proxy in the path; a header is not.
    """
    return {"Authorization": f"Bearer {token}"}


# Terminal run states per the Apify run lifecycle.
_SUCCESS = "SUCCEEDED"
_TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ProviderUnavailable(RuntimeError):
    """The provider has no usable credential. Callers skip the source."""


class ActorFailed(RuntimeError):
    """The actor ran but did not succeed."""

    def __init__(self, actor_id: str, status: str, run_id: str | None) -> None:
        super().__init__(f"apify actor {actor_id} finished with status={status} run={run_id}")
        self.actor_id = actor_id
        self.status = status
        self.run_id = run_id


@dataclass
class ActorRun:
    run_id: str
    status: str
    dataset_id: str | None
    items: list[dict[str, Any]] = field(default_factory=list)
    # Apify reports compute-unit usage; it is recorded against the research
    # budget so collection spend is visible next to LLM spend.
    compute_units: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status == _SUCCESS


class ApifyProvider:
    def __init__(self, token: str | None = None, *, timeout: float | None = None) -> None:
        settings = get_settings()
        self._token = token or settings.secret("apify_token")
        self._timeout = timeout or float(settings.apify_default_timeout_secs)
        # Apify enforces account-level concurrency; exceeding it returns 429s
        # that look like transient failures and waste budget on retries.
        self._gate = asyncio.Semaphore(settings.apify_max_concurrent_runs)

    @property
    def available(self) -> bool:
        return bool(self._token)

    def _require_token(self) -> str:
        if not self._token:
            raise ProviderUnavailable(
                "APIFY_TOKEN is not set; TikTok, Instagram, Facebook, Shopee and Lazada "
                "collection are unavailable and the research run will continue without them"
            )
        return self._token

    async def run_actor(
        self,
        actor_id: str,
        input_data: dict[str, Any],
        *,
        poll_interval: float = 5.0,
        max_items: int | None = None,
    ) -> ActorRun:
        """Start an actor, wait for it to finish, and return its dataset items."""
        token = self._require_token()
        async with self._gate:
            async with httpx.AsyncClient(timeout=60.0, headers=auth_headers(token)) as client:
                start = await client.post(f"{API_BASE}/acts/{actor_id}/runs", json=input_data)
                if start.status_code >= 400:
                    raise ActorFailed(actor_id, f"start_http_{start.status_code}", None)
                run = start.json()["data"]
                run_id = run["id"]
                log.info("apify.run_started", actor=actor_id, run_id=run_id)

                status = run.get("status", "READY")
                waited = 0.0
                while status not in _TERMINAL:
                    if waited >= self._timeout:
                        # Abort rather than leak a run that keeps billing.
                        await self._abort(client, run_id, token)
                        raise ActorFailed(actor_id, "TIMED-OUT", run_id)
                    await asyncio.sleep(poll_interval)
                    waited += poll_interval
                    detail = await client.get(f"{API_BASE}/actor-runs/{run_id}")
                    if detail.status_code >= 400:
                        continue
                    run = detail.json()["data"]
                    status = run.get("status", status)

                dataset_id = run.get("defaultDatasetId")
                compute = float((run.get("stats") or {}).get("computeUnits") or 0.0)
                if status != _SUCCESS:
                    log.warning("apify.run_failed", actor=actor_id, run_id=run_id, status=status)
                    raise ActorFailed(actor_id, status, run_id)

                items = (
                    await self.get_dataset(dataset_id, max_items=max_items) if dataset_id else []
                )
                log.info(
                    "apify.run_finished",
                    actor=actor_id,
                    run_id=run_id,
                    items=len(items),
                    compute_units=compute,
                )
                return ActorRun(run_id, status, dataset_id, items, compute)

    async def get_dataset(
        self, dataset_id: str, *, max_items: int | None = None, page_size: int = 500
    ) -> list[dict[str, Any]]:
        """Page through a dataset. Actors can emit far more than requested."""
        token = self._require_token()
        out: list[dict[str, Any]] = []
        offset = 0
        async with httpx.AsyncClient(timeout=120.0, headers=auth_headers(token)) as client:
            while True:
                limit = page_size if max_items is None else min(page_size, max_items - len(out))
                if limit <= 0:
                    break
                resp = await client.get(
                    f"{API_BASE}/datasets/{dataset_id}/items",
                    params={"offset": offset, "limit": limit, "clean": "true"},
                )
                if resp.status_code >= 400:
                    log.warning(
                        "apify.dataset_fetch_failed",
                        dataset_id=dataset_id,
                        status=resp.status_code,
                    )
                    break
                page = resp.json()
                if not isinstance(page, list) or not page:
                    break
                returned = len(page)
                # Actors do not always honour `limit`. Truncate to what was
                # actually asked for, otherwise an over-returning actor blows
                # straight past max_items and bills for content we discard.
                if max_items is not None:
                    page = page[: max_items - len(out)]
                out.extend(page)
                offset += returned
                if max_items is not None and len(out) >= max_items:
                    break
                if returned < limit:
                    break
        return out

    @staticmethod
    async def _abort(client: httpx.AsyncClient, run_id: str, token: str) -> None:
        # client already carries the Bearer header; token kept for signature
        # stability with callers that construct their own client.
        _ = token
        try:
            await client.post(f"{API_BASE}/actor-runs/{run_id}/abort")
            log.info("apify.run_aborted", run_id=run_id)
        except Exception as exc:  # pragma: no cover
            log.warning("apify.abort_failed", run_id=run_id, error=str(exc))
