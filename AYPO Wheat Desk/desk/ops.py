"""Vessels and their paperwork.

UFM takes 10-12 Panamax cargoes a year, and each one drags a folder of
documents behind it -- bill of lading, USDA phytosanitary certificate,
fumigation, halal, origin, weight and quality certificates, insurance. Today
they arrive as attachments scattered across email threads from the supplier,
the surveyor, the shipping agent and the bank, which is why nobody can answer
"what's still missing on the February vessel?" without digging.

Synthetic stand-ins; the real version watches the shipment mailbox.
"""
import datetime as dt
import hashlib
import random

DOC_KINDS = [
    ("Bill of lading", "supplier"),
    ("Certificate of origin", "supplier"),
    ("USDA phytosanitary certificate", "supplier"),
    ("Fumigation certificate", "agent"),
    ("Weight certificate", "surveyor"),
    ("Quality / analysis certificate", "surveyor"),
    ("Halal certificate", "supplier"),
    ("Insurance certificate", "broker"),
    ("Charter party", "broker"),
]

CHANNELS = {"supplier": "email", "agent": "email",
            "surveyor": "whatsapp", "broker": "email"}

VESSELS = ["MV Pacific Harmony", "MV Golden Suek", "MV Ocean Pride",
           "MV Nord Quebec", "MV Spring Breeze", "MV Cielo di Tokyo",
           "MV Unity Explorer", "MV Thalassa Hope", "MV Bulk Pangaea",
           "MV Density", "MV Western Santos", "MV Asian Trust",
           "MV Chao Phraya Star", "MV Pacific Talent", "MV Grand Alliance",
           "MV Jubilant Spirit"]

PAIRINGS = [("US PNW", "HRS"), ("US PNW", "HRW"), ("US PNW", "WW"),
            ("Canada", "CWRS"), ("Australia", "ASW")]


def generate(cfg, quotes):
    """-> (shipments, documents). Flat prices are drawn from parsed quotes so
    the vessel history and the quote history agree."""
    rnd = random.Random(cfg["synthetic"]["seed"] + 13)
    today = dt.date.today()
    tonnes_full = cfg["client"]["vessel_tonnes"]
    priced = [q for q in quotes if q.get("flat_usd_mt")]

    shipments, documents = [], []
    # 13 cargoes back, 2 ahead -- roughly 11 a year.
    for i, months_off in enumerate(range(-13, 3)):
        # Step whole months. Stepping 31 days at a time drifts, which silently
        # skips a month and collides two shipment ids on another.
        m = today.month - 1 + months_off
        laycan_from = dt.date(today.year + m // 12, m % 12 + 1, rnd.randint(1, 15))
        laycan_to = laycan_from + dt.timedelta(days=rnd.randint(14, 25))
        origin, ccode = rnd.choice(PAIRINGS)

        # Pooled with other Thai mills when UFM can't fill a Panamax alone.
        pooled = rnd.random() < 0.35
        tonnes = rnd.choice([27500, 30000, 33000]) if pooled else tonnes_full

        if laycan_to < today - dt.timedelta(days=30):
            status = "Delivered"
        elif laycan_to < today:
            status = "Discharging"
        elif laycan_from < today + dt.timedelta(days=35):
            status = "In transit"
        elif laycan_from < today + dt.timedelta(days=70):
            status = "Chartered"
        else:
            status = "Working"

        match = [q for q in priced if q["wheat_class"] == ccode] or priced
        flat = round(rnd.choice(match)["flat_usd_mt"], 2) if match else None
        sid = f"V{laycan_from:%Y%m}-{ccode}"
        shipments.append({
            "id": sid,
            "vessel": VESSELS[i % len(VESSELS)] if status != "Working" else None,
            "origin": origin, "wheat_class": ccode, "tonnes": tonnes,
            "laycan_from": laycan_from.isoformat(), "laycan_to": laycan_to.isoformat(),
            "status": status, "flat_usd_mt": flat,
            "supplier": rnd.choice(cfg["suppliers"])["name"],
            "eta": (laycan_to + dt.timedelta(days=rnd.randint(18, 28))).isoformat(),
        })

        # How complete the folder is depends on how far along the vessel is.
        received_rate = {"Delivered": 0.97, "Discharging": 0.8, "In transit": 0.55,
                         "Chartered": 0.3, "Working": 0.08}[status]
        for kind, who in DOC_KINDS:
            if kind == "Halal certificate" and ccode == "ASW" and rnd.random() < 0.3:
                continue   # not always required on Australian parcels
            got = rnd.random() < received_rate
            overdue = (not got) and status in ("Delivered", "Discharging")
            documents.append({
                "id": hashlib.sha1(f"{sid}{kind}".encode()).hexdigest()[:16],
                "shipment_id": sid, "kind": kind,
                "status": "Received" if got else ("Overdue" if overdue else "Pending"),
                "received_ts": ((laycan_to + dt.timedelta(days=rnd.randint(-5, 21)))
                                .isoformat() if got else None),
                "channel": CHANNELS[who] if got else None,
            })

    return shipments, documents
