# Procurement walkthrough — Pavin Sethbhakdi, United Flour Mill

Source: call on 2026-10-04 (PJ, Ella, Pavin) plus Granola notes. The recording itself was
not transcribed — no transcript was produced alongside it, so these notes are the record.

## How the buying works

- Three origins: US, Australia, Canada. 10–12 cargoes a year, ~55,000 t Panamax.
- Three US classes — Northern Spring, Hard Red Winter, Western White — blended with
  Canadian and Australian wheat into flour grades.
- PNW/Vancouver → Pacific → Gulf of Thailand → barges up the Chao Phraya to the mill.
- Vessels are chartered whole. Short of 55,000 t, UFM pools with other Thai mills
  (Lam Tong, Kari, Mission).

## The price

Flat price = futures + basis + freight & insurance + supplier margin. A quote at $350/t on
a 55,000 t vessel is ~$19.25M. Wheat is ~80% of a mill's cost, so $5/t is $275,000 a
cargo, and the ~$90/t fall they caught was ~$5M.

Levers: weather, export demand, government policy, FX (USD/THB, USD/AUD), geopolitics
(Black Sea, Hormuz — bunker prices), and fund positioning. WASDE lands at midnight Bangkok
time and the market moves in seconds; Pavin waits up for it and calls Mitsui immediately.
AgroValues charges ~$1,500/month for a weekly outlook, but the real value is brokering the
price down and keeping a cut of the saving.

## The actual KPI

Not buying cheap — buying cheaper than the other Thai mills. Every cent of flour price
matters when bidding for KFC or Pizza Hut. Pavin is building a competitor price tracker by
hand.

## Where it hurts

1. **Fragmentation.** 10+ price-indication emails a day when in market, plus WhatsApp and
   calls. No central platform.
2. Futures tracked in a spreadsheet Pavin wrote himself — contracts placed, P&L, target
   take-profit. Most Thai mills have nothing; UFM's head of procurement keeps it in his head.
3. Shipment documents (bill of lading, USDA certificates, fumigation, halal) arrive
   scattered across email.
4. He already built a Power Automate flow using Claude Sonnet to parse supplier emails into
   an Excel sheet — which is validation that this is worth automating, and a sign of how
   much it costs him that he had to.

## What this build covers

| Opportunity | Status |
| --- | --- |
| Consolidate supplier prices into one view | **Built** — unified inbox, parser, decomposition chart |
| Document management | **Built** — documents tracked against the vessel, not the thread |
| Market intelligence in one place | **Built** — notes by driver, with stance |
| Futures tracker | **Partly** — live curve, history and settlements; no position/P&L ledger yet |
| WASDE auto-parser | **Shape only** — releases shown; no live USDA pull, no second-by-second alert |
| Competitor price benchmarking | **Not built** — needs data UFM does not have |
| Supplier matching / marketplace | **Not planned** — Pavin said relationships already exist |

## Open questions for the next call

- What does the futures position ledger need to hold — contracts, lots, target exits, P&L
  against which mark?
- Is the competitor benchmark inferable from flour bid prices, or does it need a data swap
  between mills?
- Which mailbox and which WhatsApp account would feed this, and who owns that decision?
- Would he pay for this, or only for the AgroValues-style brokering that comes after it?
