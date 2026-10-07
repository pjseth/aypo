# Wheat Procurement Desk

One page for a flour mill's wheat buying. Pulls live futures, forward curves and FX;
reads supplier quotes out of whatever channel they arrive on; keeps the research behind a
price view, the vessels on the water and their paperwork in the same place.

Built from a procurement walkthrough with Pavin Sethbhakdi (United Flour Mill, Thailand),
whose first complaint was that all of it lives in different places: 10+ price emails a day,
negotiation over WhatsApp and calls, futures tracked in a spreadsheet he wrote himself,
certificates scattered across threads.

## Run it

```bash
cd "AYPO Wheat Desk"
python -m desk.market            # live futures, FX and the forward curve (~40 requests)
python -m desk.build_dashboard   # writes dashboard/index.html
start dashboard/index.html       # macOS/Linux: open dashboard/index.html
```

`desk.market` is the only step that touches the network. Re-run it as often as you want a
fresh mark; every settlement is kept, so history accumulates across runs.

## What's where

| Path | What |
| --- | --- |
| `config.json` | Everything tunable: instruments, contract months, origins, wheat classes, suppliers, freight ranges |
| `desk/market.py` | Live pull — daily settlements, the forward curve, FX and a bunker proxy |
| `desk/comms.py` | Supplier messages (synthetic) **and** the parser that reads them (real) |
| `desk/intel.py` | Market-intelligence notes and WASDE releases (synthetic) |
| `desk/ops.py` | Vessels and their documents (synthetic) |
| `desk/store.py` | SQLite schema |
| `desk/build_dashboard.py` | Assembles the payload and writes the page |
| `desk/dashboard_template.html` | The dashboard — one file, no external dependencies |
| `tools/validate_palette.py` | Chart-palette checker (Python port of the data-viz validator) |
| `docs/one_pager.html` | The feature one-pager |
| `docs/interview_notes.md` | The procurement walkthrough this was built from |
| `docs/decision_log.md` | Decisions and why |
| `data/desk.db` | Everything, stored (not in git) |

## What is real and what is not

**Real:** Chicago SRW and KC HRW futures, their full forward curves, USD/THB, AUD/USD and
crude — live from the Yahoo Finance chart API. And the quote parser: it runs on message
text only and has no access to the values the messages were written from.

**Synthetic:** supplier messages, intelligence notes, vessels and documents — stand-ins
with the shape of the real thing, so the page can be judged before a mailbox is connected.

## Known limits

- MGEX Minneapolis spring wheat has no feed on this source, so Hard Red Spring and Canada
  Western Red Spring are priced against Chicago SRW. Western White has no futures contract
  at all — it trades on basis, and the dashboard shows it that way.
- The parser is rule-based, with a plausibility band per field. It reports what it could
  not find rather than guessing; ~96% of core fields are recovered across the five message
  shapes, and LinkedIn messages genuinely carry no futures reference.
- A reconciliation gap means the stated flat price exceeds futures + basis + freight +
  stated margin. That is usually margin the supplier did not itemise, not an error.
- Freight is a single line. Splitting bunker, war risk and canal fees needs a real charter.
