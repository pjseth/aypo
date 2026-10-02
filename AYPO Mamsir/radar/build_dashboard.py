"""Build the self-contained snack launch dashboard (dashboard/index.html) from data/radar.db.

Usage:  python -m radar.build_dashboard
"""
import json
import os
import sqlite3
from collections import Counter
from datetime import date, datetime

from radar.classify import APPLICATION_KIND, product_key, tag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "radar", "dashboard_template.html")


def load_config():
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


def months_before(d, months):
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, 28)).isoformat()


def build_products(con):
    regs, products = [], {}
    rows = con.execute(
        """SELECT account_code, product_name, brand_name, risk_type, company_name,
                  issued_on, valid_until, application_type FROM food_records"""
    ).fetchall()
    for code, pname, bname, risk, company, issued, valid, app in rows:
        t = tag(pname, bname, risk, company)
        if not t["is_snack"] or not issued:
            continue
        kind = APPLICATION_KIND.get(app, app or "Unknown")
        key = product_key(t["company_group"], bname, pname)
        regs.append({
            "d": issued, "k": kind, "g": t["company_group"], "b": bname or "", "p": pname or "",
            "t": t["snack_type"], "c": code, "ct": t["company_type"], "key": key,
        })
        prod = products.setdefault(key, {
            "rows": [], "g": t["company_group"], "ct": t["company_type"], "b": bname or "",
            "types": Counter(), "f": set(), "cl": set(), "pack": set(),
        })
        prod["rows"].append((issued, kind, pname or "", valid))
        prod["types"][t["snack_type"]] += 1
        prod["f"].update(t["flavours"])
        prod["cl"].update(t["claims"])
        if t["pack_g"]:
            prod["pack"].add(t["pack_g"])

    out = []
    for key, prod in products.items():
        prod["rows"].sort()
        first_date, first_kind, _, _ = prod["rows"][0]
        last = prod["rows"][-1]
        status = {"New": "New", "Re-application": "Re-entry"}.get(first_kind, "Existing")
        cancel = [r[0] for r in prod["rows"] if r[1] == "Cancellation"]
        out.append({
            "d": first_date, "s": status, "g": prod["g"], "ct": prod["ct"], "b": prod["b"],
            "p": last[2], "t": prod["types"].most_common(1)[0][0], "f": sorted(prod["f"]),
            "cl": sorted(prod["cl"]), "n": len(prod["rows"]), "ld": last[0],
            "x": cancel[-1] if cancel else None, "v": max((r[3] or "") for r in prod["rows"]) or None,
        })
    regs.sort(key=lambda r: r["d"], reverse=True)
    for r in regs:
        r.pop("key")
    return out, regs


def pct(a, b):
    return None if not b else round(100.0 * (a - b) / b)


def insights(products, as_of, client, watch, months=12):
    """Rule-based leads for the client, comparing the last N months with the N before."""
    a0, b0 = months_before(as_of, months), months_before(as_of, 2 * months)
    new = [p for p in products if p["s"] == "New"]
    cur = [p for p in new if p["d"] >= a0]
    prev = [p for p in new if b0 <= p["d"] < a0]
    found = []

    found.append({
        "kind": "Market",
        "title": f"{len(cur)} new snack products registered in the last {months} months",
        "body": f"{len(prev)} in the {months} months before ({pct(len(cur), len(prev)):+d}%)."
        if prev else "No comparison period available.",
    })

    by_group_c, by_group_p = Counter(p["g"] for p in cur), Counter(p["g"] for p in prev)
    movers = [(g, by_group_c[g], by_group_p[g]) for g in watch if by_group_c[g] >= 3]
    movers.sort(key=lambda x: (x[1] - x[2], x[1]), reverse=True)
    if movers:
        g, n, before = movers[0]
        names = [p["p"] for p in cur if p["g"] == g][:3]
        found.append({
            "kind": "Threat" if n > before else "Competitor",
            "title": f"{g}: {n} new snack products in {months} months (was {before})",
            "body": "Examples: " + "; ".join(n_.title() for n_ in names) + ".",
        })

    def momentum(field, multi=False, minimum=8):
        c, p_ = Counter(), Counter()
        for p in cur:
            for v in (p[field] if multi else [p[field]]):
                c[v] += 1
        for p in prev:
            for v in (p[field] if multi else [p[field]]):
                p_[v] += 1
        rows = [(k, c[k], p_[k]) for k in c if c[k] >= minimum and p_[k]]
        rows.sort(key=lambda x: x[1] / x[2], reverse=True)
        return rows

    client_recent = [p for p in products if p["g"] == client and p["d"] >= b0]
    gaps = []
    for flav, n, before in momentum("f", multi=True, minimum=6):
        mine = sum(1 for p in client_recent if flav in p["f"])
        rivals = Counter(p["g"] for p in cur if flav in p["f"] and p["g"] in watch)
        if mine <= 1 and n > before:
            gaps.append((sum(rivals.values()), flav, n, before, mine, rivals))
    gaps.sort(key=lambda x: (x[0], x[2] / max(x[3], 1)), reverse=True)
    for n_rivals, flav, n, before, mine, rivals in gaps[:2]:
        who = ", ".join(f"{g} ({k})" for g, k in rivals.most_common(3)) if rivals else "mostly smaller makers and importers"
        found.append({
            "kind": "Gap",
            "title": f"{flav} is rising ({before} → {n} new products); {client} has {mine} in 24 months",
            "body": f"Main competitors in it: {who}.",
        })
    for typ, n, before in momentum("t", minimum=10)[:2]:
        if n > before:
            mine = sum(1 for p in client_recent if p["t"] == typ)
            found.append({
                "kind": "Trend",
                "title": f"{typ}: {n} new products, up from {before}",
                "body": f"{client} has {mine} new {typ.lower()} registrations in the last 24 months.",
            })

    imp_c = sum(1 for p in cur if p["ct"] == "Importer / retailer")
    imp_p = sum(1 for p in prev if p["ct"] == "Importer / retailer")
    if cur and prev:
        found.append({
            "kind": "Imports",
            "title": f"Importers and retailers filed {round(100 * imp_c / len(cur))}% of new snack products",
            "body": f"Was {round(100 * imp_p / len(prev))}% in the prior {months} months. "
                    "Mostly imported brands registered by supermarkets, duty-free shops and trading companies.",
        })

    exits = Counter(p["g"] for p in products if p["x"] and p["x"] >= a0 and p["g"] != client)
    if sum(exits.values()) >= 3:
        g, n = exits.most_common(1)[0]
        found.append({
            "kind": "Opening",
            "title": f"{sum(exits.values())} competitor snack registrations asked to be cancelled",
            "body": f"Most from {g} ({n}). Cancelled products can mean shelf space and shoppers to win.",
        })
    return found


def main():
    config = load_config()
    con = sqlite3.connect(os.path.join(ROOT, config["paths"]["db"]))
    run = con.execute("SELECT run_id, finished_at FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    as_of = datetime.strptime(run[0], "%Y-%m-%d").date() if run else date.today()
    n_food = con.execute("SELECT COUNT(*) FROM food_records").fetchone()[0]

    products, regs = build_products(con)
    client = config["client"]["group"]
    watch = config["client"]["watchlist_groups"]
    new_watch = Counter(p["g"] for p in products if p["s"] == "New" and p["d"] >= months_before(as_of, 24))
    highlight = [client] + sorted(watch, key=lambda g: new_watch[g], reverse=True)[:4]

    payload = {
        "as_of": as_of.isoformat(),
        "run_status": "complete" if run and run[1] else "partial (pull still running)",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_food_records": n_food,
        "n_snack_regs": len(regs),
        "client": client,
        "client_name": config["client"]["name"],
        "highlight": highlight,
        "history_start": config["dashboard"]["history_start"],
        "products": products,
        "regs": regs,
        "insights": insights(products, as_of, client, watch),
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", data)
    out = os.path.join(ROOT, config["paths"]["dashboard"])
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out}: {len(products)} snack products, {len(regs)} registrations, "
          f"{len(payload['insights'])} insights, highlight={highlight}")


if __name__ == "__main__":
    main()
