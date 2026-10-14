# Pavin walkthrough — the Granola notes, checked against the recording

Source: `Pavin __ PJ and Ella - 2026_10_04 23_01 EDT - Recording.mp4`, 70:49, transcribed
locally (`transcript.md`) and read alongside 285 extracted keyframes (`artifacts.md`).

Rule applied throughout: **where the recording and the notes disagree, the recording wins.**
Every claim below carries the timestamp it came from.

Speakers are not labelled by the transcription — no diarization model is installed. Attribution
below comes from context; the voice explaining procurement is Pavin throughout.

---

## Confirmed

| Claim in the notes | Status | Evidence |
| --- | --- | --- |
| Flat price = futures + basis + freight & insurance + supplier margin | **Confirmed**, in these words | "the components that make up that 350 USD comes from four different things… the price of the futures, the price of the basis, the price of the freight and insurance, and the small amount of margin" `[08:50]` |
| Basis is the gap between futures and the cash/spot price | **Confirmed** | "the difference between the futures and the cash price, the spot, that gap is the basis" `[08:20]` |
| Wheat is ~80% of a flour mill's cost | **Confirmed**, twice | "Our cost, 80% of that cost is the cost of the wheat" `[21:30]`; "for most flour mills, that's 80%" `[45:10]` |
| $5/t ≈ $275,000 per cargo | **Confirmed**, his own arithmetic | "What happens if it increases by say $5, which is very reasonable. It can increase like that within a few days. That's five times 55,000. That's $275,000" `[22:10]` |
| A ~$90/t fall was caught recently | **Confirmed** | 797.25 ¢/bu a month ago against 691 now — "this basically lowered by like $90" `[23:00]` |
| He waits up for the WASDE and calls Mitsui immediately | **Confirmed**, and far more vivid | "I literally wait until midnight on these days… as soon as it comes out, it's like waiting for your SAT score… you quickly call Mitsui: if you're in the market right now, buy me this, sell these futures" `[41:10–42:30]` |
| Consultancy costs ~$1,500/month | **Confirmed** | `[37:06]`, `[37:15]` |
| The consultancy's real value is brokering, on a share of savings | **Confirmed** | "their main thing is they want to come in and help negotiate for you — be the middleman between me and Mitsui… if we can save you $3, we'll take like 50 cents" `[36:11]` |
| Power Automate flow using Claude Sonnet parses supplier email | **Confirmed**, mechanism described | "all emails that contain a few keywords… everything that enters this folder gets auto forwarded to my Power Automate, and I have a flow here that uses Claude Sonnet" `[17:45]` |
| Supplier matching / marketplace is not needed | **Confirmed**, twice, unprompted | "these flour mills already have the contacts. I know them by name and had drinks with them" `[62:20]` |
| Three origins: US, Australia, Canada | **Confirmed** | Slide at `[22:28]`; quote log origins `US / AUS / CA` |
| Per-customer tailoring is the key risk | **Confirmed**, his own analogy | "like how Salesforce or SAP would tailor the ERP system… getting them to adopt the app, it has to be very easy and in their own way" `[64:10]` |

---

## Corrected

### 1. Cargo count and vessel class

The notes say *10–12 shipments per year (~once a month), 55,000-ton Panamax vessels.*

The slide `UFM: Our wheat requirements` `[22:28]` says:

| Origin | Frequency | Volume | Structure |
| --- | --- | --- | --- |
| US | ~3–4 ships / year | ~55K mt | **Supramax with gear**; combined UFM + SFT |
| Australia | ~3 ships / year | ~20K mt | 15K UFM + 5K SFT; ~40% of cargo; **7 mills** |
| Canada | ~3 ships / year | ~15K mt | 10K UFM + 5K SFT; ~20% of cargo; **7 mills** |

So roughly **9–10 cargoes a year**, and the US vessel is a **Supramax with gear**, not uniformly
a Panamax. ("Panamax" is said at `[05:30]`, so both appear — the written spec says Supramax.)

### 2. Pooling is the normal case, not a fallback

The notes frame pooling as what happens *if UFM can't fill 55k tons alone*. The slide shows UFM
holding only **20–40% of the Australian and Canadian cargoes, shared across seven mills**. Those
are not contingency arrangements; they are the standing structure. A co-load feature is therefore
about tracking a share of someone else's hull, not topping up your own.

### 3. The discharge port

The notes say *Gulf of Thailand → Chao Phraya River via barges → flour mill*. True as far as it
goes, but every contract document and quote says the point of entry is **Koh Sichang (KSC)**, with
**LCB** (Laem Chabang) and **BMTP** as alternates carrying price differentials — `CFR BMTP
+USD3/MT` `[18:42]`. Koh Sichang is where the vessel discharges to barges. The port, not the
river, is the commercial term.

### 4. The consultancy's economics

The notes say *save $3/MT, keep ~$0.50/MT*. Correct. But Pavin then says they would "make like a
million dollars from that purchase" `[36:40]`, which does not follow from his own numbers:
$0.50/t × 55,000 t = **$27,500 per cargo**, or roughly **$275,000 a year** across ten cargoes.
Use the arithmetic, not the aside — it is still a large number and it is the real price ceiling
for anything we sell.

### 5. He is not currently using the consultancy

The notes imply an active relationship. He says he is "keeping them at bay" and has not let them
broker, "because I do want to get some learning" `[36:00]`. The seat is open.

---

## New — not in the notes at all

### The flat/basis split, and how he actually prices  `[53:19]`

Each cargo is divided into a **flat-priced portion (70%)** and a **basis portion (30%)**. The
basis portion is then legged into futures over weeks: individual orders at chosen levels with
`Open`/`Filled` status, each carrying its own gain or loss against the original flat, managed
against a **take-profit of +$50,000** and a **loss cut of −$25,000**. At recording time the
Pacific Wind position was **−$378,749**.

This is the single largest omission in the notes. It is also the most detailed specification we
have for anything.

### The selling side exists, and is quarterly  `[17:52]`

`Q426 Industrial Margin Analysis Pricing` — industrial customers × flour SKU, priced in
**฿/kg ex-VAT**, with margin per line and forward cost across four quarters. A `Seller` column
flips between **UFM** and **MCF** (MC Foods). Wheat cost becomes a customer price here, and
nothing in the notes mentions it.

### Documents arrive as one merged pack  `[39:14–40:43]`

`(FINAL) DOCS_MV PACIFIC WIND_UFM.pdf`, **45 pages**: Baltimore Form C berth term grain bill of
lading, USDA phytosanitary certificate, commodity inspection certificate, SGS certificates.
Certificates are issued **per hold** (Hold Number 5, 19,573,193 lb ≈ 8,878 t), not per cargo.
"This is all the paperwork that comes in. This isn't the fun part." `[46:40]`

### His own causal model of price  `[60:44]`

From OneNote, "How Price is determined":
weather → crop condition → production → supply; plus seasonal; plus export → demand; all into
supply/demand → **price**; with **Commitment of Traders** and **government policy** acting on
price directly, and **forex** from below. Any market-intelligence view should use this taxonomy.

### Freight is calculated, not quoted  `[59:59]`

From `Mitsui Training Singapore Notes`: "Calculate Theoretical Freight Rate — use system to
calculate theoretical freight", with a chartering calculator (`Vessel · Scrubber · Open Location ·
Open Date · GTC $/d · NTC $/d · Ballast Bonus`) and a rule of thumb — "based on Justin's past 8
years of experience, normal range is $30-35". Mitsui trained him to audit the freight line.

### Domain we did not know about  `[25:14, 60:44]`

**GAFTA contracts** govern the trades ("there's an entire GAFTA contracts and stuff for this",
`[46:00]`). **Despatch/demurrage** is a tracked cost with its own OneNote page. He has **options
trading notes** with KKP — not just futures. The approved supplier list runs to nine names
(Mitsui, Mitsubishi, Cargill, ADM, LDC, Columbia Grain, Bunge, CHS, United Grain) and is a hard
constraint: "we have a list of approved suppliers that we can only buy from" `[38:00]`.

### His market data is 15 minutes delayed  `[61:50]`

"Barchart, it's good enough for me, it's free… it's delayed by 15 minutes, which sucks." For
Night Watch this matters: 15 minutes is longer than his entire window of advantage.

### His existing automation loses data silently  `[18:42 vs 20:14]`

The Mitsui email of 1 Oct quotes five classes. The spreadsheet rows it produced captured four —
`NS14.0 = USD373.80` is absent. He has no way to notice.

---

## His own prioritisation, which differs from ours

> "The first thing that's easiest to make is the fragmented part, putting things together. The
> research thing is interesting and that'll be harder." `[65:30]`

We are proposing the opposite order (Night Watch first). That disagreement should be put to him
rather than quietly resolved — he offered: "you can be a design partner if you'd like… just
schedule a meeting with me" `[66:00]`.

He also set homework: "Check first to see if there's any tools out there. I doubt it. And maybe
that's also — maybe it's because it's too complicated, and that's why there's no tools."
`[63:40]`

---

## Open questions for the next call

1. What is in the `Competitor Prices` tab, and where do the numbers come from?
2. Is the 70/30 flat/basis split policy, negotiated per cargo, or his own judgement?
3. Is `PROCUREMENT TRACKER` private to him or shared with Khun Pracha?
4. What does AVS deliver weekly that the daily broker decks and US Wheat letters do not?
5. Does the Power Automate flow write only to `wheat_price_template`, or also to the tracker's
   `Price Indications` tab?
6. How are despatch and demurrage calculated today, and by whom?
7. Would he pay for the desk, or only for the brokering that comes after it?
