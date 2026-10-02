"""Polite, resumable pull of every food registration from the FDA Philippines
verification portal (public search API).

The API has no pagination, so registrations are partitioned by registration-number
prefix. A prefix that returns many rows is also split into its ten children, which
both keeps responses small and checks that no silent result cap is truncating data.

Raw responses (food records only) are kept gzipped under data/raw/<run_id>/.

Usage:  python -m radar.collect [--run-id YYYY-MM-DD]
"""
import argparse
import gzip
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone

import requests

from radar import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch(session, cfg, prefix):
    """Return (status, food_rows, seconds) with retries and backoff."""
    last_err = None
    for attempt in range(cfg["max_retries"]):
        t0 = time.time()
        try:
            resp = session.get(
                cfg["search_url"], params={"q": prefix}, timeout=cfg["timeout_seconds"]
            )
            secs = time.time() - t0
            if resp.status_code == 200:
                data = resp.json()
                food = data.get("fdafoodproducts", []) or []
                return 200, food, secs
            last_err = f"HTTP {resp.status_code}"
            if resp.status_code < 500 and resp.status_code != 429:
                return resp.status_code, None, secs
        except (requests.RequestException, ValueError) as exc:
            last_err = repr(exc)[:300]
        wait = cfg["sleep_seconds"] * (3 ** (attempt + 1))
        log(f"  {prefix}: {last_err}; retrying in {wait}s")
        time.sleep(wait)
    return None, None, 0.0


def run(run_id=None):
    config = load_config()
    cfg = config["fda"]
    run_id = run_id or datetime.now().strftime("%Y-%m-%d")
    raw_dir = os.path.join(ROOT, config["paths"]["raw_dir"], run_id)
    os.makedirs(raw_dir, exist_ok=True)
    con = store.connect(os.path.join(ROOT, config["paths"]["db"]))
    con.execute(
        "INSERT OR IGNORE INTO runs (run_id, started_at, status) VALUES (?, ?, 'running')",
        (run_id, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()

    done = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT prefix, split FROM queries WHERE run_id = ? AND http_status = 200", (run_id,)
        )
    }
    queue = deque(cfg["start_prefixes"])
    session = requests.Session()
    session.headers["User-Agent"] = cfg["user_agent"]
    totals = {"new": 0, "changed": 0, "unchanged": 0}

    while queue:
        prefix = queue.popleft()
        if prefix in done:
            if done[prefix]:
                queue.extend(prefix + d for d in "0123456789")
            continue

        status, food, secs = fetch(session, cfg, prefix)
        split = 0
        error = None
        n_rows = n_prefix = 0
        if status == 200:
            n_rows = len(food)
            mine = [r for r in food if str(r.get("ACCOUNTCODE", "")).strip().startswith(prefix)]
            n_prefix = len(mine)
            with gzip.open(os.path.join(raw_dir, f"{prefix}.json.gz"), "wt", encoding="utf-8") as f:
                json.dump({"prefix": prefix, "fetched_at": datetime.now(timezone.utc).isoformat(),
                           "fdafoodproducts": food}, f, ensure_ascii=False)
            new, changed, unchanged = store.upsert(con, run_id, mine)
            totals["new"] += new
            totals["changed"] += changed
            totals["unchanged"] += unchanged
            if n_prefix >= cfg["split_threshold"] and len(prefix) < cfg["max_prefix_len"]:
                split = 1
                queue.extend(prefix + d for d in "0123456789")
            log(f"{prefix}: {n_prefix} records ({secs:.0f}s){' -> split' if split else ''} "
                f"| new {new}, changed {changed}")
        else:
            error = f"status={status}"
            if len(prefix) < cfg["max_prefix_len"]:
                split = 1
                queue.extend(prefix + d for d in "0123456789")
            log(f"{prefix}: FAILED ({error}){' -> split' if split else ''}")

        con.execute(
            "INSERT OR REPLACE INTO queries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, prefix, status, n_rows, n_prefix, secs, split, error),
        )
        con.commit()
        time.sleep(cfg["sleep_seconds"])

    n = con.execute("SELECT COUNT(*) FROM food_records WHERE last_seen_run = ?", (run_id,)).fetchone()[0]
    con.execute(
        "UPDATE runs SET finished_at = ?, status = 'done', n_records = ? WHERE run_id = ?",
        (datetime.now(timezone.utc).isoformat(), n, run_id),
    )
    con.commit()
    log(f"Run {run_id} done: {n} records seen | {totals}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    args = parser.parse_args()
    sys.exit(run(args.run_id))
