"""Forward-only SQL migration runner.

Migrations are plain .sql files in supabase/migrations, applied in filename
order inside a transaction each, and recorded with a content hash so an edited
migration is detected rather than silently diverging.

Run with:  uv run python -m aypo.db.migrate [--status]
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import asyncpg

from aypo.config import get_settings

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS _aypo_migrations (
    filename    text PRIMARY KEY,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def migrations_dir() -> Path:
    return get_settings().repo_root / "supabase" / "migrations"


def discover() -> list[Path]:
    return sorted(migrations_dir().glob("*.sql"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def apply_all(*, dry_run: bool = False) -> dict[str, list[str]]:
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    applied: list[str] = []
    skipped: list[str] = []
    drifted: list[str] = []
    try:
        await conn.execute(_BOOTSTRAP)
        recorded = {
            row["filename"]: row["sha256"]
            for row in await conn.fetch("SELECT filename, sha256 FROM _aypo_migrations")
        }
        for path in discover():
            digest = _digest(path)
            if path.name in recorded:
                if recorded[path.name] != digest:
                    drifted.append(path.name)
                else:
                    skipped.append(path.name)
                continue
            if dry_run:
                applied.append(path.name)
                continue
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _aypo_migrations (filename, sha256) VALUES ($1, $2)",
                    path.name,
                    digest,
                )
            applied.append(path.name)
    finally:
        await conn.close()
    return {"applied": applied, "skipped": skipped, "drifted": drifted}


async def _main() -> int:
    dry = "--status" in sys.argv or "--dry-run" in sys.argv
    found = discover()
    if not found:
        print(f"no migrations found in {migrations_dir()}")
        return 1
    result = await apply_all(dry_run=dry)
    verb = "would apply" if dry else "applied"
    for name in result["applied"]:
        print(f"  {verb}: {name}")
    for name in result["skipped"]:
        print(f"  already applied: {name}")
    for name in result["drifted"]:
        print(f"  WARNING changed since it was applied: {name}")
    if not result["applied"]:
        print("database is up to date")
    return 2 if result["drifted"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
