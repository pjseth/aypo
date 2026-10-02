"""Postgres implementation of the research loop's write paths.

Two things here are easy to get wrong and expensive when wrong:

* **Platform vocabulary.** The database's canonical values come from
  ``collection_sources`` and are market-qualified for marketplaces --
  ``shopee_ph``, ``lazada_ph`` -- while the in-process ``Platform`` enum is
  not. The mapping lives here, in one place, rather than being remembered at
  each call site.
* **Idempotency.** The same item is routinely rediscovered by a later query, so
  every upsert keys on the natural identity and returns whether the row was
  new. Counting a rediscovery as a new item inflates every per-item statistic
  downstream, including the novelty metric the stopping rule reads.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import structlog

from aypo.config import get_settings
from aypo.db.pool import connection, transaction
from aypo.models.commerce import CommerceItem
from aypo.models.content import ContentItem
from aypo.models.provenance import Platform
from aypo.research.queries import GeneratedQuery, normalise_query
from aypo.research.states import RunPhase, RunState

log = structlog.get_logger(__name__)

# In-process enum -> the vocabulary in collection_sources.
# research_log.event_type is an enum, not free text. The loop speaks in its own
# dotted names, so they are mapped here rather than at each call site; an
# unmapped event falls back to a generic transition rather than failing the
# write, because losing the narrative is worse than losing its precision.
_LOG_EVENT: dict[str, str] = {
    "run.created": "run_created",
    "iteration.opened": "iteration_opened",
    "iteration.closed": "iteration_closed",
    "iteration.assessed": "saturation_measured",
    "queries.generated": "queries_proposed",
    "query.admitted": "query_admitted",
    "query.rejected": "query_rejected",
    "collection.searched": "source_searched",
    "collection.degraded": "source_degraded",
    "collection.unavailable": "source_unconfigured",
    "analysis.failed": "provider_garbage",
    "analysis.done": "items_analyzed",
    "llm.refused": "llm_refused",
    "budget.threshold": "budget_threshold",
    "stop.evaluated": "stop_evaluated",
    "run.stopped": "run_stopped",
    "run.failed": "run_failed",
}


def log_event(name: str) -> str:
    return _LOG_EVENT.get(name, "state_transition")


# research_budget_kind separates social from commerce units, because they are
# priced and capped differently and a single "collection" bucket would hide
# which side of the run actually consumed the budget.
_COMMERCE_DB_PLATFORMS = frozenset({"shopee_ph", "lazada_ph"})

# research_stop_reason is an enum and is deliberately more specific than the
# loop's own ceilings: it distinguishes, for instance, saturation from
# rank_exhaustion, whose remedies are opposite. Brief §26 requires the reason be
# stored, so an unmapped value must never silently become a different reason --
# anything unrecognised becomes fatal_error, which is visibly wrong rather than
# quietly plausible.
_STOP_REASON: dict[str, str] = {
    "collection_budget_exhausted": "budget_hard_stop",
    "llm_budget_exhausted": "budget_hard_stop",
    "item_target_reached": "content_target_reached",
    "wallclock_exhausted": "wall_clock_exhausted",
    "max_iterations_reached": "max_iterations_reached",
    "no_new_information": "saturation",
    "no_new_hypotheses": "hypothesis_starvation",
    "stopped_by_operator": "user_cancelled",
    "all_sources_unavailable": "source_exhaustion",
}


def stop_reason_db(reason: str) -> str:
    return _STOP_REASON.get(reason, "fatal_error")


def budget_kind(kind: str, platform: str | None) -> str:
    if kind == "collection":
        return (
            "commerce_unit"
            if platform and db_platform(platform) in _COMMERCE_DB_PLATFORMS
            else "social_unit"
        )
    return (
        kind
        if kind in {"llm", "embedding", "transcription", "storage", "wall"}
        else "provider_call"
    )


_PLATFORM_DB: dict[Platform, str] = {
    Platform.TIKTOK: "tiktok",
    Platform.YOUTUBE: "youtube",
    Platform.INSTAGRAM: "instagram",
    Platform.FACEBOOK: "facebook",
    Platform.WEB: "web",
    Platform.SHOPEE: "shopee_ph",
    Platform.LAZADA: "lazada_ph",
}


def db_platform(platform: Platform | str) -> str:
    if isinstance(platform, Platform):
        return _PLATFORM_DB[platform]
    try:
        return _PLATFORM_DB[Platform(platform)]
    except ValueError:
        return platform


class PostgresRunStore:
    """Implements aypo.research.store.RunStore."""

    async def create_run(
        self,
        *,
        objective: str,
        subject_brand: str,
        market_code: str,
        language_codes: list[str],
        caps: dict[str, Any],
    ) -> UUID:
        settings = get_settings()
        async with connection() as conn:
            return await conn.fetchval(
                """
                INSERT INTO research_runs
                    (objective, subject_brand, market_code, language_codes,
                     embedding_model, embedding_dim,
                     cap_collection_usd, cap_llm_usd, cap_items, cap_wall_seconds,
                     max_iterations, state, started_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'created', now())
                RETURNING id
                """,
                objective,
                subject_brand,
                market_code,
                language_codes,
                settings.aypo_embedding_model,
                settings.aypo_embedding_dimensions,
                caps.get("collection_usd"),
                caps.get("llm_usd"),
                caps.get("items"),
                caps.get("wall_seconds"),
                caps.get("max_iterations"),
            )

    async def set_run_state(
        self, run_id: UUID, state: RunState, *, detail: str | None = None
    ) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                UPDATE research_runs
                   SET state = $2::research_run_state,
                       state_entered_at = now(),
                       stop_detail = COALESCE($3, stop_detail)
                 WHERE id = $1
                """,
                run_id,
                state.value,
                {"detail": detail} if detail else None,
            )

    async def finish_run(
        self,
        run_id: UUID,
        *,
        stop_reason: str,
        stop_detail: str,
        saturation_basis: dict[str, Any],
    ) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                UPDATE research_runs
                   SET stop_reason = $2::research_stop_reason, stop_detail = $3,
                       saturation_basis = $4, stopped_at = now()
                 WHERE id = $1
                """,
                run_id,
                stop_reason_db(stop_reason),
                # stop_detail is jsonb: the structured record of why the run
                # ended, which the §32 view renders alongside the reason.
                {"detail": stop_detail, "saturation": saturation_basis},
                # saturation_basis is a short text label, not the payload.
                str(saturation_basis.get("reason", ""))[:500],
            )

    async def open_iteration(self, run_id: UUID, iteration_no: int, quota: dict[str, Any]) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                INSERT INTO research_iterations
                    (run_id, iteration_no, phase, quota, budget_snapshot, opened_at)
                VALUES ($1, $2, 'allocate', $3, '{}'::jsonb, now())
                ON CONFLICT (run_id, iteration_no) DO UPDATE
                   SET quota = EXCLUDED.quota, opened_at = now()
                """,
                run_id,
                iteration_no,
                quota,
            )
            await conn.execute(
                "UPDATE research_runs SET iteration_no = $2 WHERE id = $1", run_id, iteration_no
            )

    async def set_phase(self, run_id: UUID, iteration_no: int, phase: RunPhase) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                UPDATE research_iterations
                   SET phase = $3::research_iteration_phase, phase_entered_at = now()
                 WHERE run_id = $1 AND iteration_no = $2
                """,
                run_id,
                iteration_no,
                phase.value,
            )

    async def close_iteration(self, run_id: UUID, iteration_no: int, stats: dict[str, Any]) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                UPDATE research_iterations
                   SET closed_at = now(),
                       phase = 'closed',
                       queries_proposed = COALESCE($3, queries_proposed),
                       queries_admitted = COALESCE($4, queries_admitted),
                       queries_rejected = COALESCE($5, queries_rejected),
                       items_admitted   = COALESCE($6, items_admitted),
                       items_analysed   = COALESCE($7, items_analysed),
                       degraded         = COALESCE($8, degraded),
                       degraded_reasons = COALESCE($9, degraded_reasons)
                 WHERE run_id = $1 AND iteration_no = $2
                """,
                run_id,
                iteration_no,
                stats.get("queries_proposed"),
                stats.get("queries_admitted"),
                stats.get("queries_rejected"),
                stats.get("items_admitted"),
                stats.get("items_analysed"),
                stats.get("degraded"),
                stats.get("degraded_reasons"),
            )

    async def record_query(
        self,
        run_id: UUID,
        iteration_no: int,
        query: GeneratedQuery,
        *,
        rejected: str | None = None,
    ) -> UUID | None:
        # A hypothesis-strategy query without its hypothesis id violates a check
        # constraint, and rightly so: brief §25 requires a hypothesis query be
        # traceable to the hypothesis it tests.
        # research_strategy has five values; SamplingStrategy carries a sixth
        # ("seed") for operator-supplied hints, which is brand-level work.
        strategy = {
            "brand": "brand",
            "category": "category",
            "competitor": "competitor",
            "theme": "theme",
            "seed": "brand",
            # A hypothesis query is stored as `theme` because the schema's
            # hypothesis strategy requires a linked hypothesis row, which the
            # MVP does not create yet; storing it as theme keeps the check
            # constraint honest rather than fabricating a hypothesis id.
            "hypothesis": "theme",
        }.get(query.strategy.value, "category")
        triggered_by = {
            "brand": "objective",
            "category": "objective",
            "competitor": "competitor",
            "theme": "discovered_label",
            "seed": "seed",
            "hypothesis": "discovered_label",
        }.get(query.strategy.value, "none")

        async with connection() as conn:
            return await conn.fetchval(
                """
                INSERT INTO research_queries
                    (run_id, iteration_no, strategy, platform, query_text,
                     normalized_text, rationale, triggered_by_kind,
                     is_disconfirming, status)
                VALUES ($1, $2, $3::research_strategy, $4, $5, $6, $7, $8, $9,
                        $10::research_query_status)
                RETURNING id
                """,
                run_id,
                iteration_no,
                strategy,
                db_platform(query.platform) if query.platform else "web",
                query.text,
                normalise_query(query.text),
                query.rationale or "generated by the research agent",
                triggered_by,
                query.is_disconfirming,
                "rejected_guardrail" if rejected else "admitted",
            )

    async def record_content(
        self, run_id: UUID, query_id: UUID, iteration_no: int, item: ContentItem, *, rank: int
    ) -> tuple[UUID, bool]:
        async with transaction() as conn:
            creator_id = await self._upsert_creator(conn, item)
            row = await conn.fetchrow(
                """
                INSERT INTO content_items
                    (platform, platform_item_id, source_url, media_kind, caption,
                     hashtags, language_hint, creator_id, published_at, duration_seconds,
                     media_url, thumbnail_url, views, likes, comments_count, shares, saves,
                     sound_platform_id, sound_title, sound_author, sound_is_original,
                     platform_is_sponsored, platform_is_ad, sponsorship_source,
                     first_seen_query_id, platform_payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                        $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26)
                -- The same video is routinely found by several queries; counting
                -- it twice would inflate every per-item statistic.
                ON CONFLICT (platform, platform_item_id) DO UPDATE
                   SET views = EXCLUDED.views, likes = EXCLUDED.likes,
                       comments_count = EXCLUDED.comments_count,
                       updated_at = now()
                RETURNING id, (xmax = 0) AS was_new
                """,
                db_platform(item.platform),
                item.platform_id,
                item.url,
                item.media_kind.value,
                item.caption,
                item.hashtags,
                item.language_hint,
                creator_id,
                item.published_at,
                item.duration_seconds,
                item.media_url,
                item.thumbnail_url,
                item.engagement.views,
                item.engagement.likes,
                item.engagement.comments,
                item.engagement.shares,
                item.engagement.saves,
                item.sound.id if item.sound else None,
                item.sound.title if item.sound else None,
                item.sound.author if item.sound else None,
                item.sound.original if item.sound else None,
                bool(item.provenance.extras.get("is_sponsored")),
                bool(item.provenance.extras.get("is_ad")),
                item.provenance.extras.get("sponsorship_source"),
                query_id,
                item.raw,
            )
            content_id, was_new = row["id"], row["was_new"]
            await self._record_result(
                conn,
                run_id=run_id,
                query_id=query_id,
                iteration_no=iteration_no,
                item=item,
                rank=rank,
                content_item_id=content_id,
                commerce_item_id=None,
            )
        return content_id, bool(was_new)

    async def record_commerce(
        self, run_id: UUID, query_id: UUID, iteration_no: int, item: CommerceItem, *, rank: int
    ) -> tuple[UUID, bool]:
        async with transaction() as conn:
            seller_id = None
            if item.seller and (item.seller.seller_id or item.seller.name):
                seller_id = await conn.fetchval(
                    """
                    INSERT INTO commerce_sellers
                        (platform, platform_seller_id, name, name_slug, location)
                    VALUES ($1, $2, $3, $4, $5)
                    -- The unique index is on the COALESCE expression, so the
                    -- conflict target must name it exactly: a seller with no
                    -- platform id still has one identity, not many.
                    ON CONFLICT (platform, COALESCE(platform_seller_id, name_slug, 'unknown'))
                    DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    db_platform(item.platform_enum),
                    item.seller.seller_id,
                    item.seller.name,
                    (item.seller.name or "").strip().lower().replace(" ", "-")[:120] or None,
                    item.seller.location,
                )
            row = await conn.fetchrow(
                """
                INSERT INTO commerce_items
                    (platform, platform_item_id, source_url, title_raw, brand_surface_form,
                     brand_confidence, listing_kind, seller_id, currency, price_minor,
                     original_price_minor, discount_pct, rating_avg, rating_count,
                     review_count, sold_count_raw, sold_count_text, sold_semantics,
                     image_url, category_path, raw_payload, collection_provider)
                VALUES ($1, $2, $3, $4, $5, $6, $7::listing_kind, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, $17, $18::sold_semantics, $19, $20,
                        $21, $22)
                ON CONFLICT (platform, platform_item_id) DO UPDATE
                   SET price_minor = EXCLUDED.price_minor,
                       rating_avg = EXCLUDED.rating_avg,
                       review_count = EXCLUDED.review_count,
                       last_seen_at = now()
                RETURNING id, (xmax = 0) AS was_new
                """,
                db_platform(item.platform_enum),
                item.platform_id,
                item.url,
                item.title,
                item.brand_surface_form,
                item.brand_confidence,
                item.listing_kind.value,
                seller_id,
                item.currency,
                # Money is stored in integer minor units; a float price is a
                # rounding error waiting to be aggregated.
                int(item.price * 100) if item.price is not None else None,
                int(item.original_price * 100) if item.original_price is not None else None,
                item.effective_discount,
                item.rating,
                item.review_count,
                item.review_count,
                item.sold_count,
                item.provenance.extras.get("sold_count_raw")
                or item.provenance.extras.get("sales_count_raw"),
                # The platforms disagree about what the number means, so it is
                # never asserted to be a sales figure.
                "unknown",
                item.image_url,
                item.category_path,
                item.raw,
                item.provenance.collection_provider,
            )
            commerce_id, was_new = row["id"], row["was_new"]
            await self._record_result(
                conn,
                run_id=run_id,
                query_id=query_id,
                iteration_no=iteration_no,
                item=item,
                rank=rank,
                content_item_id=None,
                commerce_item_id=commerce_id,
            )
        return commerce_id, bool(was_new)

    async def _record_result(
        self,
        conn,
        *,
        run_id: UUID,
        query_id: UUID,
        iteration_no: int,
        item: Any,
        rank: int,
        content_item_id: UUID | None,
        commerce_item_id: UUID | None,
    ) -> None:
        """One provenance row per (query, item).

        An item discovered by two strategies gets two rows: the strategy is a
        property of the discovering query, not of the item.
        """
        platform = item.platform if content_item_id else item.platform_enum
        await conn.execute(
            """
            INSERT INTO research_results
                (run_id, query_id, iteration_no, platform, provider, collection_method,
                 external_id, url, url_canonical, raw_rank, published_at, admitted,
                 content_item_id, commerce_item_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true, $12, $13)
            ON CONFLICT DO NOTHING
            """,
            run_id,
            query_id,
            iteration_no,
            db_platform(platform),
            item.provenance.collection_provider,
            item.provenance.collection_method.value,
            item.platform_id,
            item.url,
            item.url,
            rank,
            getattr(item, "published_at", None),
            content_item_id,
            commerce_item_id,
        )

    async def _upsert_creator(self, conn, item: ContentItem) -> UUID | None:
        creator = item.creator
        if creator is None or not creator.handle:
            return None
        return await conn.fetchval(
            """
            INSERT INTO creators
                (platform, platform_user_id, handle, handle_norm, id_basis,
                 display_name, follower_count, following_count, content_count,
                 verified, bio, profile_url, avatar_url)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (platform, identity_key) DO UPDATE
               SET follower_count = EXCLUDED.follower_count, updated_at = now()
            RETURNING id
            """,
            db_platform(creator.platform),
            creator.platform_user_id,
            creator.handle,
            creator.handle.lower().strip(),
            "platform_id" if creator.platform_user_id else "handle",
            creator.display_name,
            creator.follower_count,
            creator.following_count,
            creator.content_count,
            creator.verified,
            creator.bio,
            creator.profile_url,
            creator.avatar_url,
        )

    async def record_query_results(
        self,
        query_id: UUID,
        *,
        returned: int,
        admitted: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Write back what a query actually yielded.

        These counters are denormalised onto the query because the research-run
        view reads them per query, and because a query that returned nothing is
        itself a finding about the query -- not an absence of data.
        """
        async with connection() as conn:
            await conn.execute(
                """
                UPDATE research_queries
                   SET results_total    = COALESCE(results_total, 0) + $2,
                       results_admitted = COALESCE(results_admitted, 0) + $3,
                       cost_usd         = COALESCE(cost_usd, 0) + $4,
                       finished_at      = now(),
                       status           = 'succeeded'
                 WHERE id = $1
                """,
                query_id,
                returned,
                admitted,
                cost_usd,
            )

    async def record_coverage(self, run_id: UUID, platform: str, stats: dict[str, Any]) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                INSERT INTO collection_coverage
                    (research_run_id, platform, available, unavailable_reason, queries_run,
                     items_discovered, items_admitted, planned_units, achieved_units)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (research_run_id, platform) DO UPDATE
                   SET available = EXCLUDED.available,
                       unavailable_reason = EXCLUDED.unavailable_reason,
                       queries_run = collection_coverage.queries_run
                                     + COALESCE(EXCLUDED.queries_run, 0),
                       items_discovered = collection_coverage.items_discovered
                                     + COALESCE(EXCLUDED.items_discovered, 0),
                       items_admitted = collection_coverage.items_admitted
                                     + COALESCE(EXCLUDED.items_admitted, 0),
                       updated_at = now()
                """,
                run_id,
                db_platform(platform),
                stats.get("available", True),
                stats.get("unavailable_reason"),
                stats.get("queries_run", 0),
                stats.get("items_discovered", 0),
                stats.get("items_admitted", 0),
                # NOT NULL with a default, so an explicit NULL would be rejected:
                # a platform with no allocation has planned zero, not unknown.
                stats.get("planned_units") or 0,
                stats.get("achieved_units") or 0,
            )

    async def log(
        self,
        run_id: UUID,
        *,
        event_type: str,
        summary: str,
        iteration_no: int | None = None,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        async with transaction() as conn:
            # seq is allocated from the run row so the narrative stays strictly
            # ordered even when phases write concurrently.
            seq = await conn.fetchval(
                "UPDATE research_runs SET log_seq = log_seq + 1 WHERE id = $1 RETURNING log_seq",
                run_id,
            )
            await conn.execute(
                """
                INSERT INTO research_log
                    (run_id, iteration_no, seq, event_type, summary, payload, severity)
                VALUES ($1, $2, $3, $4::research_log_event, $5, $6, $7)
                """,
                run_id,
                iteration_no,
                seq,
                log_event(event_type),
                summary[:2000],
                payload or {},
                severity,
            )

    async def record_spend(
        self, run_id: UUID, *, kind: str, usd: float, units: int, detail: dict[str, Any]
    ) -> None:
        async with connection() as conn:
            await conn.execute(
                """
                INSERT INTO research_budget_ledger
                    (run_id, kind, subsystem, idem_key, usd, qty, provider, detail)
                VALUES ($1, $2::research_budget_kind, $3, $4, $5, $6, $7, $8)
                -- idem_key makes a retried stage safe: spend is recorded once
                -- however many times the phase is re-entered.
                ON CONFLICT (run_id, idem_key) DO NOTHING
                """,
                run_id,
                budget_kind(kind, detail.get("platform")),
                detail.get("platform", "collection"),
                f"{kind}:{detail.get('platform', '')}:{detail.get('query', '')}"[:200],
                usd,
                units,
                detail.get("provider"),
                detail,
            )

    # ── analysis persistence ─────────────────────────────────────────────────

    async def ensure_analysis_config(self) -> UUID:
        """The active analysis configuration, created on first use.

        Analyses are keyed to the config that produced them, so results stay
        comparable instead of silently mixing outputs from different prompt
        versions, frame policies or models.
        """
        from aypo.analysis.media import JPEG_QSCALE, LONG_EDGE, SCORE_FPS
        from aypo.analysis.prompt import PROMPT_VERSION, SYSTEM_PROMPT

        settings = get_settings()
        frame_policy = {
            "long_edge": LONG_EDGE,
            "jpeg_qscale": JPEG_QSCALE,
            "score_fps": SCORE_FPS,
            "selector": "bucketed_scene_max",
        }
        asr_config = {"model": settings.aypo_transcription_model, "language": "auto"}
        frame_hash = hashlib.sha256(json.dumps(frame_policy, sort_keys=True).encode()).hexdigest()[
            :32
        ]
        asr_hash = hashlib.sha256(json.dumps(asr_config, sort_keys=True).encode()).hexdigest()[:32]
        fingerprint = hashlib.sha256(
            f"{PROMPT_VERSION}|{settings.aypo_analysis_model}|{settings.aypo_analysis_effort}"
            f"|{frame_hash}|{asr_hash}|{hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()}".encode()
        ).hexdigest()[:40]

        async with connection() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM analysis_configs WHERE config_fingerprint = $1", fingerprint
            )
            if existing is not None:
                return existing
            return await conn.fetchval(
                """
                INSERT INTO analysis_configs
                    (prompt_version, config_fingerprint, model, effort, max_tokens,
                     frame_policy, frame_policy_hash, asr_config, asr_config_hash,
                     system_prompt, validator_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (config_fingerprint)
                DO UPDATE SET prompt_version = EXCLUDED.prompt_version
                RETURNING id
                """,
                PROMPT_VERSION,
                fingerprint,
                settings.aypo_analysis_model,
                settings.aypo_analysis_effort,
                8000,
                frame_policy,
                frame_hash,
                asr_config,
                asr_hash,
                SYSTEM_PROMPT,
                "validator-v1",
            )

    async def record_analysis(
        self,
        *,
        run_id: UUID,
        content_item_id: UUID,
        record: Any,
        media_fingerprint: str,
        transcript: Any | None = None,
        frames: list[Any] | None = None,
    ) -> UUID:
        """Persist one analysis, its claims, frames and transcript.

        Claims are exploded into their own rows because brief §27's drill-down
        is per claim: a reader follows a finding to the specific observation and
        its anchor, not to a blob of JSON.
        """
        config_id = await self.ensure_analysis_config()
        analysis = record.analysis

        async with transaction() as conn:
            analysis_id = await conn.fetchval(
                """
                INSERT INTO content_analysis
                    (content_item_id, analysis_config_id, research_run_id, media_tier, status,
                     media_fingerprint, midpoint_fraction, payload, summary,
                     evidence_insufficient_for)
                VALUES ($1, $2, $3, $4, 'ok', $5, $6, $7, $8, $9)
                -- Idempotent on (item, config, media): re-running the same
                -- analysis must not create a second row and double every count
                -- computed from it.
                ON CONFLICT (content_item_id, analysis_config_id,
                             COALESCE(media_fingerprint, ''::bpchar))
                DO UPDATE SET payload = EXCLUDED.payload, summary = EXCLUDED.summary
                RETURNING id
                """,
                content_item_id,
                config_id,
                run_id,
                analysis.media_tier.value,
                media_fingerprint[:64],
                record.midpoint_fraction,
                analysis.model_dump(mode="json"),
                analysis.summary,
                analysis.evidence_insufficient_for,
            )

            await conn.execute(
                "DELETE FROM content_analysis_claims WHERE analysis_id = $1", analysis_id
            )
            rows: list[tuple] = []
            for ordinal, label in enumerate(analysis.labels):
                rows.append(
                    (
                        analysis_id,
                        content_item_id,
                        config_id,
                        label.facet.value,
                        "discovered_label",
                        ordinal,
                        label.value,
                        label.basis.value,
                        label.cue,
                        label.anchors,
                        len(label.anchors),
                        1,
                        "published",
                        None,
                    )
                )
            for field_name, sightings in (
                ("visible_brands", analysis.visible_brands),
                ("mentioned_brands", analysis.mentioned_brands),
            ):
                for ordinal, sighting in enumerate(sightings):
                    rows.append(
                        (
                            analysis_id,
                            content_item_id,
                            config_id,
                            field_name,
                            "brand_identity",
                            ordinal,
                            sighting.surface_form,
                            sighting.basis.value,
                            sighting.cue,
                            sighting.anchors,
                            len(sighting.anchors),
                            1,
                            "published",
                            None,
                        )
                    )
            for ordinal, product in enumerate(analysis.visible_products):
                rows.append(
                    (
                        analysis_id,
                        content_item_id,
                        config_id,
                        "visible_products",
                        "product_use",
                        ordinal,
                        product.description,
                        "seen",
                        product.cue,
                        product.anchors,
                        len(product.anchors),
                        1,
                        "published",
                        # Its own column: brief §28's appearance-versus-use
                        # distinction has to be queryable, not parsed back
                        # out of a value string.
                        product.consumption_state.value,
                    )
                )
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO content_analysis_claims
                        (analysis_id, content_item_id, analysis_config_id, field, field_group,
                         ordinal, value, basis, cue, anchors, anchor_count, modality_count,
                         claim_status, consumption_state)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                    rows,
                )

            if transcript is not None:
                await conn.execute(
                    """
                    INSERT INTO content_transcripts
                        (content_item_id, asr_config_hash, asr_model, has_speech,
                         language_detected, segments)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT DO NOTHING
                    """,
                    content_item_id,
                    "auto",
                    get_settings().aypo_transcription_model,
                    transcript.has_speech,
                    transcript.detected_language,
                    [{"start": s.start, "end": s.end, "text": s.text} for s in transcript.segments],
                )
        return analysis_id
