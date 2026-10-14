/* app/js/surfaces/market.js — Market, drivers and the WASDE clock.

   The Watch's surface. Three questions, in this order:
     1. where are the six prices that make up a landed tonne, and which way did they move
     2. why — Pavin's own causal model, with his intel hung off it
     3. when — the release clock, because the verdict is worth nothing eight hours late

   Direction is always read from the buyer's side: every instrument on this page is a cost to
   the mill, so a fall is emerald and a rise is coral. Series colours in the big chart are
   categorical (blue / amber / brick) precisely so they cannot be mistaken for direction.

   No listeners on document or window: every handler is bound to a node this surface builds,
   so a route change drops them all with the nodes. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, chip = GD.chip, dot = GD.dot;
  var D = window.DESK;

  var CARGO = D.profile.economics.cargo_mt;         /* 55,000 t */
  var CHART_W = 1080, CHART_H = 268;

  /* Categorical, and deliberately neither emerald nor coral. */
  var SERIES_COLOR = {
    MWZ26: "var(--c-futures)",
    KWZ26: "var(--c-freight)",
    ZWZ26: "var(--c-margin)"
  };

  /* ------------------------------------------------------------------ data */

  function instrument(key) {
    for (var i = 0; i < D.instruments.length; i++) {
      if (D.instruments[i].key === key) return D.instruments[i];
    }
    return null;
  }
  function byRole(hedge) {
    return D.instruments.filter(function (i) {
      return hedge ? i.role === "hedge" : i.role !== "hedge";
    });
  }
  function driver(key) {
    for (var i = 0; i < D.drivers.length; i++) {
      if (D.drivers[i].key === key) return D.drivers[i];
    }
    return { key: key, label: key, feeds: "price" };
  }

  /* Pavin's notes carry a magnitude; the stance carries the sign. Bearish pulls price
     down, which is the good direction for a buyer, so it signs negative. */
  function signedTonne(note) { return note.stance === "bearish" ? -note.usd_mt : note.usd_mt; }

  function driverStats() {
    var map = {};
    D.drivers.forEach(function (d) {
      map[d.key] = { key: d.key, label: d.label, feeds: d.feeds, n: 0, bear: 0, bull: 0, net: 0 };
    });
    D.intel.forEach(function (note) {
      var m = map[note.driver];
      if (!m) m = map[note.driver] = { key: note.driver, label: note.driver, feeds: "price", n: 0, bear: 0, bull: 0, net: 0 };
      m.n += 1;
      if (note.stance === "bearish") m.bear += note.usd_mt; else m.bull += note.usd_mt;
      m.net = m.bull - m.bear;
    });
    return map;
  }

  /* ------------------------------------------------------------ formatting */

  function dpFor(unit) { return unit === "USD/day" ? 0 : unit === "THB" ? 3 : 2; }
  function lastOf(ins) { return fmt.n(ins.last, dpFor(ins.unit)); }
  function monthChange(ins) { return ins.last / ins.month_ago - 1; }
  function roleIcon(ins) { return ins.role === "fx" ? "globe" : ins.role === "freight" ? "ship" : "wheat"; }
  function toneColor(v) { return v < 0 ? "var(--down)" : v > 0 ? "var(--up)" : "var(--ink-4)"; }
  function dotTone(v) { return v < 0 ? "good" : v > 0 ? "bad" : ""; }

  function rangeOf(vals) {
    var e = GD.chart.extent(vals);
    return { lo: e[0], hi: e[1] };
  }
  function whereInRange(v, lo, hi) {
    if (hi <= lo) return 0;
    return (v - lo) / (hi - lo);
  }
  /* The notes are date-only, so fmt.ago's "today 09:23" would invent a clock time. */
  function whenWords(date) { return date === D.built ? "today" : fmt.ago(date); }

  function rangeWords(pos) {
    if (pos <= 0.005) return "sitting on the low";
    if (pos >= 0.995) return "sitting on the high";
    return fmt.pct(pos, 0) + " up off the low";
  }
  /* c/bu -> $/t, using the ratio the dataset already states rather than a constant. */
  function tonneFactor(ins) { return ins.usd_mt ? ins.usd_mt / ins.last : null; }

  function stanceLabel(net) { return net < 0 ? "Bearish" : net > 0 ? "Bullish" : "Balanced"; }
  function stanceTone(net) { return net < 0 ? "good" : net > 0 ? "bad" : ""; }
  function stanceIcon(net) { return net < 0 ? "down" : net > 0 ? "up" : "sort"; }

  function sub(text) { return el("div", { class: "card-sub", style: { "margin-top": "0" }, text: text }); }
  function spacer() { return el("span", { style: { flex: "1" } }); }
  function sep() { return el("span", { style: { color: "var(--ink-4)" }, text: "·" }); }
  function tipRow(k, v) {
    return "<div class='tip-row'><span class='tip-mute'>" + GD.esc(k) + "</span><b>" + GD.esc(v) + "</b></div>";
  }

  function reveal(node) {
    if (!node || !node.scrollIntoView) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    node.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
  }

  function replayBtn(cls, label) {
    var b = el("button", {
      class: cls, type: "button",
      onclick: function () {
        if (GD.nightWatch) GD.nightWatch();
        else GD.toast("Night Watch is not loaded", { icon: "alert" });
      }
    }, [icon("zap", 14, 2), label]);
    GD.tipOn(b, "Replay the 9 Oct release exactly as it landed — verdict, per-cargo impact and the drafted counter");
    return b;
  }

  /* =================================================================== page */

  function render(wrap) {
    var DS = driverStats();

    var state = {
      range: 90,
      shown: { MWZ26: true, KWZ26: true, ZWZ26: true },
      single: null,        /* an FX or freight key: its unit cannot share an axis with c/bu */
      stance: "all",
      driver: null
    };

    var cards = [];        /* {ins, node, chip} — the instrument tiles */
    var flowBtns = [];     /* {key, node} — the causal-model nodes */
    var chartCard, chartHost, togglesHost, legendHost, rangeHost;
    var feedCard, feedHost, filterHost, countHost;
    var boardTable, boardRows, boardMax;

    /* ------------------------------------------------------------ 1. head */

    wrap.appendChild(el("div", { class: "page-head" }, [
      el("div", { class: "ph-text" }, [
        el("h1", { class: "page-title", text: "Market" }),
        el("p", { class: "page-sub", text: "Futures, FX and freight — and the ten levers Pavin actually watches" })
      ]),
      el("div", { class: "ph-actions" }, [
        chip("Settled " + fmt.date(D.built), "outline", "calendar"),
        replayBtn("btn btn-primary", "Replay WASDE")
      ])
    ]));

    /* ----------------------------------------------- 2. instrument tiles */

    function tipFor(ins) {
      var r = rangeOf(ins.history);
      var dp = dpFor(ins.unit);
      var s = "<b>" + GD.esc(ins.key + " · " + ins.label) + "</b>" +
        tipRow("venue", ins.venue) +
        tipRow("last", lastOf(ins) + " " + ins.unit) +
        tipRow("prev close", fmt.n(ins.prev, dp));
      if (ins.usd_mt) s += tipRow("per tonne", fmt.usd(ins.usd_mt) + "/t");
      s += tipRow("month ago", fmt.n(ins.month_ago, dp)) +
        tipRow("180-day range", fmt.n(r.lo, dp) + " – " + fmt.n(r.hi, dp)) +
        tipRow("in that range", rangeWords(whereInRange(ins.last, r.lo, r.hi)));
      if (ins.classes.length) {
        s += "<div class='tip-mute' style='margin-top:5px'>Prices " + GD.esc(ins.classes.join(", ")) + ".</div>";
      }
      s += "<div class='tip-mute' style='margin-top:5px'>" +
        (ins.synthetic_history ? "Levels are real; the daily path is reconstructed. " : "") +
        "Click to isolate it in the chart.</div>";
      return s;
    }

    function instrumentCard(ins) {
      var mc = monthChange(ins);
      var dp = dpFor(ins.unit);

      /* Where are we against the last month — the question a bar-spark answers best. */
      var bars = GD.chart.barSpark(ins.history, {
        take: 28, w: 112, h: 40, gap: 1,
        highlight: Math.min(28, ins.history.length) - 1,
        color: toneColor(mc), muted: "var(--border-2)",
        label: fmt.signedPct(mc, 1)
      });
      /* The highlighted bar's label sits at the right edge, so let it breathe into the card
         padding; and let the whole spark shrink with the tile instead of being clipped by it
         when the value beside it is long (13,831 USD/day on a narrow column). */
      bars.style.setProperty("overflow", "visible");
      bars.style.setProperty("min-width", "0");
      bars.style.setProperty("height", "auto");

      var mark = chip("In chart", "accent", "check");
      mark.style.setProperty("display", "none");

      var node = GD.stat({
        label: ins.key + " · " + ins.label,
        value: lastOf(ins),
        unit: ins.unit,
        icon: roleIcon(ins),
        aside: bars,
        delta: [
          dot(dotTone(ins.change)),
          el("b", { class: GD.toneFor(ins.change), text: fmt.signed(ins.change, dp) }),
          el("span", { class: GD.toneFor(ins.change), text: fmt.signedPct(ins.change_pct / 100) }),
          sep(),
          el("span", { text: ins.usd_mt ? fmt.usd(ins.usd_mt) + "/t" : ins.venue }),
          spacer(),
          mark
        ],
        onClick: function () { pick(ins); }
      });
      node.setAttribute("aria-pressed", "false");
      /* .stat-top is an auto-track grid, so a wide value ("13,831 USD/day") plus the spark
         sizes the track to max-content and the row spills past the card, which then clips it.
         Pinning the track to minmax(0,1fr) lets the spark shrink instead of being cut. */
      var top = node.querySelector(".stat-top");
      if (top) top.style.setProperty("grid-template-columns", "minmax(0, 1fr)");
      GD.tipOn(node, function () { return tipFor(ins); });
      cards.push({ ins: ins, node: node, chip: mark });
      return node;
    }

    function instrumentSection(title, caption, list) {
      var grid = el("div", { class: "grid g-3" }, list.map(instrumentCard));
      return el("div", { style: { display: "grid", gap: "10px" } }, [
        el("div", { style: { display: "flex", "align-items": "baseline", gap: "10px", "flex-wrap": "wrap" } }, [
          el("div", { class: "label", text: title }),
          sub(caption)
        ]),
        grid
      ]);
    }

    wrap.appendChild(instrumentSection(
      "Hedge instruments",
      "The three contracts the five wheat classes price against. Every line here is a cost to the mill, so a fall is emerald.",
      byRole(true)
    ));
    wrap.appendChild(instrumentSection(
      "FX and freight",
      "The other legs of a CFR landed price. Last, previous close and the month-ago level are real; the daily path between them is reconstructed.",
      byRole(false)
    ));

    /* ---------------------------------------------------- 3. the big chart */

    function colorFor(ins) { return SERIES_COLOR[ins.key] || "var(--c-residual)"; }
    function shownCount() {
      return byRole(true).filter(function (i) { return state.shown[i.key]; }).length;
    }
    function setAll(on) {
      byRole(true).forEach(function (i) { state.shown[i.key] = on; });
    }
    function chartSeries() {
      if (state.single) {
        var one = instrument(state.single);
        return one ? [one] : [];
      }
      return byRole(true).filter(function (i) { return state.shown[i.key]; });
    }

    /* A card click isolates that series. Clicking an already-isolated hedge restores all
       three; clicking FX or freight hands the chart its own axis, because cents a bushel
       and baht to the dollar have no business sharing one. */
    function pick(ins) {
      if (ins.role === "hedge") {
        var isolated = !state.single && state.shown[ins.key] && shownCount() === 1;
        state.single = null;
        if (isolated) setAll(true);
        else {
          setAll(false);
          state.shown[ins.key] = true;
        }
      } else {
        state.single = state.single === ins.key ? null : ins.key;
        if (!state.single) setAll(true);
      }
      drawChart();
      reveal(chartCard);
    }

    function toggleBtn(ins) {
      var on = !!state.shown[ins.key];
      var swatch = el("i", {
        style: {
          width: "9px", height: "9px", "border-radius": "3px", flex: "none", display: "inline-block",
          "background-color": on ? colorFor(ins) : "transparent",
          "box-shadow": on ? "none" : "inset 0 0 0 1.5px " + colorFor(ins)
        }
      });
      var mark = icon(on ? "check" : "x", 12, 2);
      mark.style.setProperty("color", on ? "var(--accent)" : "var(--ink-4)");
      var b = el("button", {
        class: "btn btn-sm", type: "button", "aria-pressed": String(on),
        onclick: function () { state.shown[ins.key] = !on; state.single = null; drawChart(); }
      }, [
        swatch,
        el("span", {
          text: ins.key,
          style: { color: on ? "var(--ink)" : "var(--ink-3)", "font-weight": on ? "600" : "500" }
        }),
        mark
      ]);
      GD.tipOn(b, (on ? "Hide " : "Show ") + GD.esc(ins.label));
      return b;
    }

    function drawChart() {
      var list = chartSeries();
      var n = state.range;

      /* --- controls */
      GD.clear(togglesHost);
      if (state.single) {
        var one = instrument(state.single);
        togglesHost.appendChild(chip(one.key + " · " + one.unit, "info", roleIcon(one)));
        togglesHost.appendChild(sub("On its own axis — this unit will not share one with cents a bushel."));
        togglesHost.appendChild(spacer());
        togglesHost.appendChild(el("button", {
          class: "btn btn-sm", type: "button",
          onclick: function () { state.single = null; setAll(true); drawChart(); }
        }, [icon("layers", 12, 2), "Back to the hedge trio"]));
      } else {
        byRole(true).forEach(function (i) { togglesHost.appendChild(toggleBtn(i)); });
        togglesHost.appendChild(spacer());
        togglesHost.appendChild(el("button", {
          class: "btn btn-sm btn-ghost", type: "button",
          disabled: shownCount() === 3 ? true : null,
          onclick: function () { setAll(true); drawChart(); }
        }, [icon("layers", 12, 2), "Show all three"]));
      }

      /* --- chart */
      GD.clear(chartHost);
      if (!list.length) {
        chartHost.appendChild(el("div", { class: "empty" }, [
          icon("eye", 20, 1.5),
          el("div", { text: "Every series is hidden." }),
          el("button", {
            class: "btn btn-sm", type: "button",
            onclick: function () { setAll(true); drawChart(); }
          }, [icon("layers", 12, 2), "Show all three"])
        ]));
      } else {
        var unit = list.length === 1 ? list[0].unit : "c/bu";
        chartHost.appendChild(GD.chart.line({
          w: CHART_W, h: CHART_H,
          dates: list[0].dates.slice(-n),
          series: list.map(function (i) {
            return { name: i.key, ins: i, values: i.history.slice(-n), color: colorFor(i) };
          }),
          zero: false,
          fmtY: function (v) { return fmt.n(v, unit === "USD/day" ? 0 : unit === "THB" ? 3 : 0); },
          fmtTip: function (v, s) {
            var i = s.ins, f = tonneFactor(i);
            var txt = fmt.n(v, dpFor(i.unit)) + " " + i.unit;
            if (f) txt += "  ·  " + fmt.usd(v * f) + "/t";
            return txt;
          },
          label: "Daily closes in " + unit + " for " +
            list.map(function (i) { return i.label; }).join(", ") + ", last " + n + " days"
        }));
      }

      /* --- legend */
      GD.clear(legendHost);
      legendHost.appendChild(GD.legend(list.map(function (i) {
        return { name: i.key + " · " + i.label, color: colorFor(i) };
      })));
      legendHost.appendChild(sub("Series colours are categorical. Emerald and coral are held back for direction."));

      /* --- where we are in the window on screen */
      GD.clear(rangeHost);
      list.forEach(function (i) {
        var vals = i.history.slice(-n);
        var r = rangeOf(vals), dpi = dpFor(i.unit);
        rangeHost.appendChild(el("div", null, [
          el("div", { class: "k", text: i.key + " · " + n + "-day range" }),
          el("div", { class: "v", text: fmt.n(r.lo, dpi) + " – " + fmt.n(r.hi, dpi) }),
          el("div", { class: "card-sub" }, [
            el("span", { text: "last " + fmt.n(i.last, dpi) + " · " }),
            el("span", {
              class: GD.toneFor(whereInRange(i.last, r.lo, r.hi) - 0.5),
              text: rangeWords(whereInRange(i.last, r.lo, r.hi))
            })
          ])
        ]));
      });
      if (!list.length) {
        rangeHost.appendChild(el("div", null, [
          el("div", { class: "k", text: "Range" }),
          el("div", { class: "v", text: "—" }),
          el("div", { class: "card-sub", text: "nothing selected" })
        ]));
      }

      syncCards();
    }

    function syncCards() {
      var on = {};
      chartSeries().forEach(function (i) { on[i.key] = true; });
      cards.forEach(function (c) {
        var lit = !!on[c.ins.key];
        c.node.setAttribute("aria-pressed", String(lit));
        c.chip.style.setProperty("display", lit ? "inline-flex" : "none");
        c.node.style.setProperty("border-color", lit ? "var(--accent-line)" : "");
        c.node.style.setProperty("box-shadow", lit ? "var(--sh-md)" : "");
      });
    }

    togglesHost = el("div", {
      style: { display: "flex", "align-items": "center", gap: "8px", "flex-wrap": "wrap", padding: "14px 16px 0" }
    });
    chartHost = el("div", { style: { padding: "6px 16px 2px" } });
    legendHost = el("div", {
      style: { display: "flex", "align-items": "baseline", gap: "14px", "flex-wrap": "wrap", padding: "0 16px 14px" }
    });
    rangeHost = el("div", { class: "statline", style: { "border-top": "1px solid var(--border)" } });

    chartCard = GD.card({
      title: "The hedge curve",
      sub: "Daily closes. Click any tile above to isolate it; a cargo is priced off one of these three.",
      actions: [GD.seg([
        { key: "30", label: "30d" }, { key: "90", label: "90d" }, { key: "180", label: "180d" }
      ], "90", function (k) { state.range = Number(k); drawChart(); })],
      flush: true,
      body: [togglesHost, chartHost, legendHost, rangeHost],
      foot: [
        icon("alert", 13, 1.75),
        el("span", { text: "Every instrument carries synthetic_history: the levels, previous close and month-ago figures are real, the daily path between them is reconstructed. Read the shape as indicative and the levels as firm." })
      ]
    });
    wrap.appendChild(chartCard);

    /* ------------------------------------------- 4. how price is determined */

    var NODE_STYLE = {
      height: "auto", padding: "9px 11px", "flex-direction": "column", "align-items": "flex-start",
      "justify-content": "flex-start", gap: "6px", "white-space": "normal", "min-width": "126px",
      "font-weight": "500", "text-align": "left"
    };

    function nodeKids(st) {
      var meta;
      if (st.n) {
        meta = el("div", { style: { display: "flex", "align-items": "center", gap: "5px", "flex-wrap": "wrap" } }, [
          chip(st.n === 1 ? "1 note" : st.n + " notes", "outline"),
          chip(stanceLabel(st.net), stanceTone(st.net), stanceIcon(st.net))
        ]);
      } else {
        meta = el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" }, text: "no notes" });
      }
      return [
        el("div", {
          style: { "font-size": "var(--t-sm)", "font-weight": "600", "letter-spacing": "-0.01em", color: "var(--ink)" },
          text: st.label
        }),
        meta
      ];
    }

    function flowNode(key) {
      var d = driver(key);
      var st = DS[key] || { key: key, label: d.label, n: 0, net: 0, bear: 0, bull: 0 };
      if (!st.n) {
        return el("div", {
          style: {
            display: "inline-flex", "flex-direction": "column", "align-items": "flex-start", gap: "6px",
            padding: "9px 11px", "min-width": "126px",
            border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
            background: "var(--surface)", "font-size": "var(--t-sm)"
          }
        }, nodeKids(st));
      }
      var b = el("button", {
        class: "btn", type: "button", style: NODE_STYLE,
        "aria-pressed": String(state.driver === key),
        onclick: function () { setDriver(state.driver === key ? null : key); }
      }, nodeKids(st));
      GD.tipOn(b, "<b>" + GD.esc(st.label) + "</b>" +
        tipRow("feeds", driver(key).feeds === "price" ? "price, directly" : driver(driver(key).feeds).label) +
        tipRow("notes", String(st.n)) +
        tipRow("bearish pull", fmt.usd(st.bear, 2) + "/t") +
        tipRow("bullish pull", fmt.usd(st.bull, 2) + "/t") +
        tipRow("net", fmt.signed(st.net, 2) + "/t") +
        "<div class='tip-mute' style='margin-top:5px'>Click to filter the intel feed.</div>");
      flowBtns.push({ key: key, node: b });
      return b;
    }

    function arrow(down, note) {
      return el("div", {
        style: {
          display: "flex", "align-items": "center", gap: "6px", flex: "none",
          color: "var(--ink-4)", "font-size": "var(--t-xs)"
        }
      }, [icon(down ? "chevD" : "chevR", 15, 1.75), note ? el("span", { text: note }) : null]);
    }

    function chain(keys) {
      /* stretch, not center: nodes in a chain carry different amounts of meta and a
         centred row reads as ragged */
      var row = el("div", { style: { display: "flex", "align-items": "stretch", gap: "7px", "flex-wrap": "wrap" } });
      keys.forEach(function (k, i) {
        if (i) row.appendChild(arrow(false, null));
        row.appendChild(flowNode(k));
      });
      return row;
    }

    function groupBox(title, flex, caption, kids) {
      return el("div", {
        style: {
          flex: flex, "min-width": "0",
          background: "var(--surface-2)", border: "1px solid var(--border)",
          "border-radius": "var(--r-inner)", padding: "12px",
          display: "grid", gap: "11px", "align-content": "start"
        }
      }, [
        el("div", null, [el("div", { class: "label", text: title }), el("div", { class: "card-sub", text: caption })]),
        kids
      ]);
    }

    var priceNode = el("div", {
      style: {
        display: "flex", "align-items": "center", "justify-content": "center", gap: "11px",
        "flex-wrap": "wrap", padding: "13px 16px",
        border: "1px solid var(--accent-line)", background: "var(--accent-soft)",
        "border-radius": "var(--r-inner)", color: "var(--accent-2)"
      }
    }, [
      icon("dollar", 17, 2),
      el("div", { style: { "font-size": "var(--t-head)", "font-weight": "600", "letter-spacing": "-0.012em" }, text: "Price" }),
      el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" }, text: "where all four arrows land — the flat number on the indication Pavin is sent" })
    ]);

    wrap.appendChild(GD.card({
      title: "How price is determined",
      sub: "Pavin's own causal model, taken from the OneNote page of that name — not a generic factor list.",
      body: [
        el("div", {
          style: { display: "flex", gap: "14px", "flex-wrap": "wrap", "align-items": "stretch" }
        }, [
          groupBox("Through the balance", "3 1 540px",
            "Weather sets the crop, the crop sets production and supply; exports set demand; the season moves both.", [
              el("div", { style: { display: "grid", gap: "8px", "justify-items": "start" } }, [
                chain(["weather", "crop_condition", "production", "supply"]),
                chain(["export", "demand"]),
                chain(["seasonal"])
              ]),
              arrow(true, "supply, demand and the season resolve into one balance"),
              el("div", { style: { display: "grid", gap: "8px", "justify-items": "start" } }, [
                flowNode("supply_demand"),
                arrow(true, "to price")
              ])
            ]),
          groupBox("Straight onto price", "1 1 260px",
            "Three levers skip the balance sheet and hit the screen directly.", [
              el("div", { style: { display: "grid", gap: "8px", "justify-items": "start" } }, [
                flowNode("cot"),
                flowNode("policy"),
                flowNode("forex"),
                arrow(true, "to price, no balance sheet in between")
              ])
            ]),
          el("div", { style: { flex: "1 1 100%" } }, [priceNode])
        ])
      ],
      foot: [
        icon("file", 13, 1.75),
        el("span", { text: "Ten levers; the balance of supply and demand is the eleventh node, not a lever of its own. Note counts and net stance are read off the ten intel notes below — click any node with notes to filter them." })
      ]
    }));

    /* -------------------------------------------------- 5. driver board */

    boardRows = Object.keys(DS).map(function (k) { return DS[k]; })
      .filter(function (m) { return m.n > 0; });
    boardMax = 0;
    boardRows.forEach(function (m) {
      if (m.bear > boardMax) boardMax = m.bear;
      if (m.bull > boardMax) boardMax = m.bull;
    });

    function diverge(r) {
      var bearPct = boardMax > 0 ? (r.bear / boardMax * 100) : 0;
      var bullPct = boardMax > 0 ? (r.bull / boardMax * 100) : 0;
      var host = el("div", {
        style: { display: "flex", "align-items": "center", width: "196px", "max-width": "100%" },
        role: "img",
        "aria-label": r.label + ": bearish pull " + fmt.usd(r.bear, 2) + " a tonne, bullish pull " +
          fmt.usd(r.bull, 2) + " a tonne, net " + fmt.signed(r.net, 2)
      }, [
        el("div", {
          style: {
            flex: "1", height: "9px", background: "var(--surface-3)", overflow: "hidden",
            "border-radius": "var(--r-chip) 0 0 var(--r-chip)", display: "flex", "justify-content": "flex-end"
          }
        }, [
          el("span", {
            style: {
              width: bearPct.toFixed(1) + "%", height: "9px", "background-color": "var(--down)",
              "border-radius": "var(--r-chip) 0 0 var(--r-chip)"
            }
          })
        ]),
        el("span", { style: { width: "1px", height: "15px", "background-color": "var(--border-strong)", flex: "none" } }),
        el("div", {
          style: {
            flex: "1", height: "9px", background: "var(--surface-3)", overflow: "hidden",
            "border-radius": "0 var(--r-chip) var(--r-chip) 0", display: "flex"
          }
        }, [
          el("span", {
            style: {
              width: bullPct.toFixed(1) + "%", height: "9px", "background-color": "var(--up)",
              "border-radius": "0 var(--r-chip) var(--r-chip) 0"
            }
          })
        ])
      ]);
      GD.tipOn(host, "<b>" + GD.esc(r.label) + "</b>" +
        tipRow("bearish pull", fmt.usd(r.bear, 2) + "/t") +
        tipRow("bullish pull", fmt.usd(r.bull, 2) + "/t") +
        tipRow("net", fmt.signed(r.net, 2) + "/t") +
        tipRow("on a cargo", fmt.signedMoney(r.net * CARGO)));
      return host;
    }

    boardTable = GD.table({
      cols: [
        { key: "label", label: "Driver", tdClass: "strong", width: "168px" },
        {
          key: "stance", label: "Net stance", sortValue: function (r) { return r.net; },
          render: function (r) { return chip(stanceLabel(r.net), stanceTone(r.net), stanceIcon(r.net)); }
        },
        {
          key: "bar", label: "Pressure", sortable: false, width: "212px",
          render: function (r) { return diverge(r); }
        },
        { key: "n", label: "Notes", num: true },
        {
          key: "net", label: "Net $/t", num: true,
          render: function (r) {
            return el("span", { class: GD.toneFor(r.net), text: fmt.signed(r.net, 2) });
          }
        },
        {
          key: "cargo", label: "On a 55,000 t cargo", num: true,
          sortValue: function (r) { return r.net * CARGO; },
          render: function (r) {
            return el("span", { class: GD.toneFor(r.net), text: fmt.signedMoney(r.net * CARGO) });
          }
        }
      ],
      rows: boardRows,
      sortKey: "net",
      sortDir: "asc",
      rowClass: function (r) { return state.driver === r.key ? "is-selected" : null; },
      onRow: function (r) { setDriver(state.driver === r.key ? null : r.key); },
      emptyText: "No driver carries intel yet."
    });

    wrap.appendChild(GD.card({
      title: "Driver board",
      sub: "What each lever is pulling, and how hard. Bearish runs left and emerald, because cheaper wheat is the good outcome for a buyer.",
      actions: [
        GD.legend([
          { name: "Bearish — cheaper wheat", color: "var(--down)" },
          { name: "Bullish — costs us", color: "var(--up)" }
        ]),
        chip("intel on " + boardRows.length + " of " + D.drivers.length + " nodes", "outline")
      ],
      flush: true,
      body: [boardTable],
      foot: [
        icon("scale", 13, 1.75),
        el("span", { text: "Sensitivities are the desk's own estimate per note, summed by lever — not a published number. The cargo column is $/t × 55,000 t. Click a row to filter the feed." })
      ]
    }));

    /* --------------------------------------------------- 6. intel feed */

    function noteRow(note) {
      var bear = note.stance === "bearish";
      var ton = signedTonne(note);
      var d = DS[note.driver];
      return el("div", { class: "note-row" }, [
        el("span", {
          class: "stat-ico",
          style: {
            "background-color": bear ? "var(--down-soft)" : "var(--up-soft)",
            color: bear ? "var(--accent-2)" : "var(--up)"
          }
        }, [icon(bear ? "down" : "up", 14, 2)]),
        el("div", { class: "nr-body" }, [
          el("div", { class: "nr-head" }, [
            chip(bear ? "Bearish" : "Bullish", bear ? "good" : "bad", bear ? "down" : "up"),
            chip(d ? d.label : note.driver, "outline"),
            el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" }, text: whenWords(note.date) })
          ]),
          el("h4", { text: note.headline }),
          el("p", { text: note.body }),
          el("div", { class: "nr-meta", text: note.source + " · " + fmt.date(note.date) })
        ]),
        el("div", { style: { flex: "none", "min-width": "112px", "text-align": "right" } }, [
          el("div", {
            class: "num " + GD.toneFor(ton),
            style: { "font-size": "var(--t-head)", "font-weight": "600", "letter-spacing": "-0.015em" },
            text: fmt.signed(ton, 2)
          }),
          el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" }, text: "$/t estimate" }),
          el("div", {
            class: "num",
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "3px" },
            text: fmt.signedMoney(ton * CARGO) + " a cargo"
          })
        ])
      ]);
    }

    function feedRows() {
      var out = D.intel.slice().sort(function (a, b) {
        return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
      });
      if (state.stance !== "all") {
        out = out.filter(function (n) { return n.stance === state.stance; });
      }
      if (state.driver) {
        out = out.filter(function (n) { return n.driver === state.driver; });
      }
      return out;
    }

    function drawFeed() {
      var list = feedRows();

      GD.clear(feedHost);
      if (!list.length) {
        feedHost.appendChild(el("div", { class: "empty" }, [
          icon("filter", 20, 1.5),
          el("div", { text: "No note matches both filters." }),
          el("button", {
            class: "btn btn-sm", type: "button",
            onclick: function () { state.stance = "all"; setDriver(null); }
          }, [icon("x", 12, 2), "Clear the filters"])
        ]));
      } else {
        list.forEach(function (n) { feedHost.appendChild(noteRow(n)); });
      }

      GD.clear(filterHost);
      if (state.driver) {
        var label = DS[state.driver] ? DS[state.driver].label : state.driver;
        filterHost.appendChild(el("button", {
          class: "btn btn-sm", type: "button",
          "aria-label": "Clear the " + label + " filter",
          onclick: function () { setDriver(null); }
        }, [icon("filter", 12, 2), label, icon("x", 12, 2)]));
      }

      GD.clear(countHost);
      countHost.appendChild(el("span", {
        text: "Showing " + list.length + " of " + D.intel.length + " notes" +
          (state.driver && DS[state.driver] ? " · " + DS[state.driver].label + " only" : "") +
          (state.stance === "all" ? "" : " · " + state.stance)
      }));
    }

    function setDriver(key) {
      state.driver = key;
      flowBtns.forEach(function (f) {
        var on = f.key === key;
        f.node.setAttribute("aria-pressed", String(on));
        f.node.style.setProperty("background-color", on ? "var(--accent-soft)" : "");
        f.node.style.setProperty("border-color", on ? "var(--accent-line)" : "");
      });
      boardTable.update(boardRows);
      drawFeed();
      if (key) reveal(feedCard);
    }

    filterHost = el("div", { style: { display: "flex", "align-items": "center", gap: "7px" } });
    feedHost = el("div");
    countHost = el("span");

    var bearN = D.intel.filter(function (n) { return n.stance === "bearish"; }).length;

    feedCard = GD.card({
      title: "Intel feed",
      sub: "Every note hangs off one of Pavin's levers, newest first.",
      actions: [
        filterHost,
        GD.seg([
          { key: "all", label: "All · " + D.intel.length },
          { key: "bearish", label: "Bearish · " + bearN },
          { key: "bullish", label: "Bullish · " + (D.intel.length - bearN) }
        ], "all", function (k) { state.stance = k; drawFeed(); })
      ],
      flush: true,
      body: [feedHost],
      foot: [
        icon("alert", 13, 1.75),
        countHost,
        sep(),
        el("span", { text: "The driver taxonomy is real, from OneNote. The ten notes and their $/t sensitivities are reconstructed for this build; the cargo figure is $/t × 55,000 t." })
      ]
    });
    wrap.appendChild(feedCard);

    /* ---------------------------------------------- 7. the WASDE clock */

    var released = D.wasde.filter(function (w) { return w.status === "released"; })[0];
    var scheduled = D.wasde.filter(function (w) { return w.status === "scheduled"; });
    var next = scheduled[0];
    var after = scheduled[1];
    var netTon = released ? released.verdicts.reduce(function (s, v) { return s + v.usd_mt; }, 0) : 0;
    var days = next ? fmt.daysBetween(D.built, next.date) : null;

    function clockCell(k, value, tone, note) {
      return el("div", null, [
        el("div", { class: "k", text: k }),
        el("div", { class: "v " + (tone || ""), text: value }),
        el("div", { class: "card-sub", text: note })
      ]);
    }

    var wasdeBody = [];

    wasdeBody.push(el("div", { class: "statline" }, [
      clockCell("Next release", days === null ? "—" : "in " + days + " days", "",
        next ? fmt.date(next.date) + ", Washington" : "nothing scheduled"),
      clockCell("Lands in Bangkok", next ? fmt.time(next.local) : "—", "",
        next ? fmt.date(next.local) + (next.dst ? " · US DST in force" : " · US DST over") : ""),
      clockCell("Release window", "23:00 – 00:00", "",
        "12:00 noon Washington; US daylight saving moves it an hour"),
      clockCell(fmt.dateShort(released ? released.date : D.built) + " verdict, net $/t",
        fmt.signed(netTon, 2), GD.toneFor(netTon),
        fmt.signedMoney(netTon * CARGO) + " on a 55,000 t cargo")
    ]));

    if (released) {
      wasdeBody.push(el("div", {
        style: {
          padding: "14px 16px 9px", display: "flex", "align-items": "baseline",
          gap: "10px", "flex-wrap": "wrap", "border-top": "1px solid var(--border)"
        }
      }, [
        el("div", { class: "label", text: "What " + fmt.dateShort(released.date) + " printed" }),
        sub("Released " + fmt.time(released.local) + " Bangkok" + (released.dst ? ", US daylight saving in force" : "") +
          " · " + fmt.ago(released.date))
      ]));

      wasdeBody.push(el("div", { class: "table-wrap" }, [
        el("table", null, [
          el("thead", null, [el("tr", null, [
            el("th", { scope: "col", text: "Class" }),
            el("th", { scope: "col", text: "Surprise" }),
            el("th", { scope: "col", text: "Stance" }),
            el("th", { scope: "col", class: "num", text: "$/t" }),
            el("th", { scope: "col", class: "num", text: "Per cargo" })
          ])]),
          el("tbody", null, released.verdicts.map(function (v) {
            var impact = v.usd_mt * CARGO;
            return el("tr", null, [
              el("td", { class: "strong", text: v.cls }),
              el("td", null, [
                el("div", { text: v.surprise }),
                el("div", { class: "card-sub", text: "expected " + v.expected + " · printed " + v.actual })
              ]),
              el("td", null, [chip(v.stance === "bearish" ? "Bearish" : "Bullish",
                v.stance === "bearish" ? "good" : "bad", v.stance === "bearish" ? "down" : "up")]),
              el("td", { class: "num " + GD.toneFor(v.usd_mt), text: fmt.signed(v.usd_mt, 2) }),
              el("td", { class: "num " + GD.toneFor(impact), text: fmt.signedMoney(impact) })
            ]);
          }))
        ])
      ]));
    }

    wasdeBody.push(el("div", {
      style: {
        padding: "13px 16px", "border-top": "1px solid var(--border)", background: "var(--surface-2)",
        display: "flex", "align-items": "center", gap: "10px", "flex-wrap": "wrap"
      }
    }, [
      el("span", { style: { display: "flex", "align-items": "center", gap: "7px", color: "var(--ink-2)", "font-size": "var(--t-sm)" } }, [
        icon("clock", 14, 1.75),
        el("span", {
          text: next
            ? "Next: " + fmt.date(next.local) + " at " + fmt.time(next.local) + " Bangkok, in " + days + " days."
            : "No release scheduled."
        })
      ]),
      after ? chip("Then " + fmt.dateShort(after.date) + ", in " +
        fmt.daysBetween(D.built, after.date) + " days", "outline", "calendar") : null
    ]));

    wrap.appendChild(GD.card({
      title: "The WASDE clock",
      sub: "The release Pavin refreshes a browser for at midnight. The verdict is worth money for about five minutes.",
      actions: [
        released ? chip(released.verdicts.length + " classes called", "outline") : null,
        replayBtn("btn btn-sm", "Replay Night Watch")
      ],
      flush: true,
      body: wasdeBody,
      foot: [
        icon("alert", 13, 1.75),
        el("span", { text: "The report prints at 12:00 noon Washington, so Bangkok gets it at 23:00 while US daylight saving is in force and 00:00 the next day once it ends — which is why the schedule is not a fixed local time. The per-cargo column is the $/t verdict × 55,000 t, derived here and not printed in the report; the expectation each surprise is measured against is the trade poll, the prior report and Pavin's own stated bias." })
      ]
    }));

    /* first paint of everything that has internal state */
    drawChart();
    drawFeed();
  }

  GD.route("market", {
    label: "Market",
    icon: "market",
    group: "Trade",
    badge: function () {
      var n = D.intel.filter(function (x) { return x.date === D.built; }).length;
      return n || null;
    },
    badgeTone: "accent",
    render: render
  });

})();
