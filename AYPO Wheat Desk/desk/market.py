"""Pull real wheat futures, freight proxy and FX into the desk database.

Source is the public Yahoo Finance chart endpoint: daily settlements for each
benchmark, plus the individual contract months that make up the forward curve.
Only the standard library is used, and requests are paced.

    python -m desk.market
"""
import datetime as dt
import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .store import ROOT, connect, load_config, upsert


def fetch(cfg, symbol, rng):
    url = f"{cfg['market']['chart_url']}{urllib.parse.quote(symbol)}?range={rng}&interval=1d"
    req = urllib.request.Request(url, headers={
        "User-Agent": cfg["market"]["user_agent"],
        "Accept": "application/json",
    })
    last = None
    for attempt in range(cfg["market"]["max_retries"]):
        try:
            with urllib.request.urlopen(req, timeout=cfg["market"]["timeout_seconds"]) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    print(f"  ! {symbol}: {last}", file=sys.stderr)
    return None


def series_from(payload):
    """-> (list[(date, close)], meta) or (None, None) if the symbol has no data."""
    result = (payload or {}).get("chart", {}).get("result")
    if not result:
        return None, None
    r = result[0]
    stamps = r.get("timestamp") or []
    closes = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    rows = [(dt.datetime.utcfromtimestamp(t).date().isoformat(), c)
            for t, c in zip(stamps, closes) if c is not None]
    return rows, r.get("meta", {})


def save_raw(cfg, run, name, payload):
    out = ROOT / cfg["paths"]["raw_dir"] / run
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / f"{name}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)


def main():
    cfg = load_config()
    con = connect(cfg)
    run = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    con.execute("INSERT INTO runs (run, started, note) VALUES (?,?,?)",
                (run, dt.datetime.now().isoformat(timespec="seconds"), "market pull"))
    con.commit()
    pace = cfg["market"]["sleep_seconds"]

    print(f"run {run}")
    print("benchmarks")
    for b in cfg["market"]["benchmarks"]:
        payload = fetch(cfg, b["symbol"], cfg["market"]["history_range"])
        rows, meta = series_from(payload)
        if rows is None:
            print(f"  {b['label']:<22} no data ({b['symbol']})")
            time.sleep(pace)
            continue
        save_raw(cfg, run, b["key"], payload)
        upsert(con, "prices",
               [{"symbol": b["symbol"], "d": d, "close": c} for d, c in rows],
               ["symbol", "d"])
        print(f"  {b['label']:<22} {len(rows):>4} days  last {rows[-1][1]:>10,.2f} {b['unit']}")
        time.sleep(pace)

    print("forward curve")
    curve_rows = []
    for series, spec in cfg["market"]["curve"].items():
        for m in cfg["market"]["curve_months"]:
            symbol = f"{spec['prefix']}{m['code']}{spec['suffix']}"
            payload = fetch(cfg, symbol, "5d")
            rows, meta = series_from(payload)
            close = (meta or {}).get("regularMarketPrice")
            if close is None and rows:
                close = rows[-1][1]
            curve_rows.append({"run": run, "series": series, "code": m["code"],
                               "label": m["label"], "symbol": symbol, "close": close})
            print(f"  {symbol:<14} {m['label']:<8} "
                  + (f"{close:>9,.2f}" if close is not None else "     n/a"))
            time.sleep(pace)
    upsert(con, "curve", curve_rows, ["run", "series", "code"])

    con.execute("UPDATE runs SET finished=? WHERE run=?",
                (dt.datetime.now().isoformat(timespec="seconds"), run))
    con.commit()
    print(f"\ndone: {con.execute('select count(*) from prices').fetchone()[0]:,} price rows, "
          f"{len(curve_rows)} curve points")


if __name__ == "__main__":
    main()
