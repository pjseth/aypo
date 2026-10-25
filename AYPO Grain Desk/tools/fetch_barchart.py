"""Replace the generated daily paths with real closes from Barchart OnDemand.

    BARCHART_API_KEY=... py -3.11 tools/fetch_barchart.py
    py -3.11 tools/fetch_barchart.py --dry-run        # show the plan, call nothing
    py -3.11 tools/fetch_barchart.py --only RC,BRN

WHY THIS IS A BUILD STEP AND NOT A FETCH IN THE BROWSER
-------------------------------------------------------
Barchart does publish an API, and it has a free tier. It still cannot be called from this app
at runtime, for three independent reasons, any one of which is fatal:

  1. The key. This app is a static page - `file://` or a static host, no server. A key in
     client JavaScript is a key published to everyone who opens the network tab. Exactly the
     problem flagged for ANTHROPIC_API_KEY in tools/make_env.py, and the same answer: the key
     belongs on the machine that runs the build.
  2. CORS. OnDemand does not send `Access-Control-Allow-Origin` for a browser origin, so the
     request fails before the key is even considered.
  3. The budget. The free tier is of the order of a couple of hundred calls a month. Eleven
     feeds refreshed daily is three hundred and thirty, before anybody opens the page twice.

So the data is pulled here, at build time, and baked into `app/js/ws.js` alongside everything
else - which is the architecture this app already has for every other dataset. Run it as often
as the feed matters; commit the result; the page stays static.

WHAT IT TOUCHES
---------------
Only `history` and `dates` on a feed, plus `barchart_symbol` and `synthetic_history: false` so
every screen stops saying the path is generated. The **monthly anchors the price model reads
are not touched**: a quote is argued against a published print on a stated date, and swapping
that for an intraday close would change what the fair band means. Real daily closes make the
chart real; the model stays where it was.

If the response shape differs from what is parsed below - OnDemand's exact field names are
behind their login and are not reproduced here on faith - the parser says what it got rather
than guessing, and nothing is written. Fix the mapping, re-run.
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WS = ROOT / "app" / "js" / "ws.js"
HOST = os.environ.get("BARCHART_HOST", "https://ondemand.websol.barchart.com")

# Feed key -> the Barchart symbol to pull it from. Where a feed has no exchange-traded
# equivalent the value is None and the generated path is kept, labelled as it already is.
# Continuous front-month symbols use the `*1` / `Y00` conventions; confirm against your own
# OnDemand symbol list, which is what this is deliberately easy to edit for.
SYMBOLS = {
    "RC":      "RM*1",     # ICE robusta coffee, front month
    "BRN":     "CB*1",     # ICE Brent crude
    "SB":      "SB*1",     # ICE sugar no.11
    "FCPO":    "KPO*1",    # Bursa crude palm oil
    "USDPHP":  "^USDPHP",  # spot FX
    "ZW":      "ZW*1",     # CBOT soft red winter wheat
    # No exchange-traded equivalent: assessed indices and private freight assessments.
    "POTEU":   None,
    "KRAFT":   None,
    "BOPP":    None,
    "FRTBULK": None,
    "FRTBOX":  None,
}


def load_ws():
    text = WS.read_text(encoding="utf-8")
    start = text.index("{")
    end = text.rindex("}") + 1
    return text[:start], json.loads(text[start:end]), text[end:]


def fetch(symbol, start_date, key, timeout=30):
    params = {
        "apikey": key,
        "symbol": symbol,
        "type": "daily",
        "startDate": start_date.replace("-", ""),
        "maxRecords": "500",
        "order": "asc",
    }
    url = HOST + "/getHistory.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bars(payload, symbol):
    """Pull (date, close) out of the response, tolerating the field names OnDemand uses.

    Deliberately not strict about which spelling comes back - but loud when none of them do,
    because a silent zero-length series would land in the dataset as a flat line and look
    like a market that did not move.
    """
    status = payload.get("status") or {}
    if status.get("code") not in (200, None):
        raise SystemExit("%s: barchart said %s %s"
                         % (symbol, status.get("code"), status.get("message")))
    rows = payload.get("results")
    if not rows:
        raise SystemExit("%s: no results in the response. Got keys: %s"
                         % (symbol, sorted(payload)))

    date_keys = ("tradingDay", "timestamp", "date", "sessionDate")
    close_keys = ("close", "settlement", "settle", "lastPrice")
    first = rows[0]
    dk = next((k for k in date_keys if k in first), None)
    ck = next((k for k in close_keys if k in first), None)
    if not dk or not ck:
        raise SystemExit(
            "%s: cannot find a date and a close in a row. Row keys: %s\n"
            "Edit the date_keys / close_keys lists in tools/fetch_barchart.py."
            % (symbol, sorted(first)))

    out = []
    for row in rows:
        d = str(row[dk])[:10]
        try:
            c = float(row[ck])
        except (TypeError, ValueError):
            continue
        out.append((d, c))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plan, call nothing")
    ap.add_argument("--only", help="comma-separated feed keys")
    args = ap.parse_args()

    head, payload, tail = load_ws()
    ws = next(w for w in payload["workspaces"] if w["key"] == payload["ui_workspace"])
    feeds = ws["instruments"]
    as_of = payload["as_of"]
    wanted = set(args.only.split(",")) if args.only else None

    plan = []
    for f in feeds:
        sym = SYMBOLS.get(f["key"])
        if wanted and f["key"] not in wanted:
            continue
        plan.append((f, sym))

    print("%-9s %-10s %-26s %s" % ("feed", "symbol", "name", "status"))
    for f, sym in plan:
        print("%-9s %-10s %-26s %s"
              % (f["key"], sym or "-", f["label"][:26],
                 "generated path kept" if not sym else "will pull daily closes"))

    if args.dry_run:
        print("\ndry run: nothing fetched, nothing written.")
        return 0

    key = os.environ.get("BARCHART_API_KEY", "").strip()
    if not key:
        print("\nBARCHART_API_KEY is not set. Nothing fetched.")
        print("The app runs on the generated paths, which every screen labels as generated.")
        return 1

    # One year back from the dataset's as-of, which is the longest window the chart offers.
    start = str(int(as_of[:4]) - 1) + as_of[4:]

    changed = 0
    for f, sym in plan:
        if not sym:
            continue
        try:
            rows = bars(fetch(sym, start, key), sym)
        except SystemExit:
            raise
        except Exception as err:
            print("  %-9s FAILED  %s" % (f["key"], err))
            continue
        if len(rows) < 30:
            print("  %-9s SKIPPED only %d bars came back" % (f["key"], len(rows)))
            continue
        f["dates"] = [d for d, _ in rows]
        f["history"] = [round(c, 4) for _, c in rows]
        f["last"] = round(rows[-1][1], 4)
        f["prev"] = round(rows[-2][1], 4)
        f["change"] = round(rows[-1][1] - rows[-2][1], 4)
        f["change_pct"] = round((rows[-1][1] / rows[-2][1] - 1) * 100, 3) if rows[-2][1] else 0.0
        f["barchart_symbol"] = sym
        f["synthetic_history"] = False
        changed += 1
        print("  %-9s ok      %d bars, %s to %s, last %s"
              % (f["key"], len(rows), rows[0][0], rows[-1][0], f["last"]))

    if not changed:
        print("\nNothing changed; ws.js left alone.")
        return 1

    WS.write_text(head + json.dumps(payload, separators=(",", ":")) + tail, encoding="utf-8")
    print("\nwrote %s — %d feed(s) now carry real closes." % (WS, changed))
    print("The monthly anchors the price model reads were not touched.")
    print("Re-running tools/build_ws.py regenerates the file and undoes this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
