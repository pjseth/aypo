# Snack Launch Radar (Philippines)

Tracks FDA Philippines food product registrations to show which snack products competitors are
registering, what flavours and formats are rising, and where the client (URC) has gaps.

## Run it

```bash
cd launch_radar
python3 -m radar.collect            # pull every food registration (~30–45 min, polite pacing)
python3 -m radar.build_dashboard    # writes dashboard/index.html
open dashboard/index.html
```

Run `collect` weekly (the FDA updates on Tuesdays). Each run is stored under its date, so changes between
runs — new registrations, renewals, amendments, cancellations — are kept.

## What's where

| Path | What |
| --- | --- |
| `config.json` | All settings: API, pacing, client, competitor watchlist, dashboard start date |
| `radar/collect.py` | Resumable pull from the FDA verification portal's public search API |
| `radar/store.py` | SQLite schema; latest state + every version of every record |
| `radar/classify.py` | Snack type, flavour, claim, company group and company type rules |
| `radar/build_dashboard.py` | Builds products, insights and the dashboard data |
| `radar/dashboard_template.html` | Dashboard page (no external dependencies) |
| `data/radar.db` | All records (not in git) |
| `data/raw/<run>/` | Raw API responses, gzipped (not in git) |
| `docs/foi_request.md` | Draft FOI request for the full history incl. expired records |
| `docs/decision_log.md` | Design decisions and why |

## Known limits

- Registrations show intent, not launches or sales.
- Tags come from keyword rules on product names; expect some misses.
- Company type (maker vs importer/retailer) is guessed from the company name.
- Products registered before ~2021 appear under their latest renewal date.
- Cancelled or expired registrations may drop off the portal; the FOI request asks for them.
