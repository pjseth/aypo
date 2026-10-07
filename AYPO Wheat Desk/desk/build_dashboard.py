"""Build the single-source-of-truth dashboard.

Reads the market data pulled by `desk.market`, generates the synthetic
communications / intelligence / shipment layers, stores everything in the
database, then writes one self-contained HTML file.

    python -m desk.market          # real futures, FX, forward curve
    python -m desk.build_dashboard # everything else + the page
"""
import collections
import datetime as dt
import json

from . import comms, intel, ops
from .store import ROOT, connect, load_config, upsert


def load_prices(con):
    prices = collections.defaultdict(list)
    for r in con.execute("SELECT symbol, d, close FROM prices ORDER BY d"):
        prices[r["symbol"]].append((r["d"], r["close"]))
    return prices


def latest_curve(con, cfg):
    run = con.execute("SELECT MAX(run) FROM curve").fetchone()[0]
    if not run:
        return {}
    order = {m["code"]: i for i, m in enumerate(cfg["market"]["curve_months"])}
    bpt = cfg["market"]["bushels_per_tonne"]
    out = collections.defaultdict(list)
    for r in con.execute("SELECT series, code, label, symbol, close FROM curve WHERE run=?",
                         (run,)):
        out[r["series"]].append({
            "code": r["code"], "label": r["label"], "symbol": r["symbol"],
            "c_bu": r["close"],
            "usd_mt": round(r["close"] / 100 * bpt, 2) if r["close"] else None,
        })
    for v in out.values():
        v.sort(key=lambda x: order.get(x["code"], 99))
    return out


def benchmark_tiles(cfg, prices):
    tiles = []
    for b in cfg["market"]["benchmarks"]:
        hist = prices.get(b["symbol"], [])
        if not hist:
            continue
        last, prev = hist[-1][1], (hist[-2][1] if len(hist) > 1 else None)
        tiles.append({
            "key": b["key"], "label": b["label"], "symbol": b["symbol"],
            "unit": b["unit"], "role": b["role"],
            "last": round(last, 4), "prev": round(prev, 4) if prev else None,
            "delta_pct": round(100 * (last - prev) / prev, 2) if prev else None,
            "spark": [round(c, 4) for _, c in hist[-60:]],
            "as_of": hist[-1][0],
        })
    return tiles


def main():
    cfg = load_config()
    con = connect(cfg)
    prices = load_prices(con)
    if not prices:
        raise SystemExit("no market data -- run `python -m desk.market` first")

    messages, quotes = comms.generate(cfg, prices)
    notes = intel.generate(cfg)
    shipments, documents = ops.generate(cfg, quotes)

    upsert(con, "messages", messages, ["id"])
    upsert(con, "quotes", quotes, ["id"])
    upsert(con, "intel", notes, ["id"])
    upsert(con, "shipments", shipments, ["id"])
    upsert(con, "documents", documents, ["id"])

    bpt = cfg["market"]["bushels_per_tonne"]
    sym = {b["key"]: b["symbol"] for b in cfg["market"]["benchmarks"]}
    data = {
        "as_of": dt.date.today().isoformat(),
        "built_at": dt.datetime.now().isoformat(timespec="minutes"),
        "client": cfg["client"],
        "bushels_per_tonne": bpt,
        "default_range_days": cfg["dashboard"]["default_range_days"],
        "benchmarks": benchmark_tiles(cfg, prices),
        "history": {
            "srw": [[d, round(c, 2)] for d, c in prices.get(sym["srw"], [])],
            "hrw": [[d, round(c, 2)] for d, c in prices.get(sym["hrw"], [])],
        },
        "curve": latest_curve(con, cfg),
        "classes": cfg["classes"],
        "origins": [o["label"] for o in cfg["origins"]],
        "suppliers": [s["name"] for s in cfg["suppliers"]],
        "quotes": quotes,
        "messages": messages,
        "intel": notes,
        "shipments": shipments,
        "documents": documents,
        "notes": [
            "Futures, FX and the forward curve are live market data (Yahoo Finance "
            "chart API, daily settlements).",
            "Supplier messages, market-intelligence notes, vessels and documents are "
            "synthetic stand-ins with the shape of the real thing -- the parser that "
            "reads them is real and runs on the message text alone.",
            "MGEX Minneapolis spring wheat has no feed on this source, so Hard Red "
            "Spring and Canada Western Red Spring are quoted against Chicago SRW. "
            "Western White has no futures contract at all; it trades on basis.",
            "A reconciliation gap is the stated flat price minus "
            "(futures + basis + freight & insurance + stated margin). A positive gap "
            "is margin the supplier did not itemise.",
            "Quote confidence is the share of the seven fields the desk needs that the "
            "parser recovered from the message text.",
        ],
    }

    counts = {
        "messages": len(messages), "quotes": len(quotes), "intel": len(notes),
        "shipments": len(shipments), "documents": len(documents),
        "price_rows": sum(len(v) for v in prices.values()),
    }

    tpl = (ROOT / "desk" / "dashboard_template.html").read_text(encoding="utf-8")
    out = ROOT / cfg["paths"]["dashboard"]
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, separators=(",", ":"), default=str)
    out.write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")

    print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1024:,.0f} KB)")
    for k, v in counts.items():
        print(f"  {k:<12} {v:>6,}")
    need = sum(1 for q in quotes if q["confidence"] < 1)
    gap = sum(1 for q in quotes if (q["reconcile_gap"] or 0) > 0.75)
    print(f"  {'need review':<12} {need:>6,}\n  {'unitemised':<12} {gap:>6,}")


if __name__ == "__main__":
    main()
