/* app/js/surfaces/savings.js — Savings ledger and peer benchmark.

   The honest part of this screen is the benchmark. UFM's real KPI is "did we buy
   below the other Thai mills", and that is NOT data this desk holds: it needs Thai
   customs import declarations, vessel line-ups, or a swap between mills. So the
   panel infers a BAND from the only legitimate evidence available — the spread of
   offers approved suppliers showed *us* for the same class and shipment window —
   puts UFM's own best laid-in offer against it, and says plainly on screen that
   the band is inferred rather than observed. No competitor is ever named with a
   price they paid, because we do not know one. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, D = window.DESK;

  /* ------------------------------------------------------------------ helpers */

  var MONTH_KEYS = [
    ["jan", "january"], ["feb", "february"], ["mar", "march"], ["apr", "april"],
    ["may", "may"], ["jun", "june"], ["jul", "july"], ["aug", "august"],
    ["sep", "september"], ["oct", "october"], ["nov", "november"], ["dec", "december"]
  ];

  /* Shipment windows arrive in whatever shape the supplier typed: "20 DEC - 20 JAN
     2027", "Dec", "1-31 JANUARY 2027", "Nov 20 - Dec 20". Two offers are treated as
     comparable when their windows share a calendar month. The rule is crude but it
     is stated on screen, which is the point. */
  function monthsIn(text) {
    var low = String(text || "").toLowerCase(), out = [];
    MONTH_KEYS.forEach(function (pair, i) {
      if (low.indexOf(pair[0]) >= 0) out.push(i);
    });
    return out;
  }
  function shareMonth(a, b) {
    var ma = monthsIn(a), mb = monthsIn(b);
    if (!ma.length || !mb.length) return String(a) === String(b);
    for (var i = 0; i < ma.length; i++) if (mb.indexOf(ma[i]) >= 0) return true;
    return false;
  }

  function portOf(code) {
    var found = null;
    D.profile.ports.forEach(function (p) { if (p.code === code) found = p; });
    return found;
  }
  function portDiff(code) {
    var p = portOf(code);
    return p ? p.diff_usd_mt : 0;
  }
  function portName(code) {
    var p = portOf(code);
    return p ? p.name : code;
  }

  /* Laid-in = the offer as received (CNFFO at a named Thai port) plus UFM's own
     port differential. The differential is ours, not the supplier's — said so. */
  function laidIn(q) {
    return Math.round((q.flat + portDiff(q.port)) * 100) / 100;
  }

  function median(nums) {
    var s = nums.slice().sort(function (a, b) { return a - b; });
    var n = s.length;
    if (!n) return null;
    if (n % 2) return s[(n - 1) / 2];
    return Math.round(((s[n / 2 - 1] + s[n / 2]) / 2) * 100) / 100;
  }

  function isApproved(name) {
    var ok = false;
    D.profile.suppliers.forEach(function (s) { if (s.name === name && s.approved) ok = true; });
    return ok;
  }
  function classMeta(code) {
    var found = null;
    D.profile.classes.forEach(function (c) { if (c.code === code) found = c; });
    return found;
  }
  function originMeta(code) {
    var found = null;
    D.profile.origins.forEach(function (o) { if (o.code === code) found = o; });
    return found;
  }

  /* --------------------------------------------------------------- benchmark */

  /* One class in, one inferred band out — or null when we hold no offers at all.
     `n === 1` is returned honestly rather than padded into a fake range. */
  function benchFor(code) {
    var cls = classMeta(code);
    if (!cls) return null;
    var pool = D.quotes.filter(function (q) { return q.cls === code && isApproved(q.supplier); });
    if (!pool.length) return null;

    /* anchor = the cheapest laid-in offer we hold for the class; its window defines
       the comparable set, because that is the trade the desk would actually do. */
    var anchor = pool[0];
    pool.forEach(function (q) {
      var a = laidIn(q), b = laidIn(anchor);
      if (a < b || (a === b && q.received > anchor.received)) anchor = q;
    });

    var set = pool.filter(function (q) { return shareMonth(q.shipment, anchor.shipment); });
    var vals = set.map(laidIn);
    var sups = [];
    set.forEach(function (q) { if (sups.indexOf(q.supplier) < 0) sups.push(q.supplier); });

    var ufm = Math.min.apply(null, vals);
    var hi = Math.max.apply(null, vals);
    var floor = median(vals);
    var origin = originMeta(cls.origin);
    var tonnes = origin ? origin.ufm_mt : D.profile.economics.cargo_mt;
    var edge = Math.round((floor - ufm) * 100) / 100;

    return {
      code: code,
      cls: cls,
      origin: origin,
      anchor: anchor,
      offers: set,
      suppliers: sups,
      values: vals,
      ufm: ufm,
      floor: floor,
      ceiling: hi,
      spread: Math.round((hi - ufm) * 100) / 100,
      edge: edge,
      tonnes: tonnes,
      edgeUsd: Math.round(edge * tonnes),
      crossSupplier: sups.length > 1,
      hasSpread: hi > ufm
    };
  }

  function allBench() {
    var rows = [];
    D.profile.classes.forEach(function (c) {
      var b = benchFor(c.code);
      if (b) rows.push(b);
    });
    /* Deterministic default: most distinct approved suppliers, then most offers,
       then the class UFM lifts the most tonnes of. No clock, no randomness. */
    rows.sort(function (a, b) {
      return (b.suppliers.length - a.suppliers.length) ||
             (b.offers.length - a.offers.length) ||
             (b.tonnes - a.tonnes) ||
             (a.code < b.code ? -1 : 1);
    });
    return rows;
  }

  /* --------------------------------------------------------------- kind chips */

  var KINDS = {
    counter: { label: "Counter", tone: "accent", icon: "scale", color: "var(--c-basis)",
               blurb: "a price countered and re-offered" },
    timing:  { label: "Timing", tone: "info", icon: "clock", color: "var(--c-futures)",
               blurb: "basis or futures held, not locked" },
    freight: { label: "Freight", tone: "warn", icon: "ship", color: "var(--c-freight)",
               blurb: "a freight line challenged" }
  };
  function kindMeta(k) {
    return KINDS[k] || { label: String(k), tone: "outline", icon: "sparkle",
                         color: "var(--c-residual)", blurb: "" };
  }

  /* ------------------------------------------------------------- small pieces */

  /* Hatching, not a flat fill: the band is an estimate and should not read like a
     measured block. Both stops are theme tokens, so it survives light and dark. */
  var HATCH = "repeating-linear-gradient(135deg, var(--border-2) 0 4px, var(--surface) 4px 9px)";

  /* A callout. `tone`: "mute" (default) or "accent". Deliberately not alarming —
     the limitation on this page is a limitation, not an error. */
  function callout(cfg) {
    var accent = cfg.tone === "accent";
    var box = el("div", {
      style: {
        display: "flex", gap: "12px", padding: "13px 14px",
        "border-radius": "10px",
        "background-color": accent ? "var(--accent-soft)" : "var(--surface-2)",
        border: "1px solid " + (accent ? "var(--accent-line)" : "var(--border)")
      }
    }, [
      el("span", {
        style: {
          width: "26px", height: "26px", "border-radius": "7px", flex: "none",
          display: "grid", "place-items": "center",
          "background-color": accent ? "var(--surface)" : "var(--surface-3)",
          color: accent ? "var(--accent-2)" : "var(--ink-3)"
        }
      }, [icon(cfg.icon || "alert", 14, 1.9)]),
      el("div", { style: { flex: "1", "min-width": "0" } }, [
        el("div", {
          style: {
            "font-size": "var(--t-sm)", "font-weight": "600",
            color: accent ? "var(--accent-2)" : "var(--ink)", "margin-bottom": "3px"
          },
          text: cfg.title
        }),
        el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" }, text: cfg.body }),
        cfg.extra ? el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "5px" },
          text: cfg.extra
        }) : null
      ])
    ]);
    return box;
  }

  function swatch(fill, hatched) {
    return el("i", {
      style: {
        width: "10px", height: "10px", "border-radius": "3px", flex: "none",
        display: "inline-block",
        background: hatched ? HATCH : fill,
        border: hatched ? "1px solid var(--border-2)" : "0"
      }
    });
  }

  function legendRow(items) {
    return el("div", { class: "legend" }, items.map(function (it) {
      return el("span", null, [
        swatch(it.fill, it.hatched),
        el("span", { text: it.name }),
        it.value ? el("b", { style: { color: "var(--ink-2)", "font-variant-numeric": "tabular-nums" }, text: it.value }) : null
      ]);
    }));
  }

  /* The band gauge. DOM, not hand-rolled SVG: the shape is a track, a shaded block
     and one marker, and every number on it is also written out as text beside it. */
  function bandGauge(b) {
    var pad = Math.max(0.6, (b.ceiling - b.ufm) * 0.22);
    var dlo = b.ufm - pad, dhi = b.ceiling + pad;
    var span = dhi - dlo || 1;
    function x(v) { return ((v - dlo) / span) * 100; }

    var bandLeft = x(b.floor), bandRight = x(b.ceiling);
    var markX = x(b.ufm);

    var band = el("div", {
      style: {
        position: "absolute", top: "0", bottom: "0",
        left: bandLeft.toFixed(2) + "%",
        width: Math.max(1.2, bandRight - bandLeft).toFixed(2) + "%",
        background: HATCH,
        "border-left": "1px solid var(--border-2)",
        "border-right": "1px solid var(--border-2)"
      }
    });
    GD.tipOn(band, "<b>Estimated peer band</b>" +
      "<div class='tip-row'><span class='tip-mute'>floor — median offer</span><b>" + GD.esc(fmt.usd(b.floor)) + "</b></div>" +
      "<div class='tip-row'><span class='tip-mute'>ceiling — highest offer</span><b>" + GD.esc(fmt.usd(b.ceiling)) + "</b></div>" +
      "<div class='tip-row'><span class='tip-mute'>inferred from</span><b>" + GD.esc(b.offers.length + " offers") + "</b></div>");

    var marker = el("div", {
      style: {
        position: "absolute", top: "0", bottom: "0",
        left: markX.toFixed(2) + "%", width: "3px", "margin-left": "-1.5px",
        "background-color": "var(--accent)", "border-radius": "2px"
      }
    });
    var handle = el("div", {
      style: {
        position: "absolute", left: markX.toFixed(2) + "%", top: "50%",
        width: "12px", height: "12px", "margin-left": "-6px", "margin-top": "-6px",
        "border-radius": "50%", "background-color": "var(--accent)",
        "box-shadow": "0 0 0 2.5px var(--surface)"
      }
    });
    GD.tipOn(handle, "<b>UFM best laid-in offer</b>" +
      "<div class='tip-row'><span class='tip-mute'>laid-in</span><b>" + GD.esc(fmt.usd(b.ufm)) + "</b></div>" +
      "<div class='tip-row'><span class='tip-mute'>supplier</span><b>" + GD.esc(b.anchor.supplier) + "</b></div>");

    var aria = "UFM's best laid-in offer is " + fmt.usd(b.ufm) + " per tonne. " +
      "The estimated peer band runs from " + fmt.usd(b.floor) + " to " + fmt.usd(b.ceiling) +
      " per tonne. UFM sits " + fmt.usd(b.edge) + " per tonne below the floor of the band.";

    var track = el("div", {
      role: "img", "aria-label": aria,
      style: {
        position: "relative", height: "44px", "border-radius": "10px",
        "background-color": "var(--surface-2)", border: "1px solid var(--border)",
        overflow: "hidden"
      }
    }, [band, marker, handle]);

    var pill = el("div", {
      style: {
        position: "absolute", left: Math.min(90, Math.max(10, markX)).toFixed(2) + "%",
        bottom: "0", transform: "translateX(-50%)",
        display: "inline-flex", "align-items": "center", gap: "5px",
        height: "22px", padding: "0 8px", "border-radius": "var(--r-chip)",
        "background-color": "var(--accent)", color: "var(--on-accent)",
        "font-size": "var(--t-xs)", "font-weight": "600", "white-space": "nowrap",
        "font-variant-numeric": "tabular-nums", "box-shadow": "var(--sh-sm)"
      }
    }, [el("span", { text: "UFM " + fmt.usd(b.ufm) })]);

    return el("div", { style: { display: "grid", gap: "9px" } }, [
      legendRow([
        { fill: "var(--accent)", name: "UFM best laid-in offer", value: fmt.usd(b.ufm) + "/t" },
        { hatched: true, name: "Estimated peer band (inferred)", value: fmt.usd(b.floor) + " – " + fmt.usd(b.ceiling) + "/t" }
      ]),
      el("div", { style: { position: "relative", height: "26px" } }, [pill]),
      track,
      el("div", {
        style: {
          display: "flex", "justify-content": "space-between",
          "font-size": "var(--t-xs)", color: "var(--ink-4)"
        }
      }, [
        el("span", { text: "← cheaper for the mill" }),
        el("span", { text: "dearer →" })
      ])
    ]);
  }

  function statCell(k, v, tone, sub) {
    return el("div", null, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v " + (tone || ""), text: v }),
      sub ? el("div", {
        style: { "font-size": "var(--t-xs)", color: "var(--ink-4)", "margin-top": "3px" },
        text: sub
      }) : null
    ]);
  }

  /* ------------------------------------------------------------ method sheet */

  function methodSheet() {
    var real = D.provenance.real, synth = D.provenance.synthetic;
    GD.sheet({
      title: "How these numbers are built",
      sub: "Every figure on this screen, and which of them are reconstructed.",
      body: function () {
        function h(t) {
          return el("div", { class: "label", style: { "margin-bottom": "7px" }, text: t });
        }
        function p(t) {
          return el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" }, text: t });
        }
        function bullets(items) {
          return el("ul", { style: { display: "grid", gap: "6px" } }, items.map(function (t) {
            return el("li", { style: { display: "flex", gap: "8px", "font-size": "var(--t-sm)", color: "var(--ink-2)" } }, [
              el("span", { style: { color: "var(--accent)", flex: "none" } }, [icon("check", 14, 2)]),
              el("span", { text: t })
            ]);
          }));
        }
        return [
          el("div", { style: { display: "grid", gap: "18px" } }, [
            el("div", null, [
              h("The savings ledger"),
              p("The ledger is the desk's own record of actions it took. Each row's total is " +
                "simply quantity × the per-tonne saving claimed for that action; no supplier " +
                "credit note confirms it. Treat it as the desk's account of its own work, " +
                "which is what it is."),
              el("div", { style: { height: "10px" } }),
              GD.kv([
                ["Rows", fmt.n(D.savings.length) + " logged actions"],
                ["Total", fmt.usd0(D.savings_total)],
                ["Tonnes behind it", fmt.n(totalQty()) + " t"],
                ["Weighted average", fmt.usd(avgPerTonne()) + "/t"]
              ])
            ]),
            el("hr", { class: "hr" }),
            el("div", null, [
              h("The peer benchmark — the four rules"),
              bullets([
                "Laid-in = the offer exactly as received (CNFFO at a named Thai port) plus UFM's own port differential. The differential is our terminal table, not the supplier's number.",
                "The comparable set is every offer from an approved supplier for the same class whose shipment window shares a calendar month with our cheapest offer for that class.",
                "The band floor is the median of those offers: a mill that shops two or three suppliers and takes the middle of the market.",
                "The band ceiling is the highest offer in the set: a mill that takes its primary supplier's first number."
              ]),
              el("div", { style: { height: "12px" } }),
              callout({
                icon: "alert",
                title: "The band is inferred, not observed",
                body: "It is built from offers suppliers showed UFM. It is not what another " +
                      "mill paid, and nothing in this dataset is. A benchmark that could be " +
                      "defended in front of the board needs Thai customs import declarations, " +
                      "vessel line-ups, or a data-sharing arrangement between mills.",
                extra: "No competitor is named anywhere on this screen with a price, because no such price is held."
              })
            ]),
            el("hr", { class: "hr" }),
            el("div", null, [
              h("Real in this dataset"),
              bullets(real)
            ]),
            el("div", null, [
              h("Generated in this dataset"),
              bullets(synth)
            ])
          ])
        ];
      },
      foot: function (close) {
        return [
          el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", flex: "1" },
                       text: "Dataset built " + fmt.date(D.built) + "." }),
          el("button", { class: "btn btn-primary btn-pill", type: "button", "data-autofocus": true,
                         onclick: close }, [icon("check", 14, 2), el("span", { text: "Got it" })])
        ];
      }
    });
  }

  /* ------------------------------------------------------------ derived sums */

  function totalQty() {
    var t = 0;
    D.savings.forEach(function (s) { t += s.qty; });
    return t;
  }
  function avgPerTonne() {
    var q = totalQty();
    return q ? D.savings_total / q : 0;
  }
  function byKind() {
    var order = [], map = {};
    D.savings.forEach(function (s) {
      if (!map[s.kind]) { map[s.kind] = { kind: s.kind, total: 0, count: 0, best: 0 }; order.push(s.kind); }
      map[s.kind].total += s.total;
      map[s.kind].count += 1;
      if (s.usd_mt > map[s.kind].best) map[s.kind].best = s.usd_mt;
    });
    /* Keep the three known levers in a stable, meaningful order where present. */
    var preferred = ["counter", "timing", "freight"];
    var keys = preferred.filter(function (k) { return map[k]; })
      .concat(order.filter(function (k) { return preferred.indexOf(k) < 0; }));
    return keys.map(function (k) { return map[k]; });
  }

  /* ===================================================================== route */

  GD.route("savings", {
    label: "Savings & benchmark",
    icon: "savings",
    group: "Desk",
    /* A count, not the money: "$833k" is a lovely badge but it pushes the label into
       an ellipsis at 236px, and the sidebar foot already carries the money. */
    badge: function () { return D.savings.length; },
    badgeTone: "accent",

    render: function (wrap) {
      var econ = D.profile.economics;
      var adv = D.advisor;
      var kinds = byKind();
      var avg = avgPerTonne();
      var qty = totalQty();

      /* ------------------------------------------------------------ page head */

      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Savings & benchmark" }),
          el("p", {
            class: "page-sub",
            text: "What the desk has saved, and the only KPI Pavin actually named — buying " +
                  "below the other Thai mills."
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.chip("As of " + fmt.date(D.built), "outline", "calendar"),
          el("button", {
            class: "btn btn-primary btn-pill", type: "button", onclick: methodSheet
          }, [icon("shield", 14, 2), el("span", { text: "Method & limits" })])
        ])
      ]));

      /* ------------------------------------------------------------ hero row */

      var heroRow = el("div", { class: "grid g-3" }, [
        GD.stat({
          label: "Saved this year",
          value: fmt.money(D.savings_total),
          icon: "savings",
          hero: true,
          delta: [
            icon("sparkle", 13, 2),
            el("span", { text: fmt.n(D.savings.length) + " logged actions · " }),
            el("b", { text: fmt.usd(avg) + "/t" }),
            el("span", { text: " across " + fmt.n(qty) + " t" })
          ]
        }),
        GD.stat({
          label: "Advisor success fees",
          value: fmt.money(adv.fee_per_year),
          unit: "a year",
          icon: "dollar",
          delta: [
            icon("up", 13, 2),
            el("span", { text: fmt.usd0(adv.fee_per_cargo) + " a cargo at " +
                               fmt.usd(econ.advisor_share_usd_mt) + "/t of a " +
                               fmt.usd(econ.advisor_typical_saving_usd_mt, 0) + "/t saving" })
          ]
        }),
        GD.stat({
          label: "Advisor retainer",
          value: fmt.money(adv.retainer_year),
          unit: "a year",
          icon: "calendar",
          delta: [
            icon("clock", 13, 2),
            el("span", { text: fmt.usd0(econ.advisor_retainer_usd_month) + " a month" })
          ]
        })
      ]);
      wrap.appendChild(heroRow);

      wrap.appendChild(callout({
        tone: "accent",
        icon: "scale",
        title: "The product competes with the success fee, not the retainer.",
        body: "Success fees run " + fmt.money(adv.fee_per_year) + " a year — " +
              fmt.usd0(adv.fee_per_cargo) + " a cargo, charged at " +
              fmt.usd(econ.advisor_share_usd_mt) + "/t on a " +
              fmt.usd(econ.advisor_typical_saving_usd_mt, 0) + "/t saving the advisor claims — " +
              "against " + fmt.money(adv.retainer_year) + " a year of retainer. That is " +
              fmt.n(adv.fee_per_year / adv.retainer_year, 1) + "× the retainer, and it is " +
              "charged on a saving this desk already produces itself: the " +
              fmt.n(D.savings.length) + " actions below averaged " + fmt.usd(avg) + "/t.",
        extra: "The year figure assumes ten cargoes; the origin plan actually runs " +
               fmt.n(cargoesPerYear(), 1) + " a year across " +
               D.profile.origins.map(function (o) { return o.label; }).join(", ") + "."
      }));

      /* --------------------------------------------------------- the ledger */

      var ledger = GD.table({
        sortKey: "date",
        sortDir: "desc",
        emptyText: "No savings actions logged yet.",
        cols: [
          {
            key: "date", label: "Date", width: "96px",
            sortValue: function (r) { return r.date; },
            render: function (r) { return el("span", { class: "nowrap", text: fmt.dateShort(r.date) }); }
          },
          {
            key: "kind", label: "Lever", width: "108px",
            sortValue: function (r) { return r.kind; },
            render: function (r) {
              var m = kindMeta(r.kind);
              return GD.chip(m.label, m.tone, m.icon);
            }
          },
          { key: "supplier", label: "Supplier", width: "110px" },
          {
            key: "cls", label: "Class", width: "126px",
            render: function (r) { return el("span", { class: "nowrap", text: r.cls }); }
          },
          {
            key: "qty", label: "Qty", num: true, width: "92px",
            sortValue: function (r) { return r.qty; },
            render: function (r) { return fmt.n(r.qty) + " t"; }
          },
          {
            key: "usd_mt", label: "$/t saved", num: true, width: "92px",
            sortValue: function (r) { return r.usd_mt; },
            render: function (r) { return fmt.usd(r.usd_mt); }
          },
          {
            key: "total", label: "Total", num: true, width: "96px",
            sortValue: function (r) { return r.total; },
            render: function (r) {
              var cell = el("b", { class: GD.toneFor(r.total, true), text: fmt.money(r.total) });
              GD.tipOn(cell, "<b>" + GD.esc(fmt.usd0(r.total)) + "</b>" +
                "<div class='tip-row'><span class='tip-mute'>" + GD.esc(fmt.n(r.qty) + " t") +
                " × " + GD.esc(fmt.usd(r.usd_mt)) + "</span></div>");
              return cell;
            }
          },
          {
            key: "note", label: "What was done", sortable: false,
            render: function (r) {
              return el("div", {
                style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "max-width": "380px" },
                text: r.note
              });
            }
          }
        ],
        rows: D.savings
      });

      wrap.appendChild(GD.card({
        title: "Savings ledger",
        sub: "The desk's own record. Each total is quantity × the per-tonne saving — no supplier credit note confirms it.",
        flush: true,
        body: [ledger],
        foot: [
          icon("savings", 13, 1.75),
          el("span", { text: fmt.n(D.savings.length) + " actions" }),
          el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
          el("span", { text: fmt.n(qty) + " t" }),
          el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
          el("span", { text: fmt.usd(avg) + "/t weighted average" }),
          el("div", { style: { flex: "1" } }),
          el("span", { class: "label", text: "Total saved" }),
          el("b", {
            class: GD.toneFor(D.savings_total, true),
            style: { "font-size": "var(--t-head)", "font-variant-numeric": "tabular-nums" },
            text: fmt.usd0(D.savings_total)
          })
        ]
      }));

      /* ------------------------------------------------------- the breakdown */

      var top = kinds.slice().sort(function (a, b) { return b.total - a.total; })[0];
      var compoParts = kinds.map(function (k) {
        return { name: kindMeta(k.kind).label, value: k.total, color: kindMeta(k.kind).color };
      });
      var bar = GD.compo(compoParts, D.savings_total);
      /* core's compo tooltip is written for $/tonne segments; these segments are
         whole dollars, so re-attach a correct one. The later listener wins. */
      GD.qsa("span", bar).forEach(function (seg, i) {
        var p = compoParts.filter(function (x) { return x.value > 0; })[i];
        if (!p) return;
        GD.tipOn(seg, "<b>" + GD.esc(p.name) + "</b>" +
          "<div class='tip-row'><span class='tip-mute'>saved</span><b>" + GD.esc(fmt.usd0(p.value)) + "</b></div>" +
          "<div class='tip-row'><span class='tip-mute'>share of total</span><b>" +
          GD.esc(fmt.pct(p.value / D.savings_total)) + "</b></div>");
      });

      var chrono = D.savings.slice().sort(function (a, b) { return a.date < b.date ? -1 : 1; });
      var perTonne = chrono.map(function (s) { return s.usd_mt; });
      var bestIdx = 0, worstIdx = 0;
      perTonne.forEach(function (v, i) {
        if (v > perTonne[bestIdx]) bestIdx = i;
        if (v < perTonne[worstIdx]) worstIdx = i;
      });

      var breakdownBody = [
        bar,
        GD.legend(compoParts.map(function (p) { return { name: p.name, color: p.color }; })),
        el("div", {
          style: {
            display: "grid", gap: "0", "border-radius": "10px",
            border: "1px solid var(--border)", overflow: "hidden"
          }
        }, kinds.map(function (k, i) {
          var m = kindMeta(k.kind);
          return el("div", {
            style: {
              display: "flex", "align-items": "center", gap: "10px",
              padding: "9px 12px",
              "border-top": i ? "1px solid var(--border)" : "0",
              "background-color": "var(--surface)"
            }
          }, [
            swatch(m.color),
            el("b", { style: { "font-size": "var(--t-sm)", "min-width": "64px" }, text: m.label }),
            el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", flex: "1" },
                         text: m.blurb + " · " + fmt.n(k.count) + (k.count === 1 ? " action" : " actions") }),
            el("span", { class: "num", style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
                         text: fmt.pct(k.total / D.savings_total) }),
            el("b", { class: "num " + GD.toneFor(k.total, true), style: { "min-width": "66px", "text-align": "right" },
                      text: fmt.usd0(k.total) })
          ]);
        })),
        el("div", {
          style: {
            display: "flex", "align-items": "center", gap: "16px", "flex-wrap": "wrap",
            padding: "12px 14px", "border-radius": "10px",
            "background-color": "var(--surface-2)", border: "1px solid var(--border)"
          }
        }, [
          el("div", { style: { flex: "1", "min-width": "220px" } }, [
            el("div", { class: "label", style: { "margin-bottom": "4px" }, text: "Saving per action, $/t" }),
            el("p", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: "Oldest first. Tallest bar " + fmt.usd(perTonne[bestIdx]) + "/t on " +
                    fmt.date(chrono[bestIdx].date) + "; shortest " + fmt.usd(perTonne[worstIdx]) +
                    "/t on " + fmt.date(chrono[worstIdx].date) + ". Bars are scaled between those two."
            })
          ]),
          GD.chart.barSpark(perTonne, {
            w: 196, h: 48, gap: 5, take: perTonne.length, highlight: bestIdx,
            muted: "var(--border-2)", label: fmt.usd(perTonne[bestIdx]) + "/t"
          })
        ]),
        el("p", {
          style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
          text: kindMeta(top.kind).label + " is the lever that produced the most: " +
                fmt.usd0(top.total) + " of " + fmt.usd0(D.savings_total) + ", " +
                fmt.pct(top.total / D.savings_total) + " of the total, from " +
                fmt.n(top.count) + (top.count === 1 ? " action" : " actions") + " — " +
                "and its best single action was worth " + fmt.usd(top.best) + "/t. " +
                leastSentence(kinds)
        })
      ];

      wrap.appendChild(GD.card({
        title: "Which lever did the work",
        sub: "The same " + fmt.usd0(D.savings_total) + ", split by the kind of action that produced it.",
        body: [el("div", { style: { display: "grid", gap: "14px" } }, breakdownBody)]
      }));

      /* -------------------------------------------------- THE BENCHMARK PANEL */

      var benchRows = allBench();
      if (!benchRows.length) {
        wrap.appendChild(GD.card({
          title: "Peer benchmark",
          body: [el("div", { class: "empty" }, [
            icon("users", 20, 1.5),
            el("div", { text: "No approved-supplier offers are held, so no band can be inferred." })
          ])]
        }));
        return;
      }

      var state = { code: benchRows[0].code };
      var panelBody = el("div", { style: { display: "grid", gap: "16px" } });

      var picker = el("select", {
        class: "field", "aria-label": "Wheat class for the peer benchmark",
        onchange: function (ev) {
          state.code = ev.target.value;
          drawPanel();
          classTable.update(benchRows);
        }
      }, benchRows.slice().sort(function (a, b) {
        return a.cls.label < b.cls.label ? -1 : 1;
      }).map(function (b) {
        return el("option", { value: b.code, text: b.cls.code + " · " + b.cls.label,
                              selected: b.code === state.code ? true : null });
      }));

      function drawPanel() {
        GD.clear(panelBody);
        var b = benchFor(state.code);
        if (!b) return;

        var tone = GD.toneFor(b.edge, true);
        var port = b.anchor.port;
        var diff = portDiff(port);

        panelBody.appendChild(el("div", { class: "statline", style: {
          "border-radius": "10px", border: "1px solid var(--border)", overflow: "hidden"
        } }, [
          statCell("UFM best laid-in", fmt.usd(b.ufm) + " /t", "",
                   b.anchor.supplier + " · " + b.anchor.shipment),
          statCell("Estimated peer band", fmt.usd(b.floor) + " – " + fmt.usd(b.ceiling), "",
                   b.hasSpread ? "median → highest of " + fmt.n(b.offers.length) + " offers"
                               : "one offer only — no spread"),
          statCell("Below the band floor", fmt.usd(b.edge) + " /t", tone,
                   b.hasSpread ? "against the median offer" : "nothing to compare against"),
          statCell("On UFM's own tonnes", fmt.signedMoney(b.edgeUsd), tone,
                   fmt.n(b.tonnes) + " t a cargo" + (b.origin ? " · " + b.origin.label : ""))
        ]));

        if (b.hasSpread) {
          panelBody.appendChild(bandGauge(b));
          panelBody.appendChild(el("p", {
            style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
            text: "UFM's best laid-in offer for " + b.cls.code + " sits " + fmt.usd(b.edge) +
                  "/t below the floor of the inferred band and " + fmt.usd(b.spread) +
                  "/t below its ceiling. On " + fmt.n(b.tonnes) + " t that is " +
                  fmt.money(b.edgeUsd) + " against a mill that takes the middle of the market, " +
                  "and " + fmt.money(Math.round(b.spread * b.tonnes)) +
                  " against one that takes its primary supplier's first number."
          }));
          var note = ledgerNote(b);
          if (note) panelBody.appendChild(callout(note));
        } else {
          panelBody.appendChild(el("div", { class: "empty" }, [
            icon("filter", 20, 1.5),
            el("div", { text: "Only one offer is held for " + b.cls.code + " in this window, so there is no spread to infer a band from." }),
            el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" },
                        text: "Pick another class, or get a second approved supplier to quote the same laycan." })
          ]));
        }

        panelBody.appendChild(GD.kv([
          ["Class", b.cls.label + " · " + b.cls.code],
          ["Shipment window", b.anchor.shipment + " — offers sharing a calendar month"],
          ["Offers in the set", fmt.n(b.offers.length) + " from " + fmt.n(b.suppliers.length) +
            (b.suppliers.length === 1 ? " approved supplier" : " approved suppliers") +
            " (" + b.suppliers.join(", ") + ")"],
          ["Laid-in basis", "offer as received + " + portName(port) + " differential " +
            fmt.usd(diff) + "/t — UFM's own terminal table, not the supplier's"],
          ["Band floor", "the median offer we hold — a mill that shops two or three suppliers"],
          ["Band ceiling", "the highest offer we hold — a mill that takes its primary's first number"]
        ]));

        panelBody.appendChild(callout({
          icon: "alert",
          title: b.crossSupplier
            ? "Inferred from our own quote spread, not from what competitors paid."
            : "Inferred from one supplier's own offers — weaker still.",
          body: b.crossSupplier
            ? "This band is the spread of offers " + b.suppliers.join(" and ") +
              " showed UFM for the same class and laycan. It is a plausible range for a mill " +
              "that does not counter. It is not a measurement of what any other Thai mill paid, " +
              "and nothing in this dataset is."
            : "Every offer in this set came from " + b.suppliers[0] + ", so the band is that " +
              "one supplier's own price movement across dates and windows rather than a " +
              "cross-supplier spread. It is not a measurement of what any other Thai mill paid.",
          extra: "A benchmark that would survive a board meeting needs Thai customs import " +
                 "declarations, vessel line-ups, or a data-sharing arrangement between mills. " +
                 "None of the three is in this dataset."
        }));
      }

      /* the per-class comparison, which doubles as the selector */
      var classTable = GD.table({
        sortKey: "edge",
        sortDir: "desc",
        emptyText: "No classes with approved-supplier offers.",
        rowClass: function (r) { return r.code === state.code ? "is-selected" : null; },
        onRow: function (r) {
          state.code = r.code;
          picker.value = r.code;
          drawPanel();
          classTable.update(benchRows);
        },
        cols: [
          {
            key: "code", label: "Class", width: "150px",
            render: function (r) {
              return el("div", null, [
                el("b", { class: "nowrap", style: { color: "var(--ink)" }, text: r.cls.code }),
                el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-4)" },
                            text: r.cls.label })
              ]);
            }
          },
          {
            key: "origin", label: "Origin", width: "92px",
            sortValue: function (r) { return r.cls.origin; },
            render: function (r) { return GD.chip(r.origin ? r.origin.label : r.cls.origin, "outline"); }
          },
          {
            key: "evidence", label: "Evidence", width: "140px",
            sortValue: function (r) { return r.suppliers.length * 100 + r.offers.length; },
            render: function (r) {
              if (!r.hasSpread) return GD.chip("1 offer · no spread", "outline", "x");
              if (r.crossSupplier) {
                return GD.chip(fmt.n(r.suppliers.length) + " suppliers · " +
                               fmt.n(r.offers.length) + " offers", "info", "users");
              }
              return GD.chip("1 supplier · " + fmt.n(r.offers.length) + " offers", "warn", "alert");
            }
          },
          {
            key: "ufm", label: "UFM laid-in", num: true, width: "104px",
            render: function (r) { return el("b", { style: { color: "var(--ink)" }, text: fmt.usd(r.ufm) }); }
          },
          {
            key: "band", label: "Inferred band", num: true, width: "150px",
            sortValue: function (r) { return r.floor; },
            render: function (r) {
              return r.hasSpread
                ? el("span", { class: "nowrap", text: fmt.usd(r.floor) + " – " + fmt.usd(r.ceiling) })
                : el("span", { style: { color: "var(--ink-4)" }, text: "—" });
            }
          },
          {
            key: "edge", label: "Below floor", num: true, width: "100px",
            render: function (r) {
              return el("span", { class: "nowrap" }, [
                GD.dot(r.edge > 0 ? "good" : "warn"),
                el("b", { class: GD.toneFor(r.edge, true), style: { "margin-left": "6px" },
                          text: fmt.usd(r.edge) + "/t" })
              ]);
            }
          },
          {
            key: "edgeUsd", label: "On UFM tonnes", num: true, width: "118px",
            render: function (r) {
              var cell = el("b", { class: GD.toneFor(r.edgeUsd, true), text: fmt.signedMoney(r.edgeUsd) });
              GD.tipOn(cell, "<b>" + GD.esc(r.cls.code) + "</b>" +
                "<div class='tip-row'><span class='tip-mute'>below floor</span><b>" +
                GD.esc(fmt.usd(r.edge) + "/t") + "</b></div>" +
                "<div class='tip-row'><span class='tip-mute'>UFM tonnes a cargo</span><b>" +
                GD.esc(fmt.n(r.tonnes) + " t") + "</b></div>" +
                "<div class='tip-row'><span class='tip-mute'>= </span><b>" +
                GD.esc(fmt.usd0(r.edgeUsd)) + "</b></div>");
              return cell;
            }
          }
        ],
        rows: benchRows
      });

      drawPanel();

      wrap.appendChild(GD.card({
        title: "Peer laid-in cost — estimated, not measured",
        sub: "Where UFM's best laid-in offer sits against a band inferred from the spread of " +
             "offers approved suppliers showed us for the same class and laycan.",
        actions: [
          el("span", { class: "label", text: "Class" }),
          picker
        ],
        body: [panelBody]
      }));

      wrap.appendChild(GD.card({
        title: "Every class we hold offers for",
        sub: "Click a row to put that class in the panel above. Evidence quality is marked on each row — " +
             "a single-supplier band is weaker than a cross-supplier one.",
        flush: true,
        body: [classTable],
        foot: [
          icon("alert", 13, 1.75),
          el("span", { text: "Every band on this table is inferred from UFM's own quote book. None of it is what another mill paid." })
        ]
      }));

      /* ------------------------------------------------ what would make it real */

      var sources = [
        {
          icon: "file",
          title: "Thai customs import declarations",
          body: "Wheat import declarations carry importer, quantity and declared value per " +
                "shipment. That is a laid-in cost per tonne for every mill in the country, " +
                "from a source none of them controls.",
          meta: "Route: a licensed customs-data reseller, or a standing request against the " +
                "Customs Department's import statistics. Monthly, with a lag."
        },
        {
          icon: "ship",
          title: "Vessel line-ups and AIS",
          body: "Berth line-ups at " + D.profile.ports.map(function (p) { return p.code; }).join(", ") +
                " plus AIS tracks tie a cargo to a mill, an origin and a laycan. That dates a " +
                "competitor's purchase even before a price is attached to it.",
          meta: "Route: a shipbroker line-up feed, plus commercial AIS. Daily, and it is the " +
                "cheapest of the three."
        },
        {
          icon: "users",
          title: "Co-load cargo visibility",
          body: coloadSentence(),
          meta: "Route: a data-sharing arrangement inside the pool UFM is already in — the " +
                "allocation and freight split is a peer cost basis the mill is contractually " +
                "standing next to."
        }
      ];

      wrap.appendChild(GD.card({
        title: "What would make this real",
        sub: "Three sources that would turn the band above into a measurement. None of them is in this dataset today.",
        flush: true,
        body: sources.map(function (s) {
          return el("div", { class: "note-row" }, [
            el("span", {
              style: {
                width: "28px", height: "28px", "border-radius": "8px", flex: "none",
                display: "grid", "place-items": "center",
                "background-color": "var(--surface-3)", color: "var(--ink-2)"
              }
            }, [icon(s.icon, 15, 1.8)]),
            el("div", { class: "nr-body" }, [
              el("div", { class: "nr-head" }, [
                el("h4", { text: s.title }),
                GD.chip("not held", "outline", "x")
              ]),
              el("p", { text: s.body }),
              el("div", { class: "nr-meta", text: s.meta })
            ])
          ]);
        }),
        foot: [
          icon("shield", 13, 1.75),
          el("span", { text: "Until one of these lands, the benchmark panel is an estimate and is labelled as one on screen." })
        ]
      }));
    }
  });

  /* ----------------------------------------------------- sentence generators */

  /* Where the ledger has an action on the same class, the two halves of this page
     touch — and often the band floor IS the pre-counter offer, which means the two
     numbers are one event seen twice rather than two sources agreeing. Saying that
     out loud is the whole point of the screen. */
  function ledgerNote(b) {
    var rows = D.savings.filter(function (s) { return s.cls === b.code; })
      .sort(function (a, b2) { return a.date < b2.date ? 1 : -1; });
    if (!rows.length) return null;
    var r = rows[0];
    var m = kindMeta(r.kind).label.toLowerCase();

    var floorQ = null;
    b.offers.forEach(function (q) {
      if (laidIn(q) === b.floor && (!floorQ || q.received < floorQ.received)) floorQ = q;
    });
    var sameEvent = floorQ && Math.abs(r.usd_mt - b.edge) <= 0.25 &&
                    floorQ.supplier === b.anchor.supplier;

    if (sameEvent) {
      return {
        icon: "layers",
        title: "One event seen twice — not two sources agreeing.",
        body: "The median offer in this set, " + fmt.usd(b.floor) + ", is " +
              floorQ.supplier + "'s own earlier offer of " + fmt.date(floorQ.received) +
              ". The ledger logs a " + fmt.usd(r.usd_mt) + "/t " + m + " on " + b.cls.code +
              " on " + fmt.date(r.date) + ". The two numbers agree because they describe the " +
              "same negotiation, not because the quote book and the ledger confirm each other.",
        extra: r.note
      };
    }
    return {
      icon: "savings",
      title: "The ledger has an action on this class.",
      body: "On " + fmt.date(r.date) + " the desk logged a " + fmt.usd(r.usd_mt) + "/t " + m +
            " on " + b.cls.code + ", worth " + fmt.money(r.total) + ". The band above is built " +
            "from the quote book alone and does not use the ledger.",
      extra: r.note
    };
  }

  function cargoesPerYear() {
    var t = 0;
    D.profile.origins.forEach(function (o) { t += o.per_year; });
    return t;
  }

  function leastSentence(kinds) {
    if (kinds.length < 2) return "";
    var least = kinds.slice().sort(function (a, b) { return a.total - b.total; })[0];
    var rows = D.savings.filter(function (s) { return s.kind === least.kind; });
    var smallest = rows[0];
    rows.forEach(function (r) { if (r.qty < smallest.qty) smallest = r; });
    return kindMeta(least.kind).label + " contributed the least, " + fmt.usd0(least.total) +
           ", because the line that was challenged covered " + fmt.n(smallest.qty) +
           " t rather than a full " + fmt.n(D.profile.economics.cargo_mt) + " t cargo — " +
           "the same " + fmt.usd(smallest.usd_mt) + "/t on a cargo would have been " +
           fmt.money(Math.round(smallest.usd_mt * D.profile.economics.cargo_mt)) + ".";
  }

  function coloadSentence() {
    var shared = D.profile.origins.filter(function (o) { return o.ufm_mt < o.cargo_mt; });
    if (!shared.length) {
      return "UFM lifts whole cargoes, so there is no pool allocation to read a peer cost from today.";
    }
    var parts = shared.map(function (o) {
      return o.label + " " + fmt.n(o.cargo_mt) + " t with " + fmt.n(o.ufm_mt) +
             " t for UFM (" + fmt.pct(o.share, 0) + ")";
    });
    return "UFM already sits inside shared cargoes — " + parts.join(", ") +
           ". The pool carries " + shared[0].pool.join(", ") +
           ", so the allocation, the freight split and the laycan of several other mills pass " +
           "across UFM's own desk on every one of those vessels.";
  }

})();
