# Decision log — Wheat Procurement Desk

Newest first.

| Date | Decision | Why |
| --- | --- | --- |
| 2026-10-06 | Scope v1 = consolidation only: one page, every fragment on it | Pavin's first-named pain is fragmentation, not analytics. Prediction, competitor benchmarking and WASDE auto-parsing come after there is one place to put them |
| 2026-10-06 | The quote parser runs on message text, with no access to the generator | A parser fed structured data proves nothing. Generating free text and parsing it back is the only honest test of the mechanism before a real mailbox exists |
| 2026-10-06 | Every extracted field carries a plausibility band | Without one, "USD 38.50 /MT" (freight) is read as the flat price whenever the flat is missing. That single misread would be the costliest bug in the product |
| 2026-10-06 | Quotes show a reconciliation gap: flat − (futures + basis + freight + margin) | Pavin negotiates the four components separately. A gap is margin the supplier did not itemise, which is exactly the conversation worth having |
| 2026-10-06 | Confidence = share of the seven core fields recovered; missing fields are named | A parser that silently guesses is worse than one that says what it could not read |
| 2026-10-06 | Futures, forward curve and FX are live; everything else is synthetic | Market data has a free public source. Supplier mail does not, until UFM connects a mailbox — but the page should be judgeable now |
| 2026-10-06 | Hero figure is the delta against the window average, in dollars per vessel | The KPI Pavin named is buying lower than the other mills, and he thinks in dollars per 55,000 t cargo. "$358/t" means less than "−$213k" |
| 2026-10-06 | Hero compares within one wheat class, named explicitly | Comparing an ASW quote to a Hard Red Spring quote is not a comparison. When the class filter is All, the most-quoted class is used and labelled |
| 2026-10-06 | Forward curve is a zero-based bar chart with a value on every cap | The user asked for bars. Truncating the baseline would exaggerate a 5% carry; zero-based plus direct labels keeps it honest and still readable |
| 2026-10-06 | Hard Red Spring and CWRS priced against Chicago SRW | MGEX spring wheat has no feed on the free source. Documented as a gap rather than silently substituted |
| 2026-10-06 | Chart palette is the data-viz default, validated by a Python port of the JS validator | Node is not installed on this machine; the port reproduces the reference figures exactly (CVD ΔE 9.1 light / 8.4 dark), so the check is computed rather than eyeballed |
| 2026-10-06 | Every chart ships a table view | Three light-mode series sit below 3:1 against the surface, which obliges the relief rule — and the tables are the accessible twin regardless |
| 2026-10-06 | Dashboard is one self-contained HTML file | Same as the Snack Launch Radar: no server, easy to send, works offline |
