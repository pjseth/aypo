"""SQLite storage for FDA Philippines food registrations.

Every record is kept: `food_records` holds the latest state of each registration,
`food_record_versions` keeps every distinct version ever seen, so week-over-week
changes (new registrations, renewals, amendments, cancellations) can be rebuilt.
"""
import hashlib
import json
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    n_records INTEGER,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS queries (
    run_id TEXT,
    prefix TEXT,
    http_status INTEGER,
    n_rows INTEGER,
    n_prefix_rows INTEGER,
    seconds REAL,
    split INTEGER,
    error TEXT,
    PRIMARY KEY (run_id, prefix)
);
CREATE TABLE IF NOT EXISTS food_records (
    account_code TEXT PRIMARY KEY,
    product_name TEXT,
    brand_name TEXT,
    risk_type TEXT,
    company_name TEXT,
    issued_on TEXT,
    valid_until TEXT,
    decision TEXT,
    application_type TEXT,
    is_canceled TEXT,
    row_hash TEXT,
    first_seen_run TEXT,
    last_seen_run TEXT,
    last_changed_run TEXT
);
CREATE TABLE IF NOT EXISTS food_record_versions (
    account_code TEXT,
    row_hash TEXT,
    run_id TEXT,
    raw_json TEXT,
    PRIMARY KEY (account_code, row_hash)
);
CREATE INDEX IF NOT EXISTS idx_food_company ON food_records(company_name);
CREATE INDEX IF NOT EXISTS idx_food_issued ON food_records(issued_on);
"""

FIELD_MAP = {
    "PRODUCT_NAME": "product_name",
    "BRAND_NAME": "brand_name",
    "TYPE_FOOD_PRODUCT_LABEL": "risk_type",
    "COMPANY_NAME": "company_name",
    "DENIAL_DATE": "issued_on",  # the API's name for the decision/issuance date
    "DATE_VALIDITY": "valid_until",
    "LOW_RISK_DECISION": "decision",
    "APPLICATION_TYPE_LABEL": "application_type",
    "IS_CANCELED": "is_canceled",
}


def connect(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def _clean(value):
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def _iso_date(value):
    value = _clean(value)
    if not value:
        return None
    for fmt in ("%d %B %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value


def normalize(raw):
    row = {"account_code": _clean(raw.get("ACCOUNTCODE"))}
    for src, dst in FIELD_MAP.items():
        val = raw.get(src)
        row[dst] = _iso_date(val) if dst in ("issued_on", "valid_until") else _clean(val)
    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    row["row_hash"] = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return row, payload


def upsert(con, run_id, raw_rows):
    """Insert or update records; returns (new, changed, unchanged)."""
    new = changed = unchanged = 0
    cur = con.cursor()
    for raw in raw_rows:
        row, payload = normalize(raw)
        if not row["account_code"]:
            continue
        existing = cur.execute(
            "SELECT row_hash FROM food_records WHERE account_code = ?", (row["account_code"],)
        ).fetchone()
        cur.execute(
            "INSERT OR IGNORE INTO food_record_versions VALUES (?, ?, ?, ?)",
            (row["account_code"], row["row_hash"], run_id, payload),
        )
        if existing is None:
            new += 1
            cur.execute(
                """INSERT INTO food_records VALUES
                (:account_code, :product_name, :brand_name, :risk_type, :company_name,
                 :issued_on, :valid_until, :decision, :application_type, :is_canceled,
                 :row_hash, :run, :run, :run)""",
                {**row, "run": run_id},
            )
        elif existing[0] != row["row_hash"]:
            changed += 1
            cur.execute(
                """UPDATE food_records SET product_name=:product_name, brand_name=:brand_name,
                   risk_type=:risk_type, company_name=:company_name, issued_on=:issued_on,
                   valid_until=:valid_until, decision=:decision, application_type=:application_type,
                   is_canceled=:is_canceled, row_hash=:row_hash, last_seen_run=:run,
                   last_changed_run=:run WHERE account_code=:account_code""",
                {**row, "run": run_id},
            )
        else:
            unchanged += 1
            cur.execute(
                "UPDATE food_records SET last_seen_run = ? WHERE account_code = ?",
                (run_id, row["account_code"]),
            )
    con.commit()
    return new, changed, unchanged
