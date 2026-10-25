/* Grain Desk — Market.

   An instrument page, in the shape anyone who has opened a trading app already knows: a
   watchlist down one side, and on the other a big chart with a price that follows the cursor
   and a window you choose. That shape is not decoration — it is the fastest way to answer the
   question a buyer asks the moment they are told a quote sits above its fair band, which is
   "well, what has the underlying been doing?".

   What is not borrowed from a trading app is the bottom of the page. An investor wants the
   instrument; a buyer wants what the instrument did to the floor under their own materials,
   in their own currency. That is the last block, and it is the one a generic market screen
   cannot build.

   One convention is deliberately *not* a trading screen's: emerald is a falling price and
   coral a rising one, because this is a buying desk and the cheaper tonne is the good
   outcome. The app has read that way since its first screen; flipping it here would be the
   one place it lied.

   Where the numbers come from: `tools/fetch_prices.py` pulls real daily sessions for the
   feeds that have a public market, and the rest — assessed indices, private freight
   assessments — keep a generated path with an invented session shape so the candles have
   something to draw. Every feed says which it is, on the tile, in the header and in the
   footer under the chart. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, P = GD.pricing;

  var WINDOWS = [
    { key: "1M", label: "1M", days: 30, say: "past month", candle: "d", grain: "daily" },
    { key: "3M", label: "3M", days: 91, say: "past three months", candle: "d", grain: "daily" },
    { key: "6M", label: "6M", days: 182, say: "past six months", candle: "w", grain: "weekly" },
    { key: "1Y", label: "1Y", days: 9999, say: "past twelve months", candle: "w", grain: "weekly" }
  ];

  function kindColor(kind) {
    return kind === "freight" ? "var(--c-freight)"
      : kind === "spread" ? "var(--c-basis)"
      : kind === "fx" ? "var(--c-margin)" : "var(--c-futures)";
  }
  function kindIcon(kind) {
    return kind === "freight" ? "ship" : kind === "fx" ? "globe"
      : kind === "spread" ? "layers" : "market";
  }
  function moves(f) { return f.kind !== "spread"; }

  function labelled(text, style) {
    var s = { "margin-bottom": "8px" };
    if (style) Object.keys(style).forEach(function (k) { s[k] = style[k]; });
    return el("div", { class: "label", style: s, text: text });
  }
  /* Decimals from the magnitude: a four-figure index reads flat, a rate needs its places. */
  function num(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var a = Math.abs(v);
    return fmt.n(v, a >= 1000 ? 0 : a >= 10 ? 2 : 4);
  }
  /* A twelve-month axis labelled "1 Oct ... 13 Oct" names two different Octobers. */
  function axisDate(d) {
    var m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return fmt.dateShort(d);
    return fmt.months[Number(m[2]) - 1] + " '" + m[1].slice(2);
  }
  function statCell(k, v, tone) {
    return el("div", null, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v " + (tone || ""), text: v })
    ]);
  }

  /* ---------------------------------------------------------------- the feed list

     Built by walking the materials, so a feed is on this screen exactly when something is
     priced off it and there is no second list to keep in step. The daily path and the rest of
     the instrument metadata are attached from the workspace's feed table.

     Held-flat spreads (a converter charge, a domestic premium) come back from here because
     they are part of the cost build-up, but they are not instruments: a flat line has no
     window to choose and no range to sit in. They appear on the material rows instead. */

  function feeds(ws) {
    var byKey = {}, out = [];
    (ws.materials || []).forEach(function (m) {
      (m.drivers || []).forEach(function (d) {
        var f = byKey[d.key];
        if (!f) {
          f = byKey[d.key] = {
            key: d.key, name: d.name, source: d.source, unit: d.unit,
            currency: d.currency, kind: d.kind, mocked: !!d.mocked, note: d.note,
            months: d.dates, monthly: d.values, materials: []
          };
          out.push(f);
        }
        f.materials.push(m);
      });
    });

    (GD.wsInstruments() || []).forEach(function (ins) {
      var f = byKey[ins.key];
      if (!f) {
        /* An FX pair is nobody's driver — it is the conversion applied to everyone else's —
           so walking the materials never finds it. It is also the feed with the widest reach
           on a book that imports most of what it buys. */
        if (ins.role !== "fx") return;
        f = byKey[ins.key] = {
          key: ins.key, name: ins.label, source: ins.venue, unit: ins.unit,
          currency: ins.currency, kind: "fx",
          /* Whether this is a placeholder is a fact about the build, not a constant: the
             rate is real whenever tools/fetch_prices.py pulled one. */
          mocked: !!ins.synthetic_history,
          note: "Every leg published in another currency is converted at this rate.",
          months: [], monthly: [],
          materials: (ws.materials || []).filter(function (m) {
            return (m.drivers || []).some(function (d) { return d.currency !== m.currency; });
          })
        };
        out.push(f);
      }
      f.values = ins.history;
      f.dates = ins.dates;
      f.ohlc = ins.ohlc || null;
      f.last = ins.last;
      f.strip = !!ins.strip;
      f.synthetic = !!ins.synthetic_history;
      f.symbol = ins.symbol || null;
      f.provider = ins.provider || null;
      f.mocked = !!ins.synthetic_history;
    });

    out.forEach(function (f) {
      /* A spread has no instrument: its series is its own held constant. */
      if (!f.values) {
        f.values = f.monthly;
        f.dates = f.months;
        f.last = f.monthly[f.monthly.length - 1];
      }
      f.hasCandles = !!(f.ohlc && f.ohlc.length);
      var v = f.values;
      f.first = v[0];
      f.year = f.first ? (f.last - f.first) / f.first : null;
      var sorted = v.slice().sort(function (a, b) { return a - b; });
      f.lo = sorted[0];
      f.hi = sorted[sorted.length - 1];
      f.where = (f.hi - f.lo) ? (f.last - f.lo) / (f.hi - f.lo) : 0.5;
    });

    var rank = { index: 0, fx: 1, freight: 2, spread: 3 };
    out.sort(function (a, b) {
      if (rank[a.kind] !== rank[b.kind]) return rank[a.kind] - rank[b.kind];
      return b.materials.length - a.materials.length;
    });
    return out;
  }

  /* A window of a feed's daily path, and what it did across it. */
  function slice(f, win) {
    var n = f.values.length;
    var take = Math.max(2, Math.min(n, win.days));
    var values = f.values.slice(n - take);
    var dates = f.dates.slice(n - take);
    var open = values[0], last = values[values.length - 1];
    var sorted = values.slice().sort(function (a, b) { return a - b; });
    var bars = f.ohlc ? f.ohlc.slice(f.ohlc.length - Math.min(f.ohlc.length, take)) : null;
    return {
      values: values, dates: dates, open: open, last: last, bars: bars,
      high: sorted[sorted.length - 1], low: sorted[0],
      change: last - open,
      pct: open ? (last - open) / open : null
    };
  }

  /* ================================================================= the watchlist */

  function watchRow(f, selected, onPick) {
    var row = el("button", {
      class: "pal-item", type: "button",
      "data-active": String(selected),
      style: {
        width: "100%", "text-align": "left", padding: "9px 11px", gap: "10px",
        "border-radius": "var(--r-ctl)", "min-width": "0"
      },
      onclick: function () { onPick(f); }
    }, [
      el("span", {
        style: {
          width: "20px", height: "20px", "border-radius": "6px", flex: "none",
          background: "var(--surface-3)", color: kindColor(f.kind),
          display: "grid", "place-items": "center"
        }
      }, [icon(kindIcon(f.kind), 12, 1.75)]),
      el("span", { style: { flex: "1", "min-width": "0", display: "grid", gap: "1px" } }, [
        el("span", {
          style: {
            "font-size": "var(--t-sm)", "font-weight": "600", display: "block",
            overflow: "hidden", "text-overflow": "ellipsis", "white-space": "nowrap"
          },
          text: f.name
        }),
        el("span", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" },
          text: f.materials.length + (f.materials.length === 1 ? " material" : " materials")
        })
      ]),
      GD.chart.spark(f.values, {
        w: 46, h: 20, take: f.values.length,
        color: f.year < 0 ? "var(--down)" : "var(--up)", fill: false, endpoint: false
      }),
      el("span", {
        style: { display: "grid", gap: "1px", "text-align": "right", flex: "none" }
      }, [
        el("span", {
          class: "num", style: { "font-size": "var(--t-sm)", "font-weight": "600" },
          text: num(f.last)
        }),
        el("span", {
          class: "num " + GD.toneFor(f.year),
          style: { "font-size": "var(--t-xs)", "font-weight": "600" },
          text: fmt.signedPct(f.year, 1)
        })
      ])
    ]);
    if (f.mocked) row.setAttribute("title", "Placeholder series for this build");
    return row;
  }

  function watchlist(list, current, onPick) {
    var groups = [
      { key: "index", label: "Commodities" },
      { key: "fx", label: "FX" },
      { key: "freight", label: "Freight" }
    ];
    var body = el("div", {
      style: { padding: "7px", "max-height": "620px", "overflow-y": "auto" }
    });
    groups.forEach(function (g) {
      var picked = list.filter(function (f) { return f.kind === g.key; });
      if (!picked.length) return;
      body.appendChild(el("div", { class: "pal-group label", text: g.label }));
      picked.forEach(function (f) {
        body.appendChild(watchRow(f, f.key === current.key, onPick));
      });
    });
    return GD.card({
      title: "Feeds",
      sub: list.length + " things this book is priced off",
      flush: true,
      body: [body],
      foot: [
        el("span", { style: { color: "var(--ink-3)" },
          text: "Emerald is a falling price. This is a buying desk, so the colours run the " +
                "opposite way to a trading screen: cheaper is the good outcome." })
      ]
    });
  }

  /* ============================================================== the instrument */

  function instrumentCard(f, ws, state, onWindow, onChart) {
    var win = WINDOWS.filter(function (w) { return w.key === state.window; })[0] || WINDOWS[2];
    var s = slice(f, win);
    var tone = s.change < 0 ? "var(--down)" : s.change > 0 ? "var(--up)" : "var(--ink-3)";

    var bigPrice = el("div", {
      class: "num",
      style: {
        "font-size": "34px", "font-weight": "600", "letter-spacing": "-0.03em",
        "line-height": "1.05"
      }
    });
    var changeLine = el("div", {
      class: "num",
      style: { "font-size": "var(--t-body)", "font-weight": "600", "margin-top": "3px" }
    });
    var whenLine = el("div", {
      style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "1px" }
    });

    function paint(value, change, when) {
      bigPrice.textContent = num(value);
      changeLine.textContent = (change > 0 ? "+" : change < 0 ? "−" : "") +
        num(Math.abs(change)) + "   " + fmt.signedPct(s.open ? change / s.open : 0, 2);
      changeLine.style.setProperty("color",
        change < 0 ? "var(--down)" : change > 0 ? "var(--up)" : "var(--ink-3)");
      whenLine.textContent = when;
    }
    /* Scrubbing reads the point under the cursor against where the window opened — which is
       exactly the comparison the dashed baseline on the chart is drawing. */
    function showPoint(idx) {
      paint(s.values[idx], s.values[idx] - s.open,
        fmt.date(s.dates[idx]) + " · against " + num(s.open) + " on " + fmt.date(s.dates[0]));
    }
    /* Scrubbing a candle reads the whole session, because that is what a candle is. */
    function showSession(bar) {
      paint(bar[4], bar[4] - s.open,
        fmt.date(bar[0]) + " · O " + num(bar[1]) + "  H " + num(bar[2]) +
        "  L " + num(bar[3]) + "  C " + num(bar[4]));
    }
    function showLatest() {
      paint(s.last, s.change, win.say + " · " + f.unit);
    }
    showLatest();

    var useCandles = state.chart === "candles" && f.hasCandles;
    var candleBars = useCandles ? GD.chart.aggregate(s.bars, win.candle) : null;
    var chart;
    if (useCandles) {
      chart = GD.chart.candles({
        bars: candleBars,
        w: 1000, h: 290,
        baseline: s.open,
        tip: false,
        onHover: function (idx, bar) { showSession(bar); },
        onLeave: showLatest,
        fmtY: num,
        fmtX: win.days > 200 ? axisDate : fmt.dateShort,
        label: f.name + ", " + win.say + ", " + win.grain + " candles"
      });
    } else {
      chart = GD.chart.line({
        series: [{ name: f.name, values: s.values, color: tone, weight: 2 }],
        dates: s.dates,
        w: 1000, h: 290,
        area: true,
        baseline: s.open,
        tip: false,
        onHover: showPoint,
        onLeave: showLatest,
        fmtY: num,
        fmtX: win.days > 200 ? axisDate : fmt.dateShort,
        label: f.name + ", " + win.say
      });
    }
    chart.classList.add("chart-reveal");

    var head = el("div", {
      style: {
        display: "flex", "align-items": "flex-start", "justify-content": "space-between",
        gap: "16px", "flex-wrap": "wrap", "margin-bottom": "14px"
      }
    }, [
      el("div", { style: { "min-width": "240px", flex: "1" } }, [
        el("div", {
          class: "flex items-center gap-8",
          style: { "margin-bottom": "5px", "min-width": "0" }
        }, [
          el("span", {
            style: {
              width: "24px", height: "24px", "border-radius": "7px", flex: "none",
              background: "var(--surface-3)", color: kindColor(f.kind),
              display: "grid", "place-items": "center"
            }
          }, [icon(kindIcon(f.kind), 14, 1.75)]),
          el("div", { class: "card-title", text: f.name }),
          f.mocked ? GD.chip("mock", "outline") : null
        ]),
        /* Name the materials while they fit on one line; past that, count them and put the
           list in the tooltip. Eight names wrapped to three lines and pushed the price,
           which is the thing somebody came here to read, below the fold of the card. */
        (function () {
          var names = f.materials.map(function (m) { return m.name; });
          var line = el("div", {
            style: {
              "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-bottom": "12px"
            },
            text: f.source + " · " + f.unit + (names.length
              ? " · " + (f.kind === "fx" ? "converts " : "prices ") +
                (names.length > 3 ? names.length + " materials" : names.join(", "))
              : "")
          });
          if (names.length > 3) {
            GD.tipOn(line, "<b>" + GD.esc(f.name) + "</b><div class='tip-mute'>" +
              GD.esc(names.join(" · ")) + "</div>");
          }
          return line;
        })(),
        bigPrice, changeLine, whenLine
      ]),
      el("div", {
        style: { "margin-top": "2px", flex: "none", display: "grid", gap: "7px",
                 "justify-items": "end" }
      }, [
        GD.seg(WINDOWS.map(function (w) { return { key: w.key, label: w.label }; }),
          win.key, onWindow),
        f.hasCandles
          ? GD.seg([{ key: "line", label: "Line" }, { key: "candles", label: "Candles" }],
              state.chart, onChart)
          : null
      ])
    ]);

    var stats = el("div", {
      class: "statline",
      style: {
        border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
        overflow: "hidden", "margin-top": "16px"
      }
    }, [
      statCell("Window open", num(s.open)),
      statCell("High", num(s.high)),
      statCell("Low", num(s.low)),
      /* Four cells, not five: a fifth wrapped onto its own row and read as an orphan. Where
         the level sits in its twelve-month range belongs next to the range itself anyway. */
      el("div", null, [
        el("div", { class: "k", text: "12-month range" }),
        el("div", { class: "v" }, num(f.lo) + " – " + num(f.hi)),
        el("div", {
          class: "num " + (f.where > 0.75 ? "val-up" : f.where < 0.25 ? "val-down" : ""),
          style: { "font-size": "var(--t-xs)", "font-weight": "600" },
          text: fmt.pct(f.where, 0) + " of the way up it"
        })
      ])
    ]);

    return GD.card({
      body: [head, chart, stats, pricesBlock(f, ws)],
      foot: [
        el("span", { style: { color: "var(--ink-3)" },
          text: f.synthetic
            ? "Generated for this build: the monthly levels are the prints the price model " +
              "reads, and the path and sessions between them are invented, not a tick feed."
            : (useCandles ? win.grain.charAt(0).toUpperCase() + win.grain.slice(1) +
                " candles" : "Daily closes") + " — real market data" +
              (f.symbol ? ", " + f.symbol : "") +
              (f.provider ? " via " + f.provider : "") + "." }),
        el("span", { style: { flex: "1", "min-width": "0" } }),
        el("span", { class: "nowrap", style: { color: "var(--ink-3)" },
          text: "as of " + fmt.date(GD.wsAsOf()) })
      ]
    });
  }

  /* What this feed is worth, per unit, inside each material it touches — the translation
     step that turns an index into somebody's cost.

     An FX pair needs a different sum. It is not a leg on anything; it is the multiplier on
     every leg published in another currency. So for FX the figure is the converted value of
     those legs, which on an importer's book is most of the price. */
  function worthIn(f, m, asOf) {
    var cost = P.driverCost(m, asOf);
    var value = null;
    if (f.kind === "fx") {
      var sum = 0, any = false;
      P.legs(m, asOf).forEach(function (l) {
        if (l.currency === m.currency || l.value === null) return;
        sum += l.value;
        any = true;
      });
      value = any ? P.round2(sum) : null;
    } else {
      P.legs(m, asOf).forEach(function (l) { if (l.key === f.key) value = l.value; });
    }
    return { value: value, share: (value !== null && cost) ? value / cost : null };
  }

  function pricesBlock(f, ws) {
    if (!f.materials.length) return null;
    var asOf = GD.wsAsOf();
    return el("div", { style: { "margin-top": "18px" } }, [
      labelled(f.kind === "fx"
        ? "What it converts inside each material"
        : "What it is worth inside each material"),
      el("div", { class: "grid g-4", style: { gap: "10px" } },
        f.materials.map(function (m) {
          var w = worthIn(f, m, asOf);
          var leg = w.value === null ? null : { value: w.value };
          var share = w.share;
          return el("button", {
            class: "card is-hoverable", type: "button",
            style: {
              padding: "11px 12px", "text-align": "left", display: "grid", gap: "4px",
              cursor: "pointer", "min-width": "0"
            },
            onclick: function () {
              GD.go("pricecheck");
              setTimeout(function () {
                if (GD.filterPriceCheck) GD.filterPriceCheck(m.id);
              }, 80);
            }
          }, [
            el("div", {
              style: {
                "font-size": "var(--t-xs)", color: "var(--ink-3)", overflow: "hidden",
                "text-overflow": "ellipsis", "white-space": "nowrap"
              },
              text: m.name
            }),
            el("div", { class: "num", style: { "font-weight": "600" },
              text: leg ? GD.curUnit(leg.value, m) + "/" + m.unit : "—" }),
            el("div", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" },
              text: share === null ? "" : fmt.pct(share, 0) + " of driver cost"
            })
          ]);
        }))
    ]);
  }

  /* ============================================= what it did to our materials */

  function materialRows(ws) {
    var dates = ws.materials[0] && ws.materials[0].drivers[0]
      ? ws.materials[0].drivers[0].dates : [];
    return ws.materials.map(function (m) {
      var series = dates.map(function (d) { return P.driverCost(m, d); })
        .filter(function (v) { return v !== null; });
      var now = series[series.length - 1];
      var first = series[0];
      var prev = series.length > 1 ? series[series.length - 2] : null;
      var q = series.length > 3 ? series[series.length - 4] : first;
      return {
        m: m, series: series, now: now,
        month: prev ? (now - prev) / prev : null,
        quarter: q ? (now - q) / q : null,
        year: first ? (now - first) / first : null,
        drivers: m.drivers
      };
    });
  }

  function materialsCard(ws, rows) {
    return GD.card({
      title: "What that did to our materials",
      sub: "Driver cost is the published floor under a price — the part no supplier sets",
      flush: true,
      body: [GD.table({
        cols: [
          {
            key: "name", label: "Material", tdClass: "strong", width: "160px",
            sortValue: function (r) { return r.m.name; },
            render: function (r) {
              return el("div", null, [
                el("div", { text: r.m.name }),
                el("div", {
                  style: { "font-size": "var(--t-xs)", color: "var(--ink-3)",
                           "font-weight": "400" },
                  text: (r.m.category === "packaging" ? "Packaging" : "Raw material") +
                        " · per " + r.m.unit
                })
              ]);
            }
          },
          {
            key: "drivers", label: "Priced off", sortable: false, width: "230px",
            render: function (r) {
              return el("div", { style: { display: "grid", gap: "3px" } },
                r.drivers.map(function (d) {
                  var row = el("div", { class: "flex items-center gap-8" }, [
                    el("i", {
                      style: {
                        width: "8px", height: "8px", "border-radius": "2px", flex: "none",
                        "background-color": kindColor(d.kind)
                      }
                    }),
                    el("span", { style: { "font-size": "var(--t-xs)" }, text: d.name }),
                    d.mocked ? GD.chip("mock", "outline") : null
                  ]);
                  GD.tipOn(row, "<b>" + GD.esc(d.name) + "</b><div class='tip-mute'>" +
                    GD.esc(d.source + " · " + d.unit) + "</div><div class='tip-mute'>" +
                    GD.esc(d.note) + "</div>");
                  return row;
                }));
            }
          },
          {
            key: "spark", label: "12 months", sortable: false, width: "120px",
            render: function (r) {
              return GD.chart.spark(r.series, {
                w: 104, h: 30, take: r.series.length,
                color: r.year < 0 ? "var(--down)" : "var(--up)"
              });
            }
          },
          {
            key: "now", label: "Driver cost", num: true, tdClass: "strong", width: "120px",
            render: function (r) { return GD.curUnit(r.now, r.m) + "/" + r.m.unit; }
          },
          {
            key: "month", label: "1 month", num: true, width: "92px",
            render: function (r) {
              return el("span", { class: GD.toneFor(r.month), text: fmt.signedPct(r.month, 1) });
            }
          },
          {
            key: "quarter", label: "3 months", num: true, width: "92px",
            render: function (r) {
              return el("span", { class: GD.toneFor(r.quarter),
                text: fmt.signedPct(r.quarter, 1) });
            }
          },
          {
            key: "year", label: "12 months", num: true, width: "96px",
            render: function (r) {
              return el("span", { class: GD.toneFor(r.year), text: fmt.signedPct(r.year, 1) });
            }
          },
          {
            key: "budget", label: "Budget", num: true, width: "116px",
            sortValue: function (r) { return r.m.budget_price; },
            render: function (r) {
              if (r.m.budget_price === null || r.m.budget_price === undefined) {
                var n = el("span", { class: "val-mute", text: "none set" });
                GD.tipOn(n, GD.esc(r.m.budget_note));
                return n;
              }
              var node = el("span", { text: GD.curUnit(r.m.budget_price, r.m) });
              GD.tipOn(node, "<b>Budget rate</b><div class='tip-mute'>" +
                GD.esc(r.m.budget_note) + "</div><div class='tip-row'>" +
                "<span class='tip-mute'>headroom over driver cost</span><b>" +
                GD.esc(GD.curUnit(r.m.budget_price - r.now, r.m)) + "</b></div>");
              return node;
            }
          }
        ],
        rows: rows,
        sortKey: "year",
        sortDir: "desc",
        onRow: function (r) {
          GD.go("pricecheck");
          setTimeout(function () {
            if (GD.filterPriceCheck) GD.filterPriceCheck(r.m.id);
          }, 80);
        },
        emptyText: "No materials in this workspace."
      })],
      foot: [
        el("span", null, [
          el("b", { text: "Driver cost" }),
          " is every published leg added up and converted — the floor under the price. What " +
          "a supplier adds on top of it is on the price check."
        ]),
        el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
        el("span", { style: { color: "var(--ink-3)" },
          text: "Click a material to open its quotes." })
      ]
    });
  }

  /* ===================================================================== the surface */

  GD.route("prices", {
    label: "Market",
    icon: "market",
    group: "Desk",
    ws: ["urc"],

    render: function (wrap) {
      var ws = GD.ws();
      var all = feeds(ws);
      var list = all.filter(moves);
      var held = all.filter(function (f) { return !moves(f); });
      /* Open on the feed with the widest reach rather than the first alphabetically. On a
         book that imports most of what it buys that is the FX pair, which is under every
         material — and is exactly the thing a buyer here would check first. */
      var widest = list.slice().sort(function (a, b) {
        return b.materials.length - a.materials.length;
      })[0];
      var state = { feed: widest || list[0], window: "6M", chart: "line" };

      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Market" }),
          el("div", {
            class: "page-sub",
            text: list.length + " feeds setting the floor under " + ws.materials.length +
                  " materials — what they did, and what that did to us"
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.demoChip(),
          GD.chip("as of " + fmt.date(GD.wsAsOf()), "outline", "calendar")
        ])
      ]));

      /* The instrument and its watchlist, side by side and swapped in place rather than by
         re-rendering the surface: a route re-render restarts the entrance stagger and throws
         the scroll position away, which is a lot of movement for picking a different feed. */
      var main = el("div", { class: "grid g-main" });
      var leftSlot = el("div", { style: { "min-width": "0" } });
      var rightSlot = el("div", { style: { "min-width": "0" } });
      main.appendChild(leftSlot);
      main.appendChild(rightSlot);
      wrap.appendChild(main);

      function drawInstrument() {
        GD.clear(leftSlot);
        leftSlot.appendChild(instrumentCard(state.feed, ws, state, function (k) {
          state.window = k;
          drawInstrument();
        }, function (k) {
          state.chart = k;
          drawInstrument();
        }));
      }
      function drawList() {
        GD.clear(rightSlot);
        rightSlot.appendChild(watchlist(list, state.feed, function (f) {
          if (f.key === state.feed.key) return;
          state.feed = f;
          drawInstrument();
          drawList();
        }));
      }
      drawInstrument();
      drawList();

      if (held.length) {
        wrap.appendChild(el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", margin: "-8px 2px 0" },
          text: held.length + " more legs are held constant and have no market to track — " +
                held.map(function (f) { return f.name; }).join(", ") +
                ". They are part of driver cost and appear on the material rows below."
        }));
      }

      wrap.appendChild(materialsCard(ws, materialRows(ws)));
    }
  });

})();
