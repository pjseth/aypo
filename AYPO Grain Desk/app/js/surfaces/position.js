/* Grain Desk — Position Book.

   This screen is deliberately Pavin's own Excel tab, in his own words: flat portion
   and basis portion, lots, Filled and Open, take profit and loss cut, mark to market
   and breakeven. Nothing here is renamed into trading-desk English, because the point
   is that he recognises it on sight.

   What the workbook cannot do, this page does: it says how far past the loss cut the
   position is, and for how many days.

   Classic script, one IIFE, no modules. Every number comes from window.DESK. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, D = window.DESK;

  /* A bushel of wheat is 27.2155 kg, so one cent per bushel is $0.367437 a tonne.
     Used only to re-derive a figure the workbook already states, never to replace it. */
  var CENTS_TO_USD_MT = 0.367437;

  /* The line that justifies the whole surface. Stated once, verbatim. */
  var EXCEL_LINE = "Excel cannot tell him this";

  /* Two categorical hues for the two hedge instruments. Emerald is reserved for
     "good for the buyer", so it is not spent on a series name. */
  var SERIES_COLORS = ["var(--c-futures)", "var(--c-freight)"];

  /* ------------------------------------------------------------------ format */

  /* "−$378,749" — exact dollars, with a true minus sign. A ledger is read to the
     dollar; fmt.money() is for headlines. */
  function sgnUsd(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + fmt.usd0(Math.abs(v));
  }
  function sgnThb(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + fmt.thb(Math.abs(v), 0);
  }
  function cents(v, dp) {
    return v === null || v === undefined || isNaN(v) ? "—" : fmt.n(v, dp === undefined ? 2 : dp) + " ¢/bu";
  }
  function perT(v) {
    return v === null || v === undefined || isNaN(v) ? "—" : fmt.usd(v) + "/t";
  }
  function tonnes(v) {
    return v === null || v === undefined || isNaN(v) ? "—" : fmt.n(v, 0) + " t";
  }
  function plural(n, one, many) {
    return fmt.n(n, 0) + " " + (n === 1 ? one : many);
  }
  /* How long the position has been through the loss cut, or null when the workbook
     carries the breach without a date. Everything that prints the age goes through
     here, so a missing date drops the clause instead of printing "— days". */
  function breachDays(P) {
    return P.breached_on ? fmt.daysBetween(P.breached_on, D.built) : null;
  }
  function breachAge(P) {
    var d = breachDays(P);
    return d === null ? null : plural(d, "day", "days");
  }
  /* P&L: a positive number is the good outcome, so toneFor is inverted. */
  function pnlTone(v) { return GD.toneFor(v, true); }

  /* ------------------------------------------------------------------ pieces */

  function strong(text, toneClass) {
    return el("b", { class: "num " + (toneClass || ""), text: text });
  }
  function muted(text) {
    return el("div", { class: "card-sub", text: text });
  }
  function labelled(text) {
    return el("div", { class: "label", text: text });
  }
  /* Every style key here is kebab-case on purpose: GD.el writes them through
     style.setProperty, which ignores camelCase. */
  function merge(base, extra) {
    if (extra) Object.keys(extra).forEach(function (k) { base[k] = extra[k]; });
    return base;
  }
  /* An inset panel inside a card body. Radius stays under the card's so the
     corners read as concentric rather than pinched. */
  function panel(kids, extra) {
    return el("div", {
      style: merge({
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
        "border-radius": "var(--r-inner)",
        padding: "12px 13px"
      }, extra)
    }, kids);
  }
  function hairline() {
    return el("div", { class: "hr", role: "presentation" });
  }
  function stack(gap, kids) {
    return el("div", { style: { display: "grid", gap: gap + "px" } }, kids);
  }
  function row(gap, kids, extra) {
    return el("div", {
      style: merge({ display: "flex", "align-items": "center", gap: gap + "px", "flex-wrap": "wrap" }, extra)
    }, kids);
  }

  /* GD.stat paints its value in ink. A position is a signed number, so it gets the tone. */
  function statToned(cfg, toneClass) {
    var node = GD.stat(cfg);
    if (toneClass) {
      var v = GD.qs(".stat-value", node);
      if (v) v.classList.add(toneClass);
    }
    return node;
  }

  /* ------------------------------------------------------------------- lookup */

  function portLabel(code) {
    var hit = (D.profile.ports || []).filter(function (p) { return p.code === code; })[0];
    return hit ? hit.name + " (" + hit.code + ")" : String(code || "—");
  }

  /* His workbook books the hedge on the September contract (MWU26, KWU26); the live
     screen quotes December (MWZ26, KWZ26). Match on the two-character root, which is
     the instrument family, and say so on screen rather than pretending they are one. */
  function liveFor(legKey) {
    if (!legKey) return null;
    var root = String(legKey).slice(0, 2).toUpperCase();
    var hits = D.instruments.filter(function (ins) {
      return ins.role === "hedge" && String(ins.key).slice(0, 2).toUpperCase() === root;
    });
    return hits[0] || null;
  }

  function blockTitle(b) {
    return (String(b.cls || "") + " " + String(b.spec || "")).replace(/\s+/g, " ").trim();
  }

  function allLegs() {
    var out = [];
    D.position.blocks.forEach(function (b) {
      b.legs.forEach(function (l) { out.push({ block: b, leg: l }); });
    });
    return out;
  }

  function sumBy(list, key) {
    return list.reduce(function (s, x) { return s + (x[key] || 0); }, 0);
  }

  /* ============================================================== leg detail */

  /* "Show me the arithmetic." The flat price is futures plus basis, and the workbook's
     $/t for the futures leg re-derives from its own cent price — worth proving, because
     everywhere else on this desk the split is reconstructed rather than quoted. */
  function showLeg(b, leg) {
    var live = liveFor(b.instrument);
    var derived = leg.cents * CENTS_TO_USD_MT;
    var gap = derived - leg.usd_mt;
    var distance = live ? live.last - leg.cents : null;

    GD.sheet({
      title: b.instrument + " · " + fmt.n(leg.lots, 0) + " lots · " + leg.status,
      sub: blockTitle(b) + " · booked " + fmt.date(leg.date),
      body: function () {
        return [
          GD.kv([
            ["Date", fmt.date(leg.date)],
            ["Lots", fmt.n(leg.lots, 0)],
            ["Status", leg.status === "Open"
              ? row(6, [GD.dot("good"), GD.chip("Open", "accent")])
              : GD.chip("Filled", "outline", "check")],
            ["Futures", cents(leg.cents)],
            ["Futures in $/t", perT(leg.usd_mt)],
            ["Basis", perT(leg.basis)],
            ["Flat", perT(leg.flat)],
            ["Gain / loss", leg.pnl === null || leg.pnl === undefined
              ? el("span", { class: "val-mute", text: "not booked against this leg" })
              : el("span", { class: pnlTone(leg.pnl), text: sgnUsd(leg.pnl) })]
          ]),
          hairline(),
          labelled("How the flat price is put together"),
          panel([
            stack(5, [
              row(8, [el("span", { class: "card-sub", text: "futures" }), strong(cents(leg.cents)),
                el("span", { class: "card-sub", text: "× $0.367437" }), strong(perT(leg.usd_mt))]),
              row(8, [el("span", { class: "card-sub", text: "+ basis" }), strong(perT(leg.basis))]),
              hairline(),
              row(8, [el("span", { class: "card-sub", text: "= flat" }), strong(perT(leg.flat))])
            ])
          ]),
          el("p", { class: "card-sub", style: { "margin-top": "9px" } },
            "A bushel of wheat is 27.2155 kg, so one cent a bushel is $0.367437 a tonne. " +
            "The workbook's own $/t for this leg re-derives from its cent price to " +
            fmt.usd(derived) + " — the same cent, a difference of " +
            fmt.usd(Math.abs(gap), 3) + "."),
          leg.status === "Open" && live ? el("div", { style: { "margin-top": "16px" } }, [
            labelled("Resting order"),
            panel([
              el("div", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" } }, [
                el("span", { text: fmt.n(leg.lots, 0) + " lots are still resting at " }),
                strong(cents(leg.cents)),
                el("span", { text: ". " + live.key + " last printed " }),
                strong(cents(live.last)),
                el("span", { text: ", " }),
                el("span", { class: "num", text: fmt.n(Math.abs(distance), 2) + " ¢/bu" }),
                el("span", {
                  text: " " + (distance > 0 ? "above" : distance < 0 ? "below" : "level with") + " the order."
                })
              ])
            ], { "margin-top": "7px" })
          ]) : null
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary btn-pill", type: "button", "data-autofocus": "true",
            onclick: close
          }, [icon("check", 14, 2), "Close"]),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Read off the PROCUREMENT TRACKER workbook" })
        ];
      }
    });
  }

  /* ========================================================== escalation note */

  /* The breach is 11 days old and nobody has had to look at a spreadsheet to know it.
     The agent writes the note; Pavin sends it. */
  function escalate() {
    var P = D.position;
    var age = breachAge(P);
    var mw = P.blocks[0], kw = P.blocks[1];
    var open = allLegs().filter(function (x) { return x.leg.status === "Open"; });

    var draft =
      "Khun Pracha — the US Aug–Sep '26 futures position is marked at " + sgnUsd(P.total_usd) +
      " against the " + sgnUsd(P.loss_cut_usd) + " loss cut. " +
      (P.breached_on
        ? "It went through the cut on " + fmt.date(P.breached_on) +
          (age ? " and has been through it for " + age : "") + ". "
        : "") +
      (mw ? fmt.n(mw.filled_lots, 0) + " lots filled on " + mw.instrument : "") +
      (kw ? " and " + fmt.n(kw.filled_lots, 0) + " on " + kw.instrument : "") + ". " +
      (open.length
        ? fmt.n(sumBy(open.map(function (x) { return x.leg; }), "lots"), 0) +
          " lots are still resting at " + fmt.n(open[0].leg.cents, 2) + ". "
        : "") +
      "I am not lifting or closing anything until you and I have spoken. — Pavin";

    GD.sheet({
      title: P.breach ? "Loss cut breached" : "Position note",
      sub: P.breach
        ? "Through " + sgnUsd(P.loss_cut_usd) + " on " + fmt.date(P.breached_on) +
          (age ? " · " + age + " ago" : "")
        : "Inside both thresholds",
      body: function () {
        return [
          GD.kv([
            ["Position", el("span", { class: pnlTone(P.total_usd), text: sgnUsd(P.total_usd) })],
            ["In baht", el("span", { class: pnlTone(P.total_thb), text: sgnThb(P.total_thb) })],
            ["FX used", fmt.n(P.fx, 2) + " THB per USD"],
            ["Loss cut", el("span", { class: "val-up", text: sgnUsd(P.loss_cut_usd) })],
            ["Take profit", el("span", { class: "val-down", text: sgnUsd(P.take_profit_usd) })],
            ["Headroom to take profit", fmt.usd0(P.headroom_to_tp)],
            /* The day count, not fmt.ago: ago() rounds 11 days to "last week", which
               would contradict the "11 days" this same sheet states in its subtitle. */
            ["Breached on", P.breached_on
              ? fmt.date(P.breached_on) + (age ? " · " + age + " ago" : "")
              : "—"],
            ["Cargo", P.cargo + " · " + P.counterparty]
          ]),
          hairline(),
          labelled("Drafted, not sent"),
          panel([
            muted("To: " + D.profile.head_of_procurement),
            el("div", {
              style: { "font-size": "var(--t-sm)", color: "var(--ink-2)", "margin-top": "6px" },
              text: draft
            })
          ], { "margin-top": "7px" }),
          el("p", { class: "card-sub", style: { "margin-top": "10px" } },
            "Every figure in the note is the figure on this page. The desk drafts; it does not send, " +
            "and it never closes a position.")
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary btn-pill", type: "button", "data-autofocus": "true",
            onclick: function () {
              close();
              GD.toast("Note sent to " + D.profile.head_of_procurement, { icon: "send" });
            }
          }, [icon("send", 14, 2), "Send the note"]),
          el("button", {
            class: "btn", type: "button", text: "Edit first",
            onclick: function () { GD.toast("Opened for editing", { icon: "file" }); }
          }),
          el("button", { class: "btn btn-ghost", type: "button", text: "Later", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Drafts only. Pavin sends." })
        ];
      }
    });
  }

  /* ============================================================ legs as text */

  function exportLegs() {
    var lines = ["block\tinstrument\tdate\tlots\tstatus\tcents_bu\tusd_mt\tbasis_usd_mt\tflat_usd_mt\tpnl_usd"];
    D.position.blocks.forEach(function (b) {
      b.legs.forEach(function (l) {
        lines.push([
          blockTitle(b), b.instrument, l.date, l.lots, l.status,
          l.cents, l.usd_mt, l.basis, l.flat,
          l.pnl === null || l.pnl === undefined ? "" : l.pnl
        ].join("\t"));
      });
    });
    var text = lines.join("\n");
    var count = lines.length - 1;
    var field = null;

    GD.sheet({
      title: "Legs as tab-separated text",
      sub: plural(count, "leg", "legs") + " across " +
        plural(D.position.blocks.length, "block", "blocks") + " · paste straight back into Excel",
      body: function () {
        var ta = el("textarea", {
          readonly: true, "aria-label": "Futures legs as tab-separated values",
          "data-autofocus": "true",
          style: {
            width: "100%", "min-height": "230px", padding: "12px",
            "font-family": "var(--mono)", "font-size": "11.5px", "line-height": "1.7",
            border: "1px solid var(--border)", "border-radius": "var(--r-inner)",
            background: "var(--surface-2)", color: "var(--ink)",
            resize: "vertical", "white-space": "pre"
          }
        });
        ta.value = text;
        field = ta;
        return [
          ta,
          el("p", { class: "card-sub", style: { "margin-top": "9px" } },
            "Columns are the workbook's own. Gain / loss is left blank where the workbook " +
            "books none against that leg.")
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary btn-pill", type: "button",
            onclick: function () {
              var ok = false;
              try {
                if (field) { field.focus(); field.select(); }
                if (navigator.clipboard && navigator.clipboard.writeText) {
                  var p = navigator.clipboard.writeText(text);
                  if (p && p.catch) p.catch(function () { /* denied — the text is still selected */ });
                  ok = true;
                } else if (document.execCommand) {
                  ok = document.execCommand("copy");
                }
              } catch (e) { ok = false; }
              GD.toast(ok ? count + " legs copied" : "Selected — press Ctrl+C to copy",
                { icon: ok ? "check" : "file" });
            }
          }, [icon("file", 14, 2), "Copy"]),
          el("button", { class: "btn btn-ghost", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Nothing leaves the browser." })
        ];
      }
    });
  }

  /* ============================================================ threshold bar

     One horizontal scale carrying four real numbers: the loss cut, breakeven, the take
     profit, and where the position actually is. The scale is built from plain divs and
     inline styles — no new CSS. Every level is named with its number in the key beneath,
     and the breached stretch is striped as well as coral, so the card reads with the
     colour switched off. */

  function thresholdBar() {
    var P = D.position;
    var pos = P.total_usd, lc = P.loss_cut_usd, tp = P.take_profit_usd;

    var lo = Math.min(pos, lc, 0), hi = Math.max(pos, tp, 0);
    var span = (hi - lo) || 1;
    lo -= span * 0.08;
    hi += span * 0.08;
    span = hi - lo;
    function x(v) { return Math.max(0, Math.min(100, ((v - lo) / span) * 100)); }

    var xPos = x(pos), xLc = x(lc), xZero = x(0), xTp = x(tp);
    var markerColor = pos < 0 ? "var(--up)" : pos > 0 ? "var(--accent)" : "var(--ink-3)";

    /* ---- callout row: a pill naming the position and a stem down onto the marker.
       The pill sits at the top of the row and the stem runs out of its underside to
       the marker — the stem is drawn first so the pill covers the part behind it.
       Within 14% of either end the pill is anchored to that end rather than centred
       on the marker, which is what keeps it from clipping out of the card. */
    var pillStyle = { position: "absolute", top: "0", "white-space": "nowrap" };
    if (xPos < 14) pillStyle.left = "0";
    else if (xPos > 86) pillStyle.right = "0";
    else { pillStyle.left = xPos.toFixed(2) + "%"; pillStyle.transform = "translateX(-50%)"; }

    var callout = el("div", { style: { position: "relative", height: "32px" } }, [
      el("div", {
        /* translateX(-50%) so the 1px stem is centred on the marker, which is also
           centred on its own position — without it the stem sits half a pixel right
           of the dot it is pointing at. */
        style: {
          position: "absolute", left: xPos.toFixed(2) + "%", top: "20px", bottom: "-4px",
          width: "1px", transform: "translateX(-50%)", "background-color": "var(--border-2)"
        },
        "aria-hidden": "true"
      }),
      el("div", {
        style: merge({
          background: "var(--black)", color: "var(--on-black)",
          "border-radius": "var(--r-chip)", padding: "4px 9px",
          "font-size": "var(--t-xs)", "font-weight": "600",
          "font-variant-numeric": "tabular-nums",
          "box-shadow": "var(--sh-md)"
        }, pillStyle)
      }, [el("span", { text: "Now " + sgnUsd(pos) })])
    ]);

    /* ---- the track itself */
    function band(from, to, fill, stripe, name, tip) {
      var style = {
        position: "absolute", top: "0", bottom: "0",
        left: from.toFixed(3) + "%", width: Math.max(0, to - from).toFixed(3) + "%",
        "background-color": fill
      };
      if (stripe) {
        style["background-image"] =
          "repeating-linear-gradient(135deg, rgba(120,120,132,0.16) 0 5px, rgba(120,120,132,0) 5px 10px)";
      }
      var node = el("div", { style: style, "aria-hidden": "true" });
      GD.tipOn(node, "<b>" + GD.esc(name) + "</b><div class='tip-mute'>" + GD.esc(tip) + "</div>");
      return node;
    }

    /* A 2px rule is unhittable with a mouse, so the visible tick sits inside a
       transparent 14px box. The three boxes are 40px apart on screen, so no two
       hit areas overlap. */
    function tick(at, color, name, value) {
      var node = el("div", {
        style: {
          position: "absolute", top: "0", height: "24px", width: "14px",
          left: at.toFixed(3) + "%", transform: "translateX(-50%)",
          display: "grid", "place-items": "center"
        },
        "aria-hidden": "true"
      }, [
        el("div", {
          style: {
            width: "2px", height: "24px", "border-radius": "1px",
            "background-color": color
          }
        })
      ]);
      GD.tipOn(node, "<b>" + GD.esc(name) + "</b><div class='tip-row'><span class='tip-mute'>level</span><b>" +
        GD.esc(value) + "</b></div>");
      return node;
    }

    var track = el("div", {
      style: {
        position: "absolute", left: "0", right: "0", top: "6px", height: "12px",
        "border-radius": "var(--r-pill)", background: "var(--surface-3)", overflow: "hidden"
      }
    }, [
      band(0, xLc, "var(--up-soft)", true, "Through the loss cut",
        "Anything left of the loss cut is a position Pavin said he would not carry."),
      band(xTp, 100, "var(--accent-soft)", false, "Above the take profit",
        "Right of here the position is worth taking off."),
      band(xLc, xTp, "transparent", false, "Inside the thresholds",
        "Between " + sgnUsd(lc) + " and " + sgnUsd(tp) + " — no action written down.")
    ]);

    var marker = el("div", {
      style: {
        position: "absolute", left: xPos.toFixed(2) + "%", top: "4px",
        width: "16px", height: "16px", "border-radius": "50%",
        transform: "translateX(-50%)",
        "background-color": markerColor,
        border: "2.5px solid var(--surface)",
        "box-shadow": "var(--sh-md)"
      },
      "aria-hidden": "true"
    });
    GD.tipOn(marker, "<b>Position</b><div class='tip-row'><span class='tip-mute'>marked</span><b>" +
      GD.esc(sgnUsd(pos)) + "</b></div><div class='tip-row'><span class='tip-mute'>in baht</span><b>" +
      GD.esc(sgnThb(P.total_thb)) + "</b></div>");

    var trackRow = el("div", {
      role: "img",
      "aria-label": "Position " + sgnUsd(pos) + " against a loss cut of " + sgnUsd(lc) +
        ", breakeven at zero and a take profit of " + sgnUsd(tp) +
        (P.breach && P.breached_on ? ". The loss cut was breached on " + fmt.date(P.breached_on) + "." : "."),
      style: { position: "relative", height: "24px" }
    }, [
      track,
      tick(xLc, "var(--up)", "Loss cut", sgnUsd(lc)),
      tick(xZero, "var(--ink-4)", "Breakeven", fmt.usd0(0)),
      tick(xTp, "var(--accent)", "Take profit", sgnUsd(tp)),
      marker
    ]);

    /* Every level on the scale, named and with its number, so the card reads with
       the colour switched off. The scale's own ends are not labelled: they are the
       position and the take profit plus 8% of padding, and labelling padding as a
       level would be the one untrue thing on the card. */
    var key = el("div", { class: "legend", style: { "margin-top": "16px", gap: "6px 18px" } }, [
      keyItem("var(--up)", "Loss cut", sgnUsd(lc)),
      keyItem("var(--ink-4)", "Breakeven", fmt.usd0(0)),
      keyItem("var(--accent)", "Take profit", sgnUsd(tp)),
      keyItem(markerColor, "Position now", sgnUsd(pos))
    ]);

    return stack(0, [callout, trackRow, key]);
  }

  function keyItem(color, name, value) {
    return el("span", null, [
      el("i", {
        style: {
          width: "3px", height: "13px", "border-radius": "2px",
          "background-color": color, flex: "none"
        }
      }),
      el("span", { text: name }),
      el("b", { class: "num", style: { color: "var(--ink)" }, text: value })
    ]);
  }

  /* ================================================================== blocks */

  function splitBar(b) {
    /* GD.compo wants shares; these are shares of a block, not dollars a tonne. */
    var bar = GD.compo([
      { name: "Flat portion", value: b.flat_pct, color: "var(--c-futures)" },
      { name: "Basis portion", value: b.basis_pct, color: "var(--c-basis)" }
    ], 1);

    var parts = [
      { name: "Flat portion", pct: b.flat_pct, qty: b.flat_qty, lots: b.flat_contracts },
      { name: "Basis portion", pct: b.basis_pct, qty: b.basis_qty, lots: b.basis_contracts }
    ].filter(function (p) { return p.pct > 0; });

    /* compo labels and tips its own segments in dollars per tonne, which these shares
       are not. Swapping each segment for a clone drops those listeners and that
       aria-label with them, instead of leaving two tooltips racing on one node. */
    GD.qsa("span", bar).forEach(function (original, i) {
      var p = parts[i];
      var seg = original.cloneNode(true);
      original.parentNode.replaceChild(seg, original);
      if (!p) return;
      var text = p.name + " " + fmt.pct(p.pct, 0) + ", " + tonnes(p.qty) + ", " + fmt.n(p.lots, 0) + " lots";
      seg.setAttribute("aria-label", text);
      GD.tipOn(seg, "<b>" + GD.esc(p.name) + "</b>" +
        "<div class='tip-row'><span class='tip-mute'>share</span><b>" + GD.esc(fmt.pct(p.pct, 0)) + "</b></div>" +
        "<div class='tip-row'><span class='tip-mute'>quantity</span><b>" + GD.esc(tonnes(p.qty)) + "</b></div>" +
        "<div class='tip-row'><span class='tip-mute'>contracts</span><b>" + GD.esc(fmt.n(p.lots, 0) + " lots") + "</b></div>");
    });

    function side(color, name, pct, qty, lots, align) {
      return el("div", { style: { "min-width": "0", "text-align": align || "left" } }, [
        row(6, [
          el("i", {
            style: {
              width: "9px", height: "9px", "border-radius": "3px",
              "background-color": color, flex: "none", display: "inline-block"
            }
          }),
          el("span", { class: "label", text: name })
        ], align === "right" ? { "justify-content": "flex-end" } : null),
        el("div", {
          class: "num", style: { "font-size": "var(--t-sm)", "margin-top": "3px", color: "var(--ink)" },
          text: fmt.pct(pct, 0) + " · " + tonnes(qty) + " · " + fmt.n(lots, 0) + " lots"
        })
      ]);
    }

    /* Block 2 carries a 70/30 split but books every lot against the flat portion.
       That is how his workbook has it, so it is shown as-is and explained rather
       than quietly tidied into agreement. */
    var oddity = (b.basis_pct > 0 && b.basis_contracts === 0)
      ? muted("The split is 70/30 on this block, but all " + fmt.n(b.total_contracts, 0) +
              " lots are booked against the flat portion. Shown the way he keeps it.")
      : null;

    return stack(10, [
      bar,
      el("div", { style: { display: "flex", "justify-content": "space-between", gap: "14px" } }, [
        side("var(--c-futures)", "Flat portion", b.flat_pct, b.flat_qty, b.flat_contracts),
        side("var(--c-basis)", "Basis portion", b.basis_pct, b.basis_qty, b.basis_contracts, "right")
      ]),
      oddity
    ]);
  }

  /* "Where are we in the range" — the one question a lone last price cannot answer.
     Thin bars, the newest one highlighted, and the window's own high and low written
     out underneath so a bar sitting flat on the floor reads as "at the low", not as a
     chart that failed to draw. No label on the spark itself: the SVG clips anything
     wider than four characters at its right edge. */
  function rangePanel(b) {
    var live = liveFor(b.instrument);
    if (!live) return null;
    var WINDOW = 28;
    var recent = live.history.slice(-WINDOW);
    var ext = GD.chart.extent(recent);
    var toBe = live.last - b.mtm;
    var place = (live.last - ext[0]) / ((ext[1] - ext[0]) || 1);
    var where = place <= 0.15 ? "at the low" : place >= 0.85 ? "at the high" : "mid-range";

    /* The muted bars default to --surface-3, which is all but invisible on a white
       card. --border-2 is the same neutral family with enough contrast to read.
       The newest bar is emphasised in ink, not in a hue: emerald and coral mean
       "good or bad for this position" everywhere else on the page, and the day's
       own direction is the opposite sign to the position's — the sentence under
       the spark is where that gets said, in words, with the tone on it. */
    var spark = GD.chart.barSpark(live.history, {
      w: 240, h: 40, take: WINDOW, muted: "var(--border-2)", color: "var(--ink-2)"
    });
    GD.tipOn(spark, "<b>" + GD.esc(live.key) + "</b><div class='tip-row'><span class='tip-mute'>last</span><b>" +
      GD.esc(cents(live.last)) + "</b></div><div class='tip-row'><span class='tip-mute'>" + WINDOW +
      "-session high</span><b>" + GD.esc(fmt.n(ext[1], 2)) + "</b></div>" +
      "<div class='tip-row'><span class='tip-mute'>" + WINDOW + "-session low</span><b>" +
      GD.esc(fmt.n(ext[0], 2)) + "</b></div>" +
      "<div class='tip-row'><span class='tip-mute'>this block's breakeven</span><b>" +
      GD.esc(fmt.n(b.mtm, 2)) + "</b></div>");

    return stack(8, [
      row(8, [
        labelled(live.key + " · last " + WINDOW + " sessions"),
        el("span", { style: { flex: "1" } }),
        el("span", { class: "num", style: { "font-weight": "600" }, text: cents(live.last) })
      ]),
      spark,
      row(8, [
        el("span", { class: "card-sub", text: "Range over " + WINDOW + " sessions" }),
        GD.chip(where, "outline"),
        el("span", { style: { flex: "1" } }),
        el("span", { class: "num card-sub", text: fmt.n(ext[0], 2) + " – " + fmt.n(ext[1], 2) })
      ]),
      el("div", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" } }, [
        el("span", { text: "Sits " }),
        strong(fmt.n(Math.abs(toBe), 2) + " ¢/bu", pnlTone(toBe)),
        el("span", {
          text: " " + (toBe < 0 ? "below" : toBe > 0 ? "above" : "level with") + " this block's "
        }),
        el("span", { class: "num", text: fmt.n(b.mtm, 2) }),
        el("span", { text: " breakeven." })
      ])
    ]);
  }

  function restingNote(b) {
    var live = liveFor(b.instrument);
    var open = b.legs.filter(function (l) { return l.status === "Open"; });

    if (!open.length) {
      return row(7, [
        GD.dot(),
        el("span", { class: "card-sub", text: "No resting orders — all " + fmt.n(b.filled_lots, 0) + " lots filled." })
      ]);
    }

    return stack(7, open.map(function (l) {
      var distance = live ? live.last - l.cents : null;
      return panel([
        row(7, [
          GD.dot("good"),
          GD.chip("Open", "accent"),
          el("span", { class: "label", text: fmt.n(l.lots, 0) + " lots resting" })
        ]),
        el("div", {
          style: { "font-size": "var(--t-sm)", color: "var(--ink-2)", "margin-top": "7px" }
        }, [
          el("span", { text: "Resting at " }),
          strong(cents(l.cents)),
          live ? el("span", { text: ". " + live.key + " last " }) : null,
          live ? strong(cents(live.last)) : null,
          live ? el("span", { text: " — " }) : null,
          live ? el("span", { class: "num", text: fmt.n(Math.abs(distance), 2) + " ¢/bu" }) : null,
          live ? el("span", {
            text: " " + (distance > 0 ? "above the order." : distance < 0 ? "below the order." : "level with the order.")
          }) : el("span", { text: "." })
        ])
      ], { background: "var(--accent-soft)", "border-color": "var(--accent-line)" });
    }));
  }

  function legsTable(b) {
    function pnlCell(l) {
      if (l.pnl === null || l.pnl === undefined) {
        var dash = el("span", { class: "val-mute", text: "—" });
        GD.tipOn(dash, "The workbook books no gain or loss against this leg.");
        return dash;
      }
      /* To the dollar, not fmt.money's "+$4k": this column is the ledger. */
      return el("span", { class: "num " + pnlTone(l.pnl), text: sgnUsd(l.pnl) });
    }

    return GD.table({
      cols: [
        {
          key: "date", label: "Date", width: "112px",
          render: function (l) { return fmt.date(l.date); },
          sortValue: function (l) { return l.date; }
        },
        { key: "lots", label: "Lots", num: true, render: function (l) { return fmt.n(l.lots, 0); } },
        {
          key: "status", label: "Status",
          render: function (l) {
            return l.status === "Open"
              ? row(6, [GD.dot("good"), GD.chip("Open", "accent")])
              : GD.chip("Filled", "outline", "check");
          },
          sortValue: function (l) { return l.status; }
        },
        { key: "cents", label: "¢/bu", num: true, render: function (l) { return fmt.n(l.cents, 2); } },
        { key: "usd_mt", label: "$/t", num: true, render: function (l) { return fmt.usd(l.usd_mt); } },
        { key: "basis", label: "Basis", num: true, render: function (l) { return fmt.usd(l.basis); } },
        { key: "flat", label: "Flat", num: true, render: function (l) { return fmt.usd(l.flat); } },
        { key: "pnl", label: "Gain / loss", num: true, render: pnlCell }
      ],
      rows: b.legs,
      onRow: function (l) { showLeg(b, l); },
      emptyText: "No legs booked in this block."
    });
  }

  function blockCard(b) {
    var live = liveFor(b.instrument);
    var impliedBasis = el("span", null, [
      el("span", { text: cents(b.implied_basis, 1) + " " }),
      GD.chip("reconstructed", "outline")
    ]);
    GD.tipOn(impliedBasis, "<b>Implied basis</b><div class='tip-mute'>Flat less the futures settlement. " +
      "Derived here from his own two numbers — Mitsui never quoted it as a separate line.</div>");

    var mtmValue = el("span", null, [
      el("span", { class: "num", text: cents(b.mtm) + " " }),
      GD.chip(b.mtm_label, "info")
    ]);
    GD.tipOn(mtmValue, "<b>Mark to market</b><div class='tip-mute'>The futures level at which this block " +
      "breaks even. His word, his number.</div>");

    var facts = GD.kv([
      ["Flat price", perT(b.flat_price)],
      ["Futures settlement", cents(b.futures_settlement)],
      ["Implied basis", impliedBasis],
      ["Quantity — total / flat / basis",
        fmt.n(b.total_qty, 0) + " / " + fmt.n(b.flat_qty, 0) + " / " + fmt.n(b.basis_qty, 0) + " t"],
      ["Contracts — total / flat / basis",
        fmt.n(b.total_contracts, 0) + " / " + fmt.n(b.flat_contracts, 0) + " / " + fmt.n(b.basis_contracts, 0) + " lots"],
      ["Remaining", b.remaining === 0
        ? el("span", null, [el("span", { text: "0 t " }), GD.chip("fully priced", "good", "check")])
        : tonnes(b.remaining)],
      ["Avg futures", cents(b.avg_futures)],
      ["Avg flat", perT(b.avg_flat)],
      ["Mark to market", mtmValue]
    ]);

    var left = stack(16, [
      stack(9, [labelled("Flat portion vs basis portion"), splitBar(b)]),
      hairline(),
      rangePanel(b),
      restingNote(b)
    ]);

    var legsBox = el("div", {
      style: {
        border: "1px solid var(--border)", "border-radius": "var(--r-inner)",
        overflow: "hidden"
      }
    }, [legsTable(b)]);

    return GD.card({
      title: blockTitle(b),
      sub: fmt.n(b.filled_lots, 0) + " lots filled" +
        (b.open_lots ? " · " + fmt.n(b.open_lots, 0) + " open" : "") +
        " · booked as " + b.instrument + (live ? ", quoted live as " + live.key : ""),
      actions: [
        GD.chip("Flat " + perT(b.flat_price), "outline"),
        b.open_lots ? row(6, [GD.dot("good"), GD.chip(fmt.n(b.open_lots, 0) + " open", "accent")]) : null
      ],
      body: [stack(16, [
        el("div", { class: "grid g-2" }, [left, facts]),
        stack(9, [labelled("Legs — every fill, in workbook order"), legsBox]),
        muted("The workbook books this hedge on " + b.instrument +
          (live ? "; the live screen quotes " + live.key + ". Matched on the " +
            String(b.instrument).slice(0, 2) + " root, not assumed to be the same contract." : "."))
      ])],
      foot: [
        el("span", { class: "label", text: "Block subtotal" }),
        el("b", { class: "num " + pnlTone(b.subtotal_usd), text: sgnUsd(b.subtotal_usd) }),
        el("span", { style: { flex: "1" } }),
        el("span", {
          class: "card-sub",
          text: tonnes(b.total_qty) + " · " + plural(b.legs.length, "leg", "legs") +
            " · open a leg for the arithmetic"
        })
      ]
    });
  }

  /* =================================================================== chart */

  function chartCard() {
    var list = [];
    D.position.blocks.forEach(function (b) {
      var live = liveFor(b.instrument);
      if (live && list.indexOf(live) < 0) list.push(live);
    });
    if (!list.length) {
      list = D.instruments.filter(function (i) { return i.role === "hedge"; }).slice(0, 2);
    }

    var range = 90;
    var host = el("div");

    function draw() {
      GD.clear(host);
      if (!list.length) {
        host.appendChild(el("div", { class: "empty" }, [
          icon("market", 20, 1.5), el("div", { text: "No hedge instrument matches these blocks." })
        ]));
        return;
      }
      host.appendChild(GD.chart.line({
        series: list.map(function (ins, i) {
          return {
            name: ins.key,
            values: ins.history.slice(-range),
            color: SERIES_COLORS[i % SERIES_COLORS.length]
          };
        }),
        dates: list[0].dates.slice(-range),
        w: 900, h: 270,
        fmtY: function (v) { return fmt.n(v, 0) + "¢"; },
        fmtTip: function (v) { return cents(v); },
        label: "Hedge instruments over the last " + range + " sessions, cents per bushel"
      }));
    }
    draw();

    var legend = GD.legend(list.map(function (ins, i) {
      return { name: ins.key + " — " + ins.label, color: SERIES_COLORS[i % SERIES_COLORS.length] };
    }));

    var breakevens = D.position.blocks.map(function (b) {
      var live = liveFor(b.instrument);
      return el("span", null, [
        el("span", { text: blockTitle(b) + " breakeven " }),
        el("b", { class: "num", style: { color: "var(--ink)" }, text: fmt.n(b.mtm, 2) }),
        live ? el("span", { text: " vs " + live.key + " " }) : null,
        live ? el("b", { class: "num", style: { color: "var(--ink)" }, text: fmt.n(live.last, 2) }) : null,
        el("span", { text: " ¢/bu" })
      ]);
    });

    return GD.card({
      title: "The two instruments he is hedged on",
      sub: "Cents per bushel · " + (list.map(function (i) { return i.key; }).join(" and ")),
      actions: [GD.seg([
        { key: "30", label: "30d" },
        { key: "90", label: "90d" },
        { key: "180", label: "180d" }
      ], "90", function (k) {
        range = Number(k);
        draw();
      })],
      body: [stack(14, [host, legend])],
      foot: [
        el("div", { class: "legend", style: { gap: "4px 18px" } }, breakevens),
        el("span", { style: { flex: "1" } }),
        el("span", { class: "card-sub", text: "Levels are real. The daily path is generated." })
      ]
    });
  }

  /* ============================================================== provenance */

  function provenanceCard() {
    var P = D.position;
    var real = (D.provenance && D.provenance.real) ? D.provenance.real : [];
    var synth = (D.provenance && D.provenance.synthetic) ? D.provenance.synthetic : [];
    var legSource = real.filter(function (s) { return s.indexOf("legs") >= 0; })[0] || real[1] || "";
    var pathSource = synth.filter(function (s) { return s.indexOf("price history") >= 0; })[0] || synth[0] || "";

    function noteRow(ico, tone, head, body, meta) {
      return el("div", { class: "note-row" }, [
        el("span", {
          style: {
            width: "28px", height: "28px", "border-radius": "8px", flex: "none",
            display: "grid", "place-items": "center",
            background: tone === "good" ? "var(--accent-soft)" : tone === "warn" ? "var(--warn-soft)" : "var(--neutral-soft)",
            color: tone === "good" ? "var(--accent-2)" : tone === "warn" ? "var(--warn)" : "var(--ink-2)"
          }
        }, [icon(ico, 15, 1.75)]),
        el("div", { class: "nr-body" }, [
          el("div", { class: "nr-head" }, [
            el("h4", { text: head }),
            GD.chip(tone === "good" ? "real" : tone === "warn" ? "unreconciled" : "generated",
              tone === "good" ? "good" : tone === "warn" ? "warn" : "outline")
          ]),
          el("p", { text: body }),
          meta ? el("div", { class: "nr-meta", text: meta }) : null
        ])
      ]);
    }

    return GD.card({
      title: "What is real on this page",
      sub: "Said plainly, because the four-way split elsewhere on this desk is reconstructed",
      flush: true,
      body: [
        noteRow("shield", "good", "The legs, the thresholds and the splits",
          "Every leg date, lot count, fill status, cent price, basis and flat price here was read off " +
          "Pavin's PROCUREMENT TRACKER workbook — along with the " +
          sgnUsd(P.take_profit_usd) + " take profit, the " + sgnUsd(P.loss_cut_usd) +
          " loss cut, the 70/30 flat-and-basis split and both block subtotals.",
          legSource),
        noteRow("sparkle", "", "The daily price path",
          "The chart shows the shape of a market, not the market. " +
          "Today's levels and each instrument's last print are real; the line joining them up is not.",
          pathSource),
        noteRow("scale", "warn", "Two totals, and the desk does not reconcile them",
          "The two block subtotals add to " + sgnUsd(D.position.blocks_total) +
          ". The position measured against the thresholds is " + sgnUsd(P.total_usd) +
          ". Both are shown exactly as the workbook carries them; nothing here quietly makes them agree.",
          "Baht converted at " + fmt.n(P.fx, 2) + " THB per USD, the rate stored with the position")
      ]
    });
  }

  /* ==================================================================== page */

  GD.route("position", {
    label: "Position Book",
    icon: "position",
    group: "Trade",

    render: function (wrap) {
      var P = D.position;
      var legs = allLegs();
      var filled = sumBy(P.blocks, "filled_lots");
      var open = sumBy(P.blocks, "open_lots");
      var qty = sumBy(P.blocks, "total_qty");
      var days = breachDays(P);
      var age = breachAge(P);
      var cargoFile = (D.cargoes || []).filter(function (c) { return c.vessel === P.cargo; })[0];
      /* The cargo berthed on 7 Oct and the dataset was built on the 13th, so the
         sub-head says "arrived", not "arrives" — read off the dates, not hard-coded,
         so the sentence stays true if the data moves. */
      var arrived = fmt.daysBetween(P.arrival, D.built);
      var arrivalVerb = arrived !== null && arrived >= 0 ? "arrived" : "arrives";

      /* ---- 1. head */
      var actions = [];
      if (P.breach) {
        actions.push(GD.chip(
          "Loss cut breached" + (age ? " · " + age : ""),
          "bad", "alert"
        ));
      } else {
        actions.push(GD.chip("Inside both thresholds", "good", "check"));
      }
      /* Only offer the cross-link if the surface that owns the cargo file is loaded —
         otherwise the button would navigate off this page and open nothing. */
      if (cargoFile && GD.showCargo) {
        actions.push(el("button", {
          class: "btn", type: "button",
          onclick: function () {
            GD.go("cargo");
            if (GD.showCargo) setTimeout(function () { GD.showCargo(cargoFile); }, 60);
          }
        }, [icon("ship", 14, 1.75), "Cargo file"]));
      }
      actions.push(el("button", {
        class: "btn", type: "button", onclick: exportLegs
      }, [icon("file", 14, 1.75), "Copy legs"]));
      actions.push(el("button", {
        class: "btn btn-primary btn-pill", type: "button", onclick: escalate
      }, [icon("send", 14, 2), P.breach ? "Escalate the breach" : "Write the position note"]));

      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Position book" }),
          el("div", {
            class: "page-sub",
            text: P.cargo + " · " + P.origin + " · " + P.counterparty +
              " · contracted " + fmt.date(P.contract_date) +
              " · " + arrivalVerb + " " + fmt.date(P.arrival) + " at " + portLabel(P.port)
          })
        ]),
        el("div", { class: "ph-actions" }, actions)
      ]));

      /* ---- 2. the contract, in one line of hairline-split cells */
      function cell(k, v, tip) {
        var node = el("div", null, [
          el("div", { class: "k", text: k }),
          el("div", { class: "v", text: v })
        ]);
        if (tip) GD.tipOn(node, tip);
        return node;
      }
      wrap.appendChild(GD.card({
        flush: true,
        body: [el("div", { class: "statline" }, [
          cell("Cargo", P.cargo, "<b>" + GD.esc(P.cargo) + "</b><div class='tip-mute'>" +
            GD.esc(P.origin + " origin, " + fmt.n(qty, 0) + " t priced in this book") + "</div>"),
          cell("Shipment window", P.shipment),
          cell("Contract date", fmt.date(P.contract_date)),
          cell("Arrival", fmt.date(P.arrival),
            "<b>" + (arrivalVerb === "arrived" ? "Arrived " : "Arrives ") +
            GD.esc(fmt.ago(P.arrival)) + "</b><div class='tip-mute'>" +
            GD.esc(portLabel(P.port)) + "</div>"),
          cell("Counterparty", P.counterparty)
        ])]
      }));

      /* ---- 3. the four numbers */
      var posStat = statToned({
        label: "Position",
        icon: "position",
        value: sgnUsd(P.total_usd),
        delta: [
          icon("globe", 13, 1.75),
          el("span", { text: "Baht" }),
          el("b", { class: pnlTone(P.total_thb), text: sgnThb(P.total_thb) })
        ]
      }, pnlTone(P.total_usd));
      GD.tipOn(posStat, "<b>Mark to market</b><div class='tip-row'><span class='tip-mute'>USD</span><b>" +
        GD.esc(sgnUsd(P.total_usd)) + "</b></div><div class='tip-row'><span class='tip-mute'>THB</span><b>" +
        GD.esc(sgnThb(P.total_thb)) + "</b></div><div class='tip-row'><span class='tip-mute'>at FX</span><b>" +
        GD.esc(fmt.n(P.fx, 2)) + "</b></div>");

      /* Not the up/down arrows: on this desk a falling price is the good outcome, so
         a down glyph beside the coral loss cut would read as "good". `scale` is the
         house glyph for a threshold and `alert` for the one he does not want hit. */
      var thresholdStat = statToned({
        label: "Take profit",
        icon: "scale",
        value: sgnUsd(P.take_profit_usd),
        delta: [
          icon("alert", 13, 1.75),
          el("span", { text: "Loss cut" }),
          el("b", { class: "val-up", text: sgnUsd(P.loss_cut_usd) })
        ]
      }, "val-down");
      GD.tipOn(thresholdStat, "<b>His own two levels</b><div class='tip-row'><span class='tip-mute'>take profit</span><b>" +
        GD.esc(sgnUsd(P.take_profit_usd)) + "</b></div><div class='tip-row'><span class='tip-mute'>loss cut</span><b>" +
        GD.esc(sgnUsd(P.loss_cut_usd)) + "</b></div><div class='tip-row'><span class='tip-mute'>headroom to take profit</span><b>" +
        GD.esc(fmt.usd0(P.headroom_to_tp)) + "</b></div>");

      var breachStat;
      if (P.breach) {
        breachStat = statToned({
          label: "Breach",
          icon: "alert",
          value: days === null ? "Yes" : fmt.n(days, 0),
          unit: days === null ? null : days === 1 ? "day" : "days",
          aside: P.breached_on
            ? el("span", { class: "card-sub num", text: "since " + fmt.dateShort(P.breached_on) })
            : null,
          delta: [icon("alert", 13, 1.75), el("span", { text: EXCEL_LINE })]
        }, days !== null && days >= 7 ? "val-up" : "val-mute");
        GD.tipOn(breachStat, "<b>Through the loss cut</b><div class='tip-row'><span class='tip-mute'>breached</span><b>" +
          GD.esc(fmt.date(P.breached_on)) + "</b></div>" +
          (age ? "<div class='tip-row'><span class='tip-mute'>carried for</span><b>" + GD.esc(age) + "</b></div>" : "") +
          "<div class='tip-mute'>A workbook has no clock. " + GD.esc(EXCEL_LINE) + ".</div>");
      } else {
        breachStat = GD.stat({
          label: "Breach",
          icon: "shield",
          value: "None",
          aside: GD.chip("inside", "good", "check"),
          delta: [icon("check", 13, 1.75), el("span", { text: "Headroom " }),
            el("b", { text: fmt.usd0(P.headroom_to_tp) })]
        });
      }

      var lotsStat = GD.stat({
        label: "Lots",
        icon: "layers",
        value: fmt.n(filled, 0),
        unit: "filled",
        aside: open ? row(6, [GD.dot("good"), el("span", { class: "card-sub num", text: fmt.n(open, 0) + " open" })]) : null,
        delta: [
          icon("file", 13, 1.75),
          el("span", { class: "num", text: plural(P.blocks.length, "block", "blocks") + " · " }),
          el("b", { text: tonnes(qty) })
        ]
      });
      GD.tipOn(lotsStat, "<b>Lots</b>" + P.blocks.map(function (b) {
        return "<div class='tip-row'><span class='tip-mute'>" + GD.esc(b.instrument) + "</span><b>" +
          GD.esc(fmt.n(b.filled_lots, 0) + " filled" + (b.open_lots ? " · " + fmt.n(b.open_lots, 0) + " open" : "")) +
          "</b></div>";
      }).join("") + "<div class='tip-row'><span class='tip-mute'>legs</span><b>" +
        GD.esc(fmt.n(legs.length, 0)) + "</b></div>");

      wrap.appendChild(el("div", { class: "grid g-4" }, [posStat, thresholdStat, breachStat, lotsStat]));

      /* ---- 4. the threshold scale */
      wrap.appendChild(GD.card({
        title: "Where the position sits against his own thresholds",
        sub: P.breach
          ? "Through the loss cut on " + fmt.date(P.breached_on) +
            (age ? " and carried there for " + age : "")
          : "Inside both levels",
        actions: [P.breach ? GD.chip("breached", "bad", "alert") : GD.chip("inside", "good", "check")],
        body: [thresholdBar()],
        foot: [
          el("span", { class: "label", text: "Headroom to take profit" }),
          el("b", { class: "num", text: fmt.usd0(P.headroom_to_tp) }),
          el("span", { style: { flex: "1" } }),
          el("span", {
            class: "card-sub",
            text: "Scale runs from the position to the take profit. Loss cut, breakeven and take profit are his, from the workbook."
          })
        ]
      }));

      /* ---- 5. a card for each block */
      P.blocks.forEach(function (b) { wrap.appendChild(blockCard(b)); });

      /* ---- 6. the hedge instruments */
      wrap.appendChild(chartCard());

      /* ---- 7. what is real */
      wrap.appendChild(provenanceCard());
    }
  });

})();
