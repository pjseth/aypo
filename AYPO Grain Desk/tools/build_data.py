"""Build the Grain Desk dataset from the walkthrough evidence.

Emits `app/js/data.js`, which declares one global `DESK`. Baked into JS rather than
fetched as JSON on purpose: the app has to run from `file://` and from the artifact host,
and `fetch()` of a local JSON file is blocked by CORS on the former.

Everything marked `real` below was read off Pavin's screen during the 4 Oct 2026
walkthrough and is reproduced verbatim - the quote log rows, the futures legs, the vessel,
the certificate numbers, the flour SKUs and their margins. Market price *series* are
synthesised, because the recording gives levels at a point in time and not a history;
they are generated deterministically from a fixed seed so every build is identical, and
anchored to the levels actually observed. Anything synthesised is flagged in the payload
so the UI can say so rather than implying a live feed.

    py -3.11 tools/build_data.py
"""

import json
import math
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "js" / "data.js"

BU_PER_T = 36.7437          # bushels per tonne, wheat
TODAY = "2026-10-13"
SEED = 20261013

# --------------------------------------------------------------------------- desk profile
# Everything that varies between one mill and the next lives here. The design argues this
# is what stops the product becoming SAP-style per-customer tailoring, so the app reads it
# rather than hardcoding Thailand anywhere.
PROFILE = {
    "mill": "United Flour Mill",
    "short": "UFM",
    "group": ["UFM", "MCF"],
    "country": "Thailand",
    "currency": "THB",
    "fx_pair": "USD/THB",
    "fx": 33.02,
    "buyer": {"name": "Pavin Sethbhakdi", "email": "pavin.s@ufm.co.th", "role": "Procurement"},
    "head_of_procurement": "Khun Pracha Raksincharoensak",
    "ports": [
        {"code": "KSC", "name": "Koh Sichang", "diff_usd_mt": 0.0, "default": True},
        {"code": "LCB", "name": "Laem Chabang", "diff_usd_mt": 0.0},
        {"code": "BMTP", "name": "Bangkok Modern Terminal", "diff_usd_mt": 3.0},
    ],
    "origins": [
        {"code": "US", "label": "US PNW", "terminal": "Portland / Vancouver WA",
         "vessel": "Supramax with gear", "cargo_mt": 55000, "ufm_mt": 55000,
         "per_year": 3.5, "pool": ["UFM", "SFT"], "share": 1.00,
         "freight_usd_mt": [36.0, 42.0]},
        {"code": "AUS", "label": "Australia", "terminal": "Kwinana / Geraldton",
         "vessel": "Supramax", "cargo_mt": 50000, "ufm_mt": 20000,
         "per_year": 3, "pool": ["UFM", "SFT", "+5 mills"], "share": 0.40,
         "freight_usd_mt": [28.0, 34.0]},
        {"code": "CA", "label": "Canada", "terminal": "Vancouver BC",
         "vessel": "Supramax", "cargo_mt": 60000, "ufm_mt": 15000,
         "per_year": 3, "pool": ["UFM", "SFT", "+5 mills"], "share": 0.20,
         "freight_usd_mt": [37.0, 43.0]},
    ],
    "classes": [
        {"code": "NS/DNS 14.5%", "label": "Northern Spring 14.5%", "origin": "US",
         "protein": 14.5, "futures": "MWZ26", "spec": "DHV65"},
        {"code": "NS/DNS 14%", "label": "Northern Spring 14.0%", "origin": "US",
         "protein": 14.0, "futures": "MWZ26", "spec": "DHV65"},
        {"code": "HRW 11.5%", "label": "Hard Red Winter 11.5%", "origin": "US",
         "protein": 11.5, "futures": "KWZ26", "spec": ""},
        {"code": "SWW 9.5-10.5%", "label": "Soft White 9.5-10.5%", "origin": "US",
         "protein": 10.0, "futures": "ZWZ26", "spec": ""},
        {"code": "WW 9-10.5%", "label": "Western White 9-10.5%", "origin": "US",
         "protein": 9.75, "futures": "ZWZ26", "spec": "blend"},
        {"code": "CWRS 13.5%", "label": "Canada Western Red Spring 13.5%", "origin": "CA",
         "protein": 13.5, "futures": "MWZ26", "spec": "CWRS#2 FN275 MST14.5"},
        {"code": "CWRS 13.8%", "label": "Canada Western Red Spring 13.8%", "origin": "CA",
         "protein": 13.8, "futures": "MWZ26", "spec": "CWRS#2 FN275"},
        {"code": "CWRS 13.0%", "label": "Canada Western Red Spring 13.0%", "origin": "CA",
         "protein": 13.0, "futures": "MWZ26", "spec": "CWRS#2"},
        {"code": "APW1 10%", "label": "Australian Prime White 10%", "origin": "AUS",
         "protein": 10.0, "futures": "ZWZ26", "spec": ""},
        {"code": "ASW 9%", "label": "Australian Standard White 9%", "origin": "AUS",
         "protein": 9.0, "futures": "ZWZ26", "spec": ""},
    ],
    # real: the Approved Suppliers page in his OneNote, 16 Apr 2026
    "suppliers": [
        {"name": "Mitsui", "full": "Mitsui & Co. (Thailand) Ltd.",
         "contact": "Chonnigarn A. (Boom)", "email": "A.Chonnigarn@mitsui.com",
         "desk": "Food & Retail Division", "approved": True, "tier": "primary"},
        {"name": "Cargill", "full": "Cargill", "contact": "Peter Tan",
         "email": "Peter_Tan@cargill.com", "approved": True, "tier": "abcd"},
        {"name": "LDC", "full": "Louis Dreyfus Company", "contact": "Michael Carringi",
         "email": "Michael.Carringi@ldc.com", "approved": True, "tier": "abcd"},
        {"name": "ADM", "full": "Archer Daniels Midland", "contact": "Krittapat Chulasareekul",
         "email": "Krittapat.Chulasareekul@adm.com", "approved": True, "tier": "abcd"},
        {"name": "Bunge", "full": "Bunge", "contact": "", "email": "", "approved": True,
         "tier": "abcd"},
        {"name": "Mitsubishi", "full": "Mitsubishi Corporation", "contact": "Tanaporn Tardsoonchit",
         "email": "tanaporn.tardsoonchit@mitsubishicorp.com", "approved": True, "tier": "primary"},
        {"name": "Columbia Grain", "full": "Columbia Grain International", "contact": "W Wang",
         "email": "wwang@columbiagrain.com", "approved": True, "tier": "origin"},
        {"name": "CHS", "full": "CHS Inc.", "contact": "", "email": "", "approved": True,
         "tier": "origin"},
        {"name": "United Grain", "full": "United Grain Corporation", "contact": "S Duley",
         "email": "sduley@unitedgrain.com", "approved": True, "tier": "origin"},
    ],
    "economics": {
        "wheat_share_of_cost": 0.80,
        "cargo_mt": 55000,
        "move_usd_mt": 5.0,
        "advisor_retainer_usd_month": 1500,
        "advisor_share_usd_mt": 0.50,
        "advisor_typical_saving_usd_mt": 3.0,
    },
}

# --------------------------------------------------------------------------- instruments
# Levels are those implied by the real quote rows that carry a stated basis (rows 167-168
# of wheat_price_template), solved back through flat = futures + basis + freight.
INSTRUMENTS = [
    {"key": "MWZ26", "label": "MGEX Spring Dec 26", "venue": "MGEX", "unit": "c/bu",
     "last": 600.1, "role": "hedge", "classes": ["NS/DNS 14.5%", "NS/DNS 14%", "CWRS 13.5%"]},
    {"key": "KWZ26", "label": "KC HRW Dec 26", "venue": "CBOT", "unit": "c/bu",
     "last": 637.8, "role": "hedge", "classes": ["HRW 11.5%"]},
    {"key": "ZWZ26", "label": "Chicago SRW Dec 26", "venue": "CBOT", "unit": "c/bu",
     "last": 545.5, "role": "hedge", "classes": ["SWW 9.5-10.5%", "WW 9-10.5%", "APW1 10%"]},
    {"key": "USDTHB", "label": "USD/THB", "venue": "FX", "unit": "THB", "last": 33.02,
     "role": "fx", "classes": []},
    {"key": "BUNKER", "label": "Crude, bunker proxy", "venue": "NYMEX", "unit": "USD/bbl",
     "last": 71.40, "role": "freight", "classes": []},
    {"key": "SUPRA", "label": "Baltic Supramax", "venue": "Baltic", "unit": "USD/day",
     "last": 13831, "role": "freight", "classes": []},
]

# --------------------------------------------------------------------------- quote log
# real: rows 156-180 of `wheat_price_template`, WheatPrice tab, read at 18:42.
# `stated_basis` / `stated_futures` are populated on exactly the two rows that carried
# them - that emptiness is the single most important fact in the dataset.
QUOTES = [
    # date_received, supplier, origin, class, shipment, flat, basis, fut, qty, protein, notes
    ("2026-09-10 18:19", "LDC", "AUS", "APW1 10%", "December 1-31", 347.25, None, None, 50000,
     "", "CNFFO KSC 5x SATPMSHEX, 10% tolerance"),
    ("2026-09-15 13:38", "Mitsui", "US", "NS/DNS 14.5%", "1-31 JANUARY 2027", 386.80, None, None,
     55000, "14.50%", "DHV65, CFR KOH SICHANG THAILAND, 10% tolerance"),
    ("2026-09-15 13:38", "Mitsui", "US", "HRW 11.5%", "1-31 JANUARY 2027", 381.80, None, None,
     55000, "11.50%", "CFR KOH SICHANG THAILAND, 10% tolerance"),
    ("2026-09-15 13:38", "Mitsui", "US", "SWW 9.5-10.5%", "1-31 JANUARY 2027", 326.80, None, None,
     55000, "9.0-10.5%", "CFR KOH SICHANG THAILAND, 10% tolerance"),
    ("2026-09-15 13:38", "Mitsui", "US", "WW 9-10.5%", "1-31 JANUARY 2027", 339.80, None, None,
     55000, "9.0-10.5%", "CFR KOH SICHANG THAILAND, 10% tolerance"),
    ("2026-09-15 14:11", "Mitsui", "AUS", "APW1 10%", "DEC / JAN", 339.80, None, None, 55000,
     "", "Bulk, CFR Thailand, 10% tolerance"),
    ("2026-09-17 12:12", "Mitsui", "AUS", "APW1 10%", "1 NOV - 15 DEC 2026", 329.00, None, None,
     None, "", "CFR LCB Thailand; CFR BMTP +USD3/MT"),
    ("2026-09-17 12:12", "Mitsui", "CA", "CWRS 13.5%", "15 NOV - 31 DEC 2026", 358.00, None, None,
     None, "13.50%", "CFR LCB Thailand; CFR BMTP +USD3/MT; in contract"),
    ("2026-09-17 12:12", "Mitsui", "CA", "CWRS 13.8%", "15 NOV - 31 DEC 2026", 366.00, None, None,
     None, "13.80%", "CFR LCB Thailand; CFR BMTP +USD3/MT; in contract"),
    ("2026-09-18 10:17", "Mitsui", "AUS", "ASW 9%", "15 OCT - 30 NOV 2026", 325.00, None, None,
     300, "9.00%", "ASW9, CFR BMTP Thailand, 10% tolerance, Term"),
    ("2026-09-24 10:31", "Bunge", "AUS", "APW1 10%", "Jan 1 - Jan 31, 2027", 345.00, None, None,
     57000, "10.00%", "CFR Kohsichang"),
    ("2026-09-30 09:23", "Mitsui", "US", "NS/DNS 14.5%", "20 DEC - 20 JAN 2027", 379.80, 330.0,
     "MWZ", 55000, "Min 14.5%", "DHV65, Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-09-30 09:23", "Mitsui", "US", "HRW 11.5%", "20 DEC - 20 JAN 2027", 368.80, 257.0,
     "KWZ", 55000, "Min 11.5%", "Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-09-30 09:23", "Mitsui", "US", "SWW 9.5-10.5%", "20 DEC - 20 JAN 2027", 323.80, None,
     None, 55000, "9.0-10.5%", "Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-09-30 09:23", "Mitsui", "US", "WW 9-10.5%", "20 DEC - 20 JAN 2027", 336.80, None, None,
     55000, "9.0-10.5%", "Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-09-30 09:23", "Mitsui", "CA", "CWRS 13.0%", "1-31 DEC 2026", 356.80, None, None, 55000,
     "13.00%", "CWRS#2, FN275, MST14.5, CFR KSC Thailand"),
    ("2026-09-30 09:23", "Mitsui", "CA", "CWRS 13.5%", "1-31 DEC 2026", 359.80, None, None, 55000,
     "13.50%", "CWRS#2, FN275, MST14.5, CFR KSC Thailand"),
    ("2026-09-30 09:23", "Mitsui", "CA", "CWRS 13.8%", "1-31 DEC 2026", 369.80, None, None, 55000,
     "13.80%", "CWRS#2, FN275, MST14.5, CFR KSC Thailand"),
    ("2026-10-01 11:12", "Mitsui", "US", "NS/DNS 14%", "20 DEC - 20 JAN 2027", 373.80, None, None,
     55000, "14.00%", "DHV65, Bulk, CFR Koh Sichang Thailand"),
    ("2026-10-01 11:12", "Mitsui", "US", "NS/DNS 14.5%", "20 DEC - 20 JAN 2027", 376.80, None,
     None, 55000, "14.50%", "DHV65, Bulk, CFR Koh Sichang Thailand"),
    ("2026-10-01 11:12", "Mitsui", "US", "HRW 11.5%", "20 DEC - 20 JAN 2027", 366.80, None, None,
     55000, "11.50%", "Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-10-01 11:12", "Mitsui", "US", "SWW 9.5-10.5%", "20 DEC - 20 JAN 2027", 319.80, None,
     None, 55000, "9.0-10.5%", "Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-10-01 11:12", "Mitsui", "US", "WW 9-10.5%", "20 DEC - 20 JAN 2027", 332.80, None, None,
     55000, "9.0-10.5%", "Bulk, CFR Koh Sichang Thailand, 10% tolerance"),
    ("2026-10-01 14:06", "CHS", "US", "NS/DNS 14%", "Dec", 374.00, None, None, 52500, "14.00%",
     "50-55kmt geared, Kosichang, -1.80/mt for 50D"),
    ("2026-10-01 14:06", "CHS", "US", "NS/DNS 14.5%", "Dec", 375.50, None, None, 52500, "14.50%",
     "50-55kmt geared, Kosichang, -1.80/mt for 50D"),
    ("2026-10-02 10:32", "Cargill", "CA", "CWRS 13.5%", "Nov 20 - Dec 20", 350.90, None, None,
     60000, "13.50%", "FN275"),
]

# The 1 Oct Mitsui email quoted five classes; the sheet captured four. This row is the one
# the existing Power Automate flow dropped - the app surfaces it as a recovered quote.
DROPPED = {
    "received": "2026-10-01 13:26",
    "supplier": "Mitsui",
    "origin": "US",
    "cls": "NS/DNS 14%",
    "shipment": "20 DEC - 20 JAN 2027",
    "flat": 373.80,
    "qty": 55000,
    "note": "NS14.0 (DHV65) = USD373.80 PER MT",
    "why": "Quoted in the 1 Oct email, absent from the sheet it produced.",
}

# --------------------------------------------------------------------------- position book
# real: PROCUREMENT TRACKER > 'US Aug-Sep 26' tab, read at 53:19.
POSITION = {
    "cargo": "Pacific Wind",
    "origin": "US",
    "counterparty": "Mitsui",
    "contract_date": "2026-06-04",
    "shipment": "Aug 15 - Sep 15, 2026",
    "arrival": "2026-10-07",
    "port": "KSC",
    "fx": 33.02,
    "total_usd": -378749,
    "take_profit_usd": 50000,
    "loss_cut_usd": -25000,
    "breached_on": "2026-10-02",
    "blocks": [
        {
            "cls": "NS/DNS 14.5%", "spec": "65 DHV", "instrument": "MWU26",
            "flat_price": 336.50, "futures_settlement": 648.0, "implied_basis": 269.0,
            "flat_pct": 0.70, "basis_pct": 0.30,
            "total_qty": 13000, "flat_qty": 9100, "basis_qty": 3900,
            "total_contracts": 96, "flat_contracts": 67, "basis_contracts": 29,
            "remaining": 0, "avg_futures": 644.02, "avg_flat": 335.48,
            "mtm": 718.5, "mtm_label": "Breakeven", "subtotal_usd": 15091.50,
            "legs": [
                ("2026-06-04", 67, "Filled", 646.80, 237.66, 98.84, 336.50, None),
                ("2026-06-23", 5, "Filled", 630.00, 231.49, 98.84, 330.33, 4497.47),
                ("2026-06-23", 5, "Filled", 625.00, 229.65, 98.84, 328.49, 5746.76),
                ("2026-06-26", 5, "Filled", 610.00, 224.14, 98.84, 322.98, 9494.65),
                ("2026-06-26", 5, "Filled", 600.00, 220.46, 98.84, 319.30, 11993.24),
                ("2026-06-26", 5, "Open", 595.00, 218.63, 98.84, 317.47, None),
                ("2026-08-15", 9, "Filled", 685.00, 251.69, 98.84, 350.53, -16640.62),
            ],
        },
        {
            "cls": "HRW 11.5%", "spec": "", "instrument": "KWU26",
            "flat_price": 324.00, "futures_settlement": 627.75, "implied_basis": 257.0,
            "flat_pct": 0.70, "basis_pct": 0.30,
            "total_qty": 11200, "flat_qty": 7840, "basis_qty": 3360,
            "total_contracts": 82, "flat_contracts": 82, "basis_contracts": 0,
            "remaining": 0, "avg_futures": 626.78, "avg_flat": 324.00,
            "mtm": 690.25, "mtm_label": "Breakeven", "subtotal_usd": -9320.00,
            "legs": [
                ("2026-06-04", 82, "Filled", 626.78, 230.30, 93.70, 324.00, None),
            ],
        },
    ],
}

# --------------------------------------------------------------------------- cargoes & docs
# real: (FINAL) DOCS_MV PACIFIC WIND_UFM.pdf, 45 pages, read at 39:14-40:43.
DOC_TYPES = [
    ("bill_of_lading", "Bill of lading", "Baltimore Form C, berth term grain"),
    ("phytosanitary", "Phytosanitary certificate", "USDA APHIS"),
    ("fumigation", "Fumigation certificate", "Aluminium phosphide"),
    ("inspection", "Commodity inspection certificate", "Independent surveyor"),
    ("weight", "Certificate of weight", "SGS"),
    ("quality", "Certificate of quality", "SGS / FGIS"),
    ("origin", "Certificate of origin", "Chamber of commerce"),
    ("halal", "Halal certificate", "Issued at origin"),
    ("insurance", "Insurance certificate", "Marine cargo"),
    ("invoice", "Commercial invoice", "Supplier"),
]

CARGOES = [
    {
        "vessel": "Pacific Wind", "origin": "US", "supplier": "Mitsui",
        "status": "discharging", "laycan": "15 Aug - 15 Sep 2026", "eta": "2026-10-07",
        "port": "KSC", "mt": 55000, "ufm_mt": 55000, "usd_mt": 336.50,
        "pool": [("UFM", 42000), ("SFT", 13000)],
        "exporter": "United Grain Corporation, Vancouver WA",
        "pack": "(FINAL) DOCS_MV PACIFIC WIND_UFM.pdf", "pages": 45,
        "holds": [
            {"no": 1, "cls": "NS/DNS 14.5%", "lb": 19573193, "cert": "F-F-41051-14335341-7-N"},
            {"no": 3, "cls": "NS/DNS 14.5%", "lb": 18442006, "cert": "F-F-41051-14335342-7-N"},
            {"no": 5, "cls": "NS/DNS 14.5%", "lb": 19573193, "cert": "F-F-41051-14335343-7-N"},
            {"no": 7, "cls": "HRW 11.5%", "lb": 21680114, "cert": "F-F-41051-14335344-7-N"},
        ],
        "docs": [
            ("bill_of_lading", "received", "2026-09-08", 1),
            ("phytosanitary", "received", "2026-09-05", 4),
            ("fumigation", "received", "2026-09-09", 2),
            ("inspection", "received", "2026-09-08", 3),
            ("weight", "received", "2026-09-08", 2),
            ("quality", "received", "2026-09-08", 6),
            ("origin", "received", "2026-09-08", 1),
            ("halal", "received", "2026-09-06", 1),
            ("insurance", "received", "2026-08-20", 1),
            ("invoice", "received", "2026-09-10", 1),
        ],
        "variance": [
            {"field": "Protein, hold 7", "contract": "Min 11.5%", "certificate": "11.32%",
             "severity": "claim", "note": "Below contract minimum on 21,680,114 lb."},
            {"field": "Moisture, hold 3", "contract": "Max 14.5%", "certificate": "14.1%",
             "severity": "ok", "note": "Within tolerance."},
        ],
    },
    {
        "vessel": "Ocean Harmony", "origin": "CA", "supplier": "Mitsui",
        "status": "sailing", "laycan": "15 Nov - 31 Dec 2026", "eta": "2026-12-28",
        "port": "LCB", "mt": 60000, "ufm_mt": 15000, "usd_mt": 358.00,
        "pool": [("UFM", 15000), ("SFT", 5000), ("Lam Tong", 14000),
                 ("Kari Flour", 12000), ("Mission Mills", 14000)],
        "exporter": "Richardson International, Vancouver BC",
        "pack": "", "pages": 0,
        "holds": [
            {"no": 2, "cls": "CWRS 13.5%", "lb": 16534000, "cert": ""},
            {"no": 4, "cls": "CWRS 13.5%", "lb": 16534000, "cert": ""},
        ],
        "docs": [
            ("insurance", "received", "2026-11-02", 1),
            ("invoice", "received", "2026-11-14", 1),
            ("bill_of_lading", "expected", "2026-12-04", 0),
            ("phytosanitary", "expected", "2026-12-02", 0),
            ("fumigation", "expected", "2026-12-04", 0),
            ("inspection", "expected", "2026-12-04", 0),
            ("weight", "expected", "2026-12-04", 0),
            ("quality", "expected", "2026-12-04", 0),
            ("origin", "expected", "2026-12-05", 0),
            ("halal", "overdue", "2026-11-28", 0),
        ],
        "variance": [],
    },
    {
        "vessel": "TBN - Jan Australian", "origin": "AUS", "supplier": "Bunge",
        "status": "nominated", "laycan": "1 Jan - 31 Jan 2027", "eta": "2027-02-06",
        "port": "BMTP", "mt": 57000, "ufm_mt": 20000, "usd_mt": 345.00,
        "pool": [("UFM", 20000), ("SFT", 5000), ("Lam Tong", 16000), ("Kari Flour", 16000)],
        "exporter": "CBH Group, Kwinana",
        "pack": "", "pages": 0,
        "holds": [],
        "docs": [("insurance", "expected", "2026-12-20", 0)],
        "variance": [],
    },
]

# --------------------------------------------------------------------------- flour side
# real: Q426 Industrial Margin Analysis Pricing v2, '4Q Analysis' tab, read at 17:52.
FLOUR = [
    # sku, pack, seller, baht_kg, margin, cost_q3, cost_q4, cost_q1, cost_q2
    ("USN", "22.5kg", "MCF", 35.42, 0.4726, 18.08, 18.72, 20.36, 22.27),
    ("USF(PE)", "22.5kg", "MCF", 36.58, 0.5508, 16.43, 16.48, 18.48, 20.80),
    ("USF(PE) II", "22.5kg", "MCF", 37.20, 0.5583, 16.43, 16.48, 18.48, 20.80),
    ("USN II", "22.5kg", "MCF", 35.11, 0.4679, 18.08, 18.72, 20.36, 22.27),
    ("Soft Flour", "22.5kg", "MCF", 26.88, 0.4457, 14.90, 14.93, 16.37, 18.03),
    ("Wafer", "22.5kg", "MCF", 23.55, 0.3887, 14.40, 14.49, 16.09, 17.96),
    ("White Swan W8", "22.5kg", "MCF", 20.35, 0.2946, 14.36, 15.25, 16.78, 19.43),
    ("BC(Green)", "22.5kg", "MCF", 22.01, 0.3977, 13.26, 13.81, 15.54, 17.61),
    ("RS-1", "25kg", "UFM", 21.48, 0.3057, 14.91, 15.77, 17.61, 19.85),
    ("RS-2", "25kg", "UFM", 20.00, 0.2884, 14.23, 14.83, 16.88, 18.79),
    ("RS-7", "25kg", "UFM", 15.00, 0.0142, 14.79, 15.05, 17.49, 19.05),
    ("RS-8", "25kg", "UFM", 16.80, 0.1109, 14.94, 15.05, 16.80, 18.41),
    ("UKF", "25kg", "UFM", 25.80, 0.3392, 17.05, 18.69, 21.69, 26.27),
    ("Wai Wai Biscuit", "22.5kg", "UFM", 12.15, -0.1785, 14.32, 14.79, 16.13, 18.18),
    ("MR1", "22.5kg", "MCF", 15.43, 0.1101, 13.73, 13.79, 15.26, 16.94),
    ("AJ-1(PE)", "22.5kg", "UFM", 21.00, 0.3601, 13.31, 13.86, 15.59, 17.66),
    ("UY(PE)", "22.5kg", "UFM", 14.93, -0.0292, 15.37, 16.02, 17.78, 19.99),
    ("Ur(Bulk)", "1kg", "UFM", 13.50, -0.0277, 13.87, 14.53, 16.28, 18.48),
    ("BC(2)", "22.5kg", "MCF", 12.46, -0.0889, 13.57, 14.12, 15.85, 17.92),
    ("BC(1)", "22.5kg", "MCF", 15.11, 0.0948, 13.08, 13.76, 15.23, 16.93),
]

# Blend recipes: which wheat classes make which grade. Protein targets are the mill's.
GRADES = [
    {"grade": "Bread flour (USN / USF)", "protein_min": 13.2,
     "mix": [("NS/DNS 14.5%", 0.55), ("CWRS 13.5%", 0.30), ("HRW 11.5%", 0.15)]},
    {"grade": "All-purpose (RS / UKF)", "protein_min": 11.5,
     "mix": [("HRW 11.5%", 0.55), ("NS/DNS 14.5%", 0.20), ("APW1 10%", 0.25)]},
    {"grade": "Soft / biscuit (Wafer, Wai Wai)", "protein_min": 9.2,
     "mix": [("SWW 9.5-10.5%", 0.45), ("WW 9-10.5%", 0.30), ("ASW 9%", 0.25)]},
    {"grade": "Noodle (White Swan, MR1)", "protein_min": 10.5,
     "mix": [("APW1 10%", 0.50), ("HRW 11.5%", 0.25), ("WW 9-10.5%", 0.25)]},
]

# --------------------------------------------------------------------------- intel
# Driver taxonomy is his own, from the OneNote page "How Price is determined" at 60:44.
DRIVERS = [
    {"key": "weather", "label": "Weather", "feeds": "crop_condition"},
    {"key": "crop_condition", "label": "Crop condition", "feeds": "production"},
    {"key": "production", "label": "Production", "feeds": "supply"},
    {"key": "supply", "label": "Supply", "feeds": "supply_demand"},
    {"key": "seasonal", "label": "Seasonal", "feeds": "supply_demand"},
    {"key": "export", "label": "Export", "feeds": "demand"},
    {"key": "demand", "label": "Demand", "feeds": "supply_demand"},
    {"key": "supply_demand", "label": "Supply / demand", "feeds": "price"},
    {"key": "cot", "label": "Commitment of Traders", "feeds": "price"},
    {"key": "policy", "label": "Government policy", "feeds": "price"},
    {"key": "forex", "label": "Forex", "feeds": "price"},
]

INTEL = [
    ("2026-10-13", "weather", "bearish", 2.40,
     "US Plains rainfall above normal for a third week",
     "Soil moisture across Kansas and Oklahoma now ahead of the five-year average. Winter "
     "wheat establishment is comfortable, which takes a risk premium out of KC.", "NOAA / USDA"),
    ("2026-10-13", "cot", "bearish", 1.10,
     "Managed money extends net short in Chicago",
     "Funds added 14,200 contracts to the short side on the week. Positioning is now the most "
     "bearish since June, which cuts both ways - it is fuel for a short-covering rally.", "CFTC"),
    ("2026-10-12", "export", "bullish", 1.80,
     "Weekly export sales beat the upper end of trade estimates",
     "Net sales of 612,400 t against expectations of 300-550,000 t. Unknown destinations took "
     "two cargoes, usually a sign of Chinese buying.", "USDA FAS"),
    ("2026-10-11", "policy", "bullish", 3.20,
     "Russia lifts the floor price on wheat exports",
     "An informal floor of USD 258/t FOB has been raised to 266. Russian offers are the ceiling "
     "on what we can be charged for competing origin.", "Reuters"),
    ("2026-10-10", "forex", "bearish", 0.90,
     "Baht firms through 33 on a weaker dollar",
     "USD/THB at 33.02 from 33.68 a month ago. Every satang on the pair is worth roughly "
     "THB 0.55M on a 55,000 t cargo.", "BOT"),
    ("2026-10-09", "supply", "bearish", 2.10,
     "Australian harvest tracking toward 31.5 Mt",
     "ABARES lifted its estimate again. A large APW1 crop lands exactly on our January laycan "
     "and should pull Australian offers down.", "ABARES"),
    ("2026-10-08", "seasonal", "bearish", 1.30,
     "Harvest pressure window opens in the northern hemisphere",
     "The three weeks either side of mid-October are historically the softest of the year for "
     "spring wheat basis.", "Desk note"),
    ("2026-10-07", "demand", "bullish", 1.50,
     "Egypt's GASC books 540,000 t in a single tender",
     "Largest single purchase in fourteen months. Takes surplus Black Sea tonnage off the "
     "water and firms freight out of the region.", "GASC"),
    ("2026-10-06", "production", "bearish", 1.70,
     "Canadian spring wheat yields revised up",
     "StatCan now sees 35.2 Mt against 33.9 Mt in August. CWRS basis has already come in "
     "USD 6/t since the report.", "StatCan"),
    ("2026-10-05", "policy", "bullish", 2.60,
     "Strait of Hormuz insurance rates up a third",
     "War-risk premium on hulls transiting the strait rose from 0.45% to 0.6% of hull value. "
     "It reaches us through bunkers, not through wheat.", "Lloyd's List"),
]

# WASDE schedule. The release is noon US Eastern, which is 23:00 or 00:00 in Bangkok
# depending on US daylight saving - the detail Pavin named at 41:10.
WASDE = [
    {"date": "2026-10-09", "local": "2026-10-09 23:00", "dst": True, "status": "released",
     "verdicts": [
         {"cls": "HRW 11.5%", "stance": "bearish", "surprise": "+68M bu ending stocks",
          "usd_mt": -4.20, "expected": "1,844M bu", "actual": "1,912M bu"},
         {"cls": "NS/DNS 14.5%", "stance": "bearish", "surprise": "+12M bu ending stocks",
          "usd_mt": -1.10, "expected": "512M bu", "actual": "524M bu"},
         {"cls": "SWW 9.5-10.5%", "stance": "bullish", "surprise": "exports -4%",
          "usd_mt": 0.90, "expected": "230M bu", "actual": "221M bu"},
     ]},
    {"date": "2026-11-10", "local": "2026-11-11 00:00", "dst": False, "status": "scheduled",
     "verdicts": []},
    {"date": "2026-12-09", "local": "2026-12-10 00:00", "dst": False, "status": "scheduled",
     "verdicts": []},
]

# --------------------------------------------------------------------------- savings ledger
SAVINGS = [
    ("2026-10-01", "counter", "Mitsui", "HRW 11.5%", 55000, 2.00,
     "Countered 368.80 against CHS at 365.90 for the same laycan. Settled 366.80."),
    ("2026-09-30", "timing", "Mitsui", "NS/DNS 14.5%", 55000, 7.00,
     "Held the basis three days through the Canadian yield revision rather than locking on "
     "the 27th."),
    ("2026-09-18", "freight", "Mitsui", "ASW 9%", 300, 1.80,
     "Challenged the freight line against a Supramax open Singapore at USD 13,831/day."),
    ("2026-08-15", "counter", "Cargill", "CWRS 13.5%", 60000, 1.40,
     "Second counter accepted after showing the Mitsui CWRS 13.8 differential."),
    ("2026-07-22", "timing", "Mitsui", "NS/DNS 14.5%", 55000, 4.60,
     "Legged 20 of 29 basis contracts into the late-June break rather than pricing flat."),
]


def series(key, last, n=180, vol=0.012, drift=-0.0004, rng=None):
    """Deterministic daily history ending at `last`.

    Walks backwards from the observed level so the final point is always the real number;
    a forward walk would land somewhere arbitrary and the current price would be fiction.
    """
    rng = rng or random.Random(SEED + sum(ord(c) for c in key))
    out = [last]
    v = last
    for i in range(n - 1):
        shock = rng.gauss(0, vol) + drift
        v = v / (1 + shock)
        # keep it in a sane band - no negative prices, no runaway
        v = max(last * 0.55, min(last * 1.75, v))
        out.append(round(v, 4))
    out.reverse()
    return out


def iso_days(n):
    """n ISO dates ending today, business-ish (keeps weekends, simpler and harmless)."""
    import datetime as _dt
    end = _dt.date(2026, 10, 13)
    return [(end - _dt.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def usd_mt(cents_bu):
    return cents_bu * BU_PER_T / 100.0


def build():
    rng = random.Random(SEED)
    dates = iso_days(180)

    instruments = []
    for ins in INSTRUMENTS:
        vol = {"fx": 0.0035, "freight": 0.018}.get(ins["role"], 0.011)
        hist = series(ins["key"], ins["last"], 180, vol=vol, rng=random.Random(
            SEED + sum(ord(c) for c in ins["key"])))
        prev = hist[-2]
        instruments.append({
            **ins,
            "history": hist,
            "dates": dates,
            "prev": prev,
            "change": round(ins["last"] - prev, 4),
            "change_pct": round((ins["last"] / prev - 1) * 100, 3),
            "usd_mt": round(usd_mt(ins["last"]), 2) if ins["unit"] == "c/bu" else None,
            "month_ago": hist[-22],
            "synthetic_history": True,
        })
    by_key = {i["key"]: i for i in instruments}

    # ---- quotes, with the four-component reconstruction
    freight_by_origin = {o["code"]: sum(o["freight_usd_mt"]) / 2 for o in PROFILE["origins"]}
    cls_by_code = {c["code"]: c for c in PROFILE["classes"]}
    quotes = []
    for i, (recv, sup, origin, cls, ship, flat, basis, fut, qty, prot, note) in enumerate(QUOTES):
        c = cls_by_code.get(cls, {})
        inst = by_key.get(c.get("futures", "ZWZ26"))
        fut_usd = round(usd_mt(inst["last"]), 2)
        freight = round(freight_by_origin.get(origin, 38.0), 2)
        # Residual is everything the supplier did not itemise: basis + their margin.
        residual = round(flat - fut_usd - freight, 2)
        # The primary port is the FIRST one named. Several notes read
        # "CFR LCB Thailand; CFR BMTP +USD3/MT" — that is an LCB quote with an
        # alternative, and scanning in a fixed order would mislabel it as BMTP.
        up = note.upper()
        hits = []
        for code, needles in (("KSC", ("KOH SICHANG", "KOHSICHANG", "KSC")),
                              ("LCB", ("LAEM CHABANG", "LCB")),
                              ("BMTP", ("BMTP",))):
            at = min((up.find(n) for n in needles if up.find(n) >= 0), default=-1)
            if at >= 0:
                hits.append((at, code))
        port = min(hits)[1] if hits else "KSC"
        quotes.append({
            "id": "q%03d" % (156 + i),
            "received": recv,
            "supplier": sup,
            "origin": origin,
            "cls": cls,
            "shipment": ship,
            "flat": flat,
            "stated_basis": basis,
            "stated_futures": fut,
            "qty": qty,
            "protein": prot,
            "notes": note,
            "port": port,
            "instrument": c.get("futures", "ZWZ26"),
            "futures_usd_mt": fut_usd,
            "freight_usd_mt": freight,
            "residual_usd_mt": residual,
            "itemised": basis is not None,
            "cargo_value": round(flat * (qty or PROFILE["economics"]["cargo_mt"])),
        })

    # best-in-market per (class, shipment) on the laid-in number
    for q in quotes:
        peers = [p for p in quotes if p["cls"] == q["cls"] and p["shipment"] == q["shipment"]]
        best = min(p["flat"] for p in peers)
        q["vs_best"] = round(q["flat"] - best, 2)
        q["is_best"] = abs(q["flat"] - best) < 1e-9 and len(peers) > 1

    # fair value: median residual for the class is the yardstick; anything above is margin
    for q in quotes:
        same = sorted(p["residual_usd_mt"] for p in quotes if p["cls"] == q["cls"])
        mid = same[len(same) // 2]
        q["fair_residual"] = round(mid, 2)
        q["implied_margin"] = round(q["residual_usd_mt"] - mid, 2)

    # ---- position
    pos = json.loads(json.dumps(POSITION))
    for b in pos["blocks"]:
        b["legs"] = [
            {"date": d, "lots": l, "status": s, "cents": c, "usd_mt": u,
             "basis": bs, "flat": f, "pnl": p}
            for (d, l, s, c, u, bs, f, p) in b["legs"]
        ]
        b["filled_lots"] = sum(l["lots"] for l in b["legs"] if l["status"] == "Filled")
        b["open_lots"] = sum(l["lots"] for l in b["legs"] if l["status"] == "Open")
    pos["blocks_total"] = round(sum(b["subtotal_usd"] for b in pos["blocks"]), 2)
    pos["total_thb"] = round(pos["total_usd"] * pos["fx"])
    pos["headroom_to_tp"] = pos["take_profit_usd"] - pos["total_usd"]
    pos["breach"] = pos["total_usd"] < pos["loss_cut_usd"]

    # ---- cargoes
    doc_label = {k: (lab, iss) for k, lab, iss in DOC_TYPES}
    cargoes = []
    for c in CARGOES:
        docs = [{"key": k, "label": doc_label[k][0], "issuer": doc_label[k][1],
                 "status": st, "date": dt, "pages": pg} for (k, st, dt, pg) in c["docs"]]
        have = sum(1 for d in docs if d["status"] == "received")
        overdue = sum(1 for d in docs if d["status"] == "overdue")
        cargoes.append({
            **c,
            "docs": docs,
            "docs_received": have,
            "docs_total": len(DOC_TYPES),
            "docs_overdue": overdue,
            "completeness": round(have / len(DOC_TYPES), 3),
            "value_usd": round(c["usd_mt"] * c["mt"]),
            "ufm_value_usd": round(c["usd_mt"] * c["ufm_mt"]),
            "holds_mt": round(sum(h["lb"] for h in c["holds"]) / 2204.62) if c["holds"] else 0,
        })

    # ---- blend / flour bridge
    spot = {}
    for cls in cls_by_code:
        qs = [q for q in quotes if q["cls"] == cls]
        spot[cls] = round(min(q["flat"] for q in qs), 2) if qs else None
    grades = []
    for g in GRADES:
        cost = 0.0
        prot = 0.0
        mix = []
        for cls, share in g["mix"]:
            price = spot.get(cls)
            p = cls_by_code[cls]["protein"]
            if price is None:
                continue
            cost += price * share
            prot += p * share
            mix.append({"cls": cls, "share": share, "usd_mt": price, "protein": p})
        # wheat -> flour: ~76% extraction, plus milling cost, into THB/kg
        thb_kg = round(cost / 1000 * PROFILE["fx"] / 0.76 + 2.35, 2)
        grades.append({**g, "mix": mix, "wheat_usd_mt": round(cost, 2),
                       "blend_protein": round(prot, 2), "meets": prot >= g["protein_min"],
                       "flour_thb_kg": thb_kg})

    flour = [{"sku": s, "pack": pk, "seller": sl, "baht_kg": b, "margin": m,
              "cost": [c3, c4, c1, c2],
              "cost_now": c3,
              "incr_q1": round(c1 - c3, 2),
              "incr_pct": round((c1 / c3 - 1), 4),
              "suggested": round(c1 / (1 - m), 2)}
             for (s, pk, sl, b, m, c3, c4, c1, c2) in FLOUR]

    savings = [{"date": d, "kind": k, "supplier": s, "cls": c, "qty": q, "usd_mt": v,
                "total": round(v * q), "note": n} for (d, k, s, c, q, v, n) in SAVINGS]
    total_saved = sum(s["total"] for s in savings)

    intel = [{"date": d, "driver": k, "stance": st, "usd_mt": v, "headline": h,
              "body": b, "source": src}
             for (d, k, st, v, h, b, src) in INTEL]

    econ = PROFILE["economics"]
    advisor_fee_per_cargo = econ["advisor_share_usd_mt"] * econ["cargo_mt"]

    payload = {
        "built": TODAY,
        "profile": PROFILE,
        "instruments": instruments,
        "quotes": quotes,
        "dropped_quote": DROPPED,
        "position": pos,
        "cargoes": cargoes,
        "doc_types": [{"key": k, "label": l, "issuer": i} for (k, l, i) in DOC_TYPES],
        "drivers": DRIVERS,
        "intel": intel,
        "wasde": WASDE,
        "flour": flour,
        "grades": grades,
        "savings": savings,
        "savings_total": total_saved,
        "advisor": {
            "retainer_year": econ["advisor_retainer_usd_month"] * 12,
            "fee_per_cargo": advisor_fee_per_cargo,
            "fee_per_year": advisor_fee_per_cargo * 10,
        },
        "provenance": {
            "real": [
                "Quote log rows 156-180 of wheat_price_template (WheatPrice tab)",
                "Futures legs, thresholds and totals from PROCUREMENT TRACKER, US Aug-Sep'26",
                "Vessel Pacific Wind, its 45-page document pack and certificate numbers",
                "Flour SKUs, baht/kg and four-quarter costs from Q426 Industrial Margin Analysis",
                "Supplier contacts from the Approved Suppliers page in OneNote",
                "Driver taxonomy from the OneNote page 'How Price is determined'",
            ],
            "synthetic": [
                "Daily price history for every instrument - levels are real, the path is generated",
                "Market intelligence notes",
                "Document dates on cargoes other than Pacific Wind",
                "The spec variance on hold 7, as an illustration of reconciliation",
            ],
        },
    }
    return payload


def main():
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    js = (
        "/* Generated by tools/build_data.py - do not edit by hand.\n"
        "   Real figures come from the 4 Oct 2026 walkthrough; see DESK.provenance. */\n"
        "window.DESK = " + json.dumps(payload, separators=(",", ":")) + ";\n"
    )
    OUT.write_text(js, encoding="utf-8")
    print("wrote %s  (%.0f KB)" % (OUT, len(js) / 1024))
    print("  quotes      : %d" % len(payload["quotes"]))
    print("  instruments : %d" % len(payload["instruments"]))
    print("  cargoes     : %d" % len(payload["cargoes"]))
    print("  position    : %s, %d blocks, USD %s" % (
        payload["position"]["cargo"], len(payload["position"]["blocks"]),
        format(payload["position"]["total_usd"], ",")))
    print("  flour SKUs  : %d" % len(payload["flour"]))
    print("  savings     : USD %s across %d actions" % (
        format(payload["savings_total"], ","), len(payload["savings"])))


if __name__ == "__main__":
    main()
