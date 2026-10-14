# What was on Pavin's screen

Recovered from the 71-minute recording by keyframe extraction. Every item cites the
timestamp it was read from, so it can be checked against the video.

These are the systems Pavin actually runs today. Two of them — the quote log and the futures
tracker — are the real specification for two core screens of any product we build. We should
copy his model rather than invent one.

---

## 1. `PROCUREMENT TRACKER` — the futures position book  [53:19, 54:04, 54:34, 55:24, 59:03]

Excel workbook. Tabs: `Template · Price Indications · US Aug-Sep'26 · CA Nov-Dec'26 ·
Competitor Prices · Procurement Notes`.

**One tab per cargo.** The competitor tracker the Granola notes said he was "building by
hand" exists as a tab.

Sheet `FUTURES PRICE TRACKER - US Aug/Sep'26`:

```
ORDER DETAILS
  Vessel Name:       Pacific Wind
  Origin:            PNW
  Trading Company:   Mitsui
  Shipment Time:     Aug 15 - Sep 15, 2026   (Arrive October 7th 2026 Koh Si Chang)
  Contract Date:     04-Jun-26

NS 14.5% (65 DHV)              MWU26 - Futures Tracker
  Flat Price          336.5     Date       Ctr Status  ¢/bu    USD/MT  BASIS  FLAT    GAIN/LOSS USD
  Futures Settlement  648       04-Jun-26   67 Filled  646.80  237.66  98.84  336.50      —
  Implied Basis       269       23-Jun-26    5 Filled  630.00  231.49  98.84  330.33    4,497.47
  Flat Portion        70%       23-Jun-26    5 Filled  625.00  229.65  98.84  328.49    5,746.76
  Basis Portion       30%       26-Jun-26    5 Filled  610.00  224.14  98.84  322.98    9,494.65
  Total Quantity   13,000       26-Jun-26    5 Filled  600.00  220.46  98.84  319.30   11,993.24
  Flat Quantity     9,100       26-Jun-26    5 Open    595.00  218.63  98.84  317.47      —
  Basis Quantity    3,900       15-Aug-26    9 Filled  685.00  251.69  98.84  350.53  (16,640.62)
  Total Contracts      96
  Flat Contracts       67       Mark to Market (previous day's close)   718.5 Breakeven
  Basis Contracts      29       Total                                           15,091.50
  Remaining Contracts   0
  Avg Futures Price  644.02    TOTAL KPIs
  Avg Flat Price     335.48      GAINS/LOSS (USD)      (378,749)
                                 TARGET TAKE PROFIT       50,000
HRW 11.5%                        TARGET LOSS CUT         (25,000)
  Flat Price           324     KWU26 - Futures Tracker
  Futures Settlement 627.75     04-Jun-26   82 Filled  626.78  230.30  93.70  324.00
```

Cell formula visible at M18: `=IFERROR(L18*$M$5,"")` — USD→THB using the rate in M5.

**The mechanic the Granola notes missed entirely.** Each cargo is split into a **flat-priced
portion (70%) and a basis portion (30%)**. The basis portion is then *legged into futures over
weeks* — individual orders with `Open`/`Filled` status at chosen price levels, each carrying its
own gain/loss against the original flat. 96 contracts × 5,000 bu ≈ 13,064 t, matching the
13,000 t block. Positions are managed against an explicit `TARGET TAKE PROFIT` of +$50,000 and
a `TARGET LOSS CUT` of −$25,000.

At the time of recording this cargo was **−$378,749**.

Instruments are **MWU26** (MGEX Minneapolis spring, Sep'26) and **KWU26** (KC HRW, Sep'26) —
so MGEX is load-bearing, not optional.

---

## 2. `wheat_price_template` — the supplier quote log  [18:42, 18:47]

Excel on SharePoint (`siamflour365-my.sharepoint.com`, `admin_siamflour_com`), tab
`WheatPrice`, ~180 rows. This is the output of his Power Automate + Claude Sonnet flow.

**Schema, verbatim:**

```
date_received · supplier · origin · wheat_class · shipment_period ·
cnf_flat · cnf_basis · cnf_futures · fob_flat · fob_basis · fob_futures ·
qty_mt · protein_spec · spec_notes
```

Sample rows:

| date_received | supplier | origin | wheat_class | shipment_period | cnf_flat | cnf_basis | cnf_futures | qty_mt | protein |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 9/10/2026 6:19 PM | LDC | AUS | APW1 10% | December 1-31 | 347.25 | | | 50000 | |
| 15/09/2026 13:38 | Mitsui | US | NS/DNS 14.5% | 1-31 JANUARY 2027 | 386.8 | | | 55000 | 14.50% |
| 17/09/2026 12:12 | Mitsui | CA | CWRS 13.8% | 15 NOV - 31 DEC 2026 | 366 | | | | 13.80% |
| 24/09/2026 10:31 | Bunge | AUS | APW1 10% | Jan 1 - Jan 31, 2027 | 345 | | | 57000 | 10.00% |
| 30/09/2026 9:23 | Mitsui | US | NS/DNS 14.5% | 20 DEC - 20 JAN 2027 | 379.8 | 330 | MWZ | 55000 | Min 14.5% |
| 30/09/2026 9:23 | Mitsui | US | HRW 11.5% | 20 DEC - 20 JAN 2027 | 368.8 | 257 | KWZ | 55000 | Min 11.5% |
| 10/1/2026 11:12 AM | Mitsui | US | SWW 9.5-10.5% | 20 DEC - 20 JAN 2027 | 319.8 | | | 55000 | 9.0-10.5% |
| 10/2/2026 10:32 AM | Cargill | CA | CWRS 13.5% | Nov 20 - Dec 20 | 350.9 | | | 60000 | 13.50% |

**What the data says, which matters more than the schema:**

- **`cnf_basis` and `cnf_futures` are almost always empty** — 2 of ~25 visible rows. Suppliers
  quote a *flat* number. The four-component decomposition Pavin described is something he must
  **reconstruct**, not something he receives. This is the single most important design fact in
  the whole evidence base.
- **One email becomes ~7 rows.** `30/09/2026 9:23` repeats across NS/DNS, HRW, SWW, WW and three
  CWRS grades.
- **Date formats are already inconsistent**: `15/09/2026` (D/M) next to `10/1/2026` (M/D).
- Quantities vary more than "one Panamax": 50,000 / 52,500 / 55,000 / 57,000 / 60,000, and a
  300 t outlier.
- `spec_notes` is where the commercially live detail hides: `CFR KOH SICHANG THAILAND, 10%
  tolerance`, `CFR LCB Thailand; CFR BMTP +USD3/MT`, `CWRS#2, FN275, MST14.5`,
  `50-55kmt geared, Kosichang, -1.80/mt for 50D`, `DHV65`.
- Discharge is **Koh Sichang (KSC)**, **LCB** (Laem Chabang) and **BMTP**, with per-port
  differentials — not simply "Bangkok".

---

## 3. A real supplier price indication  [19:29, 20:14]

```
RE: Mitsui/US Wheat price update/Jan'27 Shipment          Thu 10/1/2026 1:26 PM
From: Chonnigarn, Apirakadule (Boom) <A.Chonnigarn@mitsui.com>
      Mitsui & Co., (Thailand) Ltd., Food & Retail Division
To:   Pracha Raksincharoensak <pracha.r@mc-foods.com>; Khun Pracha/UFM
Cc:   Pavin Sethbhakdi <pavin.s@ufm.co.th>; Pirapol Sethbhakdi <pirapol@ufm.co.th>; ...

  Dear Khun Pracha,
  Apologies for an error in previous email ka.
  Please follow this indication ka.

  DATE: 01/10/2026
  PRICE INDICATION TO UFM
  US WHEAT IN BULK
  55KMT / 10%
  20 DEC - 20 JAN 2027
  CFR KOH SICHANG THAILAND

  NS14.0 (DHV65) = USD373.80 PER MT
  NS14.5 (DHV65) = USD376.80 PER MT
  HRW11.5        = USD366.80 PER MT
  SWW9.0-10.5    = USD319.80 PER MT
  WW9.0-10.5     = USD332.80 PER MT
```

- **Quotes get corrected and recalled.** This email *is* a correction; the folder also shows
  `Recall: Mitsui/AUS Wheat pric…`. Supersede logic is mandatory, not a refinement.
- **Visible data loss in the current pipeline.** The email quotes five classes; the spreadsheet
  rows at `10/1/2026 11:12 AM` captured four. `NS14.0 = 373.80` was dropped.
- Pavin is **CC, not To**. The addressee is Khun Pracha — very likely the head of procurement
  who, per the Granola notes, "keeps it in his head".
- `pracha.r@mc-foods.com` ties to the `MCF` seller code in the margin sheet: **UFM and MC Foods
  buy as one group**.

### Outlook structure — the ingest point already exists  [20:14]

Favorites: `Inbox · Procurement · **Price Indications** (125 items) · Wheat Market · AVS ·
Data Excellence · Excom Meetings · Factory Meetings · Sales · Sales PowerBI · HR · Sent Items`.
Mailbox `pavin.s@ufm.co.th`. A dedicated, already-populated folder answers the open question of
what would feed the product.

---

## 4. `Q426 Industrial Margin Analysis Pricing v2` — the selling side  [17:52, 17:54]

Excel, tab `4Q Analysis`. **Not mentioned anywhere in the Granola notes.**

Rows are industrial customers (Thai corporates, in Thai) × SKU: `USN 22.5kgs`, `USF(PE)`,
`Soft Flour`, `Wafer`, `White Swan W8`, `BC(Green)`, `RS-1/2/7/8 (25Kgs)`, `UKF`,
`Wai Wai Biscuit`, `MR1`, `AJ-1(PE)`, `UY(PE)`, `Ur(Bulk) 1kg`. A `Seller` column alternates
**`MCF`** and **`UFM`**.

```
Q3/26 BAHT/KG (EX-VAT) · Q3/26 MARGIN% · Incr Q1/27 Cost · Suggestive Price ·
[Q4-Q1 PROPOSED OFFER]  · % Incr · Margin ·
COST 4 QUARTERS: COST Q3/26 · COST Q4/26 · COST Q1/27 · COST Q2/27 ·
INCR Q4 · % · INCR Q1 · % · MOVEMENT BA…
```

The `Proposed Offer` column is highlighted yellow — it is the decision cell.

**Why this matters.** This is the downstream half of the business, and it is where wheat cost
becomes a customer price. It runs on a **quarterly** cycle in **THB/kg ex-VAT**, with forward
cost for four quarters and margin per customer per SKU. Any "single source of truth" that stops
at the wheat purchase stops one step short of the decision Pavin is actually paid for.

Note also: **there is a `Claude` button in his Excel ribbon.** He is already running Claude
inside his workflow.

---

## 5. `(FINAL) DOCS_MV PACIFIC WIND_UFM.pdf` — the shipment document pack  [39:14, 39:59, 40:32, 40:43]

**45 pages in one merged PDF**, named for the vessel. Same vessel as the futures tracker tab.

Pages identified:
- **Baltimore Form C — Berth Term Grain Bill of Lading (ORIGINAL)**: shipped by United Grain
  Corporation on MV Pacific Wind; port of discharge **KOHSICHANG, THAILAND**; consignee United
  Flour Mill PCL.
- **USDA APHIS Phytosanitary Certificate** No. `F-F-41051-14335343-7-N`, issued Portland OR,
  inspected September 05 2026. Treatment: fumigation, aluminium phosphide, 45 g/1000 ft³,
  18 days at 61.2 °F, **Hold Number 5**. Quantity `19,573,193 lb` (≈ 8,878 t).
  Consignee address: 177, 205 Ratchawong Road, Khet Samphanthawong, Bangkok 10100.
- **Commodity Inspection Certificate**
- **SGS certificates**

**Design consequence:** documents do not arrive as tidy separate attachments. They arrive as
**one merged pack per vessel**, and certificates are issued **per hold**, not per cargo. The
task is therefore *splitting and classifying pages inside a PDF* and reconciling per-hold
quantities to the contract — a different and more tractable problem than filing attachments.

---

## 6. `Pavin's Procurement Notes` — OneNote  [25:14, 25:38, 26:29, 60:44]

Notebook `UFM`, section `Procurement` (siblings: WORKPLAN, Factory, 331, DM, Excom, Sales, BD,
Data, Fuji, Rich's, SFT, Family Council, Others).

### "How Price is determined" — his own causal model  [60:44]

```
Weather → Crop Condition → Production → Supply ┐
                                Seasonal ──────┤
                     Export → Demand ──────────┘
                                    ↓
                             Supply / Demand
                                    ↓
    Commitment of Traders  →      PRICE      ←  Government Policy
                                    ↑
                                  Forex
```

A market-intelligence view should use **this taxonomy**, not an invented one.

### Page list — domain we did not know existed

`Approved Suppliers` · `[NCI] Intro to Procurement` · **USW Seminars** (Buhler, USW, Technical
Milling) · **Procurement Meetings** (Somchit, Pracha, UGC Visit, Agrex) · **`Gafta Contract -
Mit…`** · **`Despatch/Demurrage…`** · **Options Trading Notes** (`Procurement Seller's…`,
`KKP Options Trading…`) · **AVS Notes** (AVS Meetings 1 & 2) · **Mitsui Training 2026**
(`Mitsui Basis Covering…`, Singapore/Tokyo training).

New facts: trades run on **GAFTA contracts**; **despatch/demurrage** (laytime economics) is a
tracked cost; he uses **options**, not only futures, with KKP (Kiatnakin Phatra); **AVS** is the
consultancy with both an Outlook folder and a OneNote section.

### Approved Suppliers page  [25:14]

Price-indication contacts: `tanaporn.tardsoonchit@mitsubishicorp.com`, `Peter_Tan@cargill.com`,
`wwang@columbiagrain.com`, `Krittapat.Chulasareekul@adm.com`, `Michael.Carringi@ldc.com`,
`A.Chonnigarn@mitsui.com`. Wheat Letters: `sduley@unitedgrain.com`, `rfroom@unitedgrain.com`,
`…@uswheat.org`.

So the supplier set is **Mitsui, Mitsubishi, Cargill, ADM, LDC, Columbia Grain, Bunge, CHS,
United Grain** — nine, not the four or five the notes imply.

---

## 7. `260813 Final Presentation to Mitsui vShare` — volumes and pooling  [21:43, 22:28]

Slide 17, "UFM: Our wheat requirements":

| | Frequency | Volume | Structure |
| --- | --- | --- | --- |
| **US Wheat** | ~3-4 ships / year | ~55K mt | **Supramax with gear**; combined shipment **UFM + SFT**; spec many per SUBLOT, NS 14.5% (DHV 65) … |
| **Australian Wheat** | ~3 ships / year | ~20K mt | 15K UFM, 5K SFT; **~40% of cargo total**; combined shipment **7 mills** |
| **Canadian Wheat** | ~3 ships / year | ~15K mt | 10K UFM, 5K SFT; **~20% of cargo total**; combined shipment **7 mills** |

**This corrects the Granola notes on three points:**
1. ~9–10 cargoes a year, not 10–12.
2. **Supramax with gear** for US, not uniformly "55,000-ton Panamax".
3. Pooling is not a fallback for when UFM cannot fill a vessel. For Australian and Canadian
   wheat it is **the standard structure across 7 mills**, with UFM holding only 20–40% of the
   cargo. A co-load feature is therefore about *share of someone else's cargo*, not about
   topping up your own.

Footnotes on the slide compare price and spec parity against "other 5 mills" / "other 4 mills".

---

## 8. Market intelligence he already consumes  [34:24 – 38:24, 59:55, 61:09]

- **Broker research deck** with `MACRO ECONOMIC/FUNDAMENTAL | Summary` as a two-column
  **BEARISH | BULLISH** table — bearish: price seasonality, stocks, hawkish Fed, US weather and
  yield expectations; bullish: geopolitical risk, Strait of Hormuz and Black Sea vessel attacks,
  Red Sea tensions, Black Sea flow uncertainty, vessel insurance rates. Includes a note that the
  USDA supply and demand report is "coming up Wed next week" and is "very important".
  **This two-column format is the Daily Brief, already validated.**
- **Bloomberg/Reuters headline digest** — headline, source, date, short summary, grouped by
  commodity.
- **"Agricultural seasonal trend"** charts; **US crop cycle** calendars.
- **`Weather | USA Weather update`** — temperature and precipitation maps.
- **Baltic Index** — "Index: Average of Routes vs Individual Route", daily, "average rate of
  reported rates by Major Shipping Broking firms".
- **Daily market information emails** (e.g. October 02 2026) covering corn/soy harvest pressure,
  S&P Global yield estimates, USDA export sales announcements.
- **Barchart** price charts throughout (`Hard Red Winter Wheat Mar '27 (KEH27)`, `KEZ26`).
- Weekly **"Price Report"** emails from a `Luke Muller`, and "Vendor Report" newsletters.

## 9. Freight is computed, not looked up  [59:59]

From `Mitsui Training Singapore Notes W1`:

- *"Based on Justin's past 8 years of experience, normal range is $30-35"* (USD/MT).
- Headline: **"Calculate Theoretical Freight Rate — Use system to calculate Theoretical
  Freight."**
- A chartering calculator with columns `Vessel · Scrubber · Open Location · Open Date ·
  GTC ($/d) · NTC ($/d) · Ballast Bonus ($)` — e.g. `Baltic 38 · No Scrubber · Singapore ·
  GTC 13,831 · NTC 13,139.45 · 0`, and a Fremantle row at `15,000 / 14,250`.

Freight is built from vessel time-charter day rates, position, ballast bonus and bunkers.
Mitsui trained him to do this — i.e. to **audit the freight line in a supplier's quote** rather
than accept it. That is a concrete, named requirement.

---

## Open questions this raises for the follow-up call

1. The `Competitor Prices` tab exists — what is in it, and where do those numbers come from?
2. Is the 70/30 flat/basis split a policy, a per-cargo negotiation, or Pavin's own judgement?
3. Who else touches the `PROCUREMENT TRACKER` — is it Pavin's private file, like the Granola
   notes imply, or shared with Khun Pracha?
4. How are despatch/demurrage currently calculated and by whom?
5. What exactly does AVS deliver each week, and what does the $1,500/month buy that the daily
   broker decks and USW letters do not?
6. Does the Power Automate flow write to `wheat_price_template` only, or also into
   `PROCUREMENT TRACKER`'s `Price Indications` tab?
