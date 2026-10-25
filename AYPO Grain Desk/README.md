# Grain Desk

A procurement desk for a buyer with many materials and no independent view of what any of
them should cost.

Four screens, in the order the questions actually get asked:

| | | |
| --- | --- | --- |
| **Market** | what are prices doing | an instrument page per feed — watchlist, big chart, chosen window — and what each feed did to the floor under every material |
| **Price check** | what should I be paying | a quote split into its published drivers and the premium the supplier is carrying, against the fair band and the budget |
| **Spend** | what did I actually pay | the S/4HANA export cleaned in the browser, and the price paid per unit against budget |
| **Bulletin** | what do I tell everyone | the fortnightly market bulletin, edited and approved before it can be exported |

> **Branches.** This is `v3-dashboard`. The desk began as a wheat book for one Thai flour
> mill, built from a 71-minute walkthrough with the buyer; that version — seven surfaces, its
> own data, its own evidence — is on **`v2-urc-essentials`**, where it still runs unchanged.
> None of it is loaded here. A dashboard that carries a second customer's screens around
> unreachably is not a simpler dashboard.
>
> The evidence and the design proposal below are from that original work and are still what
> this is built on.

```bash
# the app — no build step, no dependencies, no server required
start app/index.html          # or: open app/index.html

# rebuild the dataset
py -3.11 tools/build_ws.py               # materials, feeds, quotes, and the spend sample
py -3.11 tools/build_data.py             # the wheat fixture the price model is checked against
py -3.11 tools/fetch_prices.py           # real daily OHLC, no key needed

# checks
py -3.11 tools/jscheck.py --app          # every script parses and runs
py -3.11 tools/test_lib.py               # the price math and the spend reconciliation
py -3.11 tools/test_app.py               # every route renders clean
py -3.11 tools/test_flows.py             # the four flows, driven through the real DOM
py -3.11 tools/shots.py                  # screenshots of the states worth looking at
py -3.11 tools/dump.py bulletin          # print a generated document, to read it
```

Use `py -3.11`; bare `python` on this machine is the Microsoft Store stub and exits 49.

## The app

`app/index.html` — one page, vanilla JavaScript, no framework and no bundler. Classic scripts
rather than ES modules, and the dataset is baked into `app/js/data.js` as a global rather than
fetched, so the whole thing runs correctly from `file://` as well as from a host.

| Path | What |
| --- | --- |
| `app/css/tokens.css` | Palette, type scale, radii, elevation, motion. |
| `app/css/app.css` | Shell and components. |
| `app/js/core.js` | DOM helpers, icon set, formatting, charts, overlays, router, table. |
| `app/js/workspace.js` | Whose book is on screen, currency formatting, and the precision rule. |
| `app/js/pricing.js` | Driver legs, premium, fair band, budget gap. Pure functions. |
| `app/js/spendlib.js` | CSV and xlsx reading, vendor and material matching, the report. Pure. |
| `app/js/llm.js` | The one door to a model, with the number check behind it. |
| `app/js/export.js` | Copy, download, print-to-PDF, the shared editor and document renderer. |
| `app/js/app.js` | Rail, sidebar, topbar, market strip, command palette, routing. |
| `app/js/surfaces/*.js` | One file per screen, registered through `GD.route()`. |
| `app/js/ws.js`, `app/js/spend_sample.js` | Generated. Do not edit by hand. |
| `app/data/spend_sample.csv` / `.xlsx` | The messy export, as a file you can actually upload. |
| `app/SURFACE_CONTRACT.md` | The API a surface codes against, and the data shape. |

Four surfaces — `prices`, `pricecheck`, `spend`, `bulletin` — and three agents in the rail
that own them: **The Watch** (the feeds and the fortnightly clock), **The Buyer** (what a
quote is made of) and **The Clerk** (what was actually invoiced).

### Where the look comes from

The visual language is lifted from three reference dashboards, sampled rather than guessed:

- **cool lavender-grey ground and the emerald accent** — a solar monitoring dashboard
  (`#e3e4ed`, `#049772`)
- **black pill primary buttons and hairline structure** — a billing SaaS (`#1b1b1c`, `#e4e1e2`)
- **the elevated-white active nav item, card-in-card stat recession, chip language** — an AI
  workflow dashboard

Emerald earns its place twice. It is the reference accent, and in a *buying* desk a falling
price is the good outcome — so "good" and "accent" are the same hue and a rising price is the
coral. That is the opposite of a trading screen, and it is deliberate.

One correction the reference study forced: all three designs are **sentence case throughout**,
with no uppercase letter-spaced micro-labels anywhere. The usual
`text-transform: uppercase; letter-spacing: .08em` dashboard cliché is absent from every one of
them, and adding it reads as generic immediately. The only tracked uppercase in the whole app
is the segmented control, which is the one place the references do it too.

### What is real

Nearly all of it. The quote log rows, the futures legs and thresholds, the vessel and its
certificate numbers, the flour SKUs and their margins, the supplier contacts and the driver
taxonomy were all read off Pavin's screen. `DESK.provenance` lists exactly what is real and
what is synthesised, and the app says so on screen rather than implying a live feed.

Two figures worth knowing before reading the code:

- **`stated_basis` is populated on 2 of 26 quotes.** Suppliers quote a flat number and itemise
  nothing, so the four-component split is *reconstructed* — from the settlement curve, the
  freight market and the class median. The app never implies the supplier gave it.
- On both quotes that *do* carry a stated basis, the reconstruction lands within **$1/t** of it
  (−0.95 and +1.02). That is the evidence the model is sound, and it is shown in the UI.

## The three problems this was built for


A Philippine food manufacturer: about ten plants, a 22-person central procurement team, three
thousand suppliers, thirty thousand SKUs, and about 60% of raw materials imported. Their head
of procurement named three problems. Each screen exists for one of them.

### Price check — "the suppliers set the price"

URC relies on its suppliers for the market outlook *and* the price recommendation, and has no
independent way to check either. So every quote is split into its published drivers and the
premium the supplier is carrying:

```
leg         = (print in force × per_unit + spread) × fx
driverCost  = Σ legs
premium     = quote − driverCost          ← a subtraction, not a disclosure
```

The premium is then measured against the **fair band**: the 25th–75th percentile of what
every supplier of that material has charged *over driver cost* across twelve months,
applied to this month's driver cost. A share rather than a sum of money, because the
underlying moves — Brent ran 70% across the window this build pulled, and a supplier
steady at 4% over cost has to read as steady rather than as a widening premium. Below four observations there is no band — quartiles of three numbers describe noise,
and somebody would negotiate against them. Separately, the quote against the budget rate the
business set, because "is this premium normal" and "can we afford this price" are two
questions with two answers.

Then a one-page **negotiation brief**: the market move, the band, the supplier's own premium
trend, the budget, and three things to put to them — framed as *meet us halfway*, never as an
accusation, because the relationships are twenty years old and are the reason nothing was
missed during the pandemic.

### Spend cleansing — "five days before any analysis starts"

An S/4HANA export lands with four spellings per vendor, free-text material descriptions, four
currencies and a few documents posted twice. `app/data/spend_sample.csv` (and `.xlsx`) is
1,316 lines of exactly that, one click away in the demo. The run takes a few milliseconds and
ends with 93% of lines classified, **nine decisions** for a person covering the other 88, and
a report that states its own reconciliation on screen. Corrections are remembered, so the same
text never asks twice.

Nothing in the matcher knows what potato flakes are. Vendors fold initialisms and legal
suffixes, then match on containment with prefixes and vowel-free abbreviations counted as
hits. Materials turn on a token that names exactly one material in the workspace — which is
why "Corrugated box - RSC" classifies and "MISC CONSUMABLES" does not.

### Bulletin — "it is assembled by hand, every fortnight"

One section per material: driver moves over the period, FX, freight, the crop read, and one
plain line on what it means for buying. Every figure names its feed and the date of the print
used. **Where a feed has no print for the period the bulletin says so** rather than reaching
for the nearest number — one such gap is planted in the demo data. Export is blocked until a
person approves the draft, and editing an approved draft closes the gate again, because the
thing that was approved is no longer the thing on screen.

### Running the demo

`app/index.html` opens on the Market screen. Four beats, about ten minutes:

1. **Market.** Opens on the feed with the widest reach, which on a book that imports most
   of what it buys is USD/PHP — under all eight materials, and the chart shows the peso
   actually losing ground over the year. Pick a window, scrub the chart and the big price
   follows the cursor against where the window opened. Switch to **candles** on any feed
   with real sessions behind it: daily at 1M and 3M, rolled up weekly at 6M and 1Y. Then
   the part no trading app has — what that feed is worth *inside* each material, and what
   every feed did to each material's **driver cost**, the floor under a price and the part
   no supplier sets.
2. **Price check.** The board opens on *Worth a call: Kalinga Highlands* — green coffee above
   the top of its fair band, with a premium that has widened four quarters running. Open the
   breakdown: published drivers, the FX conversion, the premium, and the line proving they add
   to the quoted price. Then **Negotiation brief**, and the table beneath it, which is every
   figure the brief is allowed to contain. Resin is the counter-example: inside the band and
   under budget.
3. **Spend.** *Load the sample export* — 1,319 lines in a few milliseconds, most classified,
   six duplicates, and the rest waiting on nine decisions. The first decision is one vendor
   spelling and clears seventy-odd lines at once. The report ties to the line items and says
   so on screen.
4. **Bulletin.** *Generate draft* — nine sections, every figure sourced, and one that says it
   has no data rather than filling the gap. Edit a line, approve it, export it. Edit it again
   and the export closes.

### Where the price data comes from

Half of it is real.

`tools/fetch_prices.py` pulls real daily sessions for every feed with a public market and
drops them into a build cache that `build_ws.py` picks up. As of the last build that is Brent,
ICE sugar no.11, CBOT wheat, Malaysian crude palm oil and the USD/PHP, EUR/PHP and MYR/PHP
rates the whole dataset converts through — about five hundred sessions each. The five feeds
with no public market — an assessed potato flake index, containerboard, BOPP resin and two
private freight assessments — keep a generated path and say so on every screen they appear.
Robusta is generated too: Yahoo does not carry it, and Barchart does.

**It is a build step, not a fetch in the browser**, for three independent reasons: a key in a
static page is a published key; neither provider sends an `Access-Control-Allow-Origin`
header, so the request fails before the key matters; and Barchart's free tier is a couple of
hundred calls a month against eleven feeds you would want daily. So it is pulled on the
machine that runs the build and baked into `app/js/ws.js` like every other dataset here.

Two providers. `yahoo` is the default and needs no key — a public but undocumented endpoint,
fine for a build step and a demo, not something to put a customer's production reporting on.
`barchart` is the licensed path: set `BARCHART_API_KEY` and it covers robusta as well. The
endpoint and parameters are confirmed live; without a key it answers `401 API key is missing
or not valid` rather than `404`.

The supplier book stays invented. Quotes, premiums, spend lines and the company itself are
generated, because they are a fictional customer's — so the workspace label reads **"Demo
book, live prices"** and the tooltip says exactly which half is which, read off a provenance
block the build writes from what actually came back.

Delete the cache (`tools/fetch_prices.py --clear`) and rebuild, and the whole dataset goes
back to being synthetic.

### What the model does, and what it is not allowed to do

Without an API key — the default, and how the demo runs — every document is written by the
app from the figures on screen. With one, `claude-opus-5` rewrites the prose from a **fact
pack and nothing else**, and every number in its reply is checked back against that pack
before anyone sees it; one untraceable figure and the draft is discarded in favour of the
app's own wording, visibly, with a note saying why. The buying recommendation in the bulletin
is a rule, never a model: it is the one sentence a reader will act on, so it has to be
traceable to the band, the budget and the driver move behind it.

The fact pack is on screen beside the brief, as a table of every figure and where it came
from. `tools/test_flows.py` asserts the acceptance criterion the way it is written: it reads
the generated brief and that table out of the live DOM and fails if any figure in the first is
missing from the second.

## The evidence

| Path | What |
| --- | --- |
| `evidence/pavin_walkthrough.md` | The Granola notes checked line by line against the recording — confirmed, corrected, and what was missing entirely |
| `evidence/artifacts.md` | Every system seen on his screen, reconstructed from keyframes |
| `evidence/transcript.md` | Transcript with `[mm:ss]` anchors |
| `evidence/contact_NN.png` | Keyframe triage grids |
| `evidence/zoom/` | Enlarged crops, readable |

Four findings the summary notes did not contain, and the design turns on all of them:

1. **Each cargo is split 70% flat / 30% basis**, and the basis portion is legged into futures
   over weeks against a +$50,000 take-profit and a −$25,000 loss cut. The position was
   −$378,749 at the time of recording, and had breached its loss cut eleven days earlier.
2. **Suppliers itemise nothing** (above).
3. **The selling side exists and is quarterly** — a flour margin workbook in ฿/kg ex-VAT per
   customer per SKU. Nobody mentioned it.
4. **His current automation loses data silently** — a Mitsui email quoting five classes
   produced four rows; `NS14.0 = USD373.80` vanished. The app surfaces that quote as recovered.

Three corrections: ~9–10 cargoes a year rather than 10–12; **Supramax with gear** for US cargoes
rather than uniformly Panamax; and co-loading across **7 mills** is the standing structure for
Australian and Canadian wheat, not a fallback — UFM holds only 20–40% of those cargoes.

## The design proposal

`docs/design.html`, published at
<https://claude.ai/code/artifact/d82d0574-c37c-42b7-b638-f7f5e34b0748>, with a PDF at
`docs/Grain Desk - design proposal.pdf` (`py -3.11 tools/make_pdf.py` rebuilds it).

It argues for **three agents**, not ten: **The Buyer** (what should I pay?), **The Watch**
(when should I move?) and **The Clerk** (did I get what I bought?). The rail down the left of
the app is those three, showing what each is holding for you.

## The video pipeline

`tools/video/` — the recording was dissected locally. The `claude-video` skill needs ffmpeg,
yt-dlp and a JS runtime, none of which exist on this machine, so the same approach is
reimplemented against PyAV and faster-whisper directly. See `tools/video/README.md`.

Worth recording: upgrading the transcript from `small.en` to `medium.en` did **not** improve
the domain vocabulary — same word count, fewer segments, and `WASDE` actually came through once
in the smaller model and not at all in the larger. The proper nouns were recovered from the
screen frames instead, which are a more reliable source than the audio for spelling.
