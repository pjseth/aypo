"""Start a research run from the command line.

    uv run python -m aypo.research --brand "Brand A" --terms "category x,category y"

Deliberately explicit about cost: the plan and its estimated spend are printed
before anything is collected, and --dry-run exercises the whole control flow
without spending provider budget at all.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aypo.collectors.registry import build_collectors, capability_map
from aypo.config import get_settings
from aypo.db.pool import close_pool
from aypo.db.run_store import PostgresRunStore
from aypo.logging import configure_logging
from aypo.research.budget import Budget
from aypo.research.orchestrator import Orchestrator, ResearchObjective


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aypo.research", description="Run a research cycle.")
    parser.add_argument("--brand", required=True, help="the research subject")
    parser.add_argument(
        "--terms",
        required=True,
        help="comma-separated category words describing what is being researched",
    )
    parser.add_argument("--market", default="PH")
    parser.add_argument("--languages", default="en,tl")
    parser.add_argument("--objective", default="")
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument(
        "--collection-usd",
        type=float,
        default=5.0,
        help="ceiling for managed-collection spend this run",
    )
    parser.add_argument("--llm-usd", type=float, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="exercise the loop without collecting or analysing anything",
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    settings = get_settings()

    objective = ResearchObjective(
        subject_brand=args.brand,
        category_terms=[t.strip() for t in args.terms.split(",") if t.strip()],
        market_code=args.market,
        language_codes=[c.strip() for c in args.languages.split(",") if c.strip()],
        objective=args.objective,
        competitor_seeds=settings.competitor_seeds,
    )

    budget = Budget.from_settings(max_collection_usd=args.collection_usd)
    if args.iterations is not None:
        budget.max_iterations = args.iterations
    if args.max_items is not None:
        budget.max_items = args.max_items
    if args.llm_usd is not None:
        budget.max_llm_usd = args.llm_usd

    await budget.refresh_collection_usage()
    _, availability = build_collectors()
    plan = budget.plan(capability_map(availability), iteration=0)

    print(f"subject    : {objective.subject_brand}  ({objective.market_code})")
    print(f"category   : {', '.join(objective.category_terms)}")
    print(
        f"sources    : {', '.join(a.platform.value for a in availability if a.available) or 'none'}"
    )
    for a in availability:
        if not a.available:
            print(f"   skipped : {a.platform.value} — {a.reason}")
    print(f"plan       : {plan.describe()}")
    print(
        f"budget     : collection ${budget.usable_collection_usd:.2f} usable "
        f"(${budget.collection_usd_now:.4f} of ${budget.max_collection_usd:.2f} already used "
        f"this cycle), LLM ${budget.max_llm_usd:.2f}, {budget.max_iterations} iterations"
    )
    if args.dry_run:
        print("mode       : DRY RUN — nothing will be collected or analysed")

    if not args.yes and not args.dry_run:
        # Collection and analysis both cost real money, so a run is confirmed
        # unless the caller explicitly opts out of the prompt.
        # input() blocks the event loop; the pool is already open by now.
        reply = (await asyncio.to_thread(input, "\nproceed? [y/N] ")).strip().lower()
        if reply not in ("y", "yes"):
            print("aborted")
            return 1

    orchestrator = Orchestrator(
        PostgresRunStore(),
        objective,
        budget=budget,
        analysis_concurrency=args.concurrency,
        dry_run=args.dry_run,
    )
    try:
        outcome = await orchestrator.run()
    finally:
        await close_pool()

    print(f"\nrun {outcome.run_id}")
    print(f"stopped    : {outcome.stop_reason.value} — {outcome.stop_detail}")
    for it in outcome.iterations:
        print(
            f"  iter {it.iteration_no}: {it.queries_admitted} queries, "
            f"{it.items_collected} items ({it.items_new} new), "
            f"{it.items_analysed} analysed, {it.analysis_failures} failed, "
            f"{it.labels_discovered} labels, {it.brands_observed} brands"
        )
        for reason in it.degraded:
            print(f"     degraded: {reason}")
    print(f"spend      : {outcome.budget}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
