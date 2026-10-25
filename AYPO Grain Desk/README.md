# Grain Desk

A wheat procurement desk for United Flour Mill, Thailand — built from a 71-minute walkthrough
with Pavin Sethbhakdi, who buys the wheat.

Three things live here: the **evidence** recovered from the recording, the **design proposal**
that came out of it, and the **working dashboard** that implements it.

```bash
# the app — no build step, no dependencies, no server required
start app/index.html          # or: open app/index.html

# rebuild the datasets
py -3.11 tools/build_data.py             # the wheat desk       -> app/js/data.js
py -3.11 tools/build_ws.py               # materials, workspaces -> app/js/ws.js + the spend sample

# checks
py -3.11 tools/jscheck.py --app          # every script parses and runs
py -3.11 tools/test_lib.py               # the price math and the spend reconciliation
py -3.11 tools/test_app.py               # every route, in every workspace
py -3.11 tools/test_flows.py             # the three new flows, driven through the real DOM
py -3.11 tools/shots.py                  # screenshots of the states worth looking at
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
| `app/js/workspace.js` | Which customer's book is on screen, and currency formatting. |
| `app/js/pricing.js` | Driver legs, premium, fair band, budget gap. Pure functions. |
| `app/js/spendlib.js` | CSV and xlsx reading, vendor and material matching, the report. Pure. |
| `app/js/llm.js` | The one door to a model, with the number check behind it. |
| `app/js/export.js` | Copy, download, print-to-PDF, the shared editor. |
| `app/js/app.js` | Rail, sidebar, topbar, market strip, command palette, Night Watch. |
| `app/js/surfaces/*.js` | One file per screen, registered through `GD.route()`. |
| `app/js/data.js`, `app/js/ws.js`, `app/js/spend_sample.js` | Generated. Do not edit by hand. |
| `app/data/spend_sample.csv` / `.xlsx` | The messy export, as a file you can actually upload. |
| `app/SURFACE_CONTRACT.md` | The API a surface codes against, and the data shape. |

Seven surfaces for the wheat desk: Morning Brief, Quote Board, Position Book, Market, Cargo &
docs, Blend & margin, Savings & benchmark. Night Watch is deliberately **not** a surface — it
is an interrupt, because that is what it is in Pavin's life. Three more sit under
**Procurement** and work on any material: Price check, Spend, Market bulletin.

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

## A second customer: materials, not wheat

`v2-urc-essentials` extends the desk from one mill's wheat book to any material with a
published price driver, for a prospective customer who buys about 3,000 suppliers' worth of
coffee, potato flakes, resin and cartons. **The wheat desk is untouched.** `app/js/data.js`
and the seven surface files are not modified; `app.js` changes only where it has to carry two
workspaces, and `ufm` is the default, so a fresh load is the product the mill already uses.

Three features, each one answering something URC's head of procurement actually said.

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
every supplier of that material has charged over twelve months, added to this month's driver
cost. Below four observations there is no band — quartiles of three numbers describe noise,
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

### Market bulletin — "it is assembled by hand, every fortnight"

One section per material: driver moves over the period, FX, freight, the crop read, and one
plain line on what it means for buying. Every figure names its feed and the date of the print
used. **Where a feed has no print for the period the bulletin says so** rather than reaching
for the nearest number — one such gap is planted in the demo data. Export is blocked until a
person approves the draft, and editing an approved draft closes the gate again, because the
thing that was approved is no longer the thing on screen.

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
