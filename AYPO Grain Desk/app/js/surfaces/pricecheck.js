/* Grain Desk — Price check.

   The problem this surface exists for, in the head of procurement's own words: the suppliers
   write the market outlook, the suppliers recommend the price, and there is no independent
   way to check either. Relationships that are twenty and thirty years old are the reason
   nothing was missed during the pandemic or the Gulf disruption, so the answer is not to go
   to war with them. The answer is to turn up to the quarterly review knowing what the price
   is made of.

   So: for every quote on every material, the same three questions the wheat desk already
   asks of a wheat quote.

     1. What is in it?      driver legs + supplier premium, adding to the quoted price
     2. Is the premium normal? the 25th-75th percentile of what every supplier of this
                               material has charged over the premium for twelve months
     3. Can we afford it?   the quote against the budget rate the business set

   Then a brief that says all of that in a page, cites every figure, and asks the supplier to
   meet us halfway rather than accusing them of anything.

   Owns the shared detail modal: GD.showPriceCheck. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, P = GD.pricing;

  var C_IDX = "var(--c-futures)",
      C_FRT = "var(--c-freight)",
      C_SPR = "var(--c-basis)",
      C_PREM = "var(--c-margin)";

  function legColor(leg) {
    if (leg.kind === "freight") return C_FRT;
    if (leg.kind === "spread") return C_SPR;
    return C_IDX;
  }

  /* ------------------------------------------------------------------- formatting */

  /* Every figure about a material prints at the material's own precision — see GD.unitDp. */
  function price(v, m) { return GD.curUnit(v, m); }
  function signedPrice(v, m) { return GD.curUnitSigned(v, m); }
  function per(m) { return "/" + m.unit; }

  function muted(text, gap) {
    return el("span", {
      style: { color: "var(--ink-3)", "margin-left": gap || "6px", "font-weight": "400" },
      text: text
    });
  }
  function labelled(text, style) {
    var s = { "margin-bottom": "8px" };
    if (style) Object.keys(style).forEach(function (k) { s[k] = style[k]; });
    return el("div", { class: "label", style: s, text: text });
  }
  function section(title, body, note) {
    return el("div", null, [
      labelled(title),
      body,
      note ? el("p", {
        style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "8px" },
        text: note
      }) : null
    ]);
  }
  function panel(kids, style) {
    var s = {
      border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
      background: "var(--surface-2)", padding: "13px 14px"
    };
    if (style) Object.keys(style).forEach(function (k) { s[k] = style[k]; });
    return el("div", { style: s }, kids);
  }
  function statCell(k, v, tone) {
    return el("div", null, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v " + (tone || ""), text: v })
    ]);
  }

  /* The verdict, as a word first. "above" in coral is still "above" with the colour off. */
  var VERDICT = {
    above: { label: "above the band", tone: "bad", ico: "up" },
    inside: { label: "inside the band", tone: "good", ico: "check" },
    below: { label: "below the band", tone: "good", ico: "down" },
    unknown: { label: "no band yet", tone: "outline", ico: "alert" }
  };
  function verdictChip(pos) {
    var v = VERDICT[pos] || VERDICT.unknown;
    return GD.chip(v.label, v.tone, v.ico);
  }

  /* ===================================================================== the band bar

     One horizontal scale carrying four real numbers: driver cost, the two edges of the band,
     and where the quote actually is — plus the budget rate when there is one. Plain divs and
     inline styles, like the position book's threshold bar; no new CSS and no chart library
     for five points on a line. Every level is named with its figure in the key beneath, and
     the out-of-band stretch is hatched as well as coloured. */

  function bandBar(a) {
    var m = a.material, band = a.band, q = a.quote;
    if (!band.established) {
      return panel([
        el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
          GD.dot("warn"),
          el("b", { text: "No band yet" }),
          el("span", {
            style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
            text: band.n + " of the " + band.min_obs + " quotes needed"
          })
        ]),
        el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "8px" },
          text: "Quartiles drawn from " + band.n + " observation" + (band.n === 1 ? "" : "s") +
                " would be describing noise, and a band that is really noise is worse than " +
                "none — somebody would negotiate against it. The breakdown above still holds."
        })
      ], { background: "var(--warn-soft)" });
    }

    var budget = m.budget_price;
    var pts = [band.driverCost, band.low, band.high, q.price];
    if (budget !== null && budget !== undefined) pts.push(budget);
    var lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    var span = (hi - lo) || 1;
    lo -= span * 0.14; hi += span * 0.14;
    span = hi - lo;
    function x(v) { return Math.max(0, Math.min(100, ((v - lo) / span) * 100)); }

    var xLow = x(band.low), xHigh = x(band.high), xQ = x(q.price), xCost = x(band.driverCost);
    var over = a.position === "above";
    var markerColor = over ? "var(--up)" : a.position === "below" ? "var(--accent)" : "var(--accent-2)";

    var track = el("div", {
      style: {
        position: "relative", height: "46px", "border-radius": "var(--r-ctl)",
        background: "var(--surface-3)", border: "1px solid var(--border)",
        overflow: "hidden"
      }
    }, [
      /* driver cost — everything to the left of this is not the supplier's to charge */
      el("div", {
        style: {
          position: "absolute", left: "0", top: "0", bottom: "0",
          width: xCost.toFixed(2) + "%", background: "var(--surface-2)",
          "border-right": "1px dashed var(--border-strong)"
        }
      }),
      /* the band itself */
      el("div", {
        style: {
          position: "absolute", left: xLow.toFixed(2) + "%",
          width: Math.max(0.6, xHigh - xLow).toFixed(2) + "%", top: "0", bottom: "0",
          background: "var(--accent-soft)",
          "border-left": "2px solid var(--accent-3)",
          "border-right": "2px solid var(--accent-3)"
        }
      }),
      /* the stretch the quote sits out in, hatched so it reads without the hue */
      over ? el("div", {
        style: {
          position: "absolute", left: xHigh.toFixed(2) + "%",
          width: Math.max(0.6, xQ - xHigh).toFixed(2) + "%", top: "0", bottom: "0",
          "background-image": "repeating-linear-gradient(135deg, var(--up-soft) 0 5px, " +
                              "transparent 5px 10px)"
        }
      }) : null,
      budget === null || budget === undefined ? null : el("div", {
        style: {
          position: "absolute", left: x(budget).toFixed(2) + "%", top: "0", bottom: "0",
          width: "0", "border-left": "2px dotted var(--ink-3)"
        }
      }),
      /* where the quote is */
      el("div", {
        style: {
          position: "absolute", left: xQ.toFixed(2) + "%", top: "4px", bottom: "4px",
          width: "3px", "margin-left": "-1.5px", background: markerColor,
          "border-radius": "2px"
        }
      }),
      el("div", {
        style: {
          position: "absolute", left: xQ.toFixed(2) + "%", top: "50%",
          width: "11px", height: "11px", "margin": "-5.5px 0 0 -5.5px",
          "border-radius": "50%", background: markerColor,
          "box-shadow": "0 0 0 3px var(--surface)"
        }
      })
    ]);
    GD.tipOn(track, function () {
      return "<b>" + GD.esc(GD.supplierName(q.supplier_id) + " · " + price(q.price, m)) +
        "</b><div class='tip-row'><span class='tip-mute'>driver cost</span><b>" +
        GD.esc(price(band.driverCost, m)) + "</b></div>" +
        "<div class='tip-row'><span class='tip-mute'>band</span><b>" +
        GD.esc(price(band.low, m) + " – " + price(band.high, m)) + "</b></div>" +
        (budget === null || budget === undefined ? "" :
          "<div class='tip-row'><span class='tip-mute'>budget</span><b>" +
          GD.esc(price(budget, m)) + "</b></div>");
    });

    var keyRow = function (color, dash, name, value) {
      return el("span", { class: "flex items-center gap-8" }, [
        el("i", {
          style: {
            width: "10px", height: "10px", "border-radius": dash ? "0" : "3px",
            flex: "none", "background-color": dash ? "transparent" : color,
            "border-left": dash ? ("2px " + dash + " " + color) : null
          }
        }),
        el("span", { text: name }),
        el("b", { class: "num", style: { color: "var(--ink)" }, text: value })
      ]);
    };

    return el("div", { style: { display: "grid", gap: "11px" } }, [
      track,
      el("div", { class: "legend", style: { "row-gap": "7px" } }, [
        keyRow("var(--surface-2)", "dashed", "Driver cost", price(band.driverCost, m)),
        keyRow("var(--accent-3)", null, "Fair band, 25th–75th",
          price(band.low, m) + " – " + price(band.high, m)),
        budget === null || budget === undefined ? null
          : keyRow("var(--ink-3)", "dotted", "Budget rate", price(budget, m)),
        keyRow(markerColor, null, "This quote", price(q.price, m))
      ])
    ]);
  }

  /* ================================================================= the fact pack

     Everything the brief is allowed to say, with the source of each figure attached. The
     template reads it, the model is given it and nothing else, and every number the model
     writes is checked back against it. */

  function factPack(a, ws, trend) {
    var m = a.material, q = a.quote, bd = a.breakdown, band = a.band;
    var sup = GD.supplier(q.supplier_id, ws);
    var rows = [];
    function add(label, display, source, value) {
      rows.push({ label: label, display: display, source: source, value: value });
    }

    add("Material", m.name, "Material master, " + ws.name, null);
    add("Supplier", sup ? sup.name : q.supplier_id, "Supplier master", null);
    add("Quoted price", price(q.price, m) + per(m),
      q.source === "email" ? "Supplier offer, " + fmt.date(q.date) + " (" + q.source_ref + ")"
                           : q.source_ref, q.price);
    add("Quantity offered", fmt.n(q.qty, 0) + " " + m.unit, "Same offer", q.qty);
    if (q.valid_to) add("Offer valid to", fmt.date(q.valid_to), "Same offer", null);
    add("Brief prepared", fmt.date(GD.wsAsOf()), "Today's date on this desk", null);

    bd.legs.forEach(function (l) {
      add(l.name,
        fmt.n(l.raw, Math.abs(l.raw) >= 1000 ? 0 : 2) + " " + l.unit +
          " → " + price(l.value, m) + per(m),
        l.source + ", print of " + fmt.date(l.at) + (l.mocked ? " (mock data)" : ""),
        l.raw);
      rows[rows.length - 1].leg = l;
    });
    bd.fx.forEach(function (g) {
      add("FX, " + g.currency + "/" + m.currency, fmt.n(g.rate, 4),
        "Indicative mid on " + fmt.date(GD.wsAsOf()) + " (mock rate)", g.rate);
      /* Both sides of the conversion, because the brief quotes both: the legs added up in
         their own currency, and what they become after the rate is applied. */
      add(g.currency + " legs before conversion", GD.cur(g.base, g.currency, 2) + per(m),
        "Sum of the " + g.currency + " legs above", g.base);
      add(g.currency + " legs after conversion", price(g.value, m) + per(m),
        "The line above × " + fmt.n(g.rate, 4), g.value);
    });
    add("Driver cost", price(bd.driverCost, m) + per(m),
      "Sum of the legs above", bd.driverCost);
    add("Supplier premium", price(bd.premium, m) + per(m),
      "Quoted price less driver cost — our arithmetic, not the supplier's", bd.premium);
    if (a.premiumPct !== null) {
      add("Premium as a share of driver cost", fmt.pct(a.premiumPct, 1),
        "Derived from the two figures above", a.premiumPct * 100);
    }

    if (band.established) {
      var sups = {};
      band.obs.forEach(function (o) { sups[o.supplier_id] = true; });
      add("Fair band", price(band.low, m) + " – " + price(band.high, m) + per(m),
        "25th–75th percentile of premium across " + band.n + " quotes from " +
        Object.keys(sups).length + " suppliers of " + m.name + ", last 12 months",
        band.low);
      rows[rows.length - 1].value2 = band.high;
      add("Observations behind the band", band.n + " quotes from " +
        Object.keys(sups).length + " suppliers", "Quote history, last 12 months", band.n);
      add("Median premium for the material", price(band.median, m) + per(m),
        "Midpoint of the same 12-month population", band.median);
      var mid = P.round2(band.driverCost + band.median);
      add("Band midpoint", price(mid, m) + per(m),
        "Driver cost plus the median premium", mid);
      /* The talking points name a landing point and what it is worth, so both belong here. */
      add("Gap to the band midpoint", signedPrice(P.round2(q.price - mid), m) + per(m),
        "Quoted price less the band midpoint", P.round2(q.price - mid));
      add("Worth on the offered quantity", GD.curM((q.price - mid) * q.qty, m.currency),
        "Gap to the midpoint × quantity offered", (q.price - mid) * q.qty);
      add("Where this quote sits", VERDICT[a.position].label,
        "Quoted price against the band", null);
      if (a.outside) {
        add("Distance outside the band", signedPrice(a.outside, m) + per(m),
          "Quoted price less the nearer edge of the band", a.outside);
        add("On the offered quantity", GD.curM(a.outside * q.qty, m.currency),
          "Distance outside × quantity offered", a.outside * q.qty);
      }
    } else {
      add("Fair band", "not established", band.n + " of the " + band.min_obs +
        " quotes needed to draw quartiles", null);
    }

    if (a.budget) {
      add("Budget rate", price(a.budget.budget, m) + per(m), m.budget_note, a.budget.budget);
      add("Quote against budget", signedPrice(a.budget.amount, m) + per(m) + " (" +
        fmt.signedPct(a.budget.pct, 1) + ")", "Quoted price less the budget rate",
        a.budget.amount);
      add("On the offered quantity", GD.curM(a.budget.amount * q.qty, m.currency),
        "Budget gap × quantity offered", a.budget.amount * q.qty);
    } else {
      add("Budget rate", "none held for this material", m.budget_note, null);
    }

    if (trend && trend.quarters.length) {
      add("This supplier's premium, first quarter on record",
        price(trend.quarters[0].avg, m) + per(m),
        "Average premium, " + trend.quarters[0].key, trend.quarters[0].avg);
      var lastQ = trend.quarters[trend.quarters.length - 1];
      add("This supplier's premium, latest quarter", price(lastQ.avg, m) + per(m),
        "Average premium, " + lastQ.key, lastQ.avg);
      add("Movement over the period", signedPrice(trend.change, m) + per(m),
        "Latest quarter less the first", trend.change);
      add("Consecutive quarters of widening", String(trend.risingRun),
        "Quarter-on-quarter averages of this supplier's premium", trend.risingRun);
    }

    var text = rows.map(function (r) {
      return "- " + r.label + ": " + r.display + "   [source: " + r.source + "]";
    }).join("\n");

    return { rows: rows, text: text };
  }

  /* ================================================================== the brief text

     Written by the app. If a key is present the model rewrites the prose from this same
     pack, and anything it writes that is not in the pack sends us straight back here. */

  function briefTemplate(a, ws, trend, pack) {
    var m = a.material, q = a.quote, bd = a.breakdown, band = a.band;
    var sup = GD.supplier(q.supplier_id, ws);
    var supName = sup ? sup.name : q.supplier_id;
    var buyer = ws.key === "ufm" && window.DESK
      ? window.DESK.profile.buyer.name : "Central procurement";
    var L = [];

    L.push("NEGOTIATION BRIEF — " + supName);
    L.push(m.name + " · quote of " + fmt.date(q.date) +
      (q.valid_to ? ", valid to " + fmt.date(q.valid_to) : ""));
    L.push("Prepared " + fmt.date(GD.wsAsOf()) + " by " + buyer +
      ". Internal working note — nothing here has been sent to the supplier.");
    L.push("");

    /* 1 — the market */
    L.push("1 · WHAT THE MARKET HAS DONE");
    bd.legs.forEach(function (l) {
      var mv = legMove(l);
      L.push("· " + l.name + " is " + fmt.n(l.raw, Math.abs(l.raw) >= 1000 ? 0 : 2) +
        " " + l.unit +
        (mv === null ? "" : ", " + fmt.signedPct(mv, 1) + " over the twelve months") +
        ". That is " + price(l.value, m) + per(m) + " of the price." +
        "  (" + l.source + ", print of " + fmt.date(l.at) + (l.mocked ? ", mock feed" : "") +
        ")");
    });
    bd.fx.forEach(function (g) {
      L.push("· The " + g.currency + " legs convert at " + fmt.n(g.rate, 4) + " " +
        g.currency + "/" + m.currency + ", turning " +
        GD.cur(g.base, g.currency, 2) + per(m) + " into " + price(g.value, m) + per(m) +
        ".  (indicative mid on " + fmt.date(GD.wsAsOf()) + ", mock rate)");
    });
    L.push("· Taken together the drivers account for " + price(bd.driverCost, m) + per(m) +
      " of this quote.");
    L.push("");

    /* 2 — where the quote sits */
    L.push("2 · WHERE THIS QUOTE SITS");
    L.push("· " + supName + " is offering " + price(q.price, m) + per(m) + " for " +
      fmt.n(q.qty, 0) + " " + m.unit + ".");
    L.push("· Above driver cost that leaves a premium of " + price(bd.premium, m) + per(m) +
      (a.premiumPct === null ? "" : ", " + fmt.pct(a.premiumPct, 1) + " of driver cost") +
      ". The premium is our subtraction, not a figure the offer itemised.");
    if (band.established) {
      var supCount = {};
      band.obs.forEach(function (o) { supCount[o.supplier_id] = true; });
      L.push("· Across " + band.n + " quotes from " + Object.keys(supCount).length +
        " suppliers of " + m.name + " over the last twelve months, the middle half of " +
        "premiums puts a fair price between " + price(band.low, m) + " and " +
        price(band.high, m) + per(m) + ".");
      if (a.position === "above") {
        L.push("· This quote is " + price(a.outside, m) + per(m) + " above the top of that " +
          "band — " + GD.curM(a.outside * q.qty, m.currency) + " on the " +
          fmt.n(q.qty, 0) + " " + m.unit + " offered.");
      } else if (a.position === "below") {
        L.push("· This quote is " + price(Math.abs(a.outside), m) + per(m) + " below the " +
          "bottom of that band, which makes it the keen end of what we have been shown.");
      } else {
        L.push("· This quote sits inside that band, so the premium is normal for the " +
          "material rather than something to push back on.");
      }
    } else {
      L.push("· We do not yet hold enough history on " + m.name + " to draw a fair band — " +
        band.n + " quotes against the " + band.min_obs + " needed. This brief therefore " +
        "argues from driver cost and budget only.");
    }
    L.push("");

    /* 3 — the supplier's own trend */
    if (trend && trend.quarters.length >= 2) {
      L.push("3 · THIS SUPPLIER'S PREMIUM OVER TIME");
      L.push("· " + supName + "'s premium has gone from " + price(trend.quarters[0].avg, m) +
        per(m) + " in " + trend.quarters[0].key + " to " +
        price(trend.quarters[trend.quarters.length - 1].avg, m) + per(m) + " in " +
        trend.quarters[trend.quarters.length - 1].key + " — " +
        signedPrice(trend.change, m) + per(m) + ".");
      if (trend.widening) {
        L.push("· That is " + trend.risingRun + " consecutive quarters of widening. The " +
          "drivers are published and we can see them; the premium is the part we cannot.");
      } else {
        L.push("· Quarter to quarter the premium has been broadly stable, with " +
          trend.risingRun + " consecutive quarter" + (trend.risingRun === 1 ? "" : "s") +
          " of widening at most.");
      }
      if (band.established) {
        L.push("· The median premium across every supplier of " + m.name + " is " +
          price(band.median, m) + per(m) + ".");
      }
      L.push("");
    }

    /* 4 — the budget */
    L.push((trend && trend.quarters.length >= 2 ? "4" : "3") + " · OUR BUDGET");
    if (a.budget) {
      L.push("· The budget rate for " + m.name + " is " + price(a.budget.budget, m) + per(m) +
        " (" + m.budget_note + ").");
      L.push("· This quote is " + signedPrice(a.budget.amount, m) + per(m) + " against it, " +
        fmt.signedPct(a.budget.pct, 1) + " — " +
        GD.curM(Math.abs(a.budget.amount) * q.qty, m.currency) + " on this quantity" +
        (a.budget.over ? ", and it comes out of the product margin." : "."));
    } else {
      L.push("· No budget rate is held for " + m.name + ". " + m.budget_note);
    }
    L.push("");

    /* 5 — the ask */
    L.push((trend && trend.quarters.length >= 2 ? "5" : "4") + " · THREE THINGS TO PUT TO THEM");
    talkingPoints(a, ws, trend).forEach(function (t) { L.push("· " + t); });
    L.push("");

    L.push("SOURCES");
    pack.rows.forEach(function (r) {
      L.push("· " + r.label + " — " + r.source);
    });
    L.push("");
    L.push("Every figure above is drawn from the data on the price-check screen for this " +
      "quote. Feeds marked [mock] are placeholder series for this build, not licensed data.");

    return L.join("\n");
  }

  /* Twelve-month move on a driver, for the market paragraph. */
  function legMove(l) {
    /* `l` carries the print in force; the series itself lives on the material's driver, so
       the move is read from the pack the leg was built from. */
    if (!l.series || l.series.length < 2) return null;
    var first = l.series[0], last = l.series[l.series.length - 1];
    return first ? (last - first) / first : null;
  }

  /* The three asks. Collaborative by construction: each one states what we see, asks the
     supplier to explain or to meet us, and says what we are offering to hold in return. */
  function talkingPoints(a, ws, trend) {
    var m = a.material, q = a.quote, bd = a.breakdown, band = a.band;
    var sup = GD.supplier(q.supplier_id, ws);
    var supName = sup ? sup.name : q.supplier_id;
    var out = [];
    var mid = band.established ? P.round2(band.driverCost + band.median) : null;

    if (band.established && a.position === "above") {
      out.push("Open with the band, not the number: \"On " + m.name + " we now track the " +
        "driver cost and the spread every supplier is carrying. The middle half of what we " +
        "have been shown this year sits between " + price(band.low, m) + " and " +
        price(band.high, m) + per(m) + ", and this offer is " + price(a.outside, m) +
        " above that. If there is a cost on your side we are not seeing, we would much " +
        "rather understand it than guess at it.\"");
      out.push("Name a landing point rather than a demand: \"Could we work this at " +
        price(mid, m) + per(m) + " — the middle of that band — for the " +
        fmt.n(q.qty, 0) + " " + m.unit + " on offer? That is " +
        GD.curM((q.price - mid) * q.qty, m.currency) + " on this parcel, and it keeps the " +
        "allocation where it is.\"");
    } else if (band.established && a.position === "below") {
      out.push("Say so plainly: \"This is the keenest " + m.name + " indication we hold — " +
        price(Math.abs(a.outside), m) + per(m) + " inside the bottom of our fair band. " +
        "We would like to take more of it.\"");
      out.push("Ask for tenor instead of price: \"Can we hold " + price(q.price, m) + per(m) +
        " for a second parcel, or extend the validity beyond " +
        (q.valid_to ? fmt.date(q.valid_to) : "this window") + "? We would rather lock the " +
        "level than re-open the conversation in a month.\"");
    } else if (band.established) {
      out.push("Confirm, do not haggle: \"Your premium of " + price(bd.premium, m) + per(m) +
        " is inside the " + price(band.low, m) + "–" + price(band.high, m) +
        " band we see across the material, so the price itself is not the question.\"");
      out.push("Ask for the next thing: \"On that basis, can we talk about volume and " +
        "validity rather than price — a second parcel at the same level, or a longer " +
        "window than " + (q.valid_to ? fmt.date(q.valid_to) : "the current one") + "?\"");
    } else {
      out.push("Be straight about what we can and cannot see: \"We hold only " + band.n +
        " quote" + (band.n === 1 ? "" : "s") + " on " + m.name + ", so we are reading this " +
        "against driver cost rather than against a spread. Driver cost works out at " +
        price(bd.driverCost, m) + per(m) + ", which puts your premium at " +
        price(bd.premium, m) + ".\"");
      out.push("Invite the detail: \"Would you be willing to walk us through how you build " +
        "the number? We are trying to get to a point where we can see a move coming " +
        "instead of reacting to it.\"");
    }

    if (trend && trend.widening) {
      out.push("Raise the trend as a shared problem, in the quarterly review rather than " +
        "on this one parcel: \"" + supName + "'s premium has widened " +
        trend.risingRun + " quarters running, from " + price(trend.quarters[0].avg, m) +
        " to " + price(trend.quarters[trend.quarters.length - 1].avg, m) + per(m) +
        ". The relationship has carried us through genuinely difficult years and we are not " +
        "looking to re-tender it. We would like to understand the direction and agree how " +
        "it gets managed from here.\"");
    } else if (a.budget && a.budget.over) {
      out.push("Put the budget on the table as a constraint, not a bluff: \"Our internal " +
        "rate for " + m.name + " is " + price(a.budget.budget, m) + per(m) + ". At " +
        price(q.price, m) + " we are " + fmt.signedPct(a.budget.pct, 1) + " over it and it " +
        "lands on the product margin. Is there a volume commitment, a payment term or a " +
        "shipment window that gets us closer?\"");
    } else {
      out.push("Ask for the thing only they have: \"What are you seeing for the next two " +
        "quarters on " + m.name + "? Your read has been worth having, and we would rather " +
        "plan with it than around it.\"");
    }
    return out.slice(0, 3);
  }

  /* ----------------------------------------------------------------- the brief sheet */

  function showBrief(a, ws) {
    var m = a.material, q = a.quote;
    var sup = GD.supplier(q.supplier_id, ws);
    var trend = P.premiumTrend(m, ws.quotes, q.supplier_id, GD.wsAsOf());
    var pack = factPack(a, ws, trend);

    /* The twelve-month move each leg's paragraph quotes comes from the driver's own series,
       attached here so the template does not reach back into the material. */
    a.breakdown.legs.forEach(function (l) {
      var d = null;
      (m.drivers || []).forEach(function (x) { if (x.key === l.key) d = x; });
      l.series = d ? d.values : null;
      if (l.series && l.series.length > 1) {
        var mv = legMove(l);
        if (mv !== null) {
          pack.rows.push({
            label: l.name + ", twelve-month move", display: fmt.signedPct(mv, 1),
            source: l.source + ", first and last print in the series", value: mv * 100
          });
        }
      }
    });
    pack.text = pack.rows.map(function (r) {
      return "- " + r.label + ": " + r.display + "   [source: " + r.source + "]";
    }).join("\n");

    var template = briefTemplate(a, ws, trend, pack);
    var state = { text: template, source: "template", note: GD.llm.statusLine() };
    var editor = null, noteNode = null, srcChip = null;

    function docTitle() {
      return "Negotiation brief — " + (sup ? sup.name : q.supplier_id) + " — " + m.name;
    }

    function printable(text) {
      return GD.docHtml(text, {
        foot: "Generated by Grain Desk on " + fmt.date(GD.wsAsOf()) + ". " +
              (ws.demo ? "Demonstration data — every figure is fictional. " : "") +
              "Internal working note; not sent to the supplier."
      });
    }

    GD.sheet({
      title: "Negotiation brief",
      sub: (sup ? sup.name : q.supplier_id) + " · " + m.name + " · " +
           price(q.price, m) + per(m) + " quoted " + fmt.date(q.date),
      class: "brief-sheet",
      body: function () {
        srcChip = state.source === "model"
          ? GD.chip(GD.llm.model, "accent", "sparkle")
          : GD.chip("written by the app", "outline", "layers");
        noteNode = el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" },
          text: state.note
        });
        editor = GD.editor(state.text, { label: "Negotiation brief draft", minHeight: 420 });

        var head = el("div", {
          style: {
            display: "flex", "align-items": "center", gap: "9px", "flex-wrap": "wrap",
            "margin-bottom": "10px"
          }
        }, [
          el("div", { class: "card-title", style: { flex: "1" }, text: "Draft — yours to edit" }),
          srcChip,
          GD.chip("not sent", "outline", "eye"),
          GD.llm.available() ? el("button", {
            class: "btn btn-sm", type: "button",
            onclick: function (ev) { regenerate(ev.currentTarget); }
          }, [icon("sparkle", 12, 1.75), "Rewrite with " + GD.llm.model]) : null
        ]);

        var factTable = GD.table({
          cols: [
            { key: "label", label: "Figure", width: "33%" },
            { key: "display", label: "Value", width: "25%" },
            { key: "source", label: "Where it came from", sortable: false }
          ],
          rows: pack.rows,
          emptyText: "No figures."
        });

        return [
          head,
          editor,
          noteNode,
          el("div", { style: { "margin-top": "18px" } }, [
            labelled("Every figure, and where it came from"),
            el("div", {
              style: {
                border: "1px solid var(--border)", "border-radius": "var(--r-card)",
                overflow: "hidden"
              }
            }, [factTable]),
            el("p", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" },
              text: "This is the whole of what the brief is allowed to say. When a model " +
                    "writes the prose it is given this table and nothing else, and any " +
                    "figure in its draft that is not in here sends the app back to its own " +
                    "wording."
            })
          ])
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: function () { GD.copyText(editor.value, "Brief"); }
          }, [icon("file", 14, 2), "Copy"]),
          el("button", {
            class: "btn", type: "button",
            onclick: function () {
              GD.downloadMarkdown("brief-" + (sup ? sup.id : q.supplier_id) + "-" + m.id,
                editor.value);
            }
          }, [icon("download", 14, 1.75), "Download .md"]),
          el("button", {
            class: "btn", type: "button",
            onclick: function () { GD.printDoc(docTitle(), printable(editor.value)); }
          }, [icon("file", 14, 1.75), "Print / PDF"]),
          el("button", { class: "btn btn-ghost", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "The desk drafts. A person sends." })
        ];
      }
    });

    function regenerate(btn) {
      btn.setAttribute("disabled", "");
      var was = btn.textContent;
      btn.textContent = "Writing…";
      GD.llm.draft({
        facts: pack.text,
        template: template,
        maxTokens: 6000,
        system: BRIEF_SYSTEM,
        instruction: "Rewrite the DRAFT below into a cleaner one-page brief, using only " +
                     "the FACTS."
      }).then(function (d) {
        state = d;
        editor.value = d.text;
        noteNode.textContent = d.note;
        var next = d.source === "model"
          ? GD.chip(GD.llm.model, "accent", "sparkle")
          : GD.chip(d.rejected ? "model draft rejected" : "written by the app",
                    d.rejected ? "warn" : "outline", d.rejected ? "alert" : "layers");
        if (srcChip.parentNode) srcChip.parentNode.replaceChild(next, srcChip);
        srcChip = next;
        GD.toast(d.source === "model" ? "Rewritten and number-checked"
                                      : "Fell back to the app's own wording",
          { icon: d.source === "model" ? "check" : "alert" });
      }).then(function () {
        btn.removeAttribute("disabled");
        btn.textContent = was;
      });
    }
  }

  var BRIEF_SYSTEM = [
    "You write one-page negotiation briefs for a food manufacturer's central procurement",
    "team. The reader is the buyer who will take the brief into a call with a supplier they",
    "have worked with for decades and intend to keep working with.",
    "",
    "Tone: collaborative and specific. State what the data shows, ask the supplier to explain",
    "or to meet in the middle, and say what we are offering to hold in return. Never accuse,",
    "never imply bad faith, never threaten to re-tender. The relationship is the asset.",
    "",
    "Keep the section structure and the headings of the draft you are given. Keep it to a",
    "page. Prefer the shorter sentence."
  ].join("\n");

  /* The app's own draft is both the fallback and the model's starting point: rewriting a
     grounded document is a much narrower job than composing one, and it keeps the section
     structure identical between the two paths. GD.llm assembles the prompt around it, so the
     pack that is checked against is by construction the pack that was sent. */

  /* ================================================================ the detail sheet */

  GD.showPriceCheck = function (quote, ws) {
    ws = ws || GD.ws();
    var m = GD.material(quote.material_id, ws);
    if (!m) return;
    var a = P.assess(m, quote, ws.quotes, GD.wsAsOf());
    var bd = a.breakdown, band = a.band;
    var sup = GD.supplier(quote.supplier_id, ws);
    var trend = P.premiumTrend(m, ws.quotes, quote.supplier_id, GD.wsAsOf());
    var anyMock = bd.legs.some(function (l) { return l.mocked; }) || bd.fx.length > 0;

    /* ---- hero: the split, and the one number the supplier actually gave us */
    var parts = bd.legs.map(function (l) {
      return { name: l.name, value: l.value, color: legColor(l) };
    }).concat([{ name: "Supplier premium", value: bd.premium, color: C_PREM }]);

    var compoBar = GD.compo(parts.filter(function (p) { return p.value > 0; }), quote.price);
    compoBar.style.setProperty("height", "30px");
    compoBar.style.setProperty("border-radius", "var(--r-ctl)");

    var hero = el("div", { style: { display: "grid", gap: "13px" } }, [
      el("div", {
        style: {
          display: "flex", "align-items": "flex-end", "justify-content": "space-between",
          gap: "18px", "flex-wrap": "wrap"
        }
      }, [
        el("div", { style: { "min-width": "210px", flex: "1" } }, [
          labelled("What is in this price", { "margin-bottom": "3px" }),
          el("div", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
            text: bd.legs.length + " published driver" + (bd.legs.length === 1 ? "" : "s") +
                  " plus the premium the supplier is carrying"
          })
        ]),
        el("div", { style: { "text-align": "right" } }, [
          labelled("Quoted", { "margin-bottom": "1px" }),
          el("div", {
            class: "num",
            style: {
              "font-size": "28px", "font-weight": "600",
              "letter-spacing": "-0.028em", "line-height": "1.1"
            },
            text: price(quote.price, m)
          }),
          el("div", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
            text: "per " + m.unit
          })
        ])
      ]),
      compoBar,
      GD.legend(parts.filter(function (p) { return p.value > 0; }).map(function (p) {
        return {
          name: p.name + " · " + price(p.value, m) + " · " + fmt.pct(p.value / quote.price, 0),
          color: p.color
        };
      }))
    ]);

    /* ---- the disclosure. Whose arithmetic is this. */
    var disclosure = el("div", {
      style: {
        display: "flex", gap: "11px", padding: "12px 13px",
        background: "var(--warn-soft)", border: "1px solid var(--border)",
        "border-radius": "var(--r-ctl)", "align-items": "flex-start"
      }
    }, [
      el("span", { style: { color: "var(--warn)", flex: "none", "margin-top": "1px" } },
        [icon("alert", 15, 2)]),
      el("div", null, [
        el("div", {
          style: { "font-weight": "600", "font-size": "var(--t-sm)", color: "var(--ink)" },
          text: (sup ? sup.name : quote.supplier_id) + " quoted one number and itemised nothing."
        }),
        el("p", {
          style: { "font-size": "var(--t-sm)", color: "var(--ink-2)", "margin-top": "3px" },
          text: "The split above is ours: each driver leg is a published print times a " +
                "stated conversion, and the premium is whatever is left when they are taken " +
                "off the quote. It is arithmetic, not a disclosure." +
                (anyMock ? " Feeds marked as mock data are placeholder series for this " +
                           "build, and the FX rate is an indicative mid rather than a live " +
                           "quote." : "")
        })
      ])
    ]);

    /* ---- the legs, with the arithmetic shown rather than asserted */
    var kvRows = [];
    bd.legs.forEach(function (l) {
      var chain = fmt.n(l.raw, Math.abs(l.raw) >= 1000 ? 0 : 2) + " " + l.unit +
        (l.per_unit !== 1 ? " × " + fmt.n(l.per_unit, l.per_unit < 0.01 ? 5 : 4) : "") +
        (l.offset ? " + " + GD.cur(l.offset, l.currency, 2) : "") +
        (l.rate !== 1 ? " × " + fmt.n(l.rate, 4) + " " + l.currency + "/" + m.currency : "");
      var node = el("span", null, [
        el("span", { text: price(l.value, m) + " " + per(m) }),
        muted(chain)
      ]);
      GD.tipOn(node, "<b>" + GD.esc(l.name) + "</b><div class='tip-mute'>" +
        GD.esc(l.source + " · print of " + fmt.date(l.at)) + "</div>" +
        "<div class='tip-row'><span class='tip-mute'>" + GD.esc(l.unit) + "</span><b>" +
        GD.esc(fmt.n(l.raw, 2)) + "</b></div>" +
        (l.per_unit !== 1 ? "<div class='tip-row'><span class='tip-mute'>per " +
          GD.esc(m.unit) + "</span><b>× " + GD.esc(fmt.n(l.per_unit, 5)) + "</b></div>" : "") +
        (l.offset ? "<div class='tip-row'><span class='tip-mute'>plus</span><b>" +
          GD.esc(GD.cur(l.offset, l.currency, 2)) + "</b></div>" : "") +
        (l.rate !== 1 ? "<div class='tip-row'><span class='tip-mute'>fx</span><b>× " +
          GD.esc(fmt.n(l.rate, 4)) + "</b></div>" : "") +
        (l.mocked ? "<div class='tip-mute'>Mock feed — placeholder series for this build.</div>"
                  : "") + "<div class='tip-mute'>" + GD.esc(l.note) + "</div>");
      kvRows.push([l.name, node]);
    });
    if (bd.fx.length) {
      bd.fx.forEach(function (g) {
        kvRows.push(["FX conversion, " + g.currency + "/" + m.currency, el("span", null, [
          el("span", { text: GD.cur(g.base, g.currency, 2) + " × " + fmt.n(g.rate, 4) }),
          muted("= " + price(g.value, m) + per(m))
        ])]);
      });
    }
    kvRows.push(["Driver cost", el("b", { class: "nowrap",
      text: price(bd.driverCost, m) + " " + per(m) })]);
    kvRows.push(["Supplier premium", el("span", {
      class: "flex items-center gap-8", style: { "flex-wrap": "wrap" }
    }, [
      GD.dot(a.position === "above" ? "bad" : a.position === "below" ? "good" : "warn"),
      el("b", { class: "nowrap", text: price(bd.premium, m) + " " + per(m) }),
      muted(a.premiumPct === null ? "" : fmt.pct(a.premiumPct, 1) + " of driver cost", "2px")
    ])]);

    var sumRow = el("div", {
      style: {
        display: "flex", "align-items": "center", gap: "9px", "margin-top": "11px",
        padding: "9px 11px", background: "var(--accent-soft)",
        "border-radius": "var(--r-ctl)", "font-size": "var(--t-sm)"
      }
    }, [
      el("span", { style: { color: "var(--accent-2)", flex: "none" } }, [icon("check", 14, 2)]),
      el("span", { style: { color: "var(--ink-2)", flex: "1" } }, [
        "Driver cost plus premium is ",
        el("b", { class: "num", style: { color: "var(--ink)" }, text: price(bd.total, m) }),
        " — the quoted price exactly."
      ])
    ]);

    var legBlock = el("div", null, [GD.kv(kvRows), sumRow]);

    /* ---- budget */
    var budgetBlock;
    if (a.budget) {
      budgetBlock = el("div", { class: "statline", style: {
        border: "1px solid var(--border)", "border-radius": "var(--r-ctl)", overflow: "hidden"
      } }, [
        statCell("Budget rate", price(a.budget.budget, m) + per(m)),
        statCell("This quote", signedPrice(a.budget.amount, m) + per(m),
          GD.toneFor(a.budget.amount)),
        statCell("As a share", fmt.signedPct(a.budget.pct, 1), GD.toneFor(a.budget.amount)),
        statCell("On " + fmt.n(quote.qty, 0) + " " + m.unit,
          GD.curM(a.budget.amount * quote.qty, m.currency), GD.toneFor(a.budget.amount))
      ]);
    } else {
      budgetBlock = panel([
        el("div", { class: "flex items-center gap-8" }, [
          GD.dot("warn"), el("b", { text: "No budget rate held for this material" })
        ]),
        el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "7px" },
          text: m.budget_note
        })
      ]);
    }

    /* ---- premium history — the generalisation of the wheat basis history */
    var series = P.premiumSeries(m, ws.quotes, quote.supplier_id, GD.wsAsOf());
    var histBlock;
    if (series.months.length >= 2) {
      histBlock = el("div", null, [
        GD.chart.line({
          series: [
            { name: (sup ? sup.name : quote.supplier_id) + " premium",
              values: series.mine, color: C_PREM, weight: 2 },
            { name: m.name + " median premium", values: series.median, color: "var(--ink-4)" }
          ],
          dates: series.months,
          w: 560, h: 196,
          fmtY: function (v) { return GD.curM(v, m.currency); },
          fmtTip: function (v) { return price(v, m) + per(m); },
          label: "Supplier premium against the material median, by month"
        }),
        GD.legend([
          { name: (sup ? sup.name : quote.supplier_id), color: C_PREM },
          { name: "Median across all suppliers of " + m.name, color: "var(--ink-4)" }
        ])
      ]);
    } else {
      histBlock = panel([el("div", {
        style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
        text: "Not enough months of quotes on " + m.name + " to draw a premium history yet."
      })]);
    }

    var trendNote = trend.quarters.length >= 2
      ? (trend.widening
          ? "This supplier's premium has widened " + trend.risingRun +
            " quarters running — " + price(trend.quarters[0].avg, m) + per(m) + " in " +
            trend.quarters[0].key + " to " +
            price(trend.quarters[trend.quarters.length - 1].avg, m) + " in " +
            trend.quarters[trend.quarters.length - 1].key + ". The drivers are published; " +
            "the premium is the part only they can explain."
          : "Quarter to quarter this supplier's premium has been broadly stable — at most " +
            trend.risingRun + " consecutive quarter" + (trend.risingRun === 1 ? "" : "s") +
            " of widening over the period.")
      : null;

    var sub = [
      m.name,
      m.category === "packaging" ? "Packaging" : "Raw material",
      fmt.n(quote.qty, 0) + " " + m.unit,
      "quoted " + fmt.date(quote.date),
      quote.valid_to ? "valid to " + fmt.date(quote.valid_to) : null
    ].filter(Boolean).join(" · ");

    GD.sheet({
      title: (sup ? sup.name : quote.supplier_id),
      sub: sub,
      body: function () {
        return [
          el("div", { style: { "margin-bottom": "18px" } }, [hero]),
          el("div", { style: { "margin-bottom": "18px" } }, [disclosure]),
          el("div", { class: "grid g-2", style: { "margin-bottom": "18px" } }, [
            section("The arithmetic", legBlock),
            el("div", { style: { display: "grid", gap: "18px", "align-content": "start" } }, [
              section("Fair band", bandBar(a),
                band.established
                  ? "The band is the 25th to 75th percentile of the premium every supplier " +
                    "of " + m.name + " has asked for over the last twelve months — " +
                    band.n + " quotes — added to this month's driver cost. It is built from " +
                    "our own quote history, not bought from anyone with a view on the answer."
                  : null),
              section("Against budget", budgetBlock)
            ])
          ]),
          section("Premium over time", histBlock, trendNote)
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: function () { showBrief(a, ws); }
          }, [icon("file", 14, 2), "Negotiation brief"]),
          el("button", { class: "btn", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: verdictText(a, m) })
        ];
      }
    });
  };

  function verdictText(a, m) {
    if (!a.band.established) return "No band yet — read against driver cost and budget.";
    if (a.position === "above") {
      return "Above the band by " + price(a.outside, m) + per(m) +
        " · " + GD.curM(a.outside * a.quote.qty, m.currency) + " on this quantity.";
    }
    if (a.position === "below") {
      return "Below the band by " + price(Math.abs(a.outside), m) + per(m) + " — the keen end.";
    }
    return "Inside the band. The premium is normal for this material.";
  }

  /* =========================================================== how the band is built */

  function methodStep(n, name, body, meta) {
    return el("div", { class: "note-row" }, [
      el("span", {
        style: {
          width: "22px", height: "22px", "border-radius": "7px", flex: "none",
          background: "var(--surface-3)", color: "var(--ink-2)", display: "grid",
          "place-items": "center", "font-size": "var(--t-xs)", "font-weight": "600"
        },
        text: String(n)
      }),
      el("div", { class: "nr-body" }, [
        el("div", { class: "nr-head" }, [el("h4", { text: name })]),
        el("p", { text: body }),
        meta ? el("div", { class: "nr-meta mono", text: meta }) : null
      ])
    ]);
  }

  function showMethod(ws) {
    var mocked = [];
    ws.materials.forEach(function (m) {
      (m.drivers || []).forEach(function (d) {
        if (d.mocked && mocked.indexOf(d.name) < 0) mocked.push(d.name);
      });
    });

    GD.sheet({
      title: "How the fair band is built",
      sub: "Four steps, all of them arithmetic on data this desk already holds",
      body: function () {
        return [
          el("div", {
            style: {
              border: "1px solid var(--border)", "border-radius": "var(--r-card)",
              overflow: "hidden", "margin-bottom": "18px"
            }
          }, [
            methodStep(1, "Driver legs",
              "Each material declares its drivers: the index or feed that moves it, how many " +
              "driver units go into one unit of material, any fixed spread, and the currency " +
              "it is published in. A leg is the print in force on the quote's date times " +
              "that conversion, converted into the material's currency. Nothing here is a " +
              "model — it is a published number and a stated multiplier.",
              "leg = (print × per_unit + spread) × fx"),
            methodStep(2, "Driver cost and premium",
              "Driver cost is the legs added up. The premium is the quote minus driver cost " +
              "— a subtraction, which is why it is called a premium and not a margin: it " +
              "contains the supplier's margin and also their handling, their risk and any " +
              "cost we cannot see. No supplier itemises it, so the screen never implies " +
              "they did.",
              "premium = quote − driver cost"),
            methodStep(3, "The band",
              "Every quote for the material from every supplier over the last twelve months " +
              "gives one premium. The 25th and 75th percentiles of that population, added " +
              "to this month's driver cost, are the band. Below four observations there is " +
              "no band at all — quartiles of three numbers describe noise, and somebody " +
              "would negotiate against them.",
              "band = driver cost + [p25, p75] of the material's premiums"),
            methodStep(4, "Budget",
              "Separate question, separate answer. The band says whether a premium is " +
              "normal; the budget rate says whether the business can afford the price. A " +
              "quote can be inside the band and over budget, which is a conversation about " +
              "product cost rather than about the supplier.",
              "budget gap = quote − budget rate")
          ]),
          panel([
            el("div", {
              class: "flex items-center gap-8",
              style: { "flex-wrap": "wrap", "margin-bottom": "9px" }
            }, [
              el("div", { class: "card-title", style: { flex: "1" }, text: "What is real here" }),
              ws.demo ? GD.chip("demo workspace", "warn", "alert") : null
            ]),
            el("p", {
              style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
              text: ws.demo
                ? "Nothing. This workspace is generated: the suppliers, the materials, the " +
                  "quotes and the index levels are all invented for the demonstration. What " +
                  "is real is the method — the four steps above are what would run against " +
                  "a customer's own quote history and their own feeds."
                : "The quote log and the futures settlements are read off the desk. The " +
                  "freight legs are the mid of a published range rather than a fixture, and " +
                  "the daily path between settlements is generated — the levels are not."
            }),
            mocked.length ? el("p", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" },
              text: "Placeholder feeds in this workspace: " + mocked.join(" · ") + "."
            }) : null,
            el("p", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" },
              text: GD.fxNote()
            })
          ])
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: close, text: "Got it"
          }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub",
            text: "Of everything on this screen, only the quoted price is the supplier's." })
        ];
      }
    });
  }

  /* ===================================================================== the surface */

  GD.route("pricecheck", {
    label: "Price check",
    icon: "scale",
    group: "Desk",
    ws: ["urc"],
    badge: function () {
      var ws = GD.ws();
      if (!ws) return null;
      var n = 0;
      ws.materials.forEach(function (m) {
        P.latestPerSupplier(m.id, ws.quotes).forEach(function (q) {
          var a = P.assess(m, q, ws.quotes, GD.wsAsOf());
          if (a.position === "above") n++;
        });
      });
      return n;
    },
    badgeTone: "warn",

    render: function (wrap) {
      var ws = GD.ws();
      var asOf = GD.wsAsOf();
      var matById = {};
      ws.materials.forEach(function (m) { matById[m.id] = m; });

      /* The live board: the latest quote from each supplier of each material, which is what a
         buyer is actually holding when the phone rings. */
      var live = [];
      ws.materials.forEach(function (m) {
        P.latestPerSupplier(m.id, ws.quotes).forEach(function (q) { live.push(q); });
      });

      var assessed = {};
      function assess(q) {
        if (!assessed[q.id]) {
          assessed[q.id] = P.assess(matById[q.material_id], q, ws.quotes, asOf);
        }
        return assessed[q.id];
      }

      var above = live.filter(function (q) { return assess(q).position === "above"; });
      var overBudget = live.filter(function (q) {
        var b = assess(q).budget;
        return b && b.over;
      });
      var recover = 0, recoverCur = ws.home_currency;
      above.forEach(function (q) {
        var a = assess(q);
        var m = matById[q.material_id];
        var v = (a.outside || 0) * q.qty * GD.fx(m.currency, ws.home_currency);
        if (v) recover += v;
      });

      /* ---------------------------------------------------------------- 1 · head */
      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Price check" }),
          el("div", {
            class: "page-sub",
            text: ws.materials.length + " materials, " + ws.suppliers.length +
                  " suppliers, " + fmt.n(ws.quotes.length, 0) + " quotes — what the drivers, " +
                  "the band and the budget say about each one"
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.demoChip(),
          GD.chip("as of " + fmt.date(asOf), "outline", "calendar"),
          el("button", {
            class: "btn", type: "button", onclick: function () { showMethod(ws); }
          }, [icon("layers", 14, 1.75), "How the band is built"])
        ])
      ]));

      /* --------------------------------------------------------------- 2 · stats */
      var statAbove = GD.stat({
        label: "Above the fair band", value: fmt.n(above.length, 0),
        unit: "of " + live.length, icon: "alert",
        aside: el("span", { class: "flex items-center gap-8" }, [
          GD.dot(above.length ? "bad" : "good"),
          el("span", {
            class: "num",
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "font-weight": "600" },
            text: fmt.pct(live.length ? above.length / live.length : 0, 0)
          })
        ]),
        delta: [
          el("span", { text: "premium over the 75th percentile for the material" })
        ]
      });

      var statBudget = GD.stat({
        label: "Over budget", value: fmt.n(overBudget.length, 0),
        unit: "of " + live.length, icon: "scale",
        delta: [
          el("span", { text: "against the rate the business set for the material" })
        ]
      });

      var statRecover = GD.stat({
        label: "Open to discuss", value: GD.curM(recover, recoverCur), unit: "on live quotes",
        icon: "savings", hero: true,
        delta: [
          el("b", { text: "the distance above the band" }),
          el("span", { text: "× the quantity offered" })
        ]
      });
      GD.tipOn(statRecover, "<b>What is on the table</b><div class='tip-mute'>" +
        "For every live quote above its fair band, the gap to the top of the band times the " +
        "quantity offered. It is the size of the conversation, not a saving already made — " +
        "and converted to " + GD.esc(recoverCur) + " at the indicative mid.</div>");

      wrap.appendChild(el("div", { class: "grid g-3" }, [
        statAbove, statBudget, statRecover
      ]));

      /* --------------------------------------------- 3 · the headline, if there is one */
      if (above.length) {
        var worst = above.slice().sort(function (x, y) {
          var ax = assess(x), ay = assess(y);
          var mx = matById[x.material_id], my = matById[y.material_id];
          return (ay.outside * y.qty * GD.fx(my.currency, ws.home_currency)) -
                 (ax.outside * x.qty * GD.fx(mx.currency, ws.home_currency));
        })[0];
        var wa = assess(worst);
        var wm = matById[worst.material_id];
        var wt = P.premiumTrend(wm, ws.quotes, worst.supplier_id, asOf);
        var wsup = GD.supplier(worst.supplier_id, ws);

        var card = GD.card({
          title: "Worth a call: " + (wsup ? wsup.name : worst.supplier_id),
          sub: wm.name + " · quoted " + fmt.date(worst.date) +
               (worst.valid_to ? " · valid to " + fmt.date(worst.valid_to) : ""),
          actions: [verdictChip(wa.position)],
          flush: true,
          body: [
            el("div", { class: "statline" }, [
              statCell("Quoted", price(worst.price, wm) + per(wm)),
              statCell("Fair band", price(wa.band.low, wm) + " – " + price(wa.band.high, wm)),
              statCell("Above the band", signedPrice(wa.outside, wm) + per(wm),
                GD.toneFor(wa.outside)),
              statCell("On " + fmt.n(worst.qty, 0) + " " + wm.unit,
                GD.curM(wa.outside * worst.qty, wm.currency), GD.toneFor(wa.outside)),
              wa.budget ? statCell("vs budget", fmt.signedPct(wa.budget.pct, 1),
                GD.toneFor(wa.budget.amount)) : null
            ]),
            el("div", {
              style: {
                padding: "14px 16px", "border-top": "1px solid var(--border)",
                display: "grid", gap: "11px"
              }
            }, [
              el("p", {
                style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
                text: wt.widening
                  ? "Their premium has widened " + wt.risingRun + " quarters running, from " +
                    price(wt.quarters[0].avg, wm) + per(wm) + " in " + wt.quarters[0].key +
                    " to " + price(wt.quarters[wt.quarters.length - 1].avg, wm) + " in " +
                    wt.quarters[wt.quarters.length - 1].key + ", while the drivers moved " +
                    "independently of it. That is the conversation for the next business " +
                    "review, and the brief frames it as one."
                  : "The premium itself is the thing to raise — the drivers are published " +
                    "and can be checked, and this quote carries " +
                    price(wa.outside, wm) + per(wm) + " more than three quarters of what " +
                    "we have been shown on " + wm.name + " this year."
              }),
              el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
                el("button", {
                  class: "btn btn-primary", type: "button",
                  onclick: function () { GD.showPriceCheck(worst, ws); }
                }, [icon("scale", 14, 2), "Open the breakdown"]),
                el("button", {
                  class: "btn", type: "button",
                  onclick: function () { showBrief(wa, ws); }
                }, [icon("file", 14, 1.75), "Negotiation brief"])
              ])
            ])
          ],
          foot: [
            el("span", { style: { color: "var(--ink-3)" },
              text: "Chosen as the largest gap to the top of its band, in money, across " +
                    "every live quote." })
          ]
        });
        card.style.setProperty("box-shadow", "var(--sh-md)");
        wrap.appendChild(card);
      }

      /* ------------------------------------------------------------- 4 · filters */
      var state = { material: "all", pos: "all", scope: "live" };
      var table;

      function pool() { return state.scope === "live" ? live : ws.quotes; }

      function filtered() {
        return pool().filter(function (q) {
          if (state.material !== "all" && q.material_id !== state.material) return false;
          if (state.pos !== "all" && assess(q).position !== state.pos) return false;
          return true;
        }).sort(function (x, y) { return x.date < y.date ? 1 : x.date > y.date ? -1 : 0; });
      }

      var countNode = el("span", {
        "aria-live": "polite",
        style: {
          "font-size": "var(--t-xs)", color: "var(--ink-3)",
          "font-variant-numeric": "tabular-nums", "white-space": "nowrap"
        }
      });
      var clearBtn = el("button", {
        class: "btn btn-sm", type: "button", disabled: true,
        onclick: function () { clearAll(); }
      }, [icon("x", 12, 1.75), "Clear"]);

      function sync(n) {
        var next = "showing " + fmt.n(n, 0) + " of " + fmt.n(pool().length, 0);
        if (countNode.textContent !== next) countNode.textContent = next;
        var dirty = state.material !== "all" || state.pos !== "all";
        if (dirty) clearBtn.removeAttribute("disabled");
        else clearBtn.setAttribute("disabled", "");
      }
      function apply() {
        var rows = filtered();
        table.update(rows);
        sync(rows.length);
      }

      var scopeSeg = GD.seg([
        { key: "live", label: "Live " + live.length },
        { key: "all", label: "12 months " + ws.quotes.length }
      ], "live", function (k) { state.scope = k; apply(); });

      var posSeg = GD.seg([
        { key: "all", label: "All" },
        { key: "above", label: "Above" },
        { key: "inside", label: "Inside" },
        { key: "below", label: "Below" }
      ], "all", function (k) { state.pos = k; apply(); });

      var matSel = el("select", {
        class: "field", "aria-label": "Filter by material",
        onchange: function () { state.material = matSel.value; apply(); }
      }, [el("option", { value: "all", text: "All materials" })].concat(
        ws.materials.map(function (m) {
          var n = ws.quotes.filter(function (q) { return q.material_id === m.id; }).length;
          return el("option", { value: m.id, text: m.name + "  (" + n + ")" });
        })
      ));

      function clearAll() {
        state.material = "all"; state.pos = "all";
        matSel.value = "all";
        GD.qsa("button", posSeg).forEach(function (b, i) {
          b.setAttribute("aria-pressed", String(i === 0));
        });
        apply();
      }

      /* How the Market surface and the command palette hand a material over. */
      GD.filterPriceCheck = function (materialId) {
        state.material = materialId;
        matSel.value = materialId;
        apply();
        var m = GD.material(materialId, ws);
        if (m) GD.toast("Filtered to " + m.name, { icon: "filter" });
        var content = GD.qs(".content");
        if (content) content.scrollTop = 0;
      };
      function divider() {
        return el("span", {
          "aria-hidden": "true",
          style: { width: "1px", height: "20px", background: "var(--border)", flex: "none" }
        });
      }

      wrap.appendChild(GD.card({
        body: [
          el("div", {
            style: { display: "flex", "align-items": "center", gap: "10px", "flex-wrap": "wrap" }
          }, [
            el("span", {
              class: "flex items-center gap-8",
              style: { color: "var(--ink-3)", flex: "none" }
            }, [icon("filter", 14, 1.75), el("span", { class: "label", text: "Board" })]),
            scopeSeg,
            divider(),
            posSeg,
            divider(),
            matSel,
            countNode,
            clearBtn
          ])
        ]
      }));

      /* --------------------------------------------------------------- 5 · table */
      var cols = [
        {
          key: "material_id", label: "Material", tdClass: "strong", width: "150px",
          render: function (q) {
            var m = matById[q.material_id];
            return el("div", null, [
              el("div", { text: m.name }),
              el("div", {
                style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "font-weight": "400" },
                text: (m.category === "packaging" ? "Packaging" : "Raw material") +
                      " · per " + m.unit
              })
            ]);
          },
          sortValue: function (q) { return matById[q.material_id].name; }
        },
        {
          key: "supplier_id", label: "Supplier", width: "150px",
          render: function (q) { return GD.supplierName(q.supplier_id, ws); },
          sortValue: function (q) { return GD.supplierName(q.supplier_id, ws); }
        },
        {
          key: "date", label: "Received", width: "96px",
          render: function (q) {
            var n = el("span", { text: fmt.ago(q.date, asOf) });
            GD.tipOn(n, "<b>" + GD.esc(fmt.date(q.date)) + "</b><div class='tip-mute'>" +
              GD.esc(q.source_ref || "") + "</div>");
            return n;
          }
        },
        {
          key: "price", label: "Quoted", num: true, tdClass: "strong", width: "104px",
          render: function (q) { return price(q.price, matById[q.material_id]); }
        },
        {
          key: "band", label: "vs band", width: "138px",
          sortValue: function (q) {
            var a = assess(q);
            return a.outside === null ? -1e9 : a.outside;
          },
          render: function (q) {
            var a = assess(q), m = matById[q.material_id];
            if (!a.band.established) return el("span", { class: "val-mute", text: "no band yet" });
            var tone = a.position === "above" ? "bad" : "good";
            var txt = a.position === "inside" ? "inside"
              : (a.position === "above" ? "above " : "below ") +
                price(Math.abs(a.outside), m);
            var c = GD.chip(txt, tone, a.position === "above" ? "up"
              : a.position === "below" ? "down" : "check");
            GD.tipOn(c, "<b>Fair band</b><div class='tip-row'><span class='tip-mute'>25th</span><b>" +
              GD.esc(price(a.band.low, m)) + "</b></div>" +
              "<div class='tip-row'><span class='tip-mute'>75th</span><b>" +
              GD.esc(price(a.band.high, m)) + "</b></div>" +
              "<div class='tip-mute'>from " + a.band.n + " quotes over 12 months</div>");
            return c;
          }
        },
        {
          key: "budget", label: "vs budget", num: true, width: "100px",
          sortValue: function (q) {
            var b = assess(q).budget;
            return b ? b.pct : -1e9;
          },
          render: function (q) {
            var b = assess(q).budget;
            if (!b) return el("span", { class: "val-mute", text: "—" });
            return el("span", { class: GD.toneFor(b.amount), text: fmt.signedPct(b.pct, 1) });
          }
        },
        {
          key: "compo", label: "Breakdown", sortable: false, width: "160px",
          render: function (q) {
            var a = assess(q), m = matById[q.material_id];
            var parts = a.breakdown.legs.map(function (l) {
              return { name: l.name, value: l.value, color: legColor(l) };
            }).concat([{ name: "Supplier premium", value: a.breakdown.premium, color: C_PREM }])
              .filter(function (p) { return p.value > 0; });
            var bar = GD.compo(parts, q.price);
            bar.style.setProperty("height", "14px");
            GD.qsa("span", bar).forEach(function (s) { s.setAttribute("tabindex", "-1"); });
            return bar;
          }
        }
      ];

      table = GD.table({
        cols: cols,
        rows: filtered(),
        sortKey: "date",
        sortDir: "desc",
        onRow: function (q) { GD.showPriceCheck(q, ws); },
        emptyText: "No quote matches these filters."
      });

      wrap.appendChild(GD.card({
        title: "Quotes",
        sub: "Click a row for the breakdown, the band, the premium history and the brief",
        actions: [GD.legend([
          { name: "Index", color: C_IDX },
          { name: "Freight", color: C_FRT },
          { name: "Spread", color: C_SPR },
          { name: "Premium", color: C_PREM }
        ])],
        flush: true,
        body: [table],
        foot: [
          el("span", null, [
            el("b", { text: "Premium" }),
            " is the quote less driver cost. No supplier itemises it — it is our subtraction."
          ]),
          el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
          el("span", null, [
            el("b", { text: "vs band" }),
            " compares it with the middle half of what every supplier of that material has " +
            "charged over twelve months."
          ]),
          el("span", { style: { flex: "1", "min-width": "0" } })
        ]
      }));

      /* The materials-and-drivers reference table that used to sit here now lives on
         the Market surface, next to what those drivers have actually done. It was
         reference data on a decision screen; it belongs with the decision it informs. */

      sync(filtered().length);
    }
  });

})();
