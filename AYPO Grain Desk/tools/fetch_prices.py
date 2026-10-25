"""Pull real daily OHLC for the feeds that have a public market, into a build-time cache.

    py -3.11 tools/fetch_prices.py                       # Yahoo, no key needed
    BARCHART_API_KEY=... py -3.11 tools/fetch_prices.py --provider barchart
    py -3.11 tools/fetch_prices.py --dry-run             # show the plan, call nothing
    py -3.11 tools/fetch_prices.py --clear               # back to the generated paths

Writes `tools/price_cache.json`. `tools/build_ws.py` picks it up on the next run and uses the
real series instead of its generated ones, for the feeds it covers; feeds with no entry keep
their generated path and their "mock" label. Delete the cache and the dataset goes back to
being entirely synthetic.

WHY THIS IS A BUILD STEP AND NOT A FETCH IN THE BROWSER
-------------------------------------------------------
The app is a static page - `file://` or a static host, no server - and a market API cannot be
called from one:

  · a key in client JavaScript is a key published to everyone who opens the network tab
  · neither provider sends an `Access-Control-Allow-Origin` header, so the request fails
    before the key is even considered
  · Barchart's free tier is a couple of hundred calls a month against eleven feeds you would
    want daily

So it is pulled here, on the machine that runs the build, and baked into `app/js/ws.js`
alongside every other dataset. Run it as often as the data matters; commit the result; the
page stays static.

THE TWO PROVIDERS
-----------------
`yahoo`    (default) needs no key and covers the exchange-traded feeds and the FX pairs. The
           endpoint is public but undocumented and unsupported - fine for a build step and a
           demo, not something to put a customer's production reporting on.
`barchart` is the licensed path. It needs an OnDemand key, which this tool will not create and
           cannot guess; set BARCHART_API_KEY and it covers robusta too, which Yahoo does not
           carry. The endpoint and parameters here are confirmed live: without a key it
           answers 401 "API key is missing or not valid" rather than 404.

WHAT IT DOES NOT TOUCH
----------------------
The supplier book. Quotes, premiums and the suppliers themselves stay generated, because they
are a fictional company's. Real prints underneath an illustrative book is the honest shape for
a demo, and every screen keeps saying which half is which.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CACHE = ROOT / "tools" / "price_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BARCHART_HOST = os.environ.get("BARCHART_HOST", "https://ondemand.websol.barchart.com")

# feed key -> (yahoo symbol, barchart symbol, what it is)
# None on both sides means an assessed index or a private freight assessment with no public
# market to pull. Those keep the generated path and keep saying so on screen.
FEEDS = {
    "BRN":     ("BZ=F",  "CB*1",  "Brent crude, front month"),
    "SB":      ("SB=F",  "SB*1",  "ICE sugar no.11"),
    "ZW":      ("ZW=F",  "ZW*1",  "CBOT soft red winter wheat"),
    "FCPO":    ("CPO=F", "KPO*1", "USD Malaysian crude palm oil"),
    "RC":      (None,    "RM*1",  "ICE robusta coffee — Barchart only"),
    "POTEU":   (None,    None,    "assessed flake index, no public market"),
    "KRAFT":   (None,    None,    "assessed containerboard index, no public market"),
    "BOPP":    (None,    None,    "assessed resin index, no public market"),
    "FRTBULK": (None,    None,    "private freight assessment, no public market"),
    "FRTBOX":  (None,    None,    "private freight assessment, no public market"),
}

# The FX table the whole dataset converts through. All three are quoted against the peso, so
# they drop straight in; the cross rates are derived from them.
FX_PAIRS = {
    "USDPHP": "PHP=X",
    "EURPHP": "EURPHP=X",
    "MYRPHP": "MYRPHP=X",
    "EURUSD": "EURUSD=X",
    "USDMYR": "MYR=X",
}


# ------------------------------------------------------------------------ yahoo
def yahoo(symbol, days):
    rng = "2y" if days > 370 else "1y"
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?interval=1d&range=" + rng)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    results = (payload.get("chart") or {}).get("result")
    if not results:
        err = (payload.get("chart") or {}).get("error")
        raise RuntimeError("no result (%s)" % (err or "unknown"))
    res = results[0]
    stamps = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    meta = res.get("meta", {})
    bars = []
    for i, ts in enumerate(stamps):
        o, h, l, c = q.get("open", [])[i], q.get("high", [])[i], q.get("low", [])[i], q.get("close", [])[i]
        # Yahoo pads holidays and the current session with nulls. A null bar is not a flat
        # day; dropping it is the only honest option.
        if None in (o, h, l, c):
            continue
        bars.append([dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
                     round(float(o), 4), round(float(h), 4),
                     round(float(l), 4), round(float(c), 4)])
    bars.sort()
    return bars, {"currency": meta.get("currency"),
                  "name": meta.get("longName") or meta.get("shortName")}


# --------------------------------------------------------------------- barchart
def barchart(symbol, days, key):
    start = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    url = BARCHART_HOST + "/getHistory.json?" + urllib.parse.urlencode({
        "apikey": key, "symbol": symbol, "type": "daily",
        "startDate": start, "maxRecords": "800", "order": "asc",
    })
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    status = payload.get("status") or {}
    if status.get("code") not in (200, None):
        raise RuntimeError("barchart said %s %s" % (status.get("code"), status.get("message")))
    rows = payload.get("results")
    if not rows:
        raise RuntimeError("no results; keys were %s" % sorted(payload))

    # Deliberately tolerant about which spelling comes back, and loud when none of them do:
    # a silently empty series would land in the dataset as a flat line and read as a market
    # that did not move.
    first = rows[0]
    dk = next((k for k in ("tradingDay", "timestamp", "date", "sessionDate") if k in first), None)
    ck = next((k for k in ("close", "settlement", "settle", "lastPrice") if k in first), None)
    if not dk or not ck:
        raise RuntimeError("no date/close in a row; keys were %s" % sorted(first))
    bars = []
    for row in rows:
        try:
            c = float(row[ck])
            o = float(row.get("open", c))
            h = float(row.get("high", c))
            l = float(row.get("low", c))
        except (TypeError, ValueError):
            continue
        bars.append([str(row[dk])[:10], round(o, 4), round(h, 4), round(l, 4), round(c, 4)])
    bars.sort()
    return bars, {"currency": None, "name": symbol}


# ------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("yahoo", "barchart"), default="yahoo")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--only", help="comma-separated feed keys")
    args = ap.parse_args()

    if args.clear:
        CACHE.unlink(missing_ok=True)
        print("cleared %s — the next build goes back to generated paths." % CACHE)
        return 0

    key = os.environ.get("BARCHART_API_KEY", "").strip()
    if args.provider == "barchart" and not key and not args.dry_run:
        print("BARCHART_API_KEY is not set, and this tool will not create an account for you.")
        print("Get a key at barchart.com/ondemand, export it, and re-run.")
        print("Meanwhile `--provider yahoo` needs no key and covers everything but robusta.")
        return 1

    wanted = set(args.only.split(",")) if args.only else None
    plan = []
    for fkey, (ysym, bsym, what) in FEEDS.items():
        if wanted and fkey not in wanted:
            continue
        sym = ysym if args.provider == "yahoo" else bsym
        plan.append((fkey, sym, what))
    fx_plan = [] if args.provider == "barchart" else list(FX_PAIRS.items())

    print("%-9s %-10s %s" % ("feed", "symbol", "what"))
    for fkey, sym, what in plan:
        print("%-9s %-10s %s" % (fkey, sym or "—", what if sym else what + "  (kept generated)"))
    for pair, sym in fx_plan:
        print("%-9s %-10s fx rate" % (pair, sym))

    if args.dry_run:
        print("\ndry run: nothing fetched, nothing written.")
        return 0

    out = {"provider": args.provider, "fetched": dt.date.today().isoformat(),
           "feeds": {}, "fx": {}}
    print()
    for fkey, sym, _what in plan:
        if not sym:
            continue
        try:
            bars, meta = (yahoo(sym, args.days) if args.provider == "yahoo"
                          else barchart(sym, args.days, key))
        except urllib.error.HTTPError as e:
            print("  %-9s FAILED  HTTP %s" % (fkey, e.code)); continue
        except Exception as e:
            print("  %-9s FAILED  %s" % (fkey, e)); continue
        if len(bars) < 60:
            print("  %-9s SKIPPED only %d bars" % (fkey, len(bars))); continue
        out["feeds"][fkey] = {"symbol": sym, "provider": args.provider,
                              "currency": meta.get("currency"), "name": meta.get("name"),
                              "bars": bars}
        print("  %-9s ok      %d bars, %s to %s, last %s %s"
              % (fkey, len(bars), bars[0][0], bars[-1][0], bars[-1][4], meta.get("currency") or ""))

    for pair, sym in fx_plan:
        try:
            bars, _meta = yahoo(sym, args.days)
        except Exception as e:
            print("  %-9s FAILED  %s" % (pair, e)); continue
        if not bars:
            continue
        out["fx"][pair] = {"symbol": sym, "rate": bars[-1][4], "as_of": bars[-1][0],
                           "bars": bars}
        print("  %-9s ok      %s on %s" % (pair, bars[-1][4], bars[-1][0]))

    if not out["feeds"] and not out["fx"]:
        print("\nNothing came back; cache left alone.")
        return 1

    CACHE.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print("\nwrote %s  (%d feeds, %d fx pairs)"
          % (CACHE, len(out["feeds"]), len(out["fx"])))
    print("Now run: py -3.11 tools/build_ws.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
