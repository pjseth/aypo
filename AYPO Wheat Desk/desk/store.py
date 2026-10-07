"""SQLite schema for the wheat procurement desk.

One database holds every channel the desk pulls from, so the dashboard reads
from a single place instead of six. Tables map to the fragments Pavin chases
today: market data, supplier messages, the quotes parsed out of them, market
intelligence, and the shipment documents that arrive scattered across email.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run        TEXT PRIMARY KEY,
  started    TEXT NOT NULL,
  finished   TEXT,
  note       TEXT
);

-- Daily settlement history per instrument (real market data).
CREATE TABLE IF NOT EXISTS prices (
  symbol  TEXT NOT NULL,
  d       TEXT NOT NULL,
  close   REAL NOT NULL,
  PRIMARY KEY (symbol, d)
);

-- Forward curve: one row per contract month per series, per run.
CREATE TABLE IF NOT EXISTS curve (
  run     TEXT NOT NULL,
  series  TEXT NOT NULL,
  code    TEXT NOT NULL,
  label   TEXT NOT NULL,
  symbol  TEXT NOT NULL,
  close   REAL,
  PRIMARY KEY (run, series, code)
);

-- Raw inbound supplier communication, whatever the channel.
CREATE TABLE IF NOT EXISTS messages (
  id        TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  channel   TEXT NOT NULL,
  supplier  TEXT NOT NULL,
  sender    TEXT,
  subject   TEXT,
  body      TEXT NOT NULL,
  thread    TEXT
);

-- Structured quotes extracted from messages by desk.comms.parse_message.
CREATE TABLE IF NOT EXISTS quotes (
  id             TEXT PRIMARY KEY,
  message_id     TEXT NOT NULL REFERENCES messages(id),
  ts             TEXT NOT NULL,
  supplier       TEXT NOT NULL,
  origin         TEXT,
  wheat_class    TEXT,
  protein        REAL,
  ship_from      TEXT,
  ship_to        TEXT,
  tonnes         INTEGER,
  futures_ref    TEXT,
  futures_c_bu   REAL,
  basis_c_bu     REAL,
  freight_usd_mt REAL,
  margin_usd_mt  REAL,
  flat_usd_mt    REAL,
  flat_stated    INTEGER NOT NULL DEFAULT 0,
  reconcile_gap  REAL,
  confidence     REAL NOT NULL DEFAULT 0,
  missing        TEXT,
  valid_until    TEXT
);
CREATE INDEX IF NOT EXISTS quotes_ts ON quotes(ts);

-- Market intelligence: WASDE releases, weather, policy, FX, geopolitics, funds.
CREATE TABLE IF NOT EXISTS intel (
  id         TEXT PRIMARY KEY,
  ts         TEXT NOT NULL,
  kind       TEXT NOT NULL,
  source     TEXT,
  lever      TEXT NOT NULL,
  stance     TEXT NOT NULL,
  headline   TEXT NOT NULL,
  body       TEXT,
  confidence TEXT
);
CREATE INDEX IF NOT EXISTS intel_ts ON intel(ts);

-- Vessels bought or being worked.
CREATE TABLE IF NOT EXISTS shipments (
  id          TEXT PRIMARY KEY,
  vessel      TEXT,
  origin      TEXT,
  wheat_class TEXT,
  tonnes      INTEGER,
  laycan_from TEXT,
  laycan_to   TEXT,
  status      TEXT NOT NULL,
  flat_usd_mt REAL,
  supplier    TEXT,
  eta         TEXT
);

-- Shipment paperwork, and which channel it showed up on.
CREATE TABLE IF NOT EXISTS documents (
  id          TEXT PRIMARY KEY,
  shipment_id TEXT NOT NULL REFERENCES shipments(id),
  kind        TEXT NOT NULL,
  status      TEXT NOT NULL,
  received_ts TEXT,
  channel     TEXT
);
"""


def connect(cfg):
    path = ROOT / cfg["paths"]["db"]
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as fh:
        return json.load(fh)


def upsert(con, table, rows, keys):
    """Insert-or-replace rows (list of dicts) keyed on `keys`."""
    if not rows:
        return 0
    cols = list(rows[0])
    sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
           f"ON CONFLICT({','.join(keys)}) DO UPDATE SET "
           + ",".join(f"{c}=excluded.{c}" for c in cols if c not in keys))
    con.executemany(sql, [[r[c] for c in cols] for r in rows])
    con.commit()
    return len(rows)
