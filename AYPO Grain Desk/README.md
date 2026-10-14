# Grain Desk

A wheat procurement desk for United Flour Mill, Thailand — built from a 71-minute walkthrough
with Pavin Sethbhakdi, who buys the wheat.

Three things live here: the **evidence** recovered from the recording, the **design proposal**
that came out of it, and the **working dashboard** that implements it.

```bash
# the app — no build step, no dependencies, no server required
start app/index.html          # or: open app/index.html

# rebuild the dataset after editing tools/build_data.py
py -3.11 tools/build_data.py

# checks
py -3.11 tools/jscheck.py --app          # every script parses and runs
py -3.11 tools/test_app.py               # every route, light and dark
py -3.11 tools/test_app.py --shots       # the same, plus screenshots
```

Use `py -3.11`; bare `python` on this machine is the Microsoft Store stub and exits 49.

## The app

`app/index.html` — one page, vanilla JavaScript, no framework and no bundler. Classic scripts
rather than ES modules, and the dataset is baked into `app/js/data.js` as a global rather than
fetched, so the whole thing runs correctly from `file://` as well as from a host.

| Path | What |
| --- | --- |
| `app/css/tokens.css` | Palette, type scale, radii, elevation, motion. Three theme states. |
| `app/css/app.css` | Shell and components. |
| `app/js/core.js` | DOM helpers, icon set, formatting, charts, overlays, router, table. |
| `app/js/app.js` | Rail, sidebar, topbar, market strip, command palette, Night Watch. |
| `app/js/surfaces/*.js` | One file per screen, registered through `GD.route()`. |
| `app/js/data.js` | Generated. Do not edit by hand. |
| `app/SURFACE_CONTRACT.md` | The API a surface codes against, and the data shape. |

Seven surfaces: Morning Brief, Quote Board, Position Book, Market, Cargo & docs, Blend &
margin, Savings & benchmark. Night Watch is deliberately **not** a surface — it is an
interrupt, because that is what it is in Pavin's life.

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
