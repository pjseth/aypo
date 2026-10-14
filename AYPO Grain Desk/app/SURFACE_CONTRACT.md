# Surface contract

Every screen is one file, `app/js/surfaces/<key>.js`, loaded as a **classic script** (no
modules, no imports, no `fetch`). It registers itself and does nothing else at load time.

```js
/* app/js/surfaces/example.js */
(function () {
  "use strict";
  var el = GD.el, icon = GD.icon, fmt = GD.fmt, D = window.DESK;

  GD.route("example", {
    label: "Example",          // sidebar + breadcrumb
    icon: "brief",             // key from the icon set below
    group: "Desk",             // sidebar section: "Desk" | "Trade" | "Operations"
    badge: function () { return D.quotes.length; },   // optional, sidebar count
    badgeTone: "accent",                               // optional chip tone
    render: function (wrap) {
      wrap.appendChild(GD.card({ title: "Hello", body: [ el("p", { text: "hi" }) ] }));
    }
  });
})();
```

`render(wrap)` receives an empty `<div class="wrap">` (a grid with 18px gap) and appends to
it. It is called on every route change — it must be idempotent and must not leak listeners
on `document` or `window` without cleaning up. Direct children of `wrap` are staggered in on
entry automatically; do not add your own entrance animation.

**Never** use `innerHTML` with data. Build nodes with `GD.el`. If you must set markup, it is
only for strings you authored yourself.

## Hard rules

1. **No `transition: all`.** Name properties. Most state changes are already covered by the
   existing classes — prefer them over new CSS.
2. **No new CSS files.** Use the classes in `app/css/app.css`. If you genuinely need a new
   rule, use an inline `style` object on the node instead of editing shared CSS.
3. **Numbers use `GD.fmt.*`** and live in a `.num` cell or an element with
   `font-variant-numeric: tabular-nums` (the `num` class and table cells already have it).
4. **Price direction is from the buyer's side**: a *falling* price is good (`val-down`,
   emerald) and a *rising* one is bad (`val-up`, coral). Use `GD.toneFor(delta)` which
   returns the right class; pass `true` as the second argument when a positive number is the
   good outcome (e.g. savings, P&L).
5. **Every interactive element is a real `<button>`** with a `type="button"`, or an element
   with `tabindex="0"` plus Enter/Space handling. Icon-only buttons need `aria-label`.
6. **Nothing is colour-only.** A status carries a label or an icon as well as a hue.
7. Charts come from `GD.chart.*`. Do not hand-roll SVG in a surface.

## Available helpers (all on `GD`)

| Helper | Signature | Notes |
| --- | --- | --- |
| `GD.el` | `el(tag, attrs, kids)` | `attrs`: `class`, `text`, `html`, `style` (object), `on<Event>` fn, anything else becomes an attribute. `kids` is a node, string, or nested array; `null`/`false` entries are skipped. |
| `GD.icon` | `icon(name, size, stroke)` | Returns an `<svg>`. Stroke `1.5` next to regular text, `2` next to semibold. |
| `GD.card` | `card({title, sub, actions, body, foot, flush, class})` | `actions`/`body`/`foot` take nodes or arrays. `flush: true` removes body padding (use for tables). |
| `GD.stat` | `stat({label, value, unit, icon, delta, aside, hero, onClick})` | KPI tile. `delta` is an array of nodes shown in the footer strip. `hero: true` paints it emerald. |
| `GD.table` | `table({cols, rows, onRow, rowClass, sortKey, sortDir, emptyText})` | Returns a node with `.update(rows)`. `cols`: `{key, label, num, render(row), sortValue(row), sortable:false, width}`. |
| `GD.seg` | `seg([{key,label}], activeKey, fn)` | Segmented control. |
| `GD.chip` | `chip(text, tone, iconName)` | tone: `good` `bad` `warn` `info` `accent` `outline` or omit. |
| `GD.dot` | `dot(tone)` | tone: `good` `warn` `bad`. |
| `GD.kv` | `kv([[label, valueOrNode], …])` | Definition list. Skips falsy rows. |
| `GD.compo` | `compo([{name, value, color}], total)` | Stacked composition bar with per-segment tooltips. |
| `GD.legend` | `legend([{name, color}])` | |
| `GD.sheet` | `sheet({title, sub, body(close), foot(close), class})` | Modal. Focus-trapped, Escape closes. |
| `GD.toast` | `toast(msg, {icon, duration})` | |
| `GD.tipOn` | `tipOn(node, htmlOrFn)` | Attaches a tooltip. HTML string — escape data with `GD.esc`. |
| `GD.go` | `go(routeKey)` | Navigate. |
| `GD.toneFor` | `toneFor(v, positiveIsGood)` | Returns `val-up` / `val-down` / `val-mute`. |
| `GD.esc` | `esc(s)` | HTML-escape, for tooltip strings only. |

### Charts

| Call | Returns |
| --- | --- |
| `GD.chart.spark(values, {w,h,take,color,fill,endpoint,weight})` | Line sparkline with area + endpoint dot. |
| `GD.chart.barSpark(values, {w,h,take,highlight,color,muted,label,gap})` | Thin bars, one highlighted with a dot and an optional label above it. |
| `GD.chart.line({series:[{name,values,color}], dates, w, h, zero, fmtY, fmtTip, label})` | Multi-series line, grid, crosshair tooltip. |
| `GD.chart.groupedBars({groups:[{label,values}], names:[{name,color}], w, h, fmtY, fmtTip, label})` | Zero-based grouped bars. |

### Formatting — `GD.fmt`

`n(v,dp)` `usd(v,dp)` `usd0(v)` `money(v)` → `$1.23M`/`$275k` · `signed(v,dp)` ·
`signedMoney(v)` · `pct(v,dp)` (takes a fraction) · `signedPct(v,dp)` · `thb(v,dp)` ·
`date(s)` · `dateShort(s)` · `time(s)` · `ago(s)` → `"today 09:23"` / `"3 days ago"` ·
`daysBetween(a,b)` · `months`.

Dates in the data are `"YYYY-MM-DD"` or `"YYYY-MM-DD HH:MM"`. `ago()` is relative to
`DESK.built` (2026-10-13), never to the viewer's clock.

### Icon names

`brief quotes position cargo market margin savings search bell sun moon chevR chevD chevU
check x alert up down clock file download plus filter sparkle ship calendar users zap shield
eye mail send layers sort menu panel dollar scale globe wheat ext`

### Colour tokens for charts

`var(--c-futures)` `var(--c-basis)` `var(--c-freight)` `var(--c-margin)` `var(--c-residual)`
and `var(--accent)` `var(--up)` `var(--down)` `var(--warn)` `var(--info)` `var(--ink-3)`
`var(--ink-4)` `var(--surface-3)`.

## The data — `window.DESK`

```
built            "2026-10-13"
profile          { mill, short, group[], country, currency, fx, buyer{name,email,role},
                   head_of_procurement, ports[{code,name,diff_usd_mt,default}],
                   origins[{code,label,terminal,vessel,cargo_mt,ufm_mt,per_year,pool[],share,
                            freight_usd_mt[lo,hi]}],
                   classes[{code,label,origin,protein,futures,spec}],
                   suppliers[{name,full,contact,email,desk,approved,tier}],
                   economics{wheat_share_of_cost,cargo_mt,move_usd_mt,
                             advisor_retainer_usd_month,advisor_share_usd_mt,
                             advisor_typical_saving_usd_mt} }

instruments[]    { key, label, venue, unit, last, role("hedge"|"fx"|"freight"), classes[],
                   history[180], dates[180], prev, change, change_pct, usd_mt|null,
                   month_ago, synthetic_history }

quotes[]         { id, received, supplier, origin, cls, shipment, flat, stated_basis|null,
                   stated_futures|null, qty|null, protein, notes, port, instrument,
                   futures_usd_mt, freight_usd_mt, residual_usd_mt, itemised, cargo_value,
                   vs_best, is_best, fair_residual, implied_margin }

dropped_quote    { received, supplier, origin, cls, shipment, flat, qty, note, why }

position         { cargo, origin, counterparty, contract_date, shipment, arrival, port, fx,
                   total_usd, take_profit_usd, loss_cut_usd, breached_on, breach,
                   total_thb, headroom_to_tp, blocks_total,
                   blocks[{ cls, spec, instrument, flat_price, futures_settlement,
                            implied_basis, flat_pct, basis_pct, total_qty, flat_qty, basis_qty,
                            total_contracts, flat_contracts, basis_contracts, remaining,
                            avg_futures, avg_flat, mtm, mtm_label, subtotal_usd,
                            filled_lots, open_lots,
                            legs[{date,lots,status("Filled"|"Open"),cents,usd_mt,basis,flat,pnl}] }] }

cargoes[]        { vessel, origin, supplier, status("discharging"|"sailing"|"nominated"),
                   laycan, eta, port, mt, ufm_mt, usd_mt, pool[[name,mt]], exporter, pack,
                   pages, holds[{no,cls,lb,cert}],
                   docs[{key,label,issuer,status("received"|"expected"|"overdue"),date,pages}],
                   variance[{field,contract,certificate,severity("claim"|"ok"),note}],
                   docs_received, docs_total, docs_overdue, completeness, value_usd,
                   ufm_value_usd, holds_mt }

doc_types[]      { key, label, issuer }
drivers[]        { key, label, feeds }      // Pavin's own causal model
intel[]          { date, driver, stance("bullish"|"bearish"), usd_mt, headline, body, source }
wasde[]          { date, local, dst, status("released"|"scheduled"),
                   verdicts[{cls,stance,surprise,usd_mt,expected,actual}] }
flour[]          { sku, pack, seller("UFM"|"MCF"), baht_kg, margin, cost[4], cost_now,
                   incr_q1, incr_pct, suggested }
grades[]         { grade, protein_min, mix[{cls,share,usd_mt,protein}], wheat_usd_mt,
                   blend_protein, meets, flour_thb_kg }
savings[]        { date, kind("counter"|"timing"|"freight"), supplier, cls, qty, usd_mt,
                   total, note }
savings_total    number
advisor          { retainer_year, fee_per_cargo, fee_per_year }
provenance       { real[], synthetic[] }
```

## Shared detail views

Two detail modals are referenced from more than one surface. Whichever surface owns them
must assign them to `GD`:

- `GD.showQuote(quote)` — owned by `surfaces/quotes.js`
- `GD.showCargo(cargo)` — owned by `surfaces/cargo.js`

Guard calls to the other one: `if (GD.showCargo) GD.showCargo(c);`

## Facts worth putting on screen

- A cargo is 55,000 t; at ~$350/t that is **$19.25M**, and **$5/t = $275,000**.
- Wheat is **80%** of the mill's cost.
- `stated_basis` is populated on **2 of 26** quotes. Everything else is reconstructed — say
  so rather than implying the four-way split came from the supplier.
- The reconstruction is good: on both quotes that *do* state a basis, the derived residual
  lands within **$1/t** of it (−0.95 and +1.02). That is worth showing.
- `dropped_quote` is a real quote the existing Power Automate flow lost.
- The position is **−$378,749** and breached its −$25,000 loss cut on 2 Oct.
- Advisor success fees run ~**$27,500 a cargo**, ~**$275,000 a year**.
