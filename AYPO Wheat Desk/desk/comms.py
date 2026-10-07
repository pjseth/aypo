"""Supplier communications: synthetic inbound messages, and a real parser.

Two halves that deliberately do not talk to each other:

  generate()      writes free-text messages in the five shapes suppliers
                  actually send -- formal offer tables, chatty prose, WhatsApp
                  shorthand, a forwarded chain with the number buried, and a
                  LinkedIn DM. Synthetic stand-ins until the real mailbox is
                  wired up.

  parse_message() reads a message body as text and extracts the quote. It gets
                  none of the values the message was written from, so what it
                  recovers is what a parser would recover from Pavin's inbox.

Every quote carries a confidence, the fields it could not find, and a
reconciliation gap: stated flat price minus (futures + basis + freight +
margin). A non-zero gap is margin the supplier did not itemise.
"""
import calendar
import datetime as dt
import hashlib
import random
import re

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NO = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}

# What a quote must have before the desk can act on it.
CORE_FIELDS = ["flat_usd_mt", "futures_c_bu", "basis_c_bu", "freight_usd_mt",
               "wheat_class", "protein", "tonnes"]

# ---------------------------------------------------------------- extraction --

NUM = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
REF = r"(?:ZW|KE|KW|MWE|MW)[FGHJKMNQUVXZ]\d{1,2}|\bW[FGHJKMNQUVXZ]\d{1,2}\b"

# Plausibility band per field. A match outside its band is discarded and the
# search continues -- without this, "USD 38.50 /MT" (freight) is read as the
# flat price whenever the real flat is absent, which is the one parse error
# that would actually cost money.
BANDS = {
    "flat_usd_mt": (150, 900),
    "futures_c_bu": (300, 1500),
    "basis_c_bu": (0, 500),
    "freight_usd_mt": (5, 120),
    "margin_usd_mt": (0, 40),
    "protein": (7, 16),
    "tonnes": (1000, 80000),
}

# Ordered alternatives per field: first in-band match wins, so the most
# explicit phrasing is tried before the loosest.
PATTERNS = {
    "flat_usd_mt": [
        rf"flat\s*(?:price)?\s*[:=]?\s*(?:USD|US\$|\$)?\s*{NUM}",
        # Loose form, but only where CIF/CFR follows on the same line: the
        # flat price is the one quoted on delivered terms.
        rf"(?:USD|US\$|\$)\s*{NUM}\s*/?\s*(?:MT|mt|per\s+MT)\b(?=[^\n]{{0,40}}(?:CIF|CFR|C&F))",
        # \b matters: without it "lands?" matches inside "Portland".
        rf"\b(?:lands?|landed|works?\s+out)\s+(?:you\s+)?(?:at|around|about)\s*(?:USD|\$)?\s*{NUM}",
    ],
    "futures_c_bu": [
        rf"fut(?:ures)?\s*(?:ref(?:erence)?)?\s*[:=]?\s*{NUM}",
        rf"{NUM}\s*c/bu\s*\((?:{REF})",
    ],
    "basis_c_bu": [
        rf"basis\s*[:=]?\s*(?:{REF})?\s*\+?\s*{NUM}",
        rf"\+\s*{NUM}\s*(?:c/bu)?\s*(?:over|vs)?\s*(?:{REF})",
        rf"{NUM}\s*(?:c/bu\s*)?over\s*(?:{REF})",
    ],
    "freight_usd_mt": [
        rf"(?:freight|frt)[^:\n]*[:=]\s*(?:USD|\$)?\s*{NUM}",
        rf"(?:freight|frt)\s*(?:&|and)?\s*(?:ins\.?|insurance)?\s*(?:at|of)?\s*[:=]?\s*(?:USD|\$)?\s*{NUM}",
    ],
    "margin_usd_mt": [
        rf"(?:our\s+)?(?:margin|mgn)\s*(?:of|at)?\s*[:=]?\s*(?:USD|\$)?\s*{NUM}",
    ],
    "protein": [
        rf"{NUM}\s*(?:%|pct)",
    ],
    "tonnes": [
        rf"(?:quantity|qty)\s*[:=]?\s*{NUM}\s*(?:MT|mt)",
        rf"{NUM}\s*(?:MT|mt)\b(?!\s*/)",
    ],
}


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _first(text, pats, band=None):
    """First in-band match across the patterns, tried in order."""
    lo, hi = band or (float("-inf"), float("inf"))
    for p in pats:
        for m in re.finditer(p, text, re.IGNORECASE):
            v = _num(m.group(1))
            if v is not None and lo <= v <= hi:
                return v
    return None


def _window(text, received):
    """Shipment window, from an explicit date range or a Mon/Mon pair."""
    m = re.search(r"(\d{1,2})\s*([A-Za-z]{3})[a-z]*\s*(\d{4})?\s*(?:-|to)\s*"
                  r"(\d{1,2})\s*([A-Za-z]{3})[a-z]*\s*(\d{4})?", text)
    if m and m.group(2).lower() in MONTH_NO and m.group(5).lower() in MONTH_NO:
        y2 = int(m.group(6) or received.year)
        y1 = int(m.group(3) or y2)
        try:
            return (dt.date(y1, MONTH_NO[m.group(2).lower()], int(m.group(1))).isoformat(),
                    dt.date(y2, MONTH_NO[m.group(5).lower()], int(m.group(4))).isoformat())
        except ValueError:
            pass
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\s*/\s*([A-Za-z]{3})[a-z]*\b", text)
    if m and m.group(1).lower() in MONTH_NO and m.group(2).lower() in MONTH_NO:
        a, b = MONTH_NO[m.group(1).lower()], MONTH_NO[m.group(2).lower()]
        ya = received.year + (1 if a < received.month else 0)
        yb = ya + (1 if b < a else 0)
        return (dt.date(ya, a, 1).isoformat(),
                dt.date(yb, b, calendar.monthrange(yb, b)[1]).isoformat())
    return None, None


def parse_message(body, supplier, received, cfg):
    """Extract a structured quote from message text.

    Returns a dict, or None when the message carries no price at all (market
    commentary, scheduling, out-of-office).
    """
    text = body.replace("–", "-")
    bpt = cfg["market"]["bushels_per_tonne"]

    q = {k: _first(text, PATTERNS[k], BANDS.get(k)) for k in PATTERNS}

    # Class: longest label match first, then the bare code.
    q["wheat_class"] = None
    for c in sorted(cfg["classes"], key=lambda c: -len(c["label"])):
        if re.search(re.escape(c["label"]), text, re.IGNORECASE):
            q["wheat_class"] = c["code"]
            break
    if not q["wheat_class"]:
        for c in cfg["classes"]:
            if re.search(rf"\b{re.escape(c['code'])}\b", text):
                q["wheat_class"] = c["code"]
                break

    # Origin: the label, or any of its terminal names.
    q["origin"] = None
    for o in cfg["origins"]:
        terms = [o["label"]] + [t.strip() for t in o["terminal"].split("/")]
        if any(re.search(re.escape(t), text, re.IGNORECASE) for t in terms):
            q["origin"] = o["label"]
            break

    m = re.search(REF, text)
    q["futures_ref"] = m.group(0) if m else None
    q["ship_from"], q["ship_to"] = _window(text, received)
    m = re.search(r"valid(?:ity)?\s*(?:till|until|:)?\s*([^\n.]{3,40})", text, re.IGNORECASE)
    q["valid_until"] = m.group(1).strip() if m else None

    if q["tonnes"] is not None:
        q["tonnes"] = int(q["tonnes"])

    # No price anywhere -> not a quote at all.
    if all(q[k] is None for k in ("flat_usd_mt", "futures_c_bu", "basis_c_bu")):
        return None

    missing = [f for f in CORE_FIELDS if q.get(f) is None]
    q["missing"] = ",".join(missing)
    q["confidence"] = round(1 - len(missing) / len(CORE_FIELDS), 2)

    # Does the stated flat equal the sum of its parts?
    parts = [q["futures_c_bu"], q["basis_c_bu"], q["freight_usd_mt"]]
    built = None
    if all(p is not None for p in parts):
        built = ((q["futures_c_bu"] + q["basis_c_bu"]) / 100 * bpt
                 + q["freight_usd_mt"] + (q["margin_usd_mt"] or 0))
    q["reconcile_gap"] = (round(q["flat_usd_mt"] - built, 2)
                          if built is not None and q["flat_usd_mt"] is not None else None)

    # A quote with no stated flat is still usable if the parts are all there.
    q["flat_stated"] = 1 if q["flat_usd_mt"] is not None else 0
    if q["flat_usd_mt"] is None and built is not None:
        q["flat_usd_mt"] = round(built, 2)

    q["supplier"] = supplier
    return q


# ----------------------------------------------------------------- generation --

GREET = ["Khun Pavin", "Pavin", "Dear Khun Pavin", "K. Pavin"]


def _body(style, f):
    """Write a message from the field bag `f`. The parser never sees `f`."""
    if style == "table":
        return (f"{f['greet']},\n\n"
                f"Please find our indication for {f['ship_text']} shipment "
                f"ex {f['terminal']}:\n\n"
                f"  Class          : {f['class_label']} {f['protein']}% min\n"
                f"  Quantity       : {f['tonnes']:,} MT (+/- 5% at seller's option)\n"
                f"  Shipment       : {f['d1']} - {f['d2']}\n"
                f"  Futures        : {f['fut']:.2f} c/bu ({f['ref']})\n"
                f"  Basis          : {f['ref']} + {f['basis']:.0f} c/bu\n"
                f"  Freight & ins. : USD {f['frt']:.2f} /MT {f['origin']}-Bangkok, Panamax\n"
                f"  Our margin     : USD {f['margin']:.2f} /MT\n"
                f"  Flat price     : USD {f['flat']:.2f} /MT CIF Bangkok\n"
                f"  Validity       : until {f['valid']} Tokyo time\n\n"
                f"Best regards,\n{f['supplier']} Grain Division")
    if style == "prose":
        return (f"{f['greet']} - following up on our call. We can work "
                f"{f['protein']}% {f['class_label']} for {f['ship_text']} out of "
                f"{f['terminal']} at {f['basis']:.0f} over {f['ref']}, which lands you "
                f"at USD {f['flat']:.2f}/MT CIF Bangkok including freight at "
                f"{f['frt']:.2f} and our margin of {f['margin']:.2f}. "
                f"Futures reference {f['fut']:.2f}. Quantity {f['tonnes']:,} MT, "
                f"shipment {f['d1']} - {f['d2']}. Can hold until {f['valid']}.")
    if style == "shorthand":
        return (f"{f['greet']}\n"
                f"{f['class_code']} {f['protein']}% {f['terminal']} {f['ship_text']} ship\n"
                f"flat {f['flat']:.2f} cif bkk\n"
                f"basis +{f['basis']:.0f} / fut {f['fut']:.2f} {f['ref']} / "
                f"frt {f['frt']:.2f} / mgn {f['margin']:.2f}\n"
                f"{f['tonnes']}mt\n"
                f"valid till {f['valid']} sgt")
    if style == "forwarded":
        return ("---------- Forwarded message ----------\n"
                f"From: {f['sender']}\n"
                f"Subject: FW: FW: UFM enquiry - {f['class_label']}\n\n"
                f"> > Please see below from our {f['terminal']} desk.\n> >\n"
                f"> > {f['class_label']} {f['protein']}pct, {f['ship_text']}, "
                f"{f['tonnes']:,}mt\n"
                f"> > basis {f['basis']:.0f} over {f['ref']}, futures {f['fut']:.2f}\n"
                f"> > freight {f['frt']:.2f}/mt, margin {f['margin']:.2f}/mt\n"
                f"> > flat USD {f['flat']:.2f}/mt CIF Bangkok\n"
                f"> > shipment {f['d1']} - {f['d2']}\n>\n> Regards")
    return (f"Hi {f['greet'].split()[-1]}, good to connect. Saw UFM is in the market. "
            f"We have {f['class_label']} {f['protein']}% availability for "
            f"{f['ship_text']} - can offer around USD {f['flat']:.2f}/MT CIF Bangkok "
            f"({f['basis']:.0f} over {f['ref']}, freight {f['frt']:.2f}). "
            f"{f['tonnes']:,} MT available. Happy to send a firm offer.")


# Messages that carry no price. The parser must return None for these.
COMMENTARY = [
    ("Morning comment - no offer attached",
     "Khun Pavin, no fresh indication this morning. Matif closed lower and the "
     "funds look to be trimming shorts ahead of the report. We will revert with "
     "numbers once the PNW desk opens."),
    ("Re: shipment schedule",
     "Noted on the laycan. Our operations team will confirm the vessel "
     "nomination by Thursday. Nothing to add on price today."),
    ("Out of office",
     "Please note our Singapore desk is closed tomorrow for a public holiday. "
     "Offers will resume the following day."),
]

# Origin/class pairings that actually trade.
PAIRINGS = [("US PNW", "HRS"), ("US PNW", "HRW"), ("US PNW", "WW"),
            ("US Gulf", "HRW"), ("Canada", "CWRS"), ("Australia", "ASW")]


def _settle(prices, cfg, series, day):
    """Last available settle for a benchmark at or before `day`."""
    sym = next(b["symbol"] for b in cfg["market"]["benchmarks"] if b["key"] == series)
    pick = [c for d, c in prices.get(sym, []) if d <= day.isoformat()]
    return round(pick[-1], 2) if pick else None


def generate(cfg, prices):
    """-> (messages, quotes). Messages are written as text; quotes are parsed
    back out of that text, so the parse path is the same one real mail takes."""
    s = cfg["synthetic"]
    rnd = random.Random(s["seed"])
    bpt = cfg["market"]["bushels_per_tonne"]
    today = dt.date.today()
    by_class = {c["code"]: c for c in cfg["classes"]}
    by_origin = {o["label"]: o for o in cfg["origins"]}
    ref_pre = {"srw": "ZW", "hrw": "KE"}

    messages = []
    for back in range(s["days_of_comms"], -1, -1):
        day = today - dt.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        if rnd.random() > 0.55:     # UFM is in the market in bursts
            continue

        for _ in range(rnd.randint(*s["quotes_per_active_day"])):
            sup = rnd.choice(cfg["suppliers"])
            channel = rnd.choice(sup["channels"])
            origin, ccode = rnd.choice(PAIRINGS)
            cls, org = by_class[ccode], by_origin[origin]
            fut = _settle(prices, cfg, cls["futures"], day)
            if fut is None:
                continue

            basis = rnd.uniform(*cls["basis_c_bu"])
            frt = rnd.uniform(*org["freight_usd_mt"])
            margin = rnd.uniform(*cfg["margin_usd_mt"])
            flat = (fut + basis) / 100 * bpt + frt + margin
            if rnd.random() < s["hidden_margin_rate"]:
                flat += rnd.uniform(1.5, 6.5)   # not itemised anywhere

            start = day + dt.timedelta(days=rnd.randint(45, 150))
            end = start + dt.timedelta(days=rnd.randint(25, 35))
            ts = dt.datetime.combine(
                day, dt.time(rnd.randint(7, 21), rnd.choice([0, 15, 30, 45])))

            f = {
                "greet": rnd.choice(GREET), "supplier": sup["name"],
                "sender": f"{sup['name'].split()[0].lower()}.grains@example.com",
                "class_label": cls["label"], "class_code": cls["code"],
                "protein": round(rnd.uniform(*cls["protein"]), 1),
                "tonnes": rnd.choice([13750, 25000, 27500, 30000, 55000]),
                "origin": origin, "terminal": org["terminal"].split(" / ")[0],
                "fut": fut, "basis": basis, "frt": frt,
                "margin": margin, "flat": flat,
                "ref": ref_pre[cls["futures"]] + rnd.choice(
                    [m["code"] for m in cfg["market"]["curve_months"]]),
                "d1": start.strftime("%d %b %Y"), "d2": end.strftime("%d %b %Y"),
                "ship_text": (f"{MONTHS[start.month - 1]}/{MONTHS[end.month - 1]}"
                              if start.month != end.month else start.strftime("%b")),
                "valid": rnd.choice(["1700", "1800", "close of business",
                                     "tomorrow noon", "1600"]),
            }
            body = _body(sup["style"], f)
            if channel == "call":
                body = "[Call note - logged by P. Sethbhakdi]\n\n" + body
            # Some arrive without the headline number. The parser must cope.
            if rnd.random() < s["unparseable_rate"]:
                body = body.replace(f"{f['flat']:.2f}", "TBC") + \
                    "\n(flat price to follow separately)"

            mid = hashlib.sha1(
                f"{sup['name']}{ts}{origin}{ccode}{flat:.2f}".encode()).hexdigest()[:16]
            messages.append({
                "id": mid, "ts": ts.isoformat(timespec="minutes"), "channel": channel,
                "supplier": sup["name"], "sender": f["sender"],
                "subject": (f"{cfg['client']['short']} / {cls['label']} "
                            f"{f['ship_text']} shipment ex {origin}")
                           if channel == "email" else None,
                "body": body, "thread": f"{ccode}-{origin}",
            })

        if rnd.random() < 0.3:
            sup = rnd.choice(cfg["suppliers"])
            subj, text = rnd.choice(COMMENTARY)
            ts = dt.datetime.combine(day, dt.time(rnd.randint(7, 20), 0))
            messages.append({
                "id": hashlib.sha1(f"c{sup['name']}{ts}{subj}".encode()).hexdigest()[:16],
                "ts": ts.isoformat(timespec="minutes"), "channel": "email",
                "supplier": sup["name"], "sender": "desk@example.com",
                "subject": subj, "body": text, "thread": None,
            })

    quotes = []
    for m in messages:
        q = parse_message(m["body"], m["supplier"],
                          dt.date.fromisoformat(m["ts"][:10]), cfg)
        if not q:
            continue
        q.update({"id": "q" + m["id"], "message_id": m["id"], "ts": m["ts"]})
        quotes.append(q)

    return messages, quotes
