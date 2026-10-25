/* Grain Desk — Market.

   The feeds that set the floor under every material, and what they have done to it.

   This is the one screen that is purely "what is the market doing", and it exists because
   the rest of the desk kept implying an answer without ever showing it: the strip along the
   top gives six levels and no history, and the bulletin reports moves inside a document you
   have to generate. A buyer being told a quote sits above its fair band will immediately ask
   what the underlying has been doing. This is where they look.

   Three blocks, in the order the question is actually asked:

     1. every feed, its level and its twelve months          — what moved
     2. one chart, indexed so mixed units can be compared    — what moved most
     3. driver cost per material, month by month             — what that did to us

   The third is the one a generic market screen cannot do: it is the market translated into
   the floor under this company's own book, material by material, in its own currency. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, P = GD.pricing;

  /* Categorical, and deliberately neither the emerald nor the coral that carry direction. */
  var SERIES = [
    "var(--c-futures)", "var(--c-freight)", "var(--c-margin)",
    "var(--info)", "var(--c-residual)", "var(--accent-3)"
  ];

  function kindColor(kind) {
    return kind === "freight" ? "var(--c-freight)"
      : kind === "spread" ? "var(--c-basis)"
      : kind === "fx" ? "var(--c-margin)" : "var(--c-futures)";
  }
  function moves(f) { return f.kind !== "spread"; }

  function labelled(text, style) {
    var s = { "margin-bottom": "8px" };
    if (style) Object.keys(style).forEach(function (k) { s[k] = style[k]; });
    return el("div", { class: "label", style: s, text: text });
  }

  /* ---------------------------------------------------------------- the feed list

     A feed is whatever a material is priced off. The list is built by walking the materials,
     so a feed is on this screen exactly when something is priced off it and there is no
     second list to keep in step — plus the FX pair, which is nobody's driver and everybody's
     problem.

     Held-flat spreads (a converter charge, a domestic premium) come back from here because
     they are part of the cost build-up, but they are filtered out of the tiles and the chart:
     a flat line in a grid of things that move is four tiles of nothing. They appear on the
     material rows, where they belong. */

  /* An instrument carries a daily path; a driver series is monthly. To put the two on one
     chart the daily one is sampled at the monthly dates — the print in force on each. */
  function monthlyFrom(ins, dates) {
    return dates.map(function (d) {
      var at = -1;
      for (var i = 0; i < ins.dates.length; i++) {
        if (ins.dates[i] <= d) at = i; else break;
      }
      return at < 0 ? ins.history[0] : ins.history[at];
    });
  }

  function feeds(ws) {
    var byKey = {}, out = [];
    (ws.materials || []).forEach(function (m) {
      (m.drivers || []).forEach(function (d) {
        var f = byKey[d.key];
        if (!f) {
          f = byKey[d.key] = {
            key: d.key, name: d.name, source: d.source, unit: d.unit,
            currency: d.currency, kind: d.kind, mocked: !!d.mocked, note: d.note,
            dates: d.dates, values: d.values, materials: []
          };
          out.push(f);
        }
        f.materials.push(m);
      });
    });

    var monthDates = out.length ? out[0].dates : [];

    (GD.wsInstruments() || []).forEach(function (ins) {
      var f = byKey[ins.key];
      if (f) {
        /* The ones with a daily path get it, for the sparkline. */
        f.instrument = ins;
        f.daily = ins.history;
        return;
      }
      /* An FX pair is nobody's driver — it is the conversion applied to everyone else's —
         so it never appears by walking the materials. It is also the single feed with the
         widest reach on a book that imports 60% of what it buys, and leaving it off the
         market screen while it sits on the strip above would be absurd. */
      if (ins.role !== "fx") return;
      byKey[ins.key] = ins;
      out.push({
        key: ins.key, name: ins.label, source: ins.venue, unit: ins.unit,
        currency: ins.currency, kind: "fx", mocked: true,
        note: "Every leg published in another currency is converted at this rate. An " +
              "indicative mid for this build, not a live feed.",
        dates: monthDates,
        values: monthlyFrom(ins, monthDates),
        daily: ins.history,
        instrument: ins,
        materials: (ws.materials || []).filter(function (m) {
          return (m.drivers || []).some(function (d) { return d.currency !== m.currency; });
        })
      });
    });

    var asOf = GD.wsAsOf();
    out.forEach(function (f) {
      /* The last point is "now" for every feed. For one with a daily path that is today's
         level — the same number the strip shows, which it had better be. For a monthly one
         it is the print still in force, which is exactly how the price model reads it. */
      f.dates = f.dates.slice(0, -1).concat([asOf]);
      if (f.instrument) {
        f.values = f.values.slice(0, -1).concat([f.instrument.last]);
      }
      var v = f.values;
      f.last = v[v.length - 1];
      f.first = v[0];
      f.prevMonth = v.length > 1 ? v[v.length - 2] : null;
      f.year = f.first ? (f.last - f.first) / f.first : null;
      f.month = f.prevMonth ? (f.last - f.prevMonth) / f.prevMonth : null;
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

  function level(f) {
    return fmt.n(f.last, Math.abs(f.last) >= 1000 ? 0 : 2);
  }

  /* ------------------------------------------------------------------- 1 · the tiles */

  function feedTile(f, ws, onPick) {
    /* Where in its own twelve-month range the feed is sitting: the question a buyer asks of
       a level before they ask anything else. */
    var rangeBar = el("div", {
      style: {
        position: "relative", height: "5px", "border-radius": "var(--r-pill)",
        background: "var(--surface-3)", "margin-top": "2px"
      }
    }, [
      el("div", {
        style: {
          position: "absolute", left: (f.where * 100).toFixed(1) + "%", top: "-2px",
          width: "9px", height: "9px", "margin-left": "-4.5px", "border-radius": "50%",
          background: f.year < 0 ? "var(--down)" : "var(--up)",
          "box-shadow": "0 0 0 2px var(--surface)"
        }
      })
    ]);

    var node = el("button", {
      class: "card is-hoverable", type: "button",
      style: {
        padding: "14px 15px", display: "grid", gap: "9px", "text-align": "left",
        cursor: "pointer"
      },
      onclick: function () { onPick(f); }
    }, [
      /* `min-width: 0` because the tile is a grid, and a grid item defaults to `min-width:
         auto` — which means this flex row refuses to shrink below its content and a long
         feed name pushes the mock chip straight out through the side of the card. */
      el("div", {
        class: "flex items-center gap-8",
        style: { "align-items": "flex-start", "min-width": "0" }
      }, [
        el("span", {
          style: {
            width: "22px", height: "22px", "border-radius": "7px", flex: "none",
            background: "var(--surface-3)", color: kindColor(f.kind), display: "grid",
            "place-items": "center"
          }
        }, [icon(f.kind === "freight" ? "ship" : f.kind === "fx" ? "globe" : "market",
                 13, 1.75)]),
        el("div", { style: { flex: "1", "min-width": "0" } }, [
          el("div", {
            style: {
              "font-size": "var(--t-sm)", "font-weight": "600", "line-height": "1.3",
              overflow: "hidden", "text-overflow": "ellipsis", "white-space": "nowrap"
            },
            text: f.name
          }),
          el("div", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
            text: f.source + " · " + f.unit
          })
        ]),
        f.mocked ? GD.chip("mock", "outline") : null
      ]),
      el("div", { class: "flex items-center gap-8", style: { "justify-content": "space-between" } }, [
        el("div", { class: "num", style: { "font-size": "21px", "font-weight": "600",
                                           "letter-spacing": "-0.02em" }, text: level(f) }),
        GD.chart.spark(f.daily || f.values, {
          w: 74, h: 26, take: (f.daily || f.values).length,
          color: f.year < 0 ? "var(--down)" : "var(--up)"
        })
      ]),
      rangeBar,
      el("div", {
        class: "flex items-center gap-8",
        style: {
          "justify-content": "space-between", "font-size": "var(--t-xs)",
          "flex-wrap": "wrap", "row-gap": "3px"
        }
      }, [
        el("span", { class: GD.toneFor(f.month) + " nowrap",
          text: fmt.signedPct(f.month, 1) + " 1m" }),
        el("span", { class: GD.toneFor(f.year) + " nowrap",
          text: fmt.signedPct(f.year, 1) + " 12m" }),
        el("span", { class: "nowrap", style: { color: "var(--ink-4)" },
          text: f.materials.length + (f.materials.length === 1 ? " material" : " materials") })
      ])
    ]);

    GD.tipOn(node, "<b>" + GD.esc(f.name) + "</b><div class='tip-mute'>" +
      GD.esc(f.source) + (f.mocked ? " — placeholder series for this build" : "") +
      "</div><div class='tip-row'><span class='tip-mute'>12-month range</span><b>" +
      GD.esc(fmt.n(f.lo, f.lo >= 1000 ? 0 : 2) + " – " + fmt.n(f.hi, f.hi >= 1000 ? 0 : 2)) +
      "</b></div><div class='tip-row'><span class='tip-mute'>prices</span><b>" +
      GD.esc(f.materials.map(function (m) { return m.name; }).join(", ")) + "</b></div>");
    return node;
  }

  /* ----------------------------------------------------------------- 2 · the chart

     Robusta trades in USD a tonne, Brent in USD a barrel, sugar in cents a pound. They cannot
     share an axis, so everything is indexed to 100 at the start of the window — which is how
     a market report would put them side by side anyway, and it answers the only question
     worth asking across feeds: which of these moved most. */

  var GROUPS = [
    { key: "raw", label: "Raw material drivers" },
    { key: "pack", label: "Packaging drivers" },
    { key: "logistics", label: "FX and freight" }
  ];

  function groupOf(f, ws) {
    if (f.kind === "freight" || f.kind === "fx") return "logistics";
    var anyRaw = f.materials.some(function (m) { return m.category === "raw"; });
    return anyRaw ? "raw" : "pack";
  }

  /* A twelve-month axis labelled "1 Oct ... 13 Oct" names two different Octobers. */
  function axisDate(d) {
    var m = String(d).match(/^(\d{4})-(\d{2})/);
    if (!m) return fmt.dateShort(d);
    return fmt.months[Number(m[2]) - 1] + " '" + m[1].slice(2);
  }

  function indexed(values) {
    var base = values[0];
    if (!base) return values.map(function () { return 100; });
    return values.map(function (v) { return P.round2((v / base) * 100); });
  }

  function chartCard(all, ws) {
    var list = all.filter(moves);
    var state = { group: "raw" };
    var host = el("div");
    var note = el("p", {
      style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" }
    });

    function draw() {
      GD.clear(host);
      var picked = list.filter(function (f) { return groupOf(f, ws) === state.group; });
      if (!picked.length) {
        host.appendChild(el("div", { class: "empty" }, [
          icon("market", 20, 1.5),
          el("div", { text: "No feeds in this group." })
        ]));
        note.textContent = "";
        return;
      }
      host.appendChild(GD.chart.line({
        series: picked.map(function (f, i) {
          return { name: f.name, values: indexed(f.values), color: SERIES[i % SERIES.length] };
        }),
        dates: picked[0].dates,
        w: 1080, h: 250,
        fmtY: function (v) { return fmt.n(v, 0); },
        fmtTip: function (v) { return fmt.n(v, 1); },
        fmtX: axisDate,
        label: "Driver feeds, indexed to 100 twelve months ago"
      }));
      host.appendChild(GD.legend(picked.map(function (f, i) {
        return {
          name: f.name + " · " + fmt.signedPct(f.year, 1),
          color: SERIES[i % SERIES.length]
        };
      })));

      var moved = picked.slice().sort(function (a, b) {
        return Math.abs(b.year) - Math.abs(a.year);
      })[0];
      note.textContent = "Each line starts at 100 twelve months ago, so the units do not have " +
        "to agree. " + moved.name + " has moved furthest over the period, " +
        fmt.signedPct(moved.year, 1) + ".";
    }

    var seg = GD.seg(GROUPS.map(function (g) {
      var n = list.filter(function (f) { return groupOf(f, ws) === g.key; }).length;
      return { key: g.key, label: g.label + " " + n };
    }), "raw", function (k) { state.group = k; draw(); });

    draw();
    return GD.card({
      title: "Twelve months, side by side",
      sub: "Indexed to 100 at the start, because the units do not compare",
      actions: [seg],
      body: [host, note]
    });
  }

  /* --------------------------------------------- 3 · what it did to our materials

     The bridge. Driver cost is the published floor under a material — the part of the price
     that is not the supplier's to set — so its path over twelve months is the honest answer
     to "has this got more expensive, and was it them or the market". */

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
        m: m, series: series, dates: dates, now: now,
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
              return el("span", { class: GD.toneFor(r.month),
                text: fmt.signedPct(r.month, 1) });
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

  /* -------------------------------------------------------------- the feed detail */

  function showFeed(f, ws) {
    GD.sheet({
      title: f.name,
      sub: f.source + " · " + f.unit +
           (f.mocked ? " · placeholder series for this build" : ""),
      body: function () {
        var rows = [
          ["Latest", el("b", { text: level(f) + " " + f.unit })],
          ["One month", el("span", { class: GD.toneFor(f.month),
            text: fmt.signedPct(f.month, 1) })],
          ["Twelve months", el("span", { class: GD.toneFor(f.year),
            text: fmt.signedPct(f.year, 1) })],
          ["12-month range", fmt.n(f.lo, f.lo >= 1000 ? 0 : 2) + " – " +
            fmt.n(f.hi, f.hi >= 1000 ? 0 : 2)],
          ["Published in", f.currency + ", " + f.unit],
          ["Source", f.source]
        ];
        return [
          el("div", { style: { "margin-bottom": "18px" } }, [
            GD.chart.line({
              series: [{ name: f.name, values: f.values, color: kindColor(f.kind), weight: 2 }],
              dates: f.dates, w: 640, h: 210,
              fmtY: function (v) { return fmt.n(v, v >= 1000 ? 0 : 2); },
              fmtTip: function (v) { return fmt.n(v, 2) + " " + f.unit; },
              fmtX: axisDate,
              label: f.name + " over twelve months"
            })
          ]),
          el("div", { class: "grid g-2" }, [
            el("div", null, [labelled("The feed"), GD.kv(rows)]),
            el("div", null, [
              labelled("What it prices"),
              GD.kv(f.materials.map(function (m) {
                var d = null;
                (m.drivers || []).forEach(function (x) { if (x.key === f.key) d = x; });
                var legs = P.legs(m, GD.wsAsOf());
                var leg = null;
                legs.forEach(function (l) { if (l.key === f.key) leg = l; });
                return [m.name, el("span", null, [
                  el("span", { text: leg ? GD.curUnit(leg.value, m) + "/" + m.unit : "—" }),
                  el("span", {
                    style: { color: "var(--ink-3)", "margin-left": "6px",
                             "font-weight": "400", "font-size": "var(--t-xs)" },
                    text: leg && leg.value && P.driverCost(m, GD.wsAsOf())
                      ? fmt.pct(leg.value / P.driverCost(m, GD.wsAsOf()), 0) + " of driver cost"
                      : ""
                  })
                ])];
              }))
            ])
          ]),
          el("p", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "16px" },
            text: f.note + (f.mocked
              ? "  This is a placeholder series for this build, not a licensed feed."
              : "")
          })
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: close, text: "Close"
          }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub",
            text: "A feed is on this screen because something is priced off it." })
        ];
      }
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
      var rows = materialRows(ws);

      var rising = rows.filter(function (r) { return r.year > 0; }).length;
      var worst = rows.slice().sort(function (a, b) { return b.year - a.year; })[0];
      var best = rows.slice().sort(function (a, b) { return a.year - b.year; })[0];

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

      /* One line of read, so the screen says something before it shows everything. */
      wrap.appendChild(GD.card({
        body: [el("div", { class: "statline", style: { margin: "-18px" } }, [
          el("div", null, [
            el("div", { class: "k", text: "Driver cost rising" }),
            el("div", { class: "v " + (rising > rows.length / 2 ? "val-up" : "") },
              rising + " of " + rows.length)
          ]),
          el("div", null, [
            el("div", { class: "k", text: "Dearest move, 12 months" }),
            el("div", { class: "v val-up" }, fmt.signedPct(worst.year, 1)),
            el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: worst.m.name })
          ]),
          el("div", null, [
            el("div", { class: "k", text: "Keenest move, 12 months" }),
            el("div", { class: "v val-down" }, fmt.signedPct(best.year, 1)),
            el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: best.m.name })
          ]),
          el("div", null, [
            el("div", { class: "k", text: "Feeds on placeholder data" }),
            el("div", { class: "v" },
              list.filter(function (f) { return f.mocked; }).length + " of " + list.length),
            el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: "labelled wherever they appear" })
          ])
        ])]
      }));

      /* 1 — the feeds */
      wrap.appendChild(el("div", { class: "grid g-3" },
        list.map(function (f) {
          return feedTile(f, ws, function (picked) { showFeed(picked, ws); });
        })));

      if (held.length) {
        wrap.appendChild(el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin": "-8px 2px 0" },
          text: held.length + " more legs are held constant and have no market to track — " +
                held.map(function (f) { return f.name; }).join(", ") +
                ". They are part of driver cost and appear on the material rows below."
        }));
      }

      /* 2 — the comparison */
      wrap.appendChild(chartCard(all, ws));

      /* 3 — the bridge */
      wrap.appendChild(materialsCard(ws, rows));
    }
  });

})();
