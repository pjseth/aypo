/* Grain Desk — Morning Brief.
   The 07:00 surface. Two questions only: what happened overnight, and what
   needs Pavin today. Everything here is derived from window.DESK at render
   time — no cached state between route changes, no Math.random(), and no
   reading of the viewer's clock (the dataset is fixed at DESK.built).

   House rules followed throughout: nodes via GD.el, numbers via GD.fmt, a
   falling price is the emerald outcome because this is a buyer's screen, and
   any figure the desk reconstructed rather than received says so on screen. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, chip = GD.chip, dot = GD.dot;
  var D = window.DESK;
  var ECON = (D.profile && D.profile.economics) || {};
  var CARGO_MT = ECON.cargo_mt || 55000;           /* one cargo, 55,000 t */

  var LB_PER_T = 0.45359237 / 1000;                /* pounds → tonnes */

  /* ------------------------------------------------------------- helpers */

  /* "−$4.23/t". U+2212 rather than a hyphen so the sign sits at digit height
     and the tabular columns stay aligned. */
  function perT(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+$" : v < 0 ? "−$" : "$") + fmt.n(Math.abs(v), dp === undefined ? 2 : dp) + "/t";
  }
  /* Exact signed dollars: "−$378,749". fmt.usd0 would print "$-378,749". */
  function usdS(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+$" : v < 0 ? "−$" : "$") + fmt.n(Math.abs(v), dp === undefined ? 0 : dp);
  }
  /* Negative money with the typographic minus: "−$379k". */
  function moneyS(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v < 0 ? "−" : "") + fmt.money(Math.abs(v));
  }
  /* Date-only dates have no time, so fmt.ago() would print "today 00:00". */
  function agoDay(s) {
    var d = fmt.daysBetween(s, D.built);
    if (d === null) return "—";
    if (d <= 0) return "today";
    if (d === 1) return "yesterday";
    if (d < 14) return fmt.n(d, 0) + " days ago";
    return fmt.dateShort(s);
  }
  function plural(n, one, many) { return fmt.n(n, 0) + " " + (n === 1 ? one : many); }

  /* .stat-delta wraps at its own 6px gap and puts tabular figures on <b> only.
     These tiles pack three facts into it, so the content rides in its own box
     with a tighter gap and equal-width digits on every number, not just the
     bold ones — otherwise "cut at −$25,000" sits on proportional digits. */
  function wrapRow(kids) {
    return el("div", {
      style: {
        display: "flex", "flex-wrap": "wrap", "align-items": "center",
        "column-gap": "5px", "row-gap": "1px", "min-width": "0",
        "font-variant-numeric": "tabular-nums"
      }
    }, kids);
  }
  function sep() { return el("span", { style: { color: "var(--ink-4)" }, text: "·" }); }
  function tinted(tone) {
    if (tone === "bad") return { "background-color": "var(--up-soft)", color: "var(--up)" };
    if (tone === "warn") return { "background-color": "var(--warn-soft)", color: "var(--warn)" };
    if (tone === "accent") return { "background-color": "var(--accent-soft)", color: "var(--accent-2)" };
    if (tone === "info") return { "background-color": "var(--info-soft)", color: "var(--info)" };
    return {};
  }
  function tipRow(label, value) {
    return "<div class='tip-row'><span class='tip-mute'>" + GD.esc(label) + "</span><b>" +
      GD.esc(value) + "</b></div>";
  }
  /* A money figure must never break across lines — "−" then "$379k" on the next
     row reads as two numbers. The tiles are wide enough for every value here. */
  function noWrapValue(st) {
    var v = GD.qs(".stat-value", st);
    if (v) v.style.setProperty("white-space", "nowrap");
    return st;
  }
  /* .btn is 500 and .btn-primary is 600, so every glyph inside a button is
     drawn at stroke 2 — the weight the icon set pairs with medium/semibold
     text. A 1.75 glyph beside a 500 label reads a shade too light next to the
     primary button sitting right beside it. */
  function goBtn(label, route, ico, cls) {
    return el("button", {
      class: cls || "btn btn-sm", type: "button",
      onclick: function () { GD.go(route); }
    }, [ico ? icon(ico, 13, 2) : null, label, icon("chevR", 12, 2)]);
  }

  /* --------------------------------------------------------- derivations */

  function instByKey(key) {
    var hit = null;
    (D.instruments || []).forEach(function (i) { if (!hit && i.key === key) hit = i; });
    return hit;
  }
  /* The listed contract that carries a wheat class, straight off the data. */
  function instForClass(cls) {
    var hit = null;
    (D.instruments || []).forEach(function (i) {
      if (!hit && i.classes && i.classes.indexOf(cls) >= 0) hit = i;
    });
    return hit;
  }
  /* c/bu → $/t, taken from the instrument itself rather than hard-coded. */
  function centsToT(ins) {
    return ins && ins.last && ins.usd_mt ? ins.usd_mt / ins.last : null;
  }
  function quotesFor(cls) {
    return (D.quotes || []).filter(function (q) { return q.cls === cls; })
      .sort(function (a, b) { return a.received < b.received ? -1 : a.received > b.received ? 1 : 0; });
  }
  /* The class the desk has worked hardest. A tie breaks to whichever class was
     quoted most recently, so the headline can never be a stale class. */
  function mostQuotedClass() {
    var tally = {}, keys = [];
    (D.quotes || []).forEach(function (q) {
      if (!tally[q.cls]) { tally[q.cls] = { cls: q.cls, n: 0, last: "" }; keys.push(q.cls); }
      var t = tally[q.cls];
      t.n += 1;
      if (q.received > t.last) t.last = q.received;
    });
    var all = keys.map(function (k) { return tally[k]; });
    all.sort(function (a, b) {
      return b.n - a.n || (a.last < b.last ? 1 : a.last > b.last ? -1 : 0);
    });
    return all.length ? all[0] : null;
  }
  /* Lowest flat on the most-quoted class, against that class's 30-day average.
     The window is measured from DESK.built, never from the viewer's clock. */
  function cheapest() {
    var top = mostQuotedClass();
    if (!top) return null;
    var qs = quotesFor(top.cls);
    if (!qs.length) return null;

    var low = qs[0], i;
    for (i = 1; i < qs.length; i++) {
      if (qs[i].flat < low.flat || (qs[i].flat === low.flat && qs[i].received > low.received)) low = qs[i];
    }
    var win = qs.filter(function (q) {
      var d = fmt.daysBetween(q.received, D.built);
      return d !== null && d <= 30;
    });
    /* One indication inside 30 days is not an average, so the window widens to
       the whole class — and then the copy has to stop calling it a 30-day
       average, because it would not be one. */
    var is30 = win.length >= 2;
    if (!is30) win = qs;
    var sum = 0;
    win.forEach(function (q) { sum += q.flat; });
    var avg = sum / win.length;

    /* Bars are capped so a heavily-quoted class cannot overflow the sparkline.
       The window is the most recent MAXB indications, slid back far enough to
       keep the lowest one inside it: slicing blind would push the highlight off
       the front and the bar labelled "best" would be whichever landed last.
       The high is the real maximum of the drawn bars, not chart.extent's padded
       one, because the copy quotes it as a price. */
    var all = qs.map(function (q) { return q.flat; });
    var MAXB = 12;
    var lowAt = qs.indexOf(low);
    var start = Math.max(0, all.length - MAXB);
    if (lowAt < start) start = lowAt;
    var flats = all.slice(start, start + MAXB);
    return {
      cls: top.cls, quote: low, avg: avg, window: win.length,
      avgLabel: is30 ? "the 30-day average" : "the average of every indication on the class",
      avgShort: is30 ? "30-day average" : "average, all indications",
      gap: low.flat - avg, flats: flats, hi: lowAt - start,
      top: Math.max.apply(null, flats), total: all.length
    };
  }
  /* The one futures leg still resting unfilled, and how far the market is from
     it. MWU26 has rolled off the board, so the comparison is against the listed
     contract that carries the same class — said out loud in the row. */
  function openLeg() {
    var out = null;
    ((D.position && D.position.blocks) || []).forEach(function (bk) {
      (bk.legs || []).forEach(function (lg) {
        if (out || lg.status !== "Open") return;
        out = { block: bk, leg: lg };
      });
    });
    if (!out) return null;
    var bk = out.block;
    out.live = instForClass(bk.cls);
    out.factor = centsToT(out.live);
    out.tonnes = bk.total_contracts ? (bk.total_qty / bk.total_contracts) * out.leg.lots : null;
    if (out.live && out.factor) {
      out.gapCents = out.live.last - out.leg.cents;
      out.gapUsdT = out.gapCents * out.factor;
      out.money = out.tonnes ? Math.abs(out.gapUsdT * out.tonnes) : 0;
    } else {
      out.gapCents = null;
      out.money = 0;
    }
    return out;
  }
  function holdFor(cargo, field) {
    var m = String(field || "").match(/hold\s*(\d+)/i);
    if (!m) return null;
    var no = Number(m[1]), hit = null;
    (cargo.holds || []).forEach(function (h) { if (!hit && h.no === no) hit = h; });
    return hit;
  }
  /* The contracted price for a parcel: the position block that priced that
     class on that vessel, otherwise the cargo's own average. */
  function contractPrice(cargo, hold) {
    var P = D.position;
    var px = null;
    if (P && P.cargo === cargo.vessel && hold) {
      (P.blocks || []).forEach(function (bk) { if (!px && bk.cls === hold.cls) px = bk.flat_price; });
    }
    return px || cargo.usd_mt || null;
  }

  var AGENT_META = {
    buyer: { name: "The Buyer", icon: "quotes", route: "quotes" },
    watch: { name: "The Watch", icon: "market", route: "market" },
    clerk: { name: "The Clerk", icon: "cargo", route: "cargo" }
  };

  /* ------------------------------------------------------- "needs you" rows
     Only real conditions in the data produce a row. Each row carries one money
     figure with a label naming what that figure is, because an exposure and a
     cost are not the same thing and a brief that blurs them is lying. */

  function needsRows() {
    var rows = [];

    /* 1 — the indication the existing Power Automate flow lost. */
    var dq = D.dropped_quote;
    if (dq) {
      rows.push({
        agent: "buyer", tone: "accent", ico: "sparkle", sev: "Recovered",
        statement: "A " + fmt.n(dq.qty, 0) + " t " + dq.cls + " indication at " + fmt.usd(dq.flat) +
          "/t never reached the sheet",
        detail: dq.supplier + " · " + dq.shipment + " · received " + fmt.date(dq.received) + " " +
          fmt.time(dq.received) + ". " + dq.why + " The Buyer read it out of the mail itself.",
        money: dq.qty * dq.flat, kind: "indication",
        tip: "The indication's own cargo value — " + fmt.n(dq.qty, 0) + " t × " + fmt.usd(dq.flat) +
          "/t. Exposure that went missing, not a loss taken.",
        route: "quotes", cta: "Quote board"
      });
    }

    /* 2 — quotes carrying more residual than the class's fair level. */
    (D.quotes || []).filter(function (q) { return q.implied_margin > 3; }).forEach(function (q) {
      var later = quotesFor(q.cls).filter(function (o) { return o.received > q.received; });
      var newest = later.length ? later[later.length - 1] : null;
      rows.push({
        agent: "buyer", tone: "warn", ico: "scale", sev: "Above fair",
        statement: q.supplier + "'s " + q.cls + " carries " + fmt.usd(q.implied_margin) +
          "/t more than the class's fair residual",
        detail: "Quoted " + fmt.dateShort(q.received) + " at " + fmt.usd(q.flat) + "/t for " + q.shipment +
          ". Residual " + fmt.usd(q.residual_usd_mt) + " against " + fmt.usd(q.fair_residual) + " fair" +
          (q.itemised ? ", measured against the basis " + q.supplier + " stated."
                      : " — reconstructed from futures and freight. " + q.supplier + " did not itemise it.") +
          (newest ? " Since re-indicated at " + fmt.usd(newest.flat) + "/t (" + newest.supplier + ", " +
            fmt.dateShort(newest.received) + ")." : ""),
        money: q.implied_margin * CARGO_MT, kind: "per cargo",
        tip: perT(q.implied_margin) + " × " + fmt.n(CARGO_MT, 0) + " t. The gap between the residual " +
          "this quote implies and the fair residual for the class.",
        route: "quotes", cta: "Quote board"
      });
    });

    /* 3 — the loss-cut breach. */
    var P = D.position;
    if (P && P.breach) {
      var past = P.total_usd - P.loss_cut_usd;
      var days = fmt.daysBetween(P.breached_on, D.built);
      rows.push({
        agent: "watch", tone: "bad", ico: "alert", sev: "Loss cut breached",
        statement: "The open position sits " + fmt.money(Math.abs(past)) + " past its loss cut, and has for " +
          plural(days, "day", "days"),
        detail: P.cargo + " · " + P.counterparty + " · " + usdS(P.total_usd) + " against a " +
          usdS(P.loss_cut_usd) + " cut, breached " + fmt.date(P.breached_on) + " · ≈ −฿" +
          fmt.n(Math.abs(P.total_thb) / 1e6, 1) + "M at " + fmt.n(P.fx, 2) + " · " +
          plural((P.blocks || []).length, "block", "blocks") + " priced, nothing unwound.",
        money: Math.abs(P.total_usd), kind: "open mark",
        tip: "Mark to market on the whole position: " + usdS(P.total_usd) + ". Take-profit sits at " +
          usdS(P.take_profit_usd) + ".",
        route: "position", cta: "Position"
      });
    }

    /* 4 — documents a cargo is late on. */
    (D.cargoes || []).forEach(function (c) {
      (c.docs || []).filter(function (dc) { return dc.status === "overdue"; }).forEach(function (dc) {
        rows.push({
          agent: "clerk", tone: "warn", ico: "file", sev: "Document overdue",
          statement: c.vessel + " is " + c.status + " with " + fmt.n(c.docs_received, 0) + " of " +
            fmt.n(c.docs_total, 0) + " documents in — the " + dc.label.toLowerCase() + " is marked overdue",
          detail: "Issuer: " + dc.issuer + " · date on file " + fmt.dateShort(dc.date) + " · ETA " +
            fmt.date(c.eta) + " at " + c.port + " · UFM takes " + fmt.n(c.ufm_mt, 0) + " t of " +
            fmt.n(c.mt, 0) + " t. Document dates on this cargo are synthesised; the ten-document pack " +
            "structure is the mill's own.",
          money: c.ufm_value_usd, kind: "exposure",
          tip: "UFM's share of the cargo this pack clears — " + fmt.n(c.ufm_mt, 0) + " t × " +
            fmt.usd(c.usd_mt) + "/t. Exposure held up, not money lost.",
          route: "cargo", cta: "Cargo desk"
        });
      });
    });

    /* 5 — a certificate that printed outside the contract. */
    (D.cargoes || []).forEach(function (c) {
      (c.variance || []).filter(function (v) { return v.severity === "claim"; }).forEach(function (v) {
        var hold = holdFor(c, v.field);
        var px = contractPrice(c, hold);
        var tonnes = hold ? hold.lb * LB_PER_T : null;
        var money = (tonnes && px) ? tonnes * px : (c.ufm_value_usd || 0);
        rows.push({
          agent: "clerk", tone: "bad", ico: "alert", sev: "Claim",
          statement: c.vessel + " — " + v.field + " printed " + v.certificate + " against " + v.contract,
          /* v.note already carries the pound weight, so the detail converts it
             rather than printing the same figure twice. */
          detail: v.note + (tonnes ? " The parcel is " + fmt.n(tonnes, 0) + " t" +
            (px ? ", at the " + fmt.usd(px) + "/t contract price on that block" : "") + "." : "") +
            (hold ? " Certificate " + hold.cert + "." : "") +
            " The certificate numbers and the pack are the mill's own; this variance is an illustration " +
            "of the reconciliation, not a claim the mill has filed.",
          money: money, kind: "parcel",
          tip: (tonnes ? fmt.n(tonnes, 0) + " t" : "The parcel") + " priced at " +
            (px ? fmt.usd(px) + "/t" : "the cargo average") + ". The parcel under review, not a claim value — " +
            "no claim rate is in the data.",
          route: "cargo", cta: "Cargo desk"
        });
      });
    });

    /* 6 — the futures leg still resting. */
    var ol = openLeg();
    if (ol && ol.gapCents !== null) {
      var below = ol.gapCents > 0;
      rows.push({
        agent: "watch", tone: "info", ico: "clock", sev: "Resting order",
        statement: plural(ol.leg.lots, "lot", "lots") + " still rest at " + fmt.n(ol.leg.cents, 1) +
          "c — the market has to come " + fmt.n(Math.abs(ol.gapCents), 1) + "c " +
          (below ? "lower" : "higher") + " to fill",
        detail: "Basis leg of " + fmt.date(ol.leg.date) + " on the " + ol.block.cls + " block (" +
          ol.block.instrument + ")" + (ol.tonnes ? ", ≈ " + fmt.n(ol.tonnes, 0) + " t unpriced" : "") + " · " +
          ol.live.key + " — the listed contract carrying this class now that " + ol.block.instrument +
          " has rolled off — printed " + fmt.n(ol.live.last, 1) + "c, " +
          fmt.usd(Math.abs(ol.gapUsdT)) + "/t away · " + fmt.usd0(ol.money) + " at today's print.",
        money: ol.money, kind: "to fill",
        tip: fmt.n(Math.abs(ol.gapCents), 1) + "c × " + fmt.n(ol.factor, 4) + " $/t per cent × " +
          fmt.n(ol.tonnes || 0, 0) + " t = " + fmt.usd0(ol.money) + ".",
        route: "position", cta: "Position"
      });
    }

    rows.sort(function (a, b) { return b.money - a.money; });
    return rows;
  }

  /* ----------------------------------------------------------- page parts */

  function pageHead() {
    var perYear = 0;
    ((D.profile && D.profile.origins) || []).forEach(function (o) { perYear += o.per_year || 0; });
    var cargoes = perYear ? (Math.floor(perYear) === Math.ceil(perYear)
      ? fmt.n(perYear, 0) : fmt.n(Math.floor(perYear), 0) + "–" + fmt.n(Math.ceil(perYear), 0)) : "—";
    var move = (ECON.move_usd_mt || 5) * CARGO_MT;

    /* cursor:help is the only affordance these two carry — they look like the
       rest of the sub-head, and without it the breakdown behind them is
       undiscoverable. Applied to every plain-text tooltip target on the page. */
    var cargoSpan = el("span", { style: { cursor: "help" }, text: cargoes + " cargoes a year" });
    GD.tipOn(cargoSpan, "<b>The buying year</b>" +
      ((D.profile && D.profile.origins) || []).map(function (o) {
        return tipRow(o.code + " · " + fmt.n(o.cargo_mt, 0) + " t", fmt.n(o.per_year, 1) + " a year");
      }).join(""));

    var moveSpan = el("span", {
      style: { cursor: "help" },
      text: fmt.usd0(ECON.move_usd_mt || 5) + "/t = " + fmt.usd0(move)
    });
    GD.tipOn(moveSpan, "<b>Why a dollar matters</b>" +
      tipRow("one cargo", fmt.n(CARGO_MT, 0) + " t") +
      tipRow("a $1/t move", fmt.usd0(CARGO_MT)) +
      tipRow("a $5/t move", fmt.usd0(move)));

    var wasde = (D.wasde || []).filter(function (w) { return w.status === "released"; })[0];
    var replay = el("button", {
      class: "btn btn-primary", type: "button",
      onclick: function () {
        if (typeof GD.nightWatch === "function") GD.nightWatch();
        else GD.toast("Night Watch has not loaded yet.", { icon: "alert" });
      }
    }, [icon("zap", 14, 2), "Replay WASDE"]);
    GD.tipOn(replay, "<b>Night Watch</b><div class='tip-mute'>Replays the " +
      GD.esc(wasde ? fmt.date(wasde.date) : "last") + " release exactly as it landed, verdict first.</div>");

    return el("div", { class: "page-head" }, [
      el("div", { class: "ph-text" }, [
        el("h1", { class: "page-title", text: "Morning brief" }),
        el("div", { class: "page-sub", style: { display: "flex", "flex-wrap": "wrap", "align-items": "center", "column-gap": "7px", "row-gap": "2px", "font-variant-numeric": "tabular-nums" } }, [
          el("b", { style: { color: "var(--ink-2)", "font-weight": "600" }, text: fmt.date(D.built) }),
          sep(), cargoSpan,
          sep(), el("span", { text: "wheat is " + fmt.pct(ECON.wheat_share_of_cost || 0.8, 0) + " of mill cost" }),
          sep(), moveSpan
        ])
      ]),
      el("div", { class: "ph-actions" }, [
        replay,
        el("button", {
          class: "btn btn-ghost", type: "button",
          onclick: function () { GD.go("quotes"); }
        }, [icon("quotes", 14, 2), "Open quote board"])
      ])
    ]);
  }

  /* ---- the four tiles */

  function cheapStat() {
    var c = cheapest();
    if (!c) return null;
    var below = c.gap < 0;
    var tone = GD.toneFor(c.gap);
    var perCargo = Math.abs(c.gap) * CARGO_MT;

    /* Each bar is one indication, drawn as its discount to the highest of the
       bars shown, so taller means cheaper — the same direction as every other
       green on this page. Plotting raw flats would put the bar that matters,
       the lowest, flat on the floor. The transform is stated in the tooltip
       and in the sparkline's accessible description. */
    var disc = c.flats.map(function (f) { return c.top - f; });
    /* The label is not decoration: it reserves the headroom barSpark needs for
       the endpoint dot, which would otherwise clip off the top of a bar that is
       the tallest in its own series. */
    var bars = GD.chart.barSpark(disc, {
      w: Math.min(92, Math.max(40, disc.length * 11)), h: 42, gap: 2.5,
      take: disc.length, highlight: c.hi, label: "best",
      color: "var(--accent)", muted: "var(--surface-3)"
    });
    var aside = el("span", {
      role: "img", style: { flex: "none" },
      "aria-label": (c.flats.length < c.total
          ? "The last " + plural(c.flats.length, "indication", "indications") + " of " +
            fmt.n(c.total, 0)
          : plural(c.flats.length, "indication", "indications")) +
        " on " + c.cls + ", each drawn as its discount to the highest of them, " +
        fmt.usd(c.top) + ". The highlighted bar is the lowest, " + fmt.usd(c.quote.flat) +
        " — " + fmt.usd(c.top - c.quote.flat) + " below."
    }, [bars]);

    /* Not "today": the best standing indication is routinely days or weeks old,
       so the label must not claim a print from this morning. The date it was
       quoted rides in the tooltip instead. */
    var st = GD.stat({
      label: "Cheapest laid-in",
      value: fmt.usd(c.quote.flat), unit: "/t",
      icon: "wheat", aside: aside,
      delta: [wrapRow([
        el("b", { text: c.cls }), sep(),
        el("span", { class: tone, style: { display: "inline-flex", "align-items": "center", gap: "3px" } }, [
          icon(below ? "down" : "up", 12, 2),
          el("b", { text: fmt.usd(Math.abs(c.gap)) + "/t" })
        ]),
        el("span", { text: (below ? "below " : "above ") + c.avgLabel }), sep(),
        el("b", { class: tone, text: fmt.money(perCargo) }),
        el("span", { text: "a cargo" })
      ])],
      onClick: function () { GD.go("quotes"); }
    });
    GD.tipOn(st, "<b>" + GD.esc(c.cls) + " — the class the desk has quoted most</b>" +
      tipRow("best flat", fmt.usd(c.quote.flat) + "/t") +
      tipRow("from", c.quote.supplier + ", " + fmt.dateShort(c.quote.received)) +
      tipRow(c.avgShort, fmt.usd(c.avg) + "/t over " + plural(c.window, "quote", "quotes")) +
      tipRow("gap", perT(c.gap) + " · " + moneyS(c.gap * CARGO_MT) + " a cargo") +
      tipRow("bars", "discount to the highest bar, " + fmt.usd(c.top) + " — taller is cheaper") +
      "<div class='tip-mute'>Flats are as quoted — CFR " + GD.esc(c.quote.port) +
      ", nothing reconstructed.</div>");
    return noWrapValue(st);
  }

  function positionStat() {
    var P = D.position;
    if (!P) return null;
    var days = P.breached_on ? fmt.daysBetween(P.breached_on, D.built) : null;
    var delta = P.breach
      ? wrapRow([
          el("span", { class: "val-up", style: { display: "inline-flex", "align-items": "center", gap: "3px" } }, [
            icon("alert", 12, 2), el("b", { text: "Loss cut breached" })
          ]),
          el("span", { text: (days === null ? "" : plural(days, "day", "days") + " ago, ") + "cut at " + usdS(P.loss_cut_usd) })
        ])
      : wrapRow([
          el("span", { class: "val-mute", style: { display: "inline-flex", "align-items": "center", gap: "3px" } }, [
            icon("check", 12, 2), el("b", { text: "Inside the band" })
          ]),
          el("span", { text: fmt.money(P.headroom_to_tp) + " to take-profit" })
        ]);

    var st = GD.stat({
      label: "Open position",
      value: moneyS(P.total_usd),
      icon: "position",
      aside: el("span", { class: "chip chip-outline", style: { flex: "none" } }, [
        dot(P.breach ? "bad" : "good"), el("span", { text: P.cargo })
      ]),
      delta: [delta],
      onClick: function () { GD.go("position"); }
    });
    GD.tipOn(st, "<b>" + GD.esc(P.cargo) + " · " + GD.esc(P.counterparty) + "</b>" +
      tipRow("mark to market", usdS(P.total_usd)) +
      tipRow("in baht", "฿" + fmt.n(P.total_thb, 0) + " at " + fmt.n(P.fx, 2)) +
      tipRow("loss cut", usdS(P.loss_cut_usd)) +
      tipRow("take profit", usdS(P.take_profit_usd)) +
      "<div class='tip-mute'>Legs, thresholds and totals are the mill's own tracker.</div>");
    return noWrapValue(st);
  }

  function cargoStat() {
    var c = (D.cargoes || [])[0];
    if (!c) return null;
    var tone = c.status === "discharging" ? "good" : c.status === "sailing" ? "warn" : null;
    var label = c.status.charAt(0).toUpperCase() + c.status.slice(1);

    /* The status rides in the delta rather than beside the value: at four tiles
       across, a chip that long crowds the number. */
    var st = GD.stat({
      label: "Cargo exposure",
      value: fmt.money(c.value_usd),
      icon: "ship",
      delta: [wrapRow([
        el("span", { class: "chip", style: { flex: "none" } }, [dot(tone), el("span", { text: label })]),
        el("b", { text: c.vessel }), sep(),
        el("b", { text: fmt.n(c.mt, 0) + " t" }),
        el("span", { text: "at" }),
        el("b", { text: fmt.usd(c.usd_mt) + "/t" }),
        c.ufm_mt !== c.mt ? sep() : null,
        c.ufm_mt !== c.mt ? el("span", { text: "UFM " + fmt.n(c.ufm_mt, 0) + " t" }) : null
      ])],
      onClick: function () { GD.go("cargo"); }
    });
    GD.tipOn(st, "<b>" + GD.esc(c.vessel) + " · " + GD.esc(c.origin) + " → " + GD.esc(c.port) + "</b>" +
      tipRow("cargo value", fmt.money(c.value_usd)) +
      tipRow("UFM share", fmt.n(c.ufm_mt, 0) + " t · " + fmt.money(c.ufm_value_usd)) +
      tipRow("laycan", c.laycan) +
      tipRow("documents", fmt.n(c.docs_received, 0) + " of " + fmt.n(c.docs_total, 0) +
        " · " + fmt.pct(c.completeness, 0) + " complete"));
    return noWrapValue(st);
  }

  function savedStat() {
    var saved = D.savings_total || 0;
    var fee = (D.advisor && D.advisor.fee_per_year) || 0;
    var ratio = fee ? saved / fee : null;
    var st = GD.stat({
      label: "Saved this year", value: fmt.money(saved), icon: "savings", hero: true,
      delta: [wrapRow([
        icon("check", 12, 2),
        ratio ? el("b", { text: fmt.n(ratio, 1) + "×" }) : null,
        el("span", { text: "vs " + fmt.money(fee) + " in advisor success fees" })
      ])],
      onClick: function () { GD.go("savings"); }
    });
    /* The hover rule would otherwise pull a grey border onto the emerald tile. */
    st.style.setProperty("border-color", "var(--accent)");
    GD.tipOn(st, "<b>" + GD.esc(plural((D.savings || []).length, "logged win", "logged wins")) + " this year</b>" +
      (D.savings || []).slice(0, 4).map(function (s) {
        return tipRow(fmt.dateShort(s.date) + " · " + s.kind, fmt.money(s.total));
      }).join("") +
      "<div class='tip-mute'>Against an advisor at " +
      GD.esc(fmt.money((D.advisor && D.advisor.fee_per_cargo) || 0)) + " a cargo.</div>");
    return noWrapValue(st);
  }

  function statRow() {
    return el("div", { class: "grid g-4" }, [cheapStat(), positionStat(), cargoStat(), savedStat()]);
  }

  /* ---- "needs you" */

  function needsRow(r) {
    var ag = AGENT_META[r.agent] || { name: "The desk", icon: "sparkle" };
    var chipTone = r.tone === "bad" ? "bad" : r.tone === "warn" ? "warn" : r.tone === "accent" ? "accent" : "info";

    var money = el("div", {
      style: {
        flex: "none", "text-align": "right", "min-width": "104px", "align-self": "center",
        cursor: r.tip ? "help" : "default"
      }
    }, [
      el("div", {
        class: "num",
        style: { "font-size": "var(--t-head)", "font-weight": "600", "letter-spacing": "-0.015em" },
        text: fmt.money(r.money)
      }),
      el("div", { class: "label", style: { "margin-top": "1px" }, text: r.kind })
    ]);
    if (r.tip) {
      GD.tipOn(money, "<b>" + GD.esc(fmt.money(r.money)) + " · " + GD.esc(r.kind) +
        "</b><div class='tip-mute'>" + GD.esc(r.tip) + "</div>");
    }

    /* Full-height control, not btn-sm: these are the actions of the morning and
       the same label repeats down the card, so the target is generous and the
       accessible name carries the row it belongs to. */
    var action = goBtn(r.cta, r.route, null, "btn");
    action.style.setProperty("align-self", "center");
    action.setAttribute("aria-label", r.cta + ": " + r.statement);

    return el("div", { class: "note-row" }, [
      el("span", { class: "stat-ico", style: tinted(r.tone) }, [icon(r.ico, 14, 1.75)]),
      el("div", { class: "nr-body" }, [
        el("div", { class: "nr-head" }, [
          chip(r.sev, chipTone),
          chip(ag.name, "outline", ag.icon)
        ]),
        el("h4", { text: r.statement }),
        /* Three or four lines of prose in a div, so the sheet's `p { text-wrap:
           pretty }` never reaches it and the last line can orphan a word. */
        el("div", { class: "nr-meta", style: { "text-wrap": "pretty" }, text: r.detail })
      ]),
      money,
      action
    ]);
  }

  /* Counted once and shared with the agent cards below, so the two never
     disagree about how many things are waiting on the same agent. */
  function countByAgent(rows) {
    var counts = {};
    rows.forEach(function (r) { counts[r.agent] = (counts[r.agent] || 0) + 1; });
    return counts;
  }

  function needsCard(rows) {
    if (!rows.length) {
      return GD.card({
        title: "Needs you · 0",
        sub: "Nothing in the data is asking for a decision this morning.",
        body: [el("div", { class: "empty" }, [icon("check", 20, 1.5), el("div", { text: "The desk is clear." })])]
      });
    }

    var host = el("div");
    function draw(which) {
      GD.clear(host);
      var list = which === "all" ? rows : rows.filter(function (r) { return r.agent === which; });
      if (!list.length) {
        host.appendChild(el("div", { class: "empty" }, [
          icon("check", 20, 1.5), el("div", { text: "Nothing waiting on this agent." })
        ]));
        return;
      }
      list.forEach(function (r) { host.appendChild(needsRow(r)); });
    }
    draw("all");

    var counts = countByAgent(rows);
    var items = [{ key: "all", label: "All " + fmt.n(rows.length, 0) }];
    ["buyer", "watch", "clerk"].forEach(function (k) {
      if (counts[k]) {
        items.push({ key: k, label: AGENT_META[k].name.replace("The ", "") + " " + fmt.n(counts[k], 0) });
      }
    });

    return GD.card({
      title: "Needs you · " + fmt.n(rows.length, 0),
      sub: "Ordered by the money at stake. Every figure is labelled — an exposure is not a cost.",
      actions: items.length > 1 ? [GD.seg(items, "all", draw)] : null,
      flush: true,
      body: [host],
      foot: [
        icon("shield", 13, 1.5),
        el("span", { text: "Agents draft and flag. Nothing here is sent, ordered or filed without Pavin." })
      ]
    });
  }

  /* ---- overnight intel */

  var DRIVER_ICON = {
    weather: "sun", crop_condition: "wheat", production: "wheat", supply: "layers",
    seasonal: "calendar", export: "ship", demand: "zap", supply_demand: "scale",
    cot: "users", policy: "shield", forex: "globe", price: "dollar"
  };
  function driverLabel(key) {
    var hit = null;
    (D.drivers || []).forEach(function (d) { if (!hit && d.key === key) hit = d; });
    return hit ? hit.label : String(key || "").replace(/_/g, " ");
  }
  /* A bearish note is the good side of this sheet: it is the mill's input cost
     falling. So the sign is flipped into the buyer's frame before toning. */
  function signedOf(n) {
    return n.stance === "bearish" ? -Math.abs(n.usd_mt) : Math.abs(n.usd_mt);
  }

  function noteRow(n) {
    var s = signedOf(n);
    var bear = n.stance === "bearish";
    return el("div", { class: "note-row" }, [
      el("span", { class: "stat-ico", style: { flex: "none" } }, [
        icon(DRIVER_ICON[n.driver] || "sparkle", 14, 1.75)
      ]),
      el("div", { class: "nr-body" }, [
        el("div", { class: "nr-head" }, [
          chip(bear ? "Bearish" : "Bullish", bear ? "good" : "bad", bear ? "down" : "up"),
          chip(driverLabel(n.driver), "outline"),
          el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" }, text: agoDay(n.date) })
        ]),
        el("h4", { text: n.headline }),
        el("p", { text: n.body }),
        el("div", { class: "nr-meta", text: n.source + " · " + fmt.date(n.date) })
      ]),
      el("div", { style: { flex: "none", "text-align": "right", "min-width": "86px", "align-self": "center" } }, [
        el("div", {
          class: "num " + GD.toneFor(s),
          style: { "font-size": "var(--t-head)", "font-weight": "600", "letter-spacing": "-0.015em" },
          text: perT(s)
        }),
        el("div", { class: "label", style: { "margin-top": "1px" }, text: "desk view" }),
        el("div", {
          class: "nr-meta",
          style: { "margin-top": "3px", "font-variant-numeric": "tabular-nums" },
          text: fmt.signedMoney(s * CARGO_MT) + " a cargo"
        })
      ])
    ]);
  }

  function overnightCard() {
    var all = (D.intel || []).slice().sort(function (a, b) {
      return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
    });
    var five = all.slice(0, 5);
    if (!five.length) {
      return GD.card({ title: "What changed overnight", body: [el("div", { class: "empty" }, [
        icon("moon", 20, 1.5), el("div", { text: "No notes logged." })
      ])] });
    }
    var net = 0;
    five.forEach(function (n) { net += signedOf(n); });

    /* No arrow on a flat net: an up-arrow on a neutral chip claims a direction
       the five notes did not add up to. */
    var netChip = el("span", {
      class: "chip " + (net < 0 ? "chip-good" : net > 0 ? "chip-bad" : ""),
      style: { cursor: "help" }
    }, [
      net === 0 ? null : icon(net < 0 ? "down" : "up", 11, 2),
      el("span", { text: "net " + perT(net) })
    ]);
    GD.tipOn(netChip, "<b>The five notes, added up</b>" +
      five.map(function (n) { return tipRow(driverLabel(n.driver), perT(signedOf(n))); }).join("") +
      tipRow("net", perT(net) + " · " + fmt.signedMoney(net * CARGO_MT) + " a cargo") +
      "<div class='tip-mute'>The desk's own additive read of its own drivers. Not a forecast.</div>");

    var host = el("div");
    five.forEach(function (n) { host.appendChild(noteRow(n)); });

    return GD.card({
      title: "What changed overnight",
      sub: fmt.n(five.length, 0) + " of the " + plural(all.length, "note", "notes") +
        " The Watch has logged, newest first",
      actions: [netChip],
      flush: true,
      body: [host],
      foot: [
        goBtn("Open the market model", "market", "market"),
        el("span", { style: { flex: "1" } }),
        el("span", { text: "Bearish is the good side here: it is the mill's input cost falling. $/t is the desk's own sensitivity, not a price." })
      ]
    });
  }

  /* ---- the three agents */

  var FALLBACK_AGENTS = [
    { key: "buyer", name: "The Buyer", icon: "quotes", question: "What should I pay?", route: "quotes" },
    { key: "watch", name: "The Watch", icon: "market", question: "When should I move?", route: "market" },
    { key: "clerk", name: "The Clerk", icon: "cargo", question: "Did I get what I bought?", route: "cargo" }
  ];

  function agentToday(key) {
    if (key === "buyer") {
      var sup = {}, n = 0;
      (D.quotes || []).forEach(function (q) { if (!sup[q.supplier]) { sup[q.supplier] = 1; n += 1; } });
      var recon = (D.quotes || []).filter(function (q) { return !q.itemised; }).length;
      return {
        icon: "quotes",
        text: "Read " + plural((D.quotes || []).length, "indication", "indications") + " from " +
          plural(n, "supplier", "suppliers") + " and rebuilt the four-way split on " + fmt.n(recon, 0) + "."
      };
    }
    if (key === "watch") {
      var w = (D.wasde || []).filter(function (x) { return x.status === "released"; })[0];
      if (!w || !(w.verdicts || []).length) {
        return { icon: "market", text: "Watching " + plural((D.instruments || []).length, "instrument", "instruments") + "." };
      }
      var bear = w.verdicts.filter(function (v) { return v.stance === "bearish"; }).length;
      var worst = w.verdicts.slice().sort(function (a, b) { return a.usd_mt - b.usd_mt; })[0];
      return {
        icon: "zap",
        text: "Called the " + fmt.dateShort(w.date) + " WASDE — " + fmt.n(bear, 0) + " of " +
          fmt.n(w.verdicts.length, 0) + " classes bearish, worst " + perT(worst.usd_mt) + "."
      };
    }
    var recv = 0, tot = 0, od = 0, cl = 0;
    (D.cargoes || []).forEach(function (c) {
      recv += c.docs_received || 0;
      tot += c.docs_total || 0;
      od += c.docs_overdue || 0;
      cl += (c.variance || []).filter(function (v) { return v.severity === "claim"; }).length;
    });
    return {
      icon: "file",
      text: fmt.n(recv, 0) + " of " + fmt.n(tot, 0) + " documents in across " +
        plural((D.cargoes || []).length, "cargo", "cargoes") + " — " + fmt.n(od, 0) + " overdue, " +
        plural(cl, "variance", "variances") + " to answer."
    };
  }

  function stateChip(st) {
    if (st.state === "attention") {
      return el("span", { class: "chip chip-warn", style: { flex: "none" } }, [
        icon("alert", 11, 2),
        el("span", { text: fmt.n(st.n, 0) + (st.n === 1 ? " needs you" : " need you") })
      ]);
    }
    if (st.state === "working") {
      return el("span", { class: "chip chip-good", style: { flex: "none" } }, [
        dot("good"), el("span", { text: "Running" })
      ]);
    }
    return el("span", { class: "chip chip-outline", style: { flex: "none" } }, [
      dot(null), el("span", { text: "Idle" })
    ]);
  }

  function agentStack(rows) {
    var list = (GD.AGENTS && GD.AGENTS.length) ? GD.AGENTS : FALLBACK_AGENTS;
    var stateOf = typeof GD.agentState === "function" ? GD.agentState : null;
    var counts = countByAgent(rows);
    var box = el("div", { class: "grid", style: { "align-content": "start" } });

    list.forEach(function (a) {
      /* The count comes from the rows the card above is listing, not from
         GD.agentState: the rail counts a narrower set (it ignores the recovered
         indication and the resting order), and two different numbers for the
         same agent on the same screen is a contradiction, not a nuance.
         agentState is still what tells Running from Idle when nothing waits. */
      var n = counts[a.key] || 0;
      var base = null;
      if (stateOf) { try { base = stateOf(a); } catch (e) { base = null; } }
      var st = {
        n: n,
        state: n ? "attention" : (base && base.state === "working" ? "working" : "idle")
      };
      var today = agentToday(a.key);
      var card = GD.stat({
        label: a.question || "",
        value: a.name,
        icon: a.icon || "sparkle",
        aside: stateChip(st),
        /* Stroke 1.5: the sentence beside it is regular weight, unlike the
           other tiles where the glyph leads a bold figure. */
        delta: [wrapRow([icon(today.icon, 12, 1.5), el("span", { text: today.text })])],
        onClick: function () { GD.go(a.route || "brief"); }
      });
      GD.tipOn(card, "<b>" + GD.esc(a.name) + "</b><div class='tip-mute'>" +
        GD.esc(a.folds || a.question || "") + "</div>" +
        (st.n ? tipRow("needs you", fmt.n(st.n, 0)) : "") +
        tipRow("opens", "the " + (a.route || "brief") + " surface"));
      box.appendChild(card);
    });
    return box;
  }

  /* ---- provenance */

  function sourceList(items, ico, tone) {
    return el("ul", null, (items || []).map(function (s) {
      return el("li", {
        style: {
          display: "flex", gap: "8px", "align-items": "flex-start",
          padding: "4px 0", "font-size": "var(--t-sm)", color: "var(--ink-2)"
        }
      }, [
        el("span", {
          style: {
            flex: "none", "margin-top": "2px",
            color: tone === "accent" ? "var(--accent)" : "var(--ink-4)"
          }
          /* Regular-weight line, so the marker is a 1.5 stroke. */
        }, [icon(ico, 12, 1.5)]),
        el("span", { style: { "text-wrap": "pretty" }, text: s })
      ]);
    }));
  }

  function provenanceCard() {
    var pv = D.provenance || { real: [], synthetic: [] };
    var qs = D.quotes || [];
    var stated = qs.filter(function (q) { return q.stated_basis !== null && q.stated_basis !== undefined; });

    /* The reconstruction's own error bars: on the only two quotes that state a
       basis, how close did the derived residual land? */
    var checks = [];
    stated.forEach(function (q) {
      var f = centsToT(instByKey(q.instrument));
      if (!f) return;
      checks.push({ q: q, delta: q.residual_usd_mt - q.stated_basis * f });
    });
    var worst = 0;
    checks.forEach(function (c) { if (Math.abs(c.delta) > worst) worst = Math.abs(c.delta); });

    var honesty = checks.length
      ? "Only " + fmt.n(stated.length, 0) + " of " + plural(qs.length, "indication", "indications") +
        " state a basis. The other " + fmt.n(qs.length - stated.length, 0) +
        " four-way splits are reconstructed from futures, freight and a fair residual — and on both " +
        "that do state one, the derived residual lands within " + fmt.usd(worst) + "/t of it (" +
        checks.map(function (c) { return fmt.signed(c.delta) + " on " + c.q.cls; }).join(", ") + ")."
      : "Reconstructed splits are marked as reconstructed wherever they appear.";

    return GD.card({
      title: "Where these numbers come from",
      sub: "Grain Desk reads the mill's own files. What is generated is named here, not buried.",
      body: [
        el("div", { class: "grid g-2" }, [
          el("div", null, [
            el("div", { class: "label", style: { "margin-bottom": "7px" }, text: "From the mill's own files" }),
            sourceList(pv.real, "check", "accent")
          ]),
          el("div", null, [
            el("div", { class: "label", style: { "margin-bottom": "7px" }, text: "Generated for this build" }),
            sourceList(pv.synthetic, "sparkle", "mute")
          ])
        ])
      ],
      foot: [
        icon("eye", 13, 1.5),
        el("span", { style: { flex: "1", "min-width": "240px", "text-wrap": "pretty" }, text: honesty }),
        el("span", { class: "mono", style: { color: "var(--ink-4)" }, text: "built " + fmt.date(D.built) })
      ]
    });
  }

  /* ---------------------------------------------------------------- route */

  GD.route("brief", {
    label: "Morning Brief",
    icon: "brief",
    group: "Desk",
    badge: function () {
      /* The sidebar calls this before any render; a bad badge must not take the
         shell down with it. */
      try { return needsRows().length || null; } catch (e) { return null; }
    },
    badgeTone: "warn",
    render: function (wrap) {
      /* Derived once per render and handed to both consumers: the card lists
         them, the agent cards count them. */
      var rows = needsRows();
      wrap.appendChild(pageHead());
      wrap.appendChild(statRow());
      wrap.appendChild(needsCard(rows));
      wrap.appendChild(el("div", { class: "grid g-main" }, [overnightCard(), agentStack(rows)]));
      wrap.appendChild(provenanceCard());
    }
  });

})();
