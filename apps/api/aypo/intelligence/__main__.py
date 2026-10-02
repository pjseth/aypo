"""Run the intelligence pipeline over an analysed corpus.

    uv run python -m aypo.intelligence --run <run-id>
    uv run python -m aypo.intelligence            # the whole corpus

Separate from the research loop on purpose: clustering is only meaningful over
a corpus, and re-clustering after every collected item would mostly measure its
own instability.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from aypo.db.pool import close_pool, fetchrow
from aypo.intelligence.pipeline import run_pipeline
from aypo.logging import configure_logging


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aypo.intelligence")
    parser.add_argument("--run", default=None, help="research run id; omit for the whole corpus")
    parser.add_argument("--no-label", action="store_true", help="cluster without labelling")
    args = parser.parse_args(argv)
    configure_logging()

    run_id: UUID | None = None
    if args.run:
        run_id = UUID(args.run)
    else:
        latest = await fetchrow("SELECT id FROM research_runs ORDER BY created_at DESC LIMIT 1")
        run_id = latest["id"] if latest else None

    try:
        result = await run_pipeline(run_id, label=not args.no_label)
    finally:
        await close_pool()

    if not result.ran:
        print(f"skipped: {result.skipped_reason}")
        return 1

    print(f"analysed   : {result.analysed}")
    print(f"embedded   : {result.embedded}")
    print(f"clusters   : {result.n_clusters}")
    print(f"labelled   : {result.labelled} coherent, {result.incoherent} incoherent")
    print(f"noise      : {result.noise_fraction:.1%} of items unclustered")
    print(f"spend      : ${result.ledger.total_usd:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
