/* app/js/surfaces/margin.js — Blend bridge and flour margin.

   The last link in the chain. The quote board decides what a tonne of wheat
   costs; this screen carries that tonne through a blend recipe and out the
   other side as baht per kilo on a customer's invoice, then asks the only
   question the mill actually cares about: does the price list still clear the
   cost line.

   Every SKU, pack, list price, four-quarter cost and margin here is read from
   the Q426 Industrial Margin Analysis workbook. Anything this file works out
   for itself — the blend fix, the break-even uplift, the wheat-to-flour
   multiple — is labelled on screen as reconstructed. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, D = window.DESK;

  var FLOUR = D.flour;
  var GRADES = D.grades;
  var FX = D.profile.fx;
  var WHEAT_SHARE = D.profile.economics.wheat_share_of_cost;
  var QUARTERS = ["Q3'26", "Q4'26", "Q1'27", "Q2'27"];
  var AVG_KEY = "__avg";
  var SEQ = 0;   /* bumped per render, so form ids stay unique across renders */

  /* ------------------------------------------------------------ palette
     A small fixed palette, assigned once: the wheat classes in the blend book
     are ordered by protein and cycled through the four category hues, so a
     class keeps the same colour on every grade block. Seven classes over four
     hues means two distant classes share one, but no recipe in this book mixes
     two classes four apart in the protein order, so no single mix shows two
     segments the same colour. Emerald is deliberately absent — on this desk
     emerald means "cheaper to buy", and a colour that carries a verdict must
     not also be a category label. */
  var PALETTE = [
    "var(--c-margin)",   /* brick  — spring wheats, the protein end */
    "var(--c-futures)",  /* blue   */
    "var(--c-freight)",  /* amber  */
    "var(--c-residual)"  /* grey   — soft wheats, the filler end */
  ];
  var CLASS_COLOR = (function () {
    var seen = {}, list = [], map = {};
    GRADES.forEach(function (g) {
      g.mix.forEach(function (m) {
        if (seen[m.cls]) return;
        seen[m.cls] = true;
        list.push(m);
      });
    });
    list.sort(function (a, b) {
      if (b.protein !== a.protein) return b.protein - a.protein;
      return a.cls < b.cls ? -1 : a.cls > b.cls ? 1 : 0;
    });
    list.forEach(function (m, i) { map[m.cls] = PALETTE[i % PALETTE.length]; });
    return map;
  })();
  function classColor(cls) { return CLASS_COLOR[cls] || "var(--c-residual)"; }

  /* ------------------------------------------------------------ figures
     All of it arithmetic over window.DESK, computed once at load. The dataset
     is fixed as of DESK.built, so none of this moves. */

  function sumBy(list, fn) {
    return list.reduce(function (s, x, i) { return s + fn(x, i); }, 0);
  }
  function mean(list, fn) { return list.length ? sumBy(list, fn) / list.length : 0; }

  var AVG_COST = QUARTERS.map(function (_, qi) {
    return mean(FLOUR, function (f) { return f.cost[qi]; });
  });
  var AVG_MARGIN = mean(FLOUR, function (f) { return f.margin; });
  var BELOW = FLOUR.filter(function (f) { return f.margin < 0; });
  var THIN = FLOUR.filter(function (f) { return f.margin >= 0 && f.margin < 0.05; });
  /* Reconstructed: SKUs whose Q1'27 cost already stands at or above today's
     list price, so they go to zero margin without a price move. */
  var BELOW_BY_Q1 = FLOUR.filter(function (f) { return f.cost[2] >= f.baht_kg; });
  var MARGINS_SORTED = FLOUR.map(function (f) { return f.margin; })
    .sort(function (a, b) { return a - b; });
  var MARGIN_LO = MARGINS_SORTED[0];
  var MARGIN_HI = MARGINS_SORTED[MARGINS_SORTED.length - 1];
  var AVG_BAR = (function () {
    var best = 0, bestD = Infinity;
    MARGINS_SORTED.forEach(function (v, i) {
      var dd = Math.abs(v - AVG_MARGIN);
      if (dd < bestD) { bestD = dd; best = i; }
    });
    return best;
  })();

  var GRADE_COSTS = GRADES.map(function (g) { return g.flour_thb_kg; });
  var CHEAPEST = GRADES.reduce(function (a, b) {
    return b.flour_thb_kg < a.flour_thb_kg ? b : a;
  }, GRADES[0]);
  var CHEAPEST_IX = GRADES.indexOf(CHEAPEST);
  var FAILING = GRADES.filter(function (g) { return !g.meets; });
  var CLASS_COUNT = Object.keys(CLASS_COLOR).length;

  /* Two integrity checks on the workbook, reported on screen rather than
     assumed. Both are cheap and both have been wrong in spreadsheets before. */
  var SUGGEST_HOLDS = FLOUR.filter(function (f) {
    if (1 - f.margin <= 0) return false;
    return Math.abs(f.cost[2] / (1 - f.margin) - f.suggested) <= 0.011;
  }).length;
  var MARGIN_TIES_COST = FLOUR.filter(function (f) {
    if (!f.baht_kg) return false;
    return Math.abs((f.baht_kg - f.cost_now) / f.baht_kg - f.margin) <= 0.0005;
  }).length;

  function shortName(grade) { return String(grade).split(" (")[0]; }

  /* Signed money, so "+฿2.28" and "+$3.64" read as moves and not as levels.
     fmt.thb/fmt.usd would put the sign inside the symbol ("$-3.64"). */
  function sthb(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + fmt.thb(Math.abs(v), dp);
  }
  function susd(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + fmt.usd(Math.abs(v), dp);
  }
  function bahtAxis(v) { return fmt.thb(v, v % 1 === 0 ? 0 : 1); }
  function perKg(v) { return fmt.thb(v) + "/kg"; }

  /* `.num` is a table-cell rule in app.css — `td.num, th.num`. A figure that
     sits outside a table cell (and outside a `.kv` value, which has its own
     rule) has to ask for the numerals by hand, and the contract says to do
     that with an inline style rather than touching the shared sheet. */
  function numStyle(extra) {
    var s = { "font-variant-numeric": "tabular-nums" };
    if (extra) Object.keys(extra).forEach(function (k) { s[k] = extra[k]; });
    return s;
  }

  /* ------------------------------------------------------------ blend fix
     For a grade that prices out under its protein minimum: how much of the
     blend has to move from the weakest class into the strongest to clear it.
     Pure arithmetic on the stated mix, and it only speaks if the stated mix
     actually reproduces the stated blend protein. */
  function blendFix(g) {
    var calcP = sumBy(g.mix, function (m) { return m.share * m.protein; });
    var calcC = sumBy(g.mix, function (m) { return m.share * m.usd_mt; });
    /* "mix-disagrees" and "no-shortfall" are not failures of the fix, they are
       the workbook disagreeing with itself. The caller says which it is rather
       than printing a shortfall nobody can stand behind. */
    if (Math.abs(calcP - g.blend_protein) > 0.011) {
      return { reason: "mix-disagrees", calcP: calcP };
    }

    var short = g.protein_min - calcP;
    if (short <= 0) return { reason: "no-shortfall", calcP: calcP };

    var hi = g.mix[0], lo = g.mix[0];
    g.mix.forEach(function (m) {
      if (m.protein > hi.protein) hi = m;
      if (m.protein < lo.protein) lo = m;
    });
    var span = hi.protein - lo.protein;
    if (span <= 0) return { reason: "one-protein", short: short, hi: hi, lo: lo };

    var x = short / span;
    if (x > lo.share) return { reason: "too-far", short: short, hi: hi, lo: lo };

    var newCost = sumBy(g.mix, function (m) {
      return (m.share + (m === hi ? x : m === lo ? -x : 0)) * m.usd_mt;
    });
    /* The workbook states no extraction rate, so the only honest route from
       $/t of wheat to ฿/kg of flour is this grade's own observed multiple. */
    var wheatThbKg = calcC * FX / 1000;
    var mult = wheatThbKg > 0 ? g.flour_thb_kg / wheatThbKg : null;
    var dCost = newCost - calcC;
    return {
      reason: "ok",
      short: short, hi: hi, lo: lo, x: x,
      newHi: hi.share + x, newLo: lo.share - x,
      newCost: newCost, dCost: dCost,
      mult: mult, dThb: mult === null ? null : dCost * FX / 1000 * mult
    };
  }

  /* ------------------------------------------------------------ small parts */

  function labelRow(text, right) {
    return el("div", {
      class: "flex items-center between",
      style: { gap: "10px", "margin-bottom": "8px" }
    }, [
      el("span", { class: "label", text: text }),
      right || null
    ]);
  }

  function hairline(top, bottom) {
    return el("hr", {
      class: "hr",
      style: { "margin-top": (top || 14) + "px", "margin-bottom": (bottom || 14) + "px" }
    });
  }

  function toned(text, v, positiveIsGood) {
    return el("span", {
      class: "num " + GD.toneFor(v, positiveIsGood), text: text, style: numStyle()
    });
  }

  function valueWithDelta(text, delta, positiveIsGood) {
    return el("span", { class: "flex items-center", style: { gap: "7px" } }, [
      el("span", { text: text }),
      toned(sthb(delta), delta, positiveIsGood)
    ]);
  }

  /* A short "this is worked out here, not supplied" line. Used wherever this
     file does arithmetic the workbook does not. */
  function reconstructed(text) {
    return el("p", {
      class: "flex",
      style: {
        gap: "7px", "font-size": "var(--t-xs)", color: "var(--ink-3)",
        "align-items": "flex-start", "margin-top": "10px"
      }
    }, [
      el("span", { style: { color: "var(--ink-4)", "flex": "none", "margin-top": "1px" } },
        [icon("sparkle", 12, 1.5)]),
      /* `text-wrap` inherits, so the sheet's `p { text-wrap: pretty }` already
         reaches this span. Stated anyway so the prose keeps it if the flex
         wrapper ever stops being a <p>. */
      el("span", { style: { "text-wrap": "pretty" }, text: text })
    ]);
  }

  function panel(tone, kids) {
    return el("div", {
      style: {
        background: tone === "warn" ? "var(--warn-soft)" : "var(--surface-2)",
        border: "1px solid var(--border)",
        "border-radius": "var(--r-inner)",
        padding: "12px 13px",
        display: "grid", gap: "9px"
      }
    }, kids);
  }

  /* ------------------------------------------------------------ stat row */

  function statRow(openReview) {
    var allMeet = !FAILING.length;

    var gradesStat = GD.stat({
      label: "Grades priced",
      value: fmt.n(GRADES.length),
      unit: "grades",
      icon: "layers",
      aside: allMeet
        ? GD.chip("all meet spec", "good", "check")
        : GD.chip(FAILING.length + " below spec", "bad", "alert"),
      delta: [
        icon("wheat", 13, 1.75),
        el("span", null, [
          el("b", { text: fmt.n(CLASS_COUNT) }),
          el("span", { text: " wheat classes in the blend book" })
        ])
      ]
    });

    var cheapStat = GD.stat({
      label: "Cheapest grade",
      value: fmt.thb(CHEAPEST.flour_thb_kg),
      unit: "/kg flour",
      icon: "scale",
      aside: GD.chart.barSpark(GRADE_COSTS, {
        take: GRADE_COSTS.length, highlight: CHEAPEST_IX,
        w: 74, h: 32, gap: 4
      }),
      delta: [
        GD.dot("good"),
        el("span", null, [
          el("b", { text: shortName(CHEAPEST.grade) }),
          el("span", { text: " — lowest of " + GRADES.length + ", dearest " + fmt.thb(Math.max.apply(null, GRADE_COSTS)) })
        ])
      ]
    });

    var lossStat = GD.stat({
      label: "SKUs below zero margin",
      value: fmt.n(BELOW.length),
      unit: "of " + FLOUR.length,
      icon: "alert",
      aside: GD.chip("needs a price", "warn", "alert"),
      onClick: openReview,
      delta: [
        GD.dot("warn"),
        el("span", null, [
          el("span", { text: "and " }),
          el("b", { text: fmt.n(THIN.length) }),
          el("span", { text: " more under 5% — open the price review" })
        ])
      ]
    });

    var marginStat = GD.stat({
      label: "Average margin across SKUs",
      value: fmt.pct(AVG_MARGIN, 1),
      icon: "margin",
      hero: true,
      aside: GD.chart.barSpark(MARGINS_SORTED, {
        take: MARGINS_SORTED.length, highlight: AVG_BAR,
        w: 86, h: 34, gap: 2,
        color: "var(--on-accent)", muted: "rgba(255, 255, 255, 0.30)"
      }),
      delta: [
        icon("sort", 13, 1.75),
        el("span", null, [
          el("span", { text: "thinnest " }),
          el("b", { text: fmt.pct(MARGIN_LO, 1) }),
          el("span", { text: " · fattest " }),
          el("b", { text: fmt.pct(MARGIN_HI, 1) })
        ])
      ]
    });

    GD.tipOn(marginStat, "<b>All " + FLOUR.length + " SKUs, thinnest margin first</b>" +
      "<div class='tip-row'><span class='tip-mute'>average</span><b>" +
      GD.esc(fmt.pct(AVG_MARGIN, 1)) + "</b></div>" +
      "<div class='tip-row'><span class='tip-mute'>marked bar</span><b>" +
      GD.esc(fmt.pct(MARGINS_SORTED[AVG_BAR], 1)) + "</b></div>" +
      "<div class='tip-mute'>The marked bar is the SKU nearest the average.</div>");

    GD.tipOn(cheapStat, "<b>Flour cost by grade</b>" +
      GRADES.map(function (g) {
        return "<div class='tip-row'><span class='tip-mute'>" + GD.esc(shortName(g.grade)) +
          "</span><b>" + GD.esc(perKg(g.flour_thb_kg)) + "</b></div>";
      }).join(""));

    return el("div", { class: "grid g-4" }, [gradesStat, cheapStat, lossStat, marginStat]);
  }

  /* ------------------------------------------------------------ grade block */

  function gradeCard(g) {
    var head = g.blend_protein - g.protein_min;
    var rank = GRADES.filter(function (o) { return o.flour_thb_kg < g.flour_thb_kg; }).length + 1;
    var ix = GRADES.indexOf(g);

    var parts = g.mix.map(function (m) {
      return {
        name: m.cls + " at " + fmt.usd(m.usd_mt) + "/t",
        value: m.share * m.usd_mt,
        color: classColor(m.cls)
      };
    });

    var body = [
      labelRow("Wheat cost build · $/t of blend",
        el("span", {
          class: "num", style: numStyle({ "font-weight": "600" }),
          text: fmt.usd(g.wheat_usd_mt) + "/t"
        })),
      GD.compo(parts, g.wheat_usd_mt),
      el("div", { style: { "margin-top": "10px" } }, [
        GD.legend(g.mix.map(function (m) {
          return { name: m.cls + " · " + fmt.pct(m.share, 0) + " of mix", color: classColor(m.cls) };
        }))
      ]),
      hairline(14, 13),
      GD.kv([
        ["Blend protein", el("span", { class: "flex items-center", style: { gap: "8px" } }, [
          el("span", { text: fmt.n(g.blend_protein, 2) + "%" }),
          head >= 0
            ? GD.chip(fmt.n(head, 2) + " pts clear", "good", "check")
            : GD.chip(fmt.n(Math.abs(head), 2) + " pts short", "bad", "alert")
        ])],
        ["Protein minimum", fmt.n(g.protein_min, 2) + "%"],
        ["Wheat cost", fmt.usd(g.wheat_usd_mt) + "/t"],
        ["Flour cost", perKg(g.flour_thb_kg)]
      ])
    ];

    if (!g.meets) {
      var fix = blendFix(g);
      var fixKids = [
        el("div", {
          class: "flex items-center",
          style: { gap: "7px", color: "var(--warn)", "font-weight": "600", "font-size": "var(--t-sm)" }
        }, [
          icon("alert", 14, 2),
          el("span", { text: "Blend fix" })
        ])
      ];

      if (fix.reason === "ok") {
        fixKids.push(el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" } },
          "This blend prices out " + fmt.n(fix.short, 2) + " protein points under the " +
          fmt.n(g.protein_min, 2) + "% minimum. Moving " + fmt.pct(fix.x, 1) +
          " of the blend out of " + fix.lo.cls + " (" + fmt.n(fix.lo.protein, 2) +
          "% protein) and into " + fix.hi.cls + " (" + fmt.n(fix.hi.protein, 2) +
          "%) lands it exactly on the minimum."));
        fixKids.push(GD.kv([
          [fix.hi.cls, fmt.pct(fix.hi.share, 1) + " → " + fmt.pct(fix.newHi, 1)],
          [fix.lo.cls, fmt.pct(fix.lo.share, 1) + " → " + fmt.pct(fix.newLo, 1)],
          ["Blend protein", el("span", { class: "flex items-center", style: { gap: "8px" } }, [
            el("span", { text: fmt.n(g.protein_min, 2) + "%" }),
            GD.chip("meets spec", "good", "check")
          ])],
          ["Wheat cost", el("span", { class: "flex items-center", style: { gap: "7px" } }, [
            el("span", { text: fmt.usd(fix.newCost) + "/t" }),
            toned(susd(fix.dCost), fix.dCost)
          ])],
          fix.dThb === null ? null
            : ["Flour cost effect", toned(sthb(fix.dThb) + "/kg", fix.dThb)]
        ]));
        if (fix.dThb !== null) {
          fixKids.push(reconstructed(
            "Worked out here, not supplied. The protein and $/t figures are arithmetic on the stated mix. " +
            "The workbook gives no extraction rate, so the " + sthb(fix.dThb) +
            "/kg flour effect applies this grade's own observed " + fmt.n(fix.mult, 2) +
            "× wheat-to-flour cost multiple at ฿" + fmt.n(FX, 2) + "/USD."));
        }
      } else {
        /* Each of these says exactly what is wrong. Saying "it cannot be closed
           by a single swap" when the real problem is that the mix does not add
           up to the stated blend protein would be a guess dressed as a finding. */
        fixKids.push(el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" } },
          fix.reason === "mix-disagrees"
            ? "The workbook marks this grade below its " + fmt.n(g.protein_min, 2) +
              "% minimum, but its stated mix averages " + fmt.n(fix.calcP, 2) +
              "% protein against a stated blend protein of " + fmt.n(g.blend_protein, 2) +
              "%. The two do not agree, so no fix is derived from them here — the " +
              "disagreement itself is the thing to take back to the blend sheet."
            : fix.reason === "no-shortfall"
            ? "The workbook marks this grade below its " + fmt.n(g.protein_min, 2) +
              "% minimum, but its own mix averages " + fmt.n(fix.calcP, 2) +
              "% protein, which clears it. The flag and the recipe disagree; that is " +
              "reported rather than reconciled here."
            : fix.reason === "one-protein"
            ? "This blend prices out " + fmt.n(fix.short, 2) + " protein points under the " +
              fmt.n(g.protein_min, 2) + "% minimum. Every class in the stated mix carries the " +
              "same protein, so there is no stronger class to shift into — the shortfall needs " +
              "a class this recipe does not contain."
            : "This blend prices out " + fmt.n(fix.short, 2) + " protein points under the " +
              fmt.n(g.protein_min, 2) + "% minimum. Lifting " + fix.hi.cls + " at the expense of " +
              fix.lo.cls + " is the obvious lever, but closing the gap would take more " +
              fix.lo.cls + " than the recipe has — the shortfall is stated here rather than a " +
              "precise fix invented."));
      }
      body.push(el("div", { style: { "margin-top": "14px" } }, [panel("warn", fixKids)]));
    }

    return GD.card({
      title: g.grade,
      sub: g.mix.length + " classes · minimum " + fmt.n(g.protein_min, 2) + "% protein",
      actions: [
        g.meets ? GD.chip("meets spec", "good", "check") : GD.chip("below protein minimum", "bad", "alert")
      ],
      body: body,
      foot: [
        /* Neutral highlight on purpose: this bar marks "you are here" among the
           four grades, not a verdict. Emerald would read as "good", and a grade
           being the dearest is not a fault — it is a different product. */
        GD.chart.barSpark(GRADE_COSTS, {
          take: GRADE_COSTS.length, highlight: ix, w: 68, h: 28, gap: 4,
          color: "var(--ink-2)"
        }),
        el("span", { text: perKg(g.flour_thb_kg) + " · " + rank + " of " + GRADES.length + " by flour cost, cheapest first" })
      ]
    });
  }

  function gradesSection() {
    return el("div", null, [
      el("div", { class: "flex items-center between", style: { gap: "12px", "flex-wrap": "wrap" } }, [
        el("div", null, [
          el("h2", { style: { "font-size": "var(--t-title)" }, text: "The blend bridge" }),
          el("p", {
            class: "card-sub",
            style: { "font-size": "var(--t-sm)", "max-width": "72ch" }
          }, "Each grade is a recipe over the same wheat book. The bar shows where each dollar " +
             "of blend wheat cost comes from; the legend gives the volume share of the mix.")
        ]),
        GD.chip(CLASS_COUNT + " classes · " + GRADES.length + " recipes", "outline", "layers")
      ]),
      /* A grade block carries a composition bar, a legend and a definition
         list, and one of them carries the blend fix on top of that. Four
         columns squeezes all of it and stretches the three short cards to the
         height of the tall one, so: two wide columns, each card its own
         height. */
      el("div", {
        class: "grid g-2",
        style: {
          "margin-top": "14px",
          "grid-template-columns": "repeat(auto-fit, minmax(430px, 1fr))",
          "align-items": "start"
        }
      }, GRADES.map(gradeCard))
    ]);
  }

  /* ------------------------------------------------------------ SKU detail */

  function skuKv(f) {
    var toQ1 = f.cost[2] - f.cost[0];
    var toQ2 = f.cost[3] - f.cost[0];
    return [
      GD.kv([
        ["List price", perKg(f.baht_kg)],
        ["Cost now · " + QUARTERS[0], perKg(f.cost_now)],
        ["Margin", el("span", { class: "flex items-center", style: { gap: "8px" } }, [
          toned(fmt.pct(f.margin, 1), f.margin, true),
          f.margin < 0 ? GD.chip("below cost", "bad", "alert") : null
        ])],
        ["Sold by", GD.chip(f.seller, f.seller === "UFM" ? "info" : null)]
      ]),
      GD.kv([
        [QUARTERS[2] + " cost", valueWithDelta(perKg(f.cost[2]), toQ1)],
        [QUARTERS[3] + " cost", valueWithDelta(perKg(f.cost[3]), toQ2)],
        ["Workbook suggested price", perKg(f.suggested)],
        ["Pack", f.pack]
      ])
    ];
  }

  function showSku(f, focusInChart) {
    GD.sheet({
      title: f.sku,
      sub: f.pack + " · sold by " + f.seller + " · Q426 Industrial Margin Analysis",
      body: function () {
        var avgSeries = AVG_COST.slice();
        var names = [
          { name: f.sku, color: "var(--c-futures)" },
          { name: "All-SKU average", color: "var(--c-residual)" }
        ];
        return [
          el("div", { class: "grid g-2", style: { "margin-bottom": "16px" } }, skuKv(f)),
          labelRow("Cost by quarter · baht per kilo",
            GD.chip(sthb(f.cost[3] - f.cost[0]) + " over four quarters", "outline")),
          GD.chart.groupedBars({
            groups: QUARTERS.map(function (q, qi) {
              return { label: q, values: [f.cost[qi], avgSeries[qi]] };
            }),
            names: names, w: 660, h: 220,
            fmtY: bahtAxis,
            fmtTip: function (v) { return perKg(v); },
            label: f.sku + " cost by quarter against the all-SKU average, baht per kilo"
          }),
          el("div", { style: { "margin-top": "10px" } }, [GD.legend(names)]),
          reconstructed("Costs, price and margin are the workbook's. The four-quarter deltas and the " +
            "all-SKU average on this chart are worked out here from those figures.")
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": "true",
            onclick: function () { close(); focusInChart(f.sku); }
          }, [icon("market", 14, 2), "Show in the quarter chart"]),
          el("button", { class: "btn", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Wheat is " + fmt.pct(WHEAT_SHARE, 0) + " of this cost line." })
        ];
      }
    });
  }

  /* ------------------------------------------------------------ price review */

  function openPriceReview() {
    var rows = BELOW.concat(THIN).sort(function (a, b) { return a.margin - b.margin; });

    GD.sheet({
      title: "Price review",
      sub: BELOW.length + " SKUs sell below their own cost, " + THIN.length +
           " more under a 5% margin. Q426 Industrial Margin Analysis.",
      body: function () {
        var table = GD.table({
          cols: [
            {
              key: "sku", label: "SKU",
              render: function (r) {
                return el("span", { class: "flex items-center", style: { gap: "9px" } }, [
                  GD.dot(r.margin < 0 ? "bad" : "warn"),
                  el("span", { style: { "font-weight": "600", color: "var(--ink)" }, text: r.sku })
                ]);
              }
            },
            {
              key: "seller", label: "Seller",
              render: function (r) { return GD.chip(r.seller, r.seller === "UFM" ? "info" : null); }
            },
            { key: "baht_kg", label: "List ฿/kg", num: true, render: function (r) { return fmt.thb(r.baht_kg); } },
            { key: "cost_now", label: "Cost now", num: true, render: function (r) { return fmt.thb(r.cost_now); } },
            {
              /* The dot beside the SKU is colour; the chip here is the label
                 that goes with it, so "below cost" never rests on hue alone. */
              key: "margin", label: "Margin", num: true,
              render: function (r) {
                return el("span", {
                  class: "flex items-center",
                  style: { gap: "7px", "justify-content": "flex-end" }
                }, [
                  r.margin < 0 ? GD.chip("below cost", "bad", "alert") : null,
                  toned(fmt.pct(r.margin, 1), r.margin, true)
                ]);
              }
            },
            {
              /* A required uplift is money off the mill's margin, so a positive
                 number is the bad one — GD.toneFor's default sense. */
              key: "uplift", label: "Uplift to cost", num: true,
              sortValue: function (r) { return r.cost_now / r.baht_kg - 1; },
              render: function (r) {
                var u = r.cost_now / r.baht_kg - 1;
                return toned(fmt.signedPct(u, 1), u);
              }
            },
            { key: "suggested", label: "Workbook suggested", num: true, render: function (r) { return fmt.thb(r.suggested); } }
          ],
          rows: rows,
          sortKey: "margin",
          sortDir: "asc"
        });

        return [
          el("div", { class: "card", style: { "box-shadow": "none", overflow: "hidden", "margin-bottom": "14px" } }, [
            el("div", { class: "card-body flush" }, [table])
          ]),
          panel(null, [
            el("div", { class: "flex items-center", style: { gap: "7px", "font-weight": "600", "font-size": "var(--t-sm)" } }, [
              icon("alert", 14, 2), el("span", { text: "What the suggested price does, and does not, do" })
            ]),
            el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" } },
              "Each workbook suggested price is the " + QUARTERS[2] + " cost held at today's margin — it " +
              "checks out on " + SUGGEST_HOLDS + " of " + FLOUR.length + " rows to within ฿0.01. For a SKU " +
              "already below cost that holds the loss steady against a rising cost line; it does not clear it. " +
              "The uplift column is the move needed to reach today's cost, which is where the margin turns zero."),
            reconstructed("Uplift to cost is worked out here — list price against the workbook's own cost now. " +
              "Everything else in this table is read from the workbook.")
          ])
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": "true",
            onclick: function () {
              close();
              GD.toast("Price review drafted — not sent", { icon: "file" });
            }
          }, [icon("file", 14, 2), "Draft the price review"]),
          el("button", { class: "btn", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Drafts only. Pavin sends." })
        ];
      }
    });
  }

  /* ------------------------------------------------------------ SKU table */

  function skuTable(focusInChart) {
    var cols = [
      {
        key: "sku", label: "SKU", width: "20%",
        render: function (r) {
          var loss = r.margin < 0;
          return el("div", {
            style: {
              display: "flex", "align-items": "center", gap: "9px",
              "margin-left": "-12px", "padding": "3px 0 3px 9px",
              "border-left": "3px solid " + (loss ? "var(--up)" : "transparent")
            }
          }, [
            GD.dot(loss ? "bad" : r.margin < 0.05 ? "warn" : "good"),
            el("span", { style: { "font-weight": "600", color: "var(--ink)" }, text: r.sku })
          ]);
        }
      },
      { key: "pack", label: "Pack" },
      {
        key: "seller", label: "Seller",
        render: function (r) { return GD.chip(r.seller, r.seller === "UFM" ? "info" : null); }
      },
      {
        key: "baht_kg", label: "฿/kg", num: true,
        render: function (r) { return fmt.thb(r.baht_kg); }
      },
      {
        key: "margin", label: "Margin", num: true,
        render: function (r) {
          return el("span", { class: "flex items-center", style: { gap: "7px", "justify-content": "flex-end" } }, [
            r.margin < 0 ? GD.chip("below cost", "bad", "alert") : null,
            toned(fmt.pct(r.margin, 1), r.margin, true)
          ]);
        }
      },
      {
        key: "cost_now", label: "Cost now", num: true,
        render: function (r) { return fmt.thb(r.cost_now); }
      },
      {
        key: "cost_q1", label: QUARTERS[2] + " cost", num: true,
        sortValue: function (r) { return r.cost[2]; },
        render: function (r) { return fmt.thb(r.cost[2]); }
      },
      {
        key: "incr_q1", label: "Increase", num: true,
        render: function (r) {
          return el("span", { class: "flex items-center", style: { gap: "6px", "justify-content": "flex-end" } }, [
            toned(sthb(r.incr_q1), r.incr_q1),
            el("span", {
              style: { color: "var(--ink-3)", "font-size": "var(--t-xs)" },
              text: "(" + fmt.pct(r.incr_pct, 1) + ")"
            })
          ]);
        }
      },
      {
        key: "suggested", label: "Suggested", num: true,
        render: function (r) { return fmt.thb(r.suggested); }
      }
    ];

    var table = GD.table({
      cols: cols,
      rows: FLOUR,
      sortKey: "margin",
      sortDir: "asc",
      /* No rowClass: app.css styles `tbody tr.is-selected` and nothing else, so
         a loss class would be a no-op. The loss state is carried in the SKU
         cell's own left rail, its dot and the "below cost" chip instead. */
      onRow: function (r) { showSku(r, focusInChart); }
    });

    return GD.card({
      title: "Flour SKUs",
      sub: FLOUR.length + " SKUs across UFM and MCF · sorted thinnest margin first, so the loss-makers surface",
      actions: [
        GD.chip(BELOW.length + " below cost", "bad", "alert"),
        el("button", {
          class: "btn btn-sm", type: "button",
          onclick: openPriceReview
        }, [icon("file", 12, 1.75), "Price review"])
      ],
      flush: true,
      body: [table],
      foot: [
        el("span", { class: "flex items-center", style: { gap: "7px" } }, [
          GD.dot("bad"), el("span", { text: "below cost" })
        ]),
        el("span", { class: "flex items-center", style: { gap: "7px" } }, [
          GD.dot("warn"), el("span", { text: "under 5% margin" })
        ]),
        el("span", { class: "flex items-center", style: { gap: "7px" } }, [
          GD.dot("good"), el("span", { text: "clear" })
        ]),
        el("span", { style: { flex: "1" } }),
        el("span", { text: "Any column sorts. A row opens the SKU." })
      ]
    });
  }

  /* ------------------------------------------------------------ quarter chart */

  function quarterSection() {
    var bySku = {};
    FLOUR.forEach(function (f) { bySku[f.sku] = f; });

    /* The route re-renders on every navigation, and a surface can legitimately
       be rendered into a second host (the probe does). A fixed id would collide
       and leave `label[for]` pointing at two nodes, so it is unique per render.
       No aria-label: the visible <label> below is the accessible name, and an
       aria-label would override the text the user can actually see. */
    var selectId = "margin-sku-" + (++SEQ);
    var select = el("select", { class: "field", id: selectId }, [
      el("option", { value: AVG_KEY, text: "All " + FLOUR.length + " SKUs — average" })
    ].concat(FLOUR.map(function (f) {
      return el("option", { value: f.sku, text: f.sku + " · " + f.pack });
    })));

    var chartHost = el("div");
    var summary = el("div");

    function draw() {
      var key = select.value;
      var f = key === AVG_KEY ? null : bySku[key];
      var names = f
        ? [{ name: f.sku, color: "var(--c-futures)" }, { name: "All-SKU average", color: "var(--c-residual)" }]
        : [{ name: "All-SKU average", color: "var(--c-futures)" }];
      var groups = QUARTERS.map(function (q, qi) {
        return { label: q, values: f ? [f.cost[qi], AVG_COST[qi]] : [AVG_COST[qi]] };
      });

      GD.clear(chartHost);
      chartHost.appendChild(GD.chart.groupedBars({
        groups: groups, names: names, w: 660, h: 250,
        fmtY: bahtAxis,
        fmtTip: function (v) { return perKg(v); },
        label: (f ? f.sku : "All-SKU average") + " flour cost by quarter, baht per kilo"
      }));
      chartHost.appendChild(el("div", { style: { "margin-top": "10px" } }, [GD.legend(names)]));

      var series = f ? f.cost : AVG_COST;
      var toQ1 = series[2] - series[0];
      var toQ2 = series[3] - series[0];
      GD.clear(summary);
      summary.appendChild(GD.kv([
        [(f ? f.sku : "All-SKU average") + " · " + QUARTERS[0], perKg(series[0])],
        [QUARTERS[2], valueWithDelta(perKg(series[2]), toQ1)],
        [QUARTERS[3], valueWithDelta(perKg(series[3]), toQ2)],
        ["Four-quarter move", el("span", { class: "flex items-center", style: { gap: "7px" } }, [
          toned(sthb(toQ2), toQ2),
          toned("(" + fmt.signedPct(series[0] ? series[3] / series[0] - 1 : 0, 1) + ")", toQ2)
        ])],
        f ? ["List price", perKg(f.baht_kg)] : null,
        f ? ["Margin", toned(fmt.pct(f.margin, 1), f.margin, true)] : null
      ]));
    }

    select.addEventListener("change", draw);
    draw();

    function focusInChart(sku) {
      select.value = sku;
      draw();
      if (select.scrollIntoView) select.scrollIntoView({ block: "center" });
      if (select.focus) select.focus();
      GD.toast(sku + " shown in the quarter chart", { icon: "market" });
    }

    var chartCard = GD.card({
      title: "Cost by quarter",
      sub: "Zero-based on purpose — a truncated baseline would exaggerate a few percent of carry.",
      actions: [
        el("label", { class: "label", for: selectId, text: "SKU" }),
        select
      ],
      body: [chartHost],
      foot: [
        icon("file", 12, 1.5),
        el("span", { text: "Four-quarter cost line from the Q426 Industrial Margin Analysis workbook." })
      ]
    });

    var sideCard = GD.card({
      title: "What the quarters say",
      sub: "The selected series on the left, read against the whole book.",
      body: [
        summary,
        hairline(14, 12),
        labelRow("Where mill cost comes from"),
        el("div", { class: "meter-track" }, [
          el("div", { class: "meter-fill", style: { width: (WHEAT_SHARE * 100).toFixed(0) + "%" } })
        ]),
        el("div", {
          class: "flex items-center between",
          style: { gap: "10px", "margin-top": "7px", "font-size": "var(--t-xs)", color: "var(--ink-3)" }
        }, [
          el("span", null, [
            el("b", { style: { color: "var(--ink)" }, text: fmt.pct(WHEAT_SHARE, 0) }),
            el("span", { text: " wheat" })
          ]),
          el("span", { text: fmt.pct(1 - WHEAT_SHARE, 0) + " everything else" })
        ]),
        hairline(13, 12),
        GD.kv([
          ["Below cost today", el("span", { class: "flex items-center", style: { gap: "8px" } }, [
            el("span", { text: BELOW.length + " of " + FLOUR.length }),
            GD.chip(BELOW.length + " to re-price", "bad", "alert")
          ])],
          ["Below cost on " + QUARTERS[2] + " costs", el("span", { class: "flex items-center", style: { gap: "8px" } }, [
            el("span", { text: BELOW_BY_Q1.length + " of " + FLOUR.length }),
            GD.chip("at today's prices", "warn", "clock")
          ])]
        ]),
        reconstructed("The second count is worked out here: SKUs whose " + QUARTERS[2] +
          " cost already stands at or above today's list price. The workbook does not state it.")
      ]
    });

    var host = el("div", { class: "grid g-main" }, [chartCard, sideCard]);
    /* Handed back so the SKU sheet can drive this chart from elsewhere on the page. */
    host.focusSku = focusInChart;
    return host;
  }

  /* ------------------------------------------------------------ method note */

  function noteRow(ico, title, chipNode, text, meta) {
    return el("div", { class: "note-row" }, [
      el("span", { style: { color: "var(--ink-4)", flex: "none", "margin-top": "2px" } }, [icon(ico, 16, 1.75)]),
      el("div", { class: "nr-body" }, [
        el("div", { class: "nr-head" }, [
          el("h4", { text: title }),
          chipNode || null
        ]),
        el("p", null, text),
        meta ? el("div", { class: "nr-meta", text: meta }) : null
      ])
    ]);
  }

  function methodCard() {
    var moveUsd = D.profile.economics.move_usd_mt;
    var cargo = D.profile.economics.cargo_mt;

    var quotesBtn = GD.routes()["quotes"]
      ? el("button", {
          class: "btn btn-sm", type: "button",
          onclick: function () { GD.go("quotes"); }
        }, [icon("quotes", 12, 1.75), "Open the quote board"])
      : null;

    return GD.card({
      title: "How to read this screen",
      sub: "What is the workbook's, what is this desk's arithmetic, and why the two cost lines are the same argument.",
      flush: true,
      body: [
        noteRow("scale", "Wheat is " + fmt.pct(WHEAT_SHARE, 0) + " of mill cost",
          GD.chip("the whole point", "accent", "zap"),
          "So the four-quarter cost line in this workbook is largely a restatement of what the quote " +
          "board decides. Win " + fmt.usd(moveUsd, 0) + "/t on a " + fmt.n(cargo) + " t cargo — " +
          fmt.money(moveUsd * cargo) + " — and most of it lands on this page as margin. The blend " +
          "recipe decides which grade it lands on.",
          "Mill economics · wheat_share_of_cost"),
        noteRow("file", "These SKUs, prices and margins are real",
          GD.chip("Q426 workbook", "outline", "check"),
          "All " + FLOUR.length + " SKUs, their packs, baht-per-kilo list prices, four-quarter costs, " +
          "margins and suggested prices are read from the Q426 Industrial Margin Analysis workbook, as " +
          "are the " + GRADES.length + " blend recipes and their protein minimums. None of it is modelled.",
          "Source: Q426 Industrial Margin Analysis · desk data built " + fmt.date(D.built)),
        noteRow("sparkle", "What this desk works out for itself",
          GD.chip("reconstructed", "warn", "alert"),
          "The blend fix on the " + (FAILING.length ? shortName(FAILING[0].grade) : "failing") +
          " grade, the wheat-to-flour cost multiple behind it, the uplift-to-cost column in the price " +
          "review and the count of SKUs that go below cost on the " + QUARTERS[2] +
          " cost line are all arithmetic done here. Each one is marked on screen where it appears.",
          "Marked with the four-point glyph throughout"),
        noteRow("alert", "Two checks run against the workbook",
          GD.chip(SUGGEST_HOLDS + "/" + FLOUR.length + " · " + MARGIN_TIES_COST + "/" + FLOUR.length, "info", "check"),
          "The suggested price is the " + QUARTERS[2] + " cost held at today's margin: that reproduces " +
          SUGGEST_HOLDS + " of " + FLOUR.length + " rows to within ฿0.01. Margin as price less cost-now over " +
          "price reproduces " + MARGIN_TIES_COST + " of " + FLOUR.length + " — so margin is shown exactly as " +
          "the workbook gives it, never recomputed, and the " + (FLOUR.length - MARGIN_TIES_COST) +
          " rows that disagree are worth a question before the next price list goes out.",
          "Checked on load, against every row")
      ],
      foot: [
        el("span", { text: "Wheat at the vessel → blend recipe → baht per kilo at the customer." }),
        el("span", { style: { flex: "1" } }),
        quotesBtn
      ]
    });
  }

  /* ------------------------------------------------------------ route */

  GD.route("margin", {
    label: "Blend & margin",
    icon: "margin",
    group: "Operations",
    badge: function () { return BELOW.length; },
    badgeTone: "bad",
    render: function (wrap) {
      var quarters = quarterSection();
      var focusInChart = quarters.focusSku;

      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Blend & margin" }),
          el("p", { class: "page-sub" },
            "From wheat at the vessel to baht per kilo at the customer — the step the quote board stops short of.")
        ]),
        el("div", { class: "ph-actions" }, [
          GD.chip("Q426 Industrial Margin Analysis", "outline", "file"),
          el("button", {
            class: "btn btn-primary", type: "button",
            onclick: openPriceReview
          }, [icon("alert", 14, 2), "Price review · " + BELOW.length])
        ])
      ]));

      wrap.appendChild(statRow(openPriceReview));
      wrap.appendChild(gradesSection());
      wrap.appendChild(skuTable(focusInChart));
      wrap.appendChild(quarters);
      wrap.appendChild(methodCard());
    }
  });

})();
