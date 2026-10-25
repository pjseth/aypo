"""Build the multi-material workspace dataset.

Emits three generated files:

    app/js/ws.js            window.WS           — workspaces, materials, drivers, quotes
    app/js/spend_sample.js  window.SPEND_SAMPLE — the messy spend export, baked in as text
    app/data/spend_sample.csv / .xlsx           — the same file, for the upload path

Baked into JS for the same reason `build_data.py` bakes `DESK`: the app runs from `file://`
as well as from a host, and `fetch()` of a local file is blocked by CORS on the former. The
one-click "load the sample" button therefore reads the baked copy; the file on disk exists so
the drag-and-drop and file-picker paths can be demonstrated against a real export.

Two workspaces:

  ufm   United Flour Mill — the existing wheat desk, modelled as materials. Derived from
        `build_data.build()` so there is one source of truth for the wheat numbers, and the
        driver legs reproduce exactly what `surfaces/quotes.js` already computes.

  urc   Demo: PH Food Manufacturer — fictional, 8 materials, 15 suppliers, 12 months of
        quotes and spend. Everything here is invented and the payload says so.

    py -3.11 tools/build_ws.py
"""

import csv
import datetime as dt
import io
import json
import math
import pathlib
import random
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_data                                    # noqa: E402  (path set above)

OUT_WS = ROOT / "app" / "js" / "ws.js"
OUT_SAMPLE_JS = ROOT / "app" / "js" / "spend_sample.js"
OUT_SAMPLE_CSV = ROOT / "app" / "data" / "spend_sample.csv"
OUT_SAMPLE_XLSX = ROOT / "app" / "data" / "spend_sample.xlsx"

# ------------------------------------------------------------------ real prices, maybe
# tools/fetch_prices.py leaves real daily OHLC here. When it is present the exchange-traded
# feeds, the FX table and the dataset's "today" come from it; everything else - the suppliers,
# the quotes, the spend - stays generated, because it is a fictional company's book. Delete
# the cache and the whole dataset goes back to being synthetic.
PRICE_CACHE = ROOT / "tools" / "price_cache.json"
REAL = {}
if PRICE_CACHE.exists():
    REAL = json.loads(PRICE_CACHE.read_text(encoding="utf-8"))

SEED = 20261013
BU_PER_T = 36.7437          # bushels per tonne, wheat


def _last_real_date():
    """The last day every real feed has a bar for. Anything later and one series would end
    short, which draws as a feed that stopped printing."""
    ends = [f["bars"][-1][0] for f in REAL.get("feeds", {}).values() if f.get("bars")]
    return min(ends) if ends else None


AS_OF = _last_real_date() or "2026-10-13"


def _months_ending(as_of, n=13):
    """`n` month starts, the last being the month `as_of` falls in."""
    y, m = int(as_of[:4]), int(as_of[5:7])
    out = []
    for back in range(n - 1, -1, -1):
        yy, mm = y, m - back
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append("%04d-%02d-01" % (yy, mm))
    return out


# Month anchors: 13 points ending in the month of AS_OF. A quote dated inside a month reads
# that month's anchor, so every figure on screen traces to one published monthly print.
MONTHS = _months_ending(AS_OF)

# Quotes and spend cover the trailing 12 months.
QUOTE_MONTHS = MONTHS[1:]


def r2(v):
    """Half-up to 2dp, matching the `round2` in pricing.js. Python's round() is banker's,
    which disagrees with JS on exact halves and would put the seed a cent out of step."""
    return math.floor(v * 100 + 0.5) / 100.0 if v >= 0 else -math.floor(-v * 100 + 0.5) / 100.0


# --------------------------------------------------------------------------------- fx
# Indicative mids. Real when tools/price_cache.json has them, and generated otherwise - the
# payload carries a note saying which, and every screen that converts prints it.
_FX_FALLBACK = {
    "USD": {"USD": 1.0, "PHP": 58.20, "THB": 33.02, "EUR": 0.9180, "MYR": 4.460},
    "PHP": {"PHP": 1.0, "USD": 1.0 / 58.20},
    "EUR": {"EUR": 1.0, "PHP": 63.40, "USD": 1.0893},
    "MYR": {"MYR": 1.0, "PHP": 13.05, "USD": 0.2242},
    "THB": {"THB": 1.0, "PHP": 1.7626, "USD": 1.0 / 33.02},
}


def build_fx():
    rates = REAL.get("fx") or {}
    need = ("USDPHP", "EURPHP", "MYRPHP", "EURUSD", "USDMYR")
    if not all(k in rates for k in need):
        return _FX_FALLBACK, False
    usdphp = rates["USDPHP"]["rate"]
    eurphp = rates["EURPHP"]["rate"]
    myrphp = rates["MYRPHP"]["rate"]
    eurusd = rates["EURUSD"]["rate"]
    usdmyr = rates["USDMYR"]["rate"]
    return {
        "USD": {"USD": 1.0, "PHP": usdphp, "EUR": 1.0 / eurusd, "MYR": usdmyr,
                "THB": 33.02},
        "PHP": {"PHP": 1.0, "USD": 1.0 / usdphp},
        "EUR": {"EUR": 1.0, "PHP": eurphp, "USD": eurusd},
        "MYR": {"MYR": 1.0, "PHP": myrphp, "USD": 1.0 / usdmyr},
        "THB": {"THB": 1.0, "PHP": usdphp / 33.02, "USD": 1.0 / 33.02},
    }, True


FX, FX_IS_REAL = build_fx()


# ------------------------------------------------------------------------- series maker
def walk(rng, anchor_last, drift_pct, vol_pct, n=13):
    """A monthly path that *ends* on `anchor_last`. Built backwards so the level we actually
    quote is the level the series finishes at, rather than wherever a forward walk lands."""
    vals = [anchor_last]
    for _ in range(n - 1):
        step = 1.0 + rng.gauss(drift_pct, vol_pct)
        vals.append(vals[-1] / max(0.75, min(1.25, step)))
    vals.reverse()
    return [round(v, 4) for v in vals]


def daily_from_monthly(rng, monthly, days=180):
    """Interpolate the monthly anchors into a daily path for the charts, so the strip, the
    market screen and the driver series are the same numbers at the same dates rather than
    three guesses.

    The wiggle between anchors is an AR(1) process rather than independent noise per day.
    Independent noise gives every point its own unrelated error, which reads as static; a
    price series wanders, so each day's deviation keeps most of the day before's. The anchors
    are still hit exactly, because the deviation is damped to zero as each one approaches.
    """
    anchors = monthly[:]
    out = []
    per = max(1, days // (len(anchors) - 1))
    dev = 0.0
    for i in range(len(anchors) - 1):
        a, b = anchors[i], anchors[i + 1]
        for k in range(per):
            t = k / per
            dev = 0.86 * dev + rng.gauss(0, 0.0022)
            # Pull the deviation out as the next anchor approaches, so the monthly print is
            # the value on the day it prints and not an interpolation artefact.
            out.append(round((a + (b - a) * t) * (1 + dev * (1 - t)), 4))
    out.append(anchors[-1])
    return out[-days:]


def daily_dates(n, end=AS_OF):
    d = dt.date.fromisoformat(end)
    return [(d - dt.timedelta(days=(n - 1 - i))).isoformat() for i in range(n)]


# =============================================================================== URC seed
#
# Everything below is invented. Levels are chosen to be plausible for a Philippine food
# manufacturer importing about 60% of its raw materials, but no figure here came from URC.

# Every feed anything is priced off. `strip` marks the six that earn a place on the market
# strip along the top; the rest are on the market screen only. All of them carry a daily path
# so the instrument chart has something to draw at 1M and 3M.
FEEDS = [
    # key, label, venue, unit, currency, last, drift, vol, role, strip
    ("RC",      "ICE Robusta coffee, front month",  "ICE Europe",  "USD/MT",  "USD", 4182.0, 0.013, 0.045, "index", True),
    ("BRN",     "Brent crude, front month",         "ICE",         "USD/bbl", "USD", 71.40, -0.004, 0.038, "index", True),
    ("SB",      "ICE Sugar No.11, front month",     "ICE US",      "USc/lb",  "USD", 19.85, 0.006, 0.040, "index", True),
    ("FCPO",    "Bursa crude palm oil, 3rd month",  "Bursa",       "MYR/MT",  "MYR", 4310.0, 0.008, 0.036, "index", True),
    ("USDPHP",  "USD / PHP spot",                   "BSP ref",     "PHP/USD", "PHP", 58.20, 0.004, 0.012, "fx", True),
    ("FRTBOX",  "Container freight, origin–Manila", "Drewry",      "USD/MT",  "USD", 62.0, -0.010, 0.060, "freight", True),
    ("POTEU",   "EU potato flake index",            "CRM Europe",  "EUR/MT",  "EUR", 1685.0, 0.011, 0.030, "index", False),
    ("ZW",      "CBOT soft red winter wheat, Dec",  "CBOT",        "USc/bu",  "USD", 546.0, 0.002, 0.042, "index", False),
    ("KRAFT",   "Containerboard kraftliner, Asia",  "Fastmarkets", "USD/MT",  "USD", 612.0, 0.005, 0.025, "index", False),
    ("BOPP",    "BOPP film grade resin, SE Asia",   "Platts",      "USD/MT",  "USD", 1683.0, -0.003, 0.028, "index", False),
    ("FRTBULK", "Supramax bulk, origin–Manila",     "Baltic",      "USD/MT",  "USD", 46.0, -0.006, 0.055, "freight", False),
]

# Daily points across the thirteen monthly anchors: 2025-10-01 to 2026-10-13.
DAILY_DAYS = 378

# Whether a feed is "mocked" is no longer a list to maintain: it is whether a real series
# came back for it. See is_real().

# How each material's FY26 rate sits against its own fair price at the last review: a little
# over where procurement built in headroom, a little under where the business squeezed. This
# is the only hand-set number in the budget, and it is what makes some materials run over and
# others under rather than all of them moving together.
BUDGET_FACTOR = {
    "potato_flakes": 1.020,
    "green_coffee":  0.975,
    "pet_resin":     1.045,
    "refined_sugar": 0.990,
    "palm_olein":    1.030,
    "wheat_flour":   0.985,
    "lam_film":      1.010,
    "cartons":       1.020,
}

# Premium a material's book typically carries, used to turn driver cost into a fair price at
# review time. Derived from PREMIUM below rather than guessed twice.
def typical_premium(material_id):
    vals = [base for (mid, _sup), (base, _drift) in PREMIUM.items() if mid == material_id]
    return sum(vals) / len(vals) if vals else 0.04


def round_rate(v):
    """Budget rates are round numbers on a planning sheet, not four significant figures."""
    if v >= 10000:
        return float(round(v / 500.0) * 500)
    if v >= 1000:
        return float(round(v / 100.0) * 100)
    if v >= 100:
        return float(round(v))
    return float(round(v * 2) / 2)


def set_budgets(materials):
    """Set each FY26 rate from the fair price at the last review, times the material's factor.

    The review is the current month: a budget rate that a buyer is being measured against
    today was set against a market they could see, and anchoring it a year back would make
    every material run over budget together, which says nothing about any of them.
    """
    review = len(MONTHS) - 1
    for m in materials:
        fair = driver_cost(m, review) * (1 + typical_premium(m["id"]))
        m["budget_price"] = round_rate(fair * BUDGET_FACTOR[m["id"]])


MATERIALS = [
    # id, name, category, unit, currency, budget, budget_note, crop feed, drivers
    {
        "id": "potato_flakes", "name": "Potato flakes", "category": "raw",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, set at the Nov 2025 planning round",
        "crop": {"status": "unavailable",
                 "reason": "The European crop circular we read for potato has not published "
                           "since 26 Sep. Nothing has replaced it in this draft."},
        "drivers": [
            {"key": "POTEU", "per_unit": 1.0, "offset": 0.0, "kind": "index",
             "note": "Flake index, delivered-works Europe"},
            {"key": "FRTBOX", "per_unit": 1.0, "offset": 0.0, "kind": "freight",
             "note": "Container freight Rotterdam–Manila, per MT of cargo"},
        ],
    },
    {
        "id": "green_coffee", "name": "Green coffee, robusta gr.2", "category": "raw",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, held since the Q2 re-forecast",
        "crop": {"status": "ok", "headline": "Vietnamese harvest starts late October",
                 "body": "Trade estimates for the 2026/27 Vietnamese crop sit a little above "
                         "last season. Rainfall in the Central Highlands has been close to "
                         "normal through September.",
                 "source": "Illustrative crop commentary — mock data, not a licensed feed"},
        "drivers": [
            {"key": "RC", "per_unit": 1.0, "offset": 0.0, "kind": "index",
             "note": "Robusta futures, the contract the origin prices against"},
            {"key": "FRTBOX", "per_unit": 1.0, "offset": 33.0, "kind": "freight",
             "note": "Container freight to Manila plus origin handling of USD 33/MT"},
        ],
    },
    {
        "id": "pet_resin", "name": "PET resin, bottle grade", "category": "packaging",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, set against a USD 70/bbl crude assumption",
        "crop": None,
        "drivers": [
            {"key": "BRN", "per_unit": 7.40, "offset": 395.0, "kind": "index",
             "note": "7.40 bbl of crude equivalent per MT of resin, plus a USD 395/MT "
                     "processing spread"},
            {"key": "FRTBULK", "per_unit": 1.0, "offset": 2.0, "kind": "freight",
             "note": "Bulk freight to Manila plus USD 2/MT port charges"},
        ],
    },
    {
        "id": "refined_sugar", "name": "Refined sugar", "category": "raw",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, set at the Nov 2025 planning round",
        "crop": {"status": "ok", "headline": "Milling season opened on schedule",
                 "body": "Negros mills opened in the first week of September. Cane tonnage "
                         "is reported in line with last season.",
                 "source": "Illustrative crop commentary — mock data, not a licensed feed"},
        "drivers": [
            {"key": "SB", "per_unit": 22.0462, "offset": 110.0, "kind": "index",
             "note": "World raw at 2,204.62 lb per MT, plus a USD 110/MT refining spread"},
            {"key": "SRADOM", "per_unit": 0.0, "offset": 300.0, "kind": "spread",
             "note": "Domestic premium over world raw — the Philippine market trades at a "
                     "persistent premium. Held flat at USD 300/MT in this dataset.",
             "flat": 1.0, "currency": "USD", "unit": "USD/MT",
             "label": "Philippine domestic premium", "source": "Mocked, held flat"},
        ],
    },
    {
        "id": "palm_olein", "name": "Refined palm olein", "category": "raw",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, re-set in May 2026 after the CPO rally",
        "crop": {"status": "ok", "headline": "Malaysian stocks building into Q4",
                 "body": "Output in Peninsular Malaysia has been seasonally strong. Stock "
                         "levels are expected to peak around October.",
                 "source": "Illustrative crop commentary — mock data, not a licensed feed"},
        "drivers": [
            {"key": "FCPO", "per_unit": 1.0, "offset": 0.0, "kind": "index",
             "note": "Crude palm oil futures, the contract refiners price against"},
            {"key": "OLEINREF", "per_unit": 0.0, "offset": 85.0, "kind": "spread",
             "note": "Refining and fractionation spread, CPO to RBD olein.",
             "flat": 1.0, "currency": "USD", "unit": "USD/MT",
             "label": "Refining spread, CPO to olein", "source": "Mocked, held flat"},
            {"key": "FRTBULK", "per_unit": 0.90, "offset": 0.0, "kind": "freight",
             "note": "Parcel freight Port Klang–Manila, 0.90 of the Supramax reference"},
        ],
    },
    {
        "id": "wheat_flour", "name": "Hard wheat flour", "category": "raw",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, set at the Nov 2025 planning round",
        "crop": {"status": "ok", "headline": "Northern hemisphere harvest complete",
                 "body": "The US and EU harvests are in. Attention has moved to southern "
                         "hemisphere conditions, which are unremarkable so far.",
                 "source": "Illustrative crop commentary — mock data, not a licensed feed"},
        "drivers": [
            {"key": "ZW", "per_unit": 0.48502, "offset": 200.0, "kind": "index",
             "note": "1.32 MT of wheat per MT of flour at 36.7437 bu/MT, plus a USD 200/MT "
                     "milling and bagging spread"},
            {"key": "FRTBULK", "per_unit": 0.82, "offset": 0.0, "kind": "freight",
             "note": "Bulk freight, 0.82 of the Supramax reference"},
        ],
    },
    {
        "id": "lam_film", "name": "Laminated film, BOPP/PET", "category": "packaging",
        "unit": "kg", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate per kg, set at the Nov 2025 planning round",
        "crop": None,
        "drivers": [
            {"key": "BOPP", "per_unit": 0.00108, "offset": 0.0, "kind": "index",
             "note": "1.08 kg of resin per kg of finished laminate, priced per MT"},
            {"key": "FILMCONV", "per_unit": 0.0, "offset": 42.0, "kind": "spread",
             "note": "Converter charge — printing, lamination, slitting.",
             "flat": 1.0, "currency": "PHP", "unit": "PHP/kg",
             "label": "Converter charge", "source": "Mocked, held flat"},
        ],
    },
    {
        "id": "cartons", "name": "Corrugated cartons", "category": "packaging",
        "unit": "MT", "currency": "PHP", "budget_price": None,  # set by set_budgets()
        "budget_note": "FY26 budget rate, set at the Nov 2025 planning round",
        "crop": None,
        "drivers": [
            {"key": "KRAFT", "per_unit": 1.12, "offset": 0.0, "kind": "index",
             "note": "1.12 MT of liner and medium per MT of finished carton, waste included"},
            {"key": "BOXCONV", "per_unit": 0.0, "offset": 9500.0, "kind": "spread",
             "note": "Converter charge — corrugating, printing, die-cutting.",
             "flat": 1.0, "currency": "PHP", "unit": "PHP/MT",
             "label": "Converter charge", "source": "Mocked, held flat"},
        ],
    },
]

SUPPLIERS = [
    # id, name, country, materials, aliases used in the spend export
    ("luzon", "Luzon Agri Ventures Inc.", "Philippines", ["potato_flakes", "wheat_flour"],
     ["LUZON AGRI VENTURES INC", "Luzon Agri Ventures, Inc.", "luzon agri ventures"]),
    ("nordpoort", "Nordpoort Food Ingredients BV", "Netherlands", ["potato_flakes"],
     ["NORDPOORT FOOD INGR. B.V.", "Nordpoort Food Ingredients", "NORDPOORT B V"]),
    ("batangas", "Batangas Prime Commodities Corp.", "Philippines",
     ["potato_flakes", "refined_sugar", "palm_olein", "wheat_flour"],
     ["BATANGAS PRIME COMMODITIES CORP", "B.P. Commodities Corp.", "batangas prime commodities"]),
    ("kalinga", "Kalinga Highlands Trading Corp.", "Philippines", ["green_coffee"],
     ["KALINGA HIGHLANDS TRADING CORP", "Kalinga Highlands Trdg. Corp.",
      "kalinga highlands trading corporation", "KALINGA HIGHLANDS"]),
    ("sierraverde", "Sierra Verde Coffee SA", "Brazil", ["green_coffee"],
     ["SIERRA VERDE COFFEE S.A.", "Sierra Verde Coffee", "SIERRA VERDE COFFEE SA"]),
    ("mekong", "Mekong Robusta Export Co.", "Vietnam", ["green_coffee"],
     ["MEKONG ROBUSTA EXPORT CO LTD", "Mekong Robusta Export", "mekong robusta exp. co."]),
    ("pacpoly", "Pacific Polymer Industries Inc.", "Philippines", ["pet_resin", "lam_film"],
     ["PACIFIC POLYMER INDUSTRIES INC", "Pacific Polymer Ind., Inc.", "PAC. POLYMER INDUSTRIES"]),
    ("sunrise", "Sunrise Resin & Chemicals Corp.", "Philippines", ["pet_resin", "lam_film"],
     ["SUNRISE RESIN AND CHEMICALS CORP", "Sunrise Resin & Chemicals",
      "sunrise resin and chemicals corporation"]),
    ("visayas", "Visayas Packaging Mills Inc.", "Philippines",
     ["pet_resin", "lam_film", "cartons"],
     ["VISAYAS PACKAGING MILLS INC", "Visayas Packaging Mills, Inc.", "VISAYAS PKG MILLS"]),
    ("northgate", "Northgate Containerboard Corp.", "Philippines", ["cartons"],
     ["NORTHGATE CONTAINERBOARD CORP", "Northgate Containerboard", "northgate containerboard corp."]),
    ("carmen", "Carmen Paper & Board Co.", "Philippines", ["cartons"],
     ["CARMEN PAPER AND BOARD CO", "Carmen Paper & Board", "CARMEN PAPER + BOARD CO."]),
    ("isla", "Isla Sugar Refiners Inc.", "Philippines", ["refined_sugar"],
     ["ISLA SUGAR REFINERS INC", "Isla Sugar Refiners, Inc.", "isla sugar refiners"]),
    ("goldenpalm", "Golden Palm Refineries Sdn Bhd", "Malaysia", ["palm_olein"],
     ["GOLDEN PALM REFINERIES SDN BHD", "Golden Palm Refineries", "GOLDEN PALM REF. SDN. BHD."]),
    ("mindanao", "Mindanao Oleo Products Inc.", "Philippines", ["palm_olein"],
     ["MINDANAO OLEO PRODUCTS INC", "Mindanao Oleo Products", "mindanao oleo prod. inc"]),
    ("harbour", "Harbour Mills Flour Corp.", "Philippines", ["wheat_flour", "refined_sugar"],
     ["HARBOUR MILLS FLOUR CORP", "Harbour Mills Flour Corporation", "harbour mills flour"]),
]

# Supplier premium behaviour: base premium as a share of driver cost, and how it drifts over
# the twelve months. The coffee story is the one planted deliberately.
PREMIUM = {
    # material         supplier        base   drift/month
    # Premiums run 2.5-8% of driver cost — the range a differential actually trades in.
    # The coffee story is the one planted deliberately: Kalinga starts at the keen end and
    # widens by about half a point a quarter until it is the dearest book on the material.
    ("green_coffee", "kalinga"):      (0.035, 0.00460),
    ("green_coffee", "sierraverde"):  (0.052, 0.00025),
    ("green_coffee", "mekong"):       (0.046, -0.00050),
    ("pet_resin", "pacpoly"):         (0.042, 0.00040),   # lands inside the band, under budget
    ("pet_resin", "sunrise"):         (0.055, 0.00060),
    ("pet_resin", "visayas"):         (0.033, -0.00030),
    ("potato_flakes", "luzon"):       (0.058, 0.00075),
    ("potato_flakes", "nordpoort"):   (0.043, 0.00010),
    ("potato_flakes", "batangas"):    (0.066, 0.00105),
    ("refined_sugar", "isla"):        (0.030, 0.00040),
    ("refined_sugar", "batangas"):    (0.044, 0.00015),
    ("refined_sugar", "harbour"):     (0.038, -0.00020),
    ("palm_olein", "goldenpalm"):     (0.026, 0.00030),
    ("palm_olein", "mindanao"):       (0.039, 0.00055),
    ("palm_olein", "batangas"):       (0.047, -0.00010),
    ("wheat_flour", "harbour"):       (0.035, 0.00025),
    ("wheat_flour", "luzon"):         (0.049, 0.00045),
    ("wheat_flour", "batangas"):      (0.042, 0.00070),
    ("lam_film", "pacpoly"):          (0.037, 0.00035),
    ("lam_film", "sunrise"):          (0.050, 0.00020),
    ("lam_film", "visayas"):          (0.044, 0.00050),
    ("cartons", "visayas"):           (0.034, 0.00030),
    ("cartons", "northgate"):         (0.045, 0.00015),
    ("cartons", "carmen"):            (0.052, 0.00065),
}

PLANTS = [
    ("1010", "Calamba"), ("1020", "Bulacan"), ("1030", "Cebu"),
    ("1040", "Davao"), ("1050", "Cagayan de Oro"),
]

# Free-text material descriptions as a buyer would actually find them in an S/4HANA export:
# abbreviated, inconsistently cased, with grades and pack sizes glued on.
DESCRIPTIONS = {
    "potato_flakes": ["POTATO FLAKES 25KG BAG", "Potato flake, food grade",
                      "POT. FLAKES DEHYDRATED", "Flakes, potato - 25kg", "POTATO FLAKE STD"],
    "green_coffee": ["GREEN COFFEE ROBUSTA GR2", "Grn coffee robusta grade 2",
                     "COFFEE GREEN BEAN ROB G2", "ROBUSTA GREEN COFFEE 60KG",
                     "Green coffee bean, robusta"],
    "pet_resin": ["PET RESIN BOTTLE GRADE IV0.80", "Resin, PET bottle grade",
                  "PET RESIN BG", "POLYETHYLENE TEREPHTHALATE RESIN", "PET BTL GRADE RESIN"],
    "refined_sugar": ["REFINED SUGAR 50KG", "Sugar, refined white",
                      "REF SUGAR BAGGED 50KG", "SUGAR REFINED LOCAL", "Refined sugar - bulk"],
    "palm_olein": ["RBD PALM OLEIN", "Palm olein, refined bleached deodorised",
                   "RBD PALM OLEIN IV56", "PALM OLEIN RBD BULK", "Olein, palm - RBD"],
    "wheat_flour": ["HARD WHEAT FLOUR 25KG", "Flour, hard wheat",
                    "HARD FLOUR BAKERS 25KG", "WHEAT FLOUR HARD", "Hard wheat flour - bulk"],
    "lam_film": ["LAMINATED FILM BOPP/PET", "Film, laminated BOPP-PET",
                 "LAM FILM BOPP PET PRINTED", "PRINTED LAMINATE FILM", "Laminate film roll"],
    "cartons": ["CORRUGATED CARTON RSC", "Carton, corrugated shipping",
                "CORR CARTON B-FLUTE", "SHIPPING CARTON CORRUGATED", "Corrugated box - RSC"],
}

# Lines nothing can classify. They are in the export because they are in the real thing:
# accruals, sundries and an attachment nobody attached.
JUNK = [
    "MISC CONSUMABLES - SEE ATTACHMENT", "FREIGHT ACCRUAL REVERSAL Q2",
    "SUNDRY PURCHASES", "ADJ - PERIOD 09 RECLASS", "PALLET DEPOSIT REFUND",
    "SERVICES - THIRD PARTY", "ROUNDING DIFFERENCE", "ACCRUAL - GR/IR CLEARING",
]


# ========================================================================== driver series
def real_bars(key):
    """The real daily bars for a feed, from either half of the cache. The FX pairs are
    fetched as rates but they are instruments too, and USD/PHP is the feed with the widest
    reach on this book, so it gets a chart like everything else."""
    f = (REAL.get("feeds") or {}).get(key)
    if f and f.get("bars"):
        return f["bars"], f
    x = (REAL.get("fx") or {}).get(key)
    if x and x.get("bars"):
        return x["bars"], x
    return None, None


def is_real(key):
    return real_bars(key)[0] is not None


def in_window(bars):
    """Only the bars inside the dataset's twelve months. The fetch asks for two years so the
    month anchors at the start of the window have a prior print to settle on."""
    return [b for b in bars if MONTHS[0] <= b[0] <= AS_OF]


def monthly_from_bars(bars):
    """The close in force on each month anchor: the last session at or before it. Not an
    average and not the first of the month - the anchor is a print that happened."""
    out, at = [], 0
    for anchor in MONTHS:
        last = None
        while at < len(bars) and bars[at][0] <= anchor:
            last = bars[at][4]
            at += 1
        if last is None:
            last = bars[0][4] if bars else 0.0
            at = 0
        out.append(round(last, 4))
    # The final anchor is the month the dataset ends in; carry the latest close onto it so
    # the price model and the chart finish on the same number.
    if bars:
        out[-1] = round(bars[-1][4], 4)
    return out


def synth_ohlc(rng, closes, dates):
    """An open, high and low around a generated close, so a candle has something to draw.

    Nothing is claimed by this: it is a session's shape invented around a level that was
    itself invented, and the screen says both. Real feeds never come through here.
    """
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        span = abs(c) * (0.0015 + abs(rng.gauss(0, 0.0022)))
        hi = max(o, c) + span * (0.4 + rng.random())
        lo = min(o, c) - span * (0.4 + rng.random())
        out.append([dates[i], round(o, 4), round(hi, 4), round(lo, 4), round(c, 4)])
        prev = c
    return out


def build_series(rng):
    """Every monthly series in the workspace, keyed by feed key.

    Real where the cache has it: the monthly anchors become the closes that actually printed
    on those dates, so the price model argues a quote against a real published number.
    """
    series = {}
    for key, label, venue, unit, cur, last, drift, vol, _role, _strip in FEEDS:
        bars, meta = real_bars(key)
        if bars:
            bars = in_window(bars)
            # US cents on the exchange tape ("USX") are still dollars for conversion; the
            # unit string already says cents and the per_unit on each driver expects them.
            real_cur = (meta.get("currency") or cur)
            cur = "USD" if real_cur in ("USX", "USD") else real_cur
            if key == "FCPO":
                # Yahoo's long name for this contract runs to "...Calendar" and gets cut off
                # in a tile. The contract is what matters; name it plainly.
                label, unit = "Malaysian crude palm oil", "USD/MT"
            series[key] = {
                "key": key, "label": label, "source": venue, "unit": unit, "currency": cur,
                "dates": MONTHS[:], "values": monthly_from_bars(bars),
                "bars": bars, "real": True,
                "symbol": meta.get("symbol"), "provider": meta.get("provider", "yahoo"),
            }
        else:
            series[key] = {
                "key": key, "label": label, "source": venue, "unit": unit, "currency": cur,
                "dates": MONTHS[:], "values": walk(rng, last, drift, vol),
                "bars": None, "real": False,
            }
    return series


def build_instruments(rng, series):
    """One instrument per feed, in the shape `app.js` and `surfaces/prices.js` render.

    A real feed carries the sessions that actually traded. A generated one carries a daily
    path interpolated across its monthly anchors, with an invented session shape around each
    close so the candles have something to draw; `synthetic_history` says which is which and
    every screen repeats it in words.
    """
    out = []
    for key, label, venue, unit, cur, last, drift, vol, role, strip in FEEDS:
        sr = series[key]
        if sr.get("real"):
            bars = sr["bars"]
            hist = [b[4] for b in bars]
            dates = [b[0] for b in bars]
            ohlc = bars
        else:
            hist = daily_from_monthly(rng, sr["values"], DAILY_DAYS)
            dates = daily_dates(len(hist))
            ohlc = synth_ohlc(rng, hist, dates)
        prev = hist[-2] if len(hist) > 1 else hist[-1]
        out.append({
            "key": key, "label": sr["label"], "venue": venue,
            "unit": sr["unit"], "currency": sr["currency"],
            "role": role, "strip": strip,
            "last": round(hist[-1], 4), "prev": round(prev, 4),
            "change": round(hist[-1] - prev, 4),
            "change_pct": round((hist[-1] / prev - 1) * 100, 3) if prev else 0.0,
            "history": hist, "dates": dates, "ohlc": ohlc,
            "month_ago": round(sr["values"][-2], 4),
            "usd_mt": None,
            "synthetic_history": not sr.get("real"),
            "symbol": sr.get("symbol"),
            "provider": sr.get("provider"),
        })
    return out


def driver_for(spec, series):
    """Expand a material's driver spec into the payload shape the app reads."""
    key = spec["key"]
    if spec.get("flat"):
        # A held-flat spread still needs a series, so that every leg is read the same way and
        # a flat line is the honest picture of a held constant. The constant itself is the
        # series - not a dummy 1.0 with the real figure hidden in the offset, which would have
        # the bulletin reporting "converter charge: 1.00 PHP/MT".
        s = {"key": key, "label": spec["label"], "source": spec["source"],
             "unit": spec["unit"], "currency": spec["currency"],
             "dates": MONTHS[:], "values": [spec["offset"]] * len(MONTHS)}
        spec = dict(spec, per_unit=1.0, offset=0.0)
        mocked = True
    else:
        s = series[key]
        mocked = not is_real(key)
    return {
        "key": key,
        "name": s["label"],
        "source": s["source"],
        "unit": s["unit"],
        "currency": s["currency"],
        "per_unit": spec["per_unit"],
        "offset": spec["offset"],
        "kind": spec["kind"],
        "note": spec["note"],
        "mocked": mocked,
        "dates": s["dates"],
        "values": s["values"],
    }


def leg_value(drv, idx, material_currency):
    raw = drv["values"][idx]
    base = raw * drv["per_unit"] + drv["offset"]
    rate = FX.get(drv["currency"], {}).get(material_currency)
    if rate is None:
        raise SystemExit("no fx %s->%s" % (drv["currency"], material_currency))
    return r2(base * rate)


def driver_cost(mat, idx):
    return r2(sum(leg_value(d, idx, mat["currency"]) for d in mat["drivers"]))


# ================================================================================ quotes
def build_urc(rng):
    series = build_series(rng)
    instruments = build_instruments(rng, series)

    materials = []
    for spec in MATERIALS:
        m = dict(spec)
        m["drivers"] = [driver_for(d, series) for d in spec["drivers"]]
        materials.append(m)
    set_budgets(materials)
    by_id = {m["id"]: m for m in materials}

    # The supplier master holds the legal name and nothing else, which is the honest starting
    # state for a new customer: SAP has a vendor record, not a table of the ways people have
    # typed it into a PO. The spellings in SPEND_VARIANTS are what the export actually
    # contains, and resolving them is the work the review queue exists to do once.
    suppliers = [{"id": sid, "name": name, "country": country, "materials": mats,
                  "aliases": []}
                 for (sid, name, country, mats, _aliases) in SUPPLIERS]

    quotes = []
    n = 0
    for m in materials:
        sups = [s for s in suppliers if m["id"] in s["materials"]]
        for s in sups:
            base, drift = PREMIUM[(m["id"], s["id"])]
            for mi, month in enumerate(QUOTE_MONTHS):
                idx = MONTHS.index(month)
                dc = driver_cost(m, idx)
                wobble = rng.gauss(0, 0.0022)
                prem = r2(dc * (base + drift * mi + wobble))
                price = r2(dc + prem)
                day = 8 + (stable_hash(s["id"] + m["id"]) % 5)
                date = month[:8] + "%02d" % day
                qty = {"MT": [60, 80, 100, 120, 150, 200], "kg": [8000, 12000, 15000, 20000]}[m["unit"]]
                n += 1
                quotes.append({
                    "id": "wq%03d" % n,
                    "material_id": m["id"],
                    "supplier_id": s["id"],
                    "date": date,
                    "price": price,
                    "currency": m["currency"],
                    "unit": m["unit"],
                    "qty": qty[rng.randrange(len(qty))],
                    "valid_to": add_days(date, 21),
                    "source": "email",
                    "source_ref": "%s offer %s-%s" % (s["name"].split()[0].upper(),
                                                      month[:7].replace("-", ""), s["id"][:3].upper()),
                })
    return instruments, materials, by_id, suppliers, quotes


def stable_hash(text):
    """hash() is salted per process; this is not."""
    h = 2166136261
    for ch in text:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def add_days(iso, n):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


# ================================================================== the messy spend export
def build_spend(rng, materials, suppliers, quotes):
    by_mat = {}
    for q in quotes:
        by_mat.setdefault((q["material_id"], q["supplier_id"], q["date"][:7]), q)
    sup_by_id = {s["id"]: s for s in suppliers}
    variants = {sid: aliases for (sid, _n, _c, _m, aliases) in SUPPLIERS}
    mat_by_id = {m["id"]: m for m in materials}

    rows = []
    doc = 5100000

    def push(date, vendor, desc, plant, qty, uom, amount, currency, po, date_style=0):
        nonlocal doc
        doc += 1
        if date_style == 1:
            d = dt.date.fromisoformat(date)
            shown = "%02d.%02d.%04d" % (d.day, d.month, d.year)
        else:
            shown = date
        rows.append([shown, "45%07d" % doc, vendor, desc, plant[0], plant[1],
                     qty, uom, amount, currency, po])

    for (mid, sid, month), q in sorted(by_mat.items()):
        m = mat_by_id[mid]
        s = sup_by_id[sid]
        # Two to four receipt lines a month, split across plants, at the quoted price with the
        # small drift a real invoice carries against the offer.
        for k in range(3 + rng.randrange(4)):
            plant = PLANTS[rng.randrange(len(PLANTS))]
            base_qty = q["qty"] / (3.0 + rng.random() * 2)
            qty = round(base_qty, 2) if m["unit"] == "MT" else round(base_qty, 0)
            price_php = q["price"] * (1 + rng.gauss(0, 0.004))
            # Imports invoice in the origin currency; local suppliers in PHP.
            if s["country"] == "Netherlands":
                cur, rate = "EUR", FX["EUR"]["PHP"]
            elif s["country"] in ("Brazil", "Vietnam"):
                cur, rate = "USD", FX["USD"]["PHP"]
            elif s["country"] == "Malaysia":
                cur, rate = "MYR", FX["MYR"]["PHP"]
            else:
                cur, rate = "PHP", 1.0
            amount = qty * price_php / rate
            spellings = variants[s["id"]]
            vendor = spellings[rng.randrange(len(spellings))]
            desc = DESCRIPTIONS[mid][rng.randrange(len(DESCRIPTIONS[mid]))]
            uom = {"MT": ["MT", "TO", "T"], "kg": ["KG", "kg"]}[m["unit"]]
            day = 1 + rng.randrange(27)
            date = month + "-%02d" % day
            push(date, vendor, desc, plant,
                 fmt_qty(qty, rng), uom[rng.randrange(len(uom))],
                 fmt_amount(amount), cur, "47%07d" % (3200000 + len(rows)),
                 1 if rng.random() < 0.18 else 0)

    # Credit notes — negative amounts that have to net correctly in the report.
    for _ in range(8):
        src = rows[rng.randrange(len(rows))][:]
        src[1] = "55%07d" % (doc + rng.randrange(900))
        src[3] = "CREDIT NOTE - " + src[3]
        src[8] = "-" + src[8].lstrip("-")
        src[6] = src[6]
        rows.append(src)

    # Exact duplicates — the same document posted twice, which is what the review queue
    # exists to catch.
    dupes = []
    for _ in range(6):
        dupes.append(rows[rng.randrange(len(rows))][:])
    rows.extend(dupes)

    # Lines nothing can classify.
    for i in range(12):
        plant = PLANTS[rng.randrange(len(PLANTS))]
        s = suppliers[rng.randrange(len(suppliers))]
        month = QUOTE_MONTHS[rng.randrange(len(QUOTE_MONTHS))][:7]
        push(month + "-%02d" % (1 + rng.randrange(27)),
             variants[s["id"]][rng.randrange(len(variants[s["id"]]))],
             JUNK[i % len(JUNK)], plant, "1", "EA",
             fmt_amount(2000 + rng.random() * 90000), "PHP",
             "47%07d" % (3900000 + i))

    rng.shuffle(rows)
    header = ["Posting Date", "Document No", "Vendor Name", "Material Description",
              "Plant", "Plant Name", "Quantity", "UoM", "Net Amount", "Currency",
              "Purchase Order"]
    return header, rows


def fmt_qty(q, rng):
    """Quantities arrive with thousands separators about half the time, exactly as a
    spreadsheet hands them over."""
    if q >= 1000 and rng.random() < 0.5:
        return "{:,.0f}".format(q)
    return ("%.2f" % q).rstrip("0").rstrip(".") if q != int(q) else str(int(q))


def fmt_amount(a):
    return "{:,.2f}".format(a)


def write_csv(header, rows):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def write_xlsx(path, header, rows):
    """A minimal single-sheet .xlsx, written by hand so the build needs no openpyxl. Every
    cell is an inline string — the reader in spendlib.js handles both inline strings and the
    shared-string table, and a real S/4HANA export uses the latter."""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def col_name(i):
        name = ""
        while True:
            name = chr(ord("A") + i % 26) + name
            i = i // 26 - 1
            if i < 0:
                break
        return name

    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             "<sheetData>"]
    for ri, row in enumerate([header] + rows, start=1):
        cells = []
        for ci, v in enumerate(row):
            cells.append('<c r="%s%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                         % (col_name(ci), ri, esc(v)))
        sheet.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
    sheet.append("</sheetData></worksheet>")

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="ZSPEND" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   "</Relationships>")
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet))


# ================================================================== the wheat workspace
def build_ufm(desk):
    """The wheat desk, modelled as materials. Drivers are built so that

        futures leg + freight leg  ==  q.futures_usd_mt + q.freight_usd_mt

    exactly, which makes the generic premium identical to `residual_usd_mt` — the number the
    Quote Board already shows. The two screens cannot disagree."""
    inst_by_key = {i["key"]: i for i in desk["instruments"]}
    cls_by_code = {c["code"]: c for c in desk["profile"]["classes"]}
    org_by_code = {o["code"]: o for o in desk["profile"]["origins"]}

    used = []
    for q in desk["quotes"]:
        if q["cls"] not in used:
            used.append(q["cls"])

    materials = []
    for code in used:
        c = cls_by_code.get(code, {})
        qs = [q for q in desk["quotes"] if q["cls"] == code]
        inst = inst_by_key.get(q0_inst(qs))
        org = org_by_code.get(c.get("origin", qs[0]["origin"]), {})
        fut = qs[0]["futures_usd_mt"]
        frt = qs[0]["freight_usd_mt"]
        dates = sorted(set(q["received"][:10] for q in qs))
        materials.append({
            "id": "wheat_" + slug(code),
            "name": c.get("label", code),
            "code": code,
            "category": "raw",
            "unit": "MT",
            "currency": "USD",
            "budget_price": None,
            "budget_note": "No budget price is held for wheat on this desk — the mill prices "
                           "against the futures curve and the class median, not a budget rate.",
            # Wheat is about as agricultural as an input gets, so "not applicable" would be
            # nonsense. The crop picture on this desk lives on the Market surface, in Pavin's
            # own driver board and the WASDE clock, and the bulletin says where to find it.
            "crop": {"status": "elsewhere",
                     "reason": "The crop and weather read for wheat lives on the Market "
                               "surface — the driver board and the WASDE clock — rather than "
                               "in a crop circular, so there is nothing to restate here."},
            "drivers": [
                {"key": inst["key"] if inst else "ZWZ26",
                 "name": (inst["label"] if inst else "Futures") + " settlement, in $/t",
                 "source": inst["venue"] if inst else "CME",
                 "unit": "USD/MT", "currency": "USD",
                 "per_unit": 1.0, "offset": 0.0, "kind": "index", "mocked": False,
                 "note": "%s at %s¢/bu × %s bu/t ÷ 100. The same conversion the Quote Board "
                         "uses." % (inst["key"] if inst else "?",
                                    ("%.2f" % inst["last"]) if inst else "?", BU_PER_T),
                 "dates": dates, "values": [fut] * len(dates)},
                {"key": "FRT_" + (org.get("code") or "X"),
                 "name": (org.get("label", "Origin")) + " Supramax freight",
                 "source": "Published range, mid",
                 "unit": "USD/MT", "currency": "USD",
                 "per_unit": 1.0, "offset": 0.0, "kind": "freight", "mocked": False,
                 "note": "Mid of the published %s range for %s. A range, not a fixture." % (
                     ("$%g–%g" % tuple(org["freight_usd_mt"])) if org.get("freight_usd_mt") else "",
                     org.get("label", "the origin")),
                 "dates": dates, "values": [frt] * len(dates)},
            ],
        })

    suppliers = [{"id": slug(s["name"]), "name": s["full"] or s["name"], "short": s["name"],
                  "country": "", "materials": [], "aliases": [s["name"]]}
                 for s in desk["profile"]["suppliers"]]
    sup_ids = {s["name"]: slug(s["name"]) for s in desk["profile"]["suppliers"]}

    quotes = []
    for q in desk["quotes"]:
        mid = "wheat_" + slug(q["cls"])
        sid = sup_ids.get(q["supplier"], slug(q["supplier"]))
        quotes.append({
            "id": q["id"],
            "material_id": mid,
            "supplier_id": sid,
            "date": q["received"][:10],
            "price": q["flat"],
            "currency": "USD",
            "unit": "MT",
            "qty": q["qty"],
            "valid_to": None,
            "source": "email",
            "source_ref": "quote log row " + q["id"],
            "desk_quote_id": q["id"],
        })
    for s in suppliers:
        s["materials"] = sorted(set(q["material_id"] for q in quotes
                                    if q["supplier_id"] == s["id"]))
    return materials, suppliers, quotes


def q0_inst(qs):
    return qs[0]["instrument"]


def slug(s):
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


# ===================================================================================== main
def main():
    rng = random.Random(SEED)
    desk = build_data.build()

    instruments, materials, by_id, suppliers, quotes = build_urc(rng)
    header, rows = build_spend(rng, materials, suppliers, quotes)
    csv_text = write_csv(header, rows)
    write_xlsx(OUT_SAMPLE_XLSX, header, rows)
    OUT_SAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)
    OUT_SAMPLE_CSV.write_text(csv_text, encoding="utf-8", newline="")

    ufm_materials, ufm_suppliers, ufm_quotes = build_ufm(desk)

    payload = {
        "as_of": AS_OF,
        # The dashboard shows one book. The other workspace stays in the payload because
        # tools/lib_tests.js uses it to assert that the generic price model reproduces the
        # original wheat Quote Board's four-way split to the cent - a cross-check worth
        # keeping and worth nothing on screen.
        "ui_workspace": "urc",
        "fx": FX,
        "fx_note": ("Mid rates on " + AS_OF + ", pulled from "
                    + (REAL.get("provider") or "?") + " by tools/fetch_prices.py."
                    if FX_IS_REAL else
                    "Indicative mid rates on " + AS_OF + ". Generated for this build \u2014 no "
                    "feed is wired up, and every screen that converts says so."),
        # What is real here and what is not, assembled from what actually came back rather
        # than written down once and left to rot. Every screen that labels the workspace
        # reads this instead of asserting that the whole thing is invented.
        "provenance": {
            "real_feeds": sorted(set(list((REAL.get("feeds") or {}).keys())
                                     + [k for k in (REAL.get("fx") or {}) if k == "USDPHP"])),
            "fx_real": FX_IS_REAL,
            "provider": REAL.get("provider"),
            "fetched": REAL.get("fetched"),
            "real": "Commodity, FX and freight prices wherever a public market exists for them",
            "generated": "The company, its plants, its suppliers, every quote, every spend "
                         "line, and the index levels for feeds with no public market",
        },
        "workspaces": [
            {
                "key": "ufm",
                "name": desk["profile"]["mill"],
                "short": desk["profile"]["short"],
                "country": desk["profile"]["country"],
                "home_currency": "USD",
                "demo": False,
                "blurb": "The original wheat desk, kept as a fixture: the price model is "
                         "asserted against its reconstructed four-way split. Not on screen.",
                "instruments": None,            # reads DESK.instruments
                "materials": ufm_materials,
                "suppliers": ufm_suppliers,
                "quotes": ufm_quotes,
                "has_spend_sample": False,
                "bulletin_cadence_days": 14,
            },
            {
                "key": "urc",
                "name": "Demo: PH Food Manufacturer",
                "short": "Demo",
                "country": "Philippines",
                "home_currency": "PHP",
                "demo": True,
                "blurb": "Eight materials, fifteen suppliers, twelve months of quotes and "
                         "spend. Every name and figure is invented.",
                "instruments": instruments,
                "materials": materials,
                "suppliers": suppliers,
                "quotes": quotes,
                "has_spend_sample": True,
                "bulletin_cadence_days": 14,
            },
        ],
    }

    OUT_WS.write_text(
        "/* Generated by tools/build_ws.py - do not edit by hand.\n"
        "   The 'urc' workspace is entirely fictional demo data. */\n"
        "window.WS = " + json.dumps(payload, separators=(",", ":")) + ";\n",
        encoding="utf-8")

    OUT_SAMPLE_JS.write_text(
        "/* Generated by tools/build_ws.py - do not edit by hand.\n"
        "   The messy spend export, baked in so the one-click demo works from file://.\n"
        "   The same bytes are on disk at app/data/spend_sample.csv. */\n"
        "window.SPEND_SAMPLE = " + json.dumps({
            "name": "ZSPEND_FY26_export.csv",
            "rows": len(rows),
            "text": csv_text,
        }, separators=(",", ":")) + ";\n",
        encoding="utf-8")

    check(materials, by_id, quotes)

    print("wrote %s  (%.0f KB)" % (OUT_WS, len(OUT_WS.read_text(encoding='utf-8')) / 1024))
    print("wrote %s  (%.0f KB)" % (OUT_SAMPLE_JS,
                                   len(OUT_SAMPLE_JS.read_text(encoding='utf-8')) / 1024))
    print("wrote %s / .xlsx" % OUT_SAMPLE_CSV)
    print("  urc  materials %d  suppliers %d  quotes %d  spend lines %d"
          % (len(materials), len(suppliers), len(quotes), len(rows)))
    print("  ufm  materials %d  suppliers %d  quotes %d"
          % (len(ufm_materials), len(ufm_suppliers), len(ufm_quotes)))


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def check(materials, by_id, quotes):
    """The two planted stories are load-bearing for the demo. Assert them at build time so a
    tweak to the premium table cannot quietly break the script."""
    latest_idx = len(MONTHS) - 1

    def premiums_for(mid):
        """Premium as a share of driver cost, matching P.band in app/js/pricing.js. In money
        it would describe when a quote was made rather than who made it, because the
        underlying moves."""
        out = []
        for q in quotes:
            if q["material_id"] != mid:
                continue
            idx = MONTHS.index(q["date"][:8] + "01")
            dc = driver_cost(by_id[mid], idx)
            if dc:
                out.append((q["price"] - dc) / dc)
        return sorted(out)

    # 1 · the coffee story — Kalinga's premium widens three quarters running, and the latest
    #     quote prints above the band.
    kal = [q for q in quotes if q["material_id"] == "green_coffee" and q["supplier_id"] == "kalinga"]
    kal.sort(key=lambda q: q["date"])
    by_q = {}
    for q in kal:
        idx = MONTHS.index(q["date"][:8] + "01")
        prem = q["price"] - driver_cost(by_id["green_coffee"], idx)
        mon = int(q["date"][5:7])
        qtr = "%s-Q%d" % (q["date"][:4], (mon - 1) // 3 + 1)
        by_q.setdefault(qtr, []).append(prem)
    keys = sorted(by_q)
    avgs = [sum(by_q[k]) / len(by_q[k]) for k in keys]
    rises = 0
    best = 0
    for i in range(1, len(avgs)):
        rises = rises + 1 if avgs[i] > avgs[i - 1] else 0
        best = max(best, rises)
    assert best >= 3, "coffee premium does not rise 3 quarters running: %s" % (
        list(zip(keys, [round(a) for a in avgs])),)

    ps = premiums_for("green_coffee")
    dc = driver_cost(by_id["green_coffee"], latest_idx)
    coffee_hi = dc * (1 + percentile(ps, 0.75))
    last_kal = kal[-1]
    assert last_kal["price"] > coffee_hi, "latest coffee quote is not above the band"

    # 2 · the resin story — inside the band and under budget.
    res = [q for q in quotes if q["material_id"] == "pet_resin" and q["supplier_id"] == "pacpoly"]
    res.sort(key=lambda q: q["date"])
    ps = premiums_for("pet_resin")
    dc = driver_cost(by_id["pet_resin"], latest_idx)
    lo, hi = dc * (1 + percentile(ps, 0.25)), dc * (1 + percentile(ps, 0.75))
    last_res = res[-1]
    assert lo <= last_res["price"] <= hi, "latest resin quote is not inside the band"
    assert last_res["price"] < by_id["pet_resin"]["budget_price"], "resin quote is not under budget"

    # Every budget has to be a plausible rate for the material: a figure below driver cost,
    # or miles above what anyone has ever quoted, is an arithmetic slip rather than a target,
    # and it only shows up later as a spend report that looks broken.
    for m in materials:
        if m["budget_price"] is None:
            continue
        dc = driver_cost(m, latest_idx)
        ps = premiums_for(m["id"])
        fair = dc * (1 + percentile(ps, 0.5))
        gap = (m["budget_price"] - fair) / fair
        assert dc < m["budget_price"], (
            "%s budget %.0f is below its driver cost %.0f" % (m["id"], m["budget_price"], dc))
        assert abs(gap) <= 0.15, (
            "%s budget %.0f is %.0f%% from the fair price %.0f"
            % (m["id"], m["budget_price"], gap * 100, fair))

    print("  planted  coffee %s above band (%s > %s), %d quarters widening"
          % (last_kal["date"], round(last_kal["price"]), round(coffee_hi), best))
    print("           resin  %s inside band (%s..%s) and under budget %s"
          % (last_res["date"], round(lo), round(hi), round(by_id["pet_resin"]["budget_price"])))


if __name__ == "__main__":
    main()
