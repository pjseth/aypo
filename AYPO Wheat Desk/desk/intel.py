"""Market intelligence: the research and notes behind a price view.

Organised by the levers Pavin named as the things that actually move his cost:
weather, export demand, government policy, exchange rates, geopolitics, and
fund positioning -- plus the WASDE release, which he stays up for because the
market moves within seconds of publication.

`stance` is the effect on price. For a buyer, bullish is bad news: the
dashboard labels it that way rather than leaving the reader to invert it.

Notes here are synthetic stand-ins with the shape of the real thing. Replacing
them means pointing `collect()` at the USDA WASDE release, a weather feed, and
whatever the desk reads (AgroValues' weekly outlook, broker notes).
"""
import datetime as dt
import hashlib
import random

# (lever, stance, headline, body, source, confidence)
POOL = [
    # -- weather ---------------------------------------------------------------
    ("weather", "bullish",
     "Dryness spreading across the southern US Plains",
     "Six-to-ten day outlook keeps Kansas and Oklahoma below normal "
     "precipitation. HRW crop ratings likely to slip in the next state report, "
     "which usually widens the KC basis before it shows up in futures.",
     "NOAA CPC 6-10 day", "high"),
    ("weather", "bearish",
     "Rain through Western Australia's cropping belt",
     "Kwinana zone picks up 15-30mm. ASW yield prospects improve and growers "
     "become more willing sellers, which should soften the Australian basis "
     "into the Feb/Mar shipment window.",
     "BOM weekly rainfall", "high"),
    ("weather", "bullish",
     "Frost risk in the Canadian Prairies",
     "An early frost event across Saskatchewan could clip CWRS protein. "
     "Protein premiums are the part of the Canadian offer that moves most, so "
     "watch the 13.5% spread rather than the flat price.",
     "Environment Canada", "medium"),
    ("weather", "neutral",
     "Black Sea winter crop conditions near normal",
     "Russian winter wheat emerged in line with the five-year average. No "
     "immediate supply signal, but soil moisture going into dormancy is the "
     "number to recheck in spring.",
     "SovEcon", "medium"),

    # -- export demand ---------------------------------------------------------
    ("export demand", "bullish",
     "Egypt tenders for 480,000 t, mostly Black Sea",
     "GASC covered a larger volume than expected. Where Egypt buys sets the "
     "tone for the global export pace, and a big clip tightens the supply the "
     "Pacific market is bidding against.",
     "GASC tender result", "high"),
    ("export demand", "bearish",
     "US weekly export inspections below trade estimates",
     "PNW inspections came in at the low end of the range for a third week. "
     "Slow US export demand tends to pull the PNW basis down even when "
     "futures hold, which is the half of the price that is negotiable.",
     "USDA FGIS", "high"),
    ("export demand", "bullish",
     "Indonesian and Philippine mills covering Q1 needs",
     "Regional buying has picked up for Jan/Feb arrival. Competing demand "
     "for the same Panamax freight window is the part that hits UFM directly.",
     "Trade press", "medium"),

    # -- policy ----------------------------------------------------------------
    ("policy", "bullish",
     "Russia raises its wheat export floor price",
     "An informal floor on Russian FOB offers removes the cheapest supply "
     "from the market and lifts the whole export curve, Australian and US "
     "offers included.",
     "Reuters", "medium"),
    ("policy", "bearish",
     "India signals it may relax wheat import duty",
     "Any Indian import demand is bullish, but duty relaxation plus a large "
     "domestic crop points the other way. Low confidence -- Indian policy "
     "reverses often.",
     "Ministry of Commerce", "low"),
    ("policy", "neutral",
     "Thai import tariff on wheat unchanged",
     "No change to the duty-free treatment of milling wheat imports in the "
     "latest notification. No action needed; worth rechecking at budget time.",
     "Thai Customs", "high"),

    # -- FX --------------------------------------------------------------------
    ("fx", "bullish",
     "Baht weakens past 33.5 to the dollar",
     "Wheat is paid in dollars and flour is sold in baht, so a weaker baht "
     "raises landed cost in the currency UFM actually earns. A 1% move on a "
     "55,000 t vessel is roughly the same money as a $3.50/t price move.",
     "BOT reference rate", "high"),
    ("fx", "bearish",
     "Australian dollar softer on rate expectations",
     "A weaker AUD makes Australian wheat cheaper in dollar terms and usually "
     "shows up as a softer ASW basis within a week or two.",
     "RBA / market", "medium"),

    # -- geopolitics -----------------------------------------------------------
    ("geopolitics", "bullish",
     "Black Sea corridor disruption reported again",
     "Renewed strikes near Odesa port infrastructure. The direct effect is on "
     "Ukrainian export capacity; the indirect one is on war-risk premiums in "
     "the freight and insurance line of every offer.",
     "Lloyd's List", "medium"),
    ("geopolitics", "bullish",
     "Tanker traffic rerouting raises bunker costs",
     "Longer routings around the Strait of Hormuz lift bunker prices, which "
     "feeds the freight component. Freight is 10-12% of UFM's landed cost, so "
     "this is not a rounding error.",
     "Argus bunker", "medium"),
    ("geopolitics", "neutral",
     "Panama Canal transit slots normalising",
     "Restrictions easing matters mainly for US Gulf origin, which UFM uses "
     "rarely. Keep for reference if Gulf parcels come back into the mix.",
     "ACP advisory", "high"),

    # -- funds -----------------------------------------------------------------
    ("funds", "bullish",
     "Managed money trims a large net short in Chicago wheat",
     "Funds covered roughly 18,000 contracts on the week. Short-covering "
     "rallies are not supply-driven and tend to fade, so they are often a "
     "chance to let an offer sit rather than chase it.",
     "CFTC Commitments of Traders", "high"),
    ("funds", "bearish",
     "Spec positioning still heavily short KC wheat",
     "A persistent net short caps rallies but also means any bullish surprise "
     "gets amplified. Relevant to the timing of a hedge, not the level.",
     "CFTC Commitments of Traders", "medium"),
]

# WASDE releases: the headline numbers and the read, as the desk would want it
# within seconds of publication.
WASDE = [
    ("bearish", "US ending stocks raised to 923 mn bu, above the 905 average estimate",
     "Yield unchanged, but a 15 mn bu cut to exports lifted carryout. World "
     "ending stocks also up 1.8 mn t on larger Russian and Australian crops. "
     "Net read: bearish, and the first 30 minutes usually overshoots."),
    ("bullish", "World ending stocks cut 3.1 mn t on smaller Argentine and Australian crops",
     "US carryout little changed, but the world number is the one the market "
     "traded. Southern hemisphere production cuts matter directly for the "
     "ASW offers in UFM's Feb/Mar window."),
    ("neutral", "Few surprises; US carryout within 5 mn bu of expectations",
     "Production, exports and feed use all close to trade estimates. No "
     "actionable signal -- the move faded within the hour."),
    ("bullish", "US exports raised 25 mn bu, ending stocks cut to 858 mn bu",
     "Stronger export commitments pulled carryout below the pre-report range. "
     "HRW balance sheet tightest of the classes, which is where UFM's winter "
     "wheat basis sits."),
]


def generate(cfg, days=180):
    """-> list of intel rows across the trailing `days`."""
    rnd = random.Random(cfg["synthetic"]["seed"] + 7)
    today = dt.date.today()
    rows = []

    # Routine notes, a few a week. The same headline must not come back
    # within COOLDOWN days -- repeats days apart read as a broken feed.
    COOLDOWN = 45
    last_used = {}
    for back in range(days, -1, -1):
        day = today - dt.timedelta(days=back)
        if day.weekday() >= 5 or rnd.random() > 0.22:
            continue
        fresh = [p for p in POOL
                 if (day - last_used.get(p[2], dt.date.min)).days > COOLDOWN]
        if not fresh:
            continue
        lever, stance, headline, body, source, conf = rnd.choice(fresh)
        last_used[headline] = day
        ts = dt.datetime.combine(day, dt.time(rnd.randint(7, 18), rnd.choice([0, 30])))
        rows.append({
            "id": hashlib.sha1(f"{ts}{headline}".encode()).hexdigest()[:16],
            "ts": ts.isoformat(timespec="minutes"), "kind": "note",
            "source": source, "lever": lever, "stance": stance,
            "headline": headline, "body": body, "confidence": conf,
        })

    # WASDE: 12th of each month, 12:00 ET -- midnight Bangkok, which is why
    # Pavin waits up for it.
    w = 0
    for back in range(days, -1, -1):
        day = today - dt.timedelta(days=back)
        if day.day != 12:
            continue
        stance, headline, body = WASDE[w % len(WASDE)]
        w += 1
        ts = dt.datetime.combine(day, dt.time(0, 0))
        rows.append({
            "id": hashlib.sha1(f"wasde{day}".encode()).hexdigest()[:16],
            "ts": ts.isoformat(timespec="minutes"), "kind": "wasde",
            "source": "USDA WASDE", "lever": "supply & demand", "stance": stance,
            "headline": headline, "body": body, "confidence": "high",
        })

    rows.sort(key=lambda r: r["ts"])
    return rows
