/* Grain Desk — Quote Board.
   Twenty-six indications arrive as prose in ten-plus emails a day. Two of them
   itemise anything. This surface parses them into rows and then does the thing
   the emails refuse to do: it splits every flat price into futures, freight and
   residual, and splits the residual again into the class median and the margin
   the seller is carrying. Where a number is ours rather than the supplier's,
   the screen says so — that honesty is the product.

   Owns the shared detail modal: GD.showQuote. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, D = window.DESK;

  /* One tonne of wheat is 36.7437 bushels. Every ¢/bu settlement on this surface
     is converted with this one constant, and the stated-basis reconciliation runs
     it in reverse — so the model number and the supplier's number are comparable
     by construction rather than by coincidence. */
  var BU_PER_T = 36.7437;

  /* Three emails state no quantity. Their cargo value assumes the standard
     55,000 t cargo, and every screen that shows one of those figures flags it. */
  var ASSUMED_CARGO = D.profile.economics.cargo_mt;

  var C_FUT = "var(--c-futures)",
      C_FRT = "var(--c-freight)",
      C_RES = "var(--c-residual)",
      C_BAS = "var(--c-basis)",
      C_MAR = "var(--c-margin)";

  /* ------------------------------------------------------------- lookups */

  function first(list, test) {
    var hit = list.filter(test);
    return hit.length ? hit[0] : null;
  }
  function instrumentFor(q) {
    return first(D.instruments, function (i) { return i.key === q.instrument; });
  }
  function originFor(code) {
    return first(D.profile.origins, function (o) { return o.code === code; });
  }
  function portFor(code) {
    return first(D.profile.ports, function (p) { return p.code === code; });
  }
  function supplierFor(name) {
    return first(D.profile.suppliers, function (s) { return s.name === name; });
  }
  function originLabel(code) {
    var o = originFor(code);
    return o ? o.label : code;
  }
  function portLabel(code) {
    var p = portFor(code);
    return p ? p.name : code;
  }
  function freightRange(code) {
    var o = originFor(code);
    if (!o || !o.freight_usd_mt) return null;
    return fmt.usd(o.freight_usd_mt[0], 0) + "–" + fmt.n(o.freight_usd_mt[1], 0);
  }
  function statedQty(q) { return q.qty === null || q.qty === undefined ? null : q.qty; }
  function workingQty(q) { var n = statedQty(q); return n === null ? ASSUMED_CARGO : n; }

  /* ------------------------------------------------------------- numbers */

  /* "+$3.00" / "−$0.95" — a signed money figure. fmt owns the formatting; this
     only owns the sign, because fmt.signed drops the currency mark. */
  function signedUsd(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + fmt.usd(Math.abs(v), dp);
  }
  function round1(v) { return Math.round(v * 10) / 10; }
  function byFlat(a, b) { return a.flat - b.flat; }
  function ordinal(n) {
    var tail = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (tail[(v - 20) % 10] || tail[v] || tail[0]);
  }
  /* "The cheapest" / "The dearest" / "The 3rd cheapest" — reads better than an
     ordinal at either end of a sorted list. */
  function rankPhrase(i, n) {
    if (i === 0) return "The cheapest";
    if (i === n - 1) return "The dearest";
    return "The " + ordinal(i + 1) + " cheapest";
  }
  function uniq(list) {
    var seen = {}, out = [];
    list.forEach(function (v) {
      if (!Object.prototype.hasOwnProperty.call(seen, v)) { seen[v] = true; out.push(v); }
    });
    return out;
  }
  function parts(q) {
    return [
      { name: "Futures", value: q.futures_usd_mt, color: C_FUT },
      { name: "Freight", value: q.freight_usd_mt, color: C_FRT },
      { name: "Residual", value: q.residual_usd_mt, color: C_RES }
    ];
  }

  /* ---------------------------------------------------------- small parts */

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
  function statCell(k, v, tone) {
    return el("div", null, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v " + (tone || ""), text: v })
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

  /* The state of an indication. Hue is never the only carrier: every chip has a
     word, and most carry a glyph too. */
  function stateChip(q) {
    if (q.itemised) return GD.chip("itemised", "accent", "check");
    if (q.is_best) return GD.chip("best", "good", "down");
    if (q.implied_margin > 3) return GD.chip("carries margin", "warn", "alert");
    return GD.chip("parsed", "outline");
  }
  function stateRank(q) {
    if (q.itemised) return 0;
    if (q.is_best) return 1;
    if (q.implied_margin > 3) return 2;
    return 3;
  }

  /* Margin is read from the buyer's side: paying above the class median is the
     bad outcome, so a positive margin is the coral one. */
  function marginDot(v) { return GD.dot(v > 3 ? "bad" : v > 0 ? "warn" : "good"); }

  /* =========================================================== the modal */

  GD.showQuote = function (q) {
    if (!q) return;

    var all = D.quotes;
    var ins = instrumentFor(q);
    var org = originFor(q.origin);
    var sup = supplierFor(q.supplier);
    var qty = workingQty(q);
    var qtyAssumed = statedQty(q) === null;
    var resShare = q.residual_usd_mt / q.flat;

    /* The supplier's own basis, converted to $/t on the same constant the model
       used on the way in. Populated on 2 of 26. */
    var stated = q.stated_basis === null || q.stated_basis === undefined
      ? null : q.stated_basis * BU_PER_T / 100;
    var delta = stated === null ? null : q.residual_usd_mt - stated;

    /* The counter: hand back the margin the price is carrying, and never ask for
       less than a dollar — below that there is nothing to negotiate about. */
    var counter = round1(q.flat - Math.max(1, q.implied_margin));
    var concession = q.flat - counter;
    var worth = concession * qty;

    /* ---- hero: the composition, and the one number the email actually gave us */
    var compoBar = GD.compo(parts(q), q.flat);
    compoBar.style.setProperty("height", "30px");
    compoBar.style.setProperty("border-radius", "var(--r-ctl)");

    var hero = el("div", { style: { display: "grid", gap: "13px" } }, [
      el("div", {
        style: {
          display: "flex", "align-items": "flex-end", "justify-content": "space-between",
          gap: "18px", "flex-wrap": "wrap"
        }
      }, [
        el("div", { style: { "min-width": "200px", flex: "1" } }, [
          labelled("Reconstructed composition", { "margin-bottom": "3px" }),
          el("div", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
            text: "Three components, rebuilt from the settlement curve and the freight market"
          })
        ]),
        el("div", { style: { "text-align": "right" } }, [
          labelled("Flat, CFR " + q.port, { "margin-bottom": "1px" }),
          el("div", {
            class: "num",
            style: {
              "font-size": "28px", "font-weight": "600",
              "letter-spacing": "-0.028em", "line-height": "1.1"
            },
            text: fmt.usd(q.flat)
          }),
          el("div", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" }, text: "per tonne" })
        ])
      ]),
      compoBar,
      GD.legend([
        { name: "Futures · " + fmt.usd(q.futures_usd_mt) + " · " + fmt.pct(q.futures_usd_mt / q.flat, 0), color: C_FUT },
        { name: "Freight · " + fmt.usd(q.freight_usd_mt) + " · " + fmt.pct(q.freight_usd_mt / q.flat, 0), color: C_FRT },
        { name: "Residual · " + fmt.usd(q.residual_usd_mt) + " · " + fmt.pct(resShare, 0), color: C_RES }
      ])
    ]);

    /* ---- the disclosure. Nothing on this screen matters more than the reader
       knowing which of these numbers came from the supplier. */
    var itemised = stated !== null;
    var discHead = itemised
      ? q.supplier + " itemised this one — the rest of the split is still ours."
      : "The residual is not itemised by " + q.supplier + ".";
    var discBody = itemised
      ? "The email states a basis of " + fmt.n(q.stated_basis, 0) + "¢ over " + q.stated_futures +
        ". Freight is still the mid of the published " + (org ? org.label : q.origin) +
        " Supramax range, and the residual is still flat minus futures minus freight. " +
        "The reconciliation below holds the two against each other."
      : "The email quotes one number — " + fmt.usd(q.flat) + "/t flat, and nothing else. " +
        "The split above is reconstructed here: futures from the " + (ins ? ins.label : q.instrument) +
        " settlement, freight from the mid of the published " + (org ? org.label : q.origin) +
        " Supramax range, and the residual is whatever is left over. It is our arithmetic, " +
        "not the supplier's.";

    var disclosure = el("div", {
      style: {
        display: "flex", gap: "11px", padding: "12px 13px",
        background: itemised ? "var(--accent-soft)" : "var(--warn-soft)",
        border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
        "align-items": "flex-start"
      }
    }, [
      el("span", {
        style: {
          color: itemised ? "var(--accent-2)" : "var(--warn)",
          flex: "none", "margin-top": "1px"
        }
      }, [icon(itemised ? "check" : "alert", 15, 2)]),
      el("div", null, [
        el("div", {
          style: { "font-weight": "600", "font-size": "var(--t-sm)", color: "var(--ink)" },
          text: discHead
        }),
        el("p", {
          style: { "font-size": "var(--t-sm)", color: "var(--ink-2)", "margin-top": "3px" },
          text: discBody
        })
      ])
    ]);

    /* ---- what is in the price */
    var futuresVal = el("span", null, [
      el("span", { text: fmt.usd(q.futures_usd_mt) + " /t" }),
      muted(ins ? ins.key + " " + fmt.n(ins.last, 2) + "¢/bu" : q.instrument)
    ]);
    if (ins) {
      GD.tipOn(futuresVal, "<b>" + GD.esc(ins.label) + "</b>" +
        "<div class='tip-row'><span class='tip-mute'>settled</span><b>" +
        GD.esc(fmt.n(ins.last, 2) + "¢/bu") + "</b></div>" +
        "<div class='tip-row'><span class='tip-mute'>× " + BU_PER_T + " bu/t</span><b>" +
        GD.esc(fmt.usd(ins.last * BU_PER_T / 100) + "/t") + "</b></div>" +
        "<div class='tip-row'><span class='tip-mute'>venue</span><b>" + GD.esc(ins.venue) + "</b></div>");
    }

    var freightVal = el("span", null, [
      el("span", { text: fmt.usd(q.freight_usd_mt) + " /t" }),
      muted(freightRange(q.origin)
        ? "mid of " + freightRange(q.origin) + " · " + (org ? org.label : q.origin)
        : (org ? org.label : q.origin))
    ]);

    var marginVal = el("span", { class: "flex items-center gap-8" }, [
      marginDot(q.implied_margin),
      el("b", { class: GD.toneFor(q.implied_margin), text: signedUsd(q.implied_margin) + " /t" }),
      muted(q.implied_margin === 0
        ? "level with the class median"
        : fmt.signedMoney(q.implied_margin * qty) + " on the cargo", "2px")
    ]);

    var kvBlock = GD.kv([
      ["Flat price", el("b", { text: fmt.usd(q.flat) + " /t" })],
      ["Futures", futuresVal],
      ["Freight", freightVal],
      ["Residual", el("span", null, [
        el("span", { text: fmt.usd(q.residual_usd_mt) + " /t" }),
        muted(fmt.pct(resShare, 0) + " of flat")
      ])],
      ["Class median residual", el("span", null, [
        el("span", { text: fmt.usd(q.fair_residual) + " /t" }),
        muted("benchmark for " + q.cls)
      ])],
      ["Implied margin", marginVal],
      ["Cargo value", el("span", null, [
        el("span", { text: fmt.money(q.cargo_value) }),
        muted(fmt.n(qty, 0) + " t" + (qtyAssumed ? " assumed — none stated" : ""))
      ])]
    ]);

    /* ---- the fourth component: the residual split again */
    var residualBlock, residualNote;
    if (q.implied_margin > 0) {
      var innerBar = GD.compo([
        { name: "Class median residual", value: q.fair_residual, color: C_BAS },
        { name: "Margin over the class", value: q.implied_margin, color: C_MAR }
      ], q.residual_usd_mt);
      innerBar.style.setProperty("height", "20px");
      residualBlock = el("div", { style: { display: "grid", gap: "10px" } }, [
        innerBar,
        GD.legend([
          { name: "Class median · " + fmt.usd(q.fair_residual), color: C_BAS },
          { name: "Over the class · " + fmt.usd(q.implied_margin), color: C_MAR }
        ])
      ]);
      residualNote = "Of the " + fmt.usd(q.residual_usd_mt) + "/t residual, " +
        fmt.usd(q.implied_margin) + "/t sits above the median residual for " + q.cls +
        ". On " + fmt.n(qty, 0) + " t that is " + fmt.money(q.implied_margin * qty) +
        ", and it is the part of the price that is negotiable.";
    } else if (q.implied_margin < 0) {
      residualBlock = panel([
        el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
          GD.dot("good"),
          el("b", { class: "num val-down", text: signedUsd(q.implied_margin) + " /t" }),
          el("span", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" }, text: "under the class median" })
        ])
      ]);
      residualNote = "This residual is thinner than the class carries, by " +
        fmt.usd(Math.abs(q.implied_margin)) + "/t — " + fmt.money(Math.abs(q.implied_margin) * qty) +
        " on the cargo. There is no margin here to claim back, so the counter below asks only for a working dollar.";
    } else {
      residualBlock = panel([
        el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
          GD.dot("good"),
          el("b", { class: "num val-mute", text: fmt.usd(0) + " /t" }),
          el("span", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" }, text: "level with the class median" })
        ])
      ]);
      residualNote = "The residual lands exactly on the median for " + q.cls +
        ". Nothing in this price is out of line; the counter below asks for a working dollar only.";
    }

    /* ---- where this one sits. Every indication on the desk, cheapest to dearest,
       with this one picked out — the only question a buyer asks of a price is
       whether it is high or low against everything else in front of him. The whole
       board rather than the class alone, because no class here has more than four
       indications and four bars is not a distribution. */
    var board = all.slice().sort(byFlat);
    var boardVals = board.map(function (x) { return x.flat; });
    var boardIdx = 0;
    board.forEach(function (x, i) { if (x.id === q.id) boardIdx = i; });
    var inCheaperHalf = boardIdx < boardVals.length / 2;

    var ladderSvg = GD.chart.barSpark(boardVals, {
      w: 306, h: 52, take: boardVals.length, highlight: boardIdx, gap: 3,
      label: fmt.usd(q.flat),
      /* Buyer's side: the cheap half of the board is the good half, so it takes
         the same emerald a falling price takes, and the dear half the coral. */
      color: inCheaperHalf ? "var(--down)" : "var(--up)",
      muted: "var(--surface-3)"
    });
    /* barSpark centres the price label over the highlighted bar and clamps it to
       10px from either edge. At the ends of the board that still overhangs the
       viewBox, and `.spark` — unlike `.chart` — clips. Let it draw, and reserve
       the gutter it draws into. */
    ladderSvg.style.setProperty("overflow", "visible");
    var ladder = el("div", { style: { padding: "0 14px" } }, [ladderSvg]);

    var peers = all.filter(function (x) { return x.cls === q.cls; }).slice().sort(byFlat);
    var peerIdx = 0;
    peers.forEach(function (x, i) { if (x.id === q.id) peerIdx = i; });

    var ladderNote = rankPhrase(boardIdx, boardVals.length) + " of the " + all.length +
      " indications on the board by flat price, and " +
      (peers.length >= 2
        ? rankPhrase(peerIdx, peers.length).toLowerCase() + " of the " + peers.length + " for " +
          q.cls + " — cheapest " + fmt.usd(peers[0].flat) + ", dearest " +
          fmt.usd(peers[peers.length - 1].flat) + "."
        : "the only " + q.cls + " anyone has quoted.") +
      " Classes are not interchangeable: this is position in the spread, not a like-for-like ranking.";

    /* ---- does the model agree with the supplier? Only 2 of 26 can answer. */
    var recon = null;
    if (stated !== null) {
      var statedRows = all.filter(function (x) {
        return x.stated_basis !== null && x.stated_basis !== undefined;
      });
      var worstAbs = 0, worstPct = 0;
      statedRows.forEach(function (x) {
        var s = x.stated_basis * BU_PER_T / 100;
        var dd = Math.abs(x.residual_usd_mt - s);
        if (dd > worstAbs) { worstAbs = dd; worstPct = dd / x.flat; }
      });

      /* Inside a dollar of the supplier's own number is the result that earns
         trust; outside it the chip must not keep wearing a tick. */
      var holds = Math.abs(delta) <= 1.1;

      recon = panel([
        el("div", {
          class: "flex items-center gap-8",
          style: { "flex-wrap": "wrap", "margin-bottom": "11px" }
        }, [
          el("div", { class: "card-title", style: { flex: "1" }, text: "Does the model agree with the supplier?" }),
          GD.chip(signedUsd(delta) + "/t vs stated", holds ? "good" : "warn", holds ? "check" : "alert")
        ]),
        GD.kv([
          ["Supplier stated", el("span", null, [
            el("span", { text: fmt.n(q.stated_basis, 0) + "¢ over " + q.stated_futures }),
            muted("= " + fmt.usd(stated) + " /t")
          ])],
          ["Model residual", fmt.usd(q.residual_usd_mt) + " /t"],
          ["Delta", el("b", null, [
            el("span", { text: signedUsd(delta) + " /t" }),
            muted(fmt.pct(Math.abs(delta) / q.flat, 2) + " of flat")
          ])]
        ]),
        el("p", {
          style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "11px" },
          text: statedRows.length + " of the " + all.length + " indications on the board state a basis. " +
                "On every one of them the reconstruction lands inside " + fmt.usd(worstAbs) +
                "/t of the supplier's own number — " +
                fmt.pct(worstPct, 2) + " of the flat price. That is the evidence behind the other " +
                (all.length - statedRows.length) + " rows, where nobody itemised anything."
        })
      ], { background: "var(--surface-2)" });
    }

    /* ---- the drafted counter. Plain text. Nothing is sent from this screen. */
    var cheaperPeer = all.filter(function (x) {
      return x.id !== q.id && x.cls === q.cls && x.shipment === q.shipment && x.flat < q.flat;
    }).slice().sort(byFlat)[0] || null;

    var grounds;
    if (cheaperPeer) {
      grounds = (cheaperPeer.supplier === q.supplier
        ? "Your own " + fmt.dateShort(cheaperPeer.received) + " indication"
        : cheaperPeer.supplier + "'s " + fmt.dateShort(cheaperPeer.received) + " indication") +
        " for the same class and the same window came in at " + fmt.usd(cheaperPeer.flat) + "/t, " +
        fmt.usd(q.flat - cheaperPeer.flat) + "/t under this one.";
    } else if (q.implied_margin > 0) {
      grounds = "Across every " + q.cls + " indication we hold, the residual in this price sits " +
        fmt.usd(q.implied_margin) + "/t above the class median — " +
        fmt.money(q.implied_margin * qty) + " on this cargo.";
    } else {
      grounds = "This is already the keenest " + q.cls + " we hold for this window, so we are asking " +
        "only for a working " + fmt.usd(concession) + "/t — " + fmt.money(worth) +
        " to this mill, and inside the noise on yours.";
    }

    function firstName(s) {
      if (!s) return null;
      var paren = s.match(/\(([^)]+)\)/);
      if (paren) return paren[1];
      return s.split(/\s+/)[0] || null;
    }
    var who = firstName(sup && sup.contact);
    var hail = who ? "Khun " + who : (sup ? sup.full : q.supplier) + " desk";

    var draft = hail + " — thank you for the " + q.cls + " indication at " + fmt.usd(q.flat) +
      "/t CFR " + portLabel(q.port) + " for " + q.shipment + ". " +
      "Read against the " + (ins ? ins.label : q.instrument) + " settlement and mid-range " +
      (org ? org.label : q.origin) + " Supramax freight, we have the residual at " +
      fmt.usd(q.residual_usd_mt) + "/t against a " + fmt.usd(q.fair_residual) +
      "/t median for the class. " + grounds +
      " We would work this at " + fmt.usd(counter) + "/t for " + fmt.n(qty, 0) +
      " t, same terms and same window." +
      (qtyAssumed ? " Please confirm the quantity — your email did not state one." : "");

    var counterBlock = el("div", {
      style: { border: "1px solid var(--border)", "border-radius": "var(--r-card)", overflow: "hidden" }
    }, [
      el("div", {
        style: {
          display: "flex", "align-items": "center", gap: "9px", padding: "11px 14px",
          "border-bottom": "1px solid var(--border)", background: "var(--surface-2)"
        }
      }, [
        el("span", { style: { color: "var(--ink-3)", flex: "none" } }, [icon("mail", 14, 2)]),
        el("div", { class: "card-title", style: { flex: "1" }, text: "Drafted counter" }),
        GD.chip("not sent", "outline", "eye")
      ]),
      el("div", { class: "statline", style: { "border-bottom": "1px solid var(--border)" } }, [
        statCell("Counter at", fmt.usd(counter) + " /t"),
        statCell("Concession asked", fmt.usd(concession) + " /t"),
        statCell("Worth on this cargo", fmt.money(worth), GD.toneFor(worth, true))
      ]),
      el("div", {
        style: { padding: "12px 14px 0", "font-size": "var(--t-xs)", color: "var(--ink-3)" },
        text: "To: " + (sup && sup.email ? sup.email : (sup ? sup.full : q.supplier)) +
              "  ·  From: " + D.profile.buyer.name + ", " + D.profile.short
      }),
      el("p", {
        style: { padding: "7px 14px 13px", "font-size": "var(--t-sm)", color: "var(--ink-2)" },
        text: draft
      })
    ]);

    /* ---- sub line for the sheet head */
    var sub = [
      q.shipment,
      "CFR " + portLabel(q.port) + " (" + q.port + ")",
      statedQty(q) === null ? "quantity not stated" : fmt.n(q.qty, 0) + " t",
      fmt.ago(q.received)
    ].join(" · ");

    GD.sheet({
      title: q.supplier + " · " + q.cls,
      sub: sub,
      body: function () {
        return [
          el("div", { style: { "margin-bottom": "18px" } }, [hero]),
          el("div", { style: { "margin-bottom": "18px" } }, [disclosure]),
          el("div", { class: "grid g-2", style: { "margin-bottom": "18px" } }, [
            section("What is in the price", kvBlock),
            el("div", { style: { display: "grid", gap: "18px", "align-content": "start" } }, [
              section("Inside the residual", residualBlock, residualNote),
              section("Where this sits on the board", ladder, ladderNote)
            ])
          ]),
          recon ? el("div", { style: { "margin-bottom": "18px" } }, [recon]) : null,
          counterBlock
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: function () { GD.toast("Counter drafted — Pavin sends it", { icon: "mail" }); }
          }, [icon("send", 14, 2), "Draft counter"]),
          el("button", { class: "btn", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "The agent drafts. Pavin sends." })
        ];
      }
    });
  };

  /* ================================================== the method, in full */

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

  function showMethod() {
    var all = D.quotes;
    var statedRows = all.filter(function (x) {
      return x.stated_basis !== null && x.stated_basis !== undefined;
    });
    var worstAbs = 0, worstPct = 0;
    statedRows.forEach(function (x) {
      var s = x.stated_basis * BU_PER_T / 100;
      var dd = Math.abs(x.residual_usd_mt - s);
      if (dd > worstAbs) { worstAbs = dd; worstPct = dd / x.flat; }
    });

    var ranges = D.profile.origins.map(function (o) {
      return o.label + " " + freightRange(o.code);
    }).join("  ·  ");

    var examples = D.instruments.filter(function (i) { return i.role === "hedge"; })
      .map(function (i) {
        return i.key + " " + fmt.n(i.last, 2) + "¢ → " + fmt.usd(i.last * BU_PER_T / 100) + "/t";
      }).join("   ");

    GD.sheet({
      title: "How the four-way split is built",
      sub: statedRows.length + " of " + all.length + " emails itemise anything. The other " +
           (all.length - statedRows.length) + " are reconstructed — here is exactly how.",
      body: function () {
        return [
          el("div", {
            style: {
              border: "1px solid var(--border)", "border-radius": "var(--r-card)",
              overflow: "hidden", "margin-bottom": "18px"
            }
          }, [
            methodStep(1, "Futures",
              "Each wheat class hedges against one instrument. One tonne of wheat is " + BU_PER_T +
              " bushels, so the settlement in ¢/bu times " + BU_PER_T + ", divided by 100, is the " +
              "futures leg in $/t. This is the only component with a published price.",
              examples),
            methodStep(2, "Freight",
              "The mid of the published Supramax range for the origin. A range, not a fixture — the " +
              "mill is one of several in the pool on the Australian and Canadian cargoes, so the exact " +
              "number is not ours to know until the vessel is nominated.",
              ranges),
            methodStep(3, "Residual",
              "Flat price minus futures minus freight. Everything the supplier declined to itemise lives " +
              "in here: the basis, the protein premium, terminal elevation, and their own margin. It is a " +
              "subtraction, which is why it is called a residual and not a basis.",
              "residual = flat − futures − freight"),
            methodStep(4, "Implied margin",
              "The residual minus the median residual for that class across the board. Positive means " +
              "this indication is carrying more than the class normally carries, and that is the part a " +
              "counter can reasonably ask for. Negative means the price is already thin.",
              "implied margin = residual − class median residual")
          ]),
          panel([
            el("div", {
              class: "flex items-center gap-8",
              style: { "flex-wrap": "wrap", "margin-bottom": "9px" }
            }, [
              el("div", { class: "card-title", style: { flex: "1" }, text: "Why trust it" }),
              GD.chip("inside " + fmt.usd(worstAbs) + "/t", "good", "check")
            ]),
            el("p", {
              style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
              text: statedRows.length + " of the " + all.length + " emails state a basis outright, so on " +
                    "those the model can be marked against the supplier's own number. " +
                    "Converted on the same " + BU_PER_T + " and compared " +
                    "with the derived residual, the two deltas are " +
                    statedRows.map(function (x) {
                      return signedUsd(x.residual_usd_mt - x.stated_basis * BU_PER_T / 100) + "/t";
                    }).join(" and ") +
                    " — at worst " + fmt.pct(worstPct, 2) + " of the flat price. A reconstruction that " +
                    "lands that close where it can be checked is worth reading where it cannot."
            })
          ]),
          el("p", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "14px" },
            text: "Price history behind the instruments is generated: the levels are the real settlements, " +
                  "the daily path between them is not. Everything else on this surface comes from the quote log."
          })
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: close, text: "Got it"
          }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Of the four components, only the flat price is the supplier's." })
        ];
      }
    });
  }

  /* ============================================================= the page */

  GD.route("quotes", {
    label: "Quote Board",
    icon: "quotes",
    group: "Trade",
    badge: function () { return D.quotes.length; },

    render: function (wrap) {
      var all = D.quotes;
      var suppliers = uniq(all.map(function (q) { return q.supplier; }));
      var classes = uniq(all.map(function (q) { return q.cls; }));
      var itemised = all.filter(function (q) { return q.itemised; });
      var cheapest = all.slice().sort(byFlat)[0];
      var received = all.map(function (q) { return q.received; }).slice().sort();
      var firstSeen = received[0], lastSeen = received[received.length - 1];

      /* Who is actually quoting. 21 of 26 rows from one counterparty is a fact
         worth a line, not a klaxon. */
      var tally = {};
      all.forEach(function (q) { tally[q.supplier] = (tally[q.supplier] || 0) + 1; });
      var topSupplier = suppliers.slice().sort(function (a, b) { return tally[b] - tally[a]; })[0];

      /* ---------------------------------------------------------- 1 · head */
      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Quote board" }),
          el("div", {
            class: "page-sub",
            text: all.length + " indications, " + suppliers.length + " suppliers, " +
                  classes.length + " wheat classes — the 10+ emails a day that arrive as prose"
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.chip("as of " + fmt.date(D.built), "outline", "calendar"),
          el("button", {
            class: "btn", type: "button", onclick: showMethod
          }, [icon("layers", 14, 1.6), "How the split is built"])
        ])
      ]));

      /* ---------------------------------------------------------- 2 · stats */
      var statParsed = GD.stat({
        label: "Quotes parsed", value: fmt.n(all.length, 0), unit: "indications", icon: "quotes",
        delta: [
          el("span", { text: "Window" }),
          el("b", { text: fmt.dateShort(firstSeen) + " – " + fmt.dateShort(lastSeen) }),
          el("span", { class: "val-mute nowrap", text: "· " + fmt.daysBetween(firstSeen, lastSeen) + " days" })
        ]
      });

      var statSuppliers = GD.stat({
        label: "Suppliers quoting", value: fmt.n(suppliers.length, 0),
        unit: "of " + D.profile.suppliers.length + " approved", icon: "users",
        delta: [
          el("span", { text: topSupplier }),
          el("b", { text: fmt.n(tally[topSupplier], 0) + " of " + fmt.n(all.length, 0) + " rows" }),
          el("span", { class: "val-mute nowrap", text: "· " + fmt.pct(tally[topSupplier] / all.length, 0) })
        ]
      });

      var statItemised = GD.stat({
        label: "Itemised by supplier", value: fmt.n(itemised.length, 0),
        unit: "of " + fmt.n(all.length, 0), icon: "layers",
        aside: el("span", { class: "flex items-center gap-8" }, [
          GD.dot("warn"),
          el("span", {
            class: "num",
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "font-weight": "600" },
            text: fmt.pct(itemised.length / all.length, 0)
          })
        ]),
        delta: [
          el("span", { text: "the other" }),
          el("b", { text: fmt.n(all.length - itemised.length, 0) }),
          el("span", { text: "are reconstructed" })
        ],
        onClick: showMethod
      });
      GD.tipOn(statItemised, "<b>Only two emails itemise</b><div class='tip-mute'>" +
        "Open the method — the other " + (all.length - itemised.length) +
        " splits are rebuilt from the settlement curve and the freight market.</div>");

      var statCheapest = GD.stat({
        label: "Cheapest laid-in", value: fmt.usd(cheapest.flat), unit: "/t", icon: "savings",
        hero: true,
        delta: [
          el("b", { text: cheapest.cls }),
          el("span", { class: "nowrap", text: "· " + cheapest.supplier })
        ]
      });
      GD.tipOn(statCheapest, "<b>Cheapest flat on the board</b><div class='tip-mute'>" +
        "Lowest CFR price of the " + all.length + " indications. Wheat classes are not " +
        "interchangeable — this is the cheapest tonne, not the cheapest grist.</div>");

      wrap.appendChild(el("div", { class: "grid g-4" }, [
        statParsed, statSuppliers, statItemised, statCheapest
      ]));

      /* -------------------------------------------- 3 · the recovered quote */
      var dq = D.dropped_quote;
      var dqQty = dq.qty === null || dq.qty === undefined ? ASSUMED_CARGO : dq.qty;
      var dqValue = dq.flat * dqQty;
      var dqPeers = all.filter(function (q) { return q.cls === dq.cls; }).slice().sort(byFlat);
      var dqBest = dqPeers.length ? dqPeers[0] : null;
      var dqDelta = dqBest ? dq.flat - dqBest.flat : null;

      var dqCard = GD.card({
        title: "Recovered: a quote the old flow lost",
        sub: dq.supplier + " · " + dq.cls + " · " + dq.shipment + " · received " + fmt.ago(dq.received),
        actions: [GD.chip("recovered", "warn", "shield")],
        flush: true,
        body: [
          el("div", { class: "statline" }, [
            statCell("Flat quoted", fmt.usd(dq.flat) + " /t"),
            statCell("Quantity", fmt.n(dqQty, 0) + " t"),
            statCell("Cargo value", fmt.money(dqValue)),
            dqDelta === null ? null : el("div", null, [
              el("div", { class: "k", text: "vs cheapest " + dq.cls + " on the board" }),
              el("div", { class: "v" }, [
                dqDelta === 0
                  ? el("span", { class: "val-mute", text: "level" })
                  : el("span", { class: GD.toneFor(dqDelta), text: signedUsd(dqDelta) + " /t" })
              ])
            ])
          ]),
          el("div", {
            style: {
              padding: "14px 16px", "border-top": "1px solid var(--border)",
              display: "grid", gap: "13px"
            }
          }, [
            el("div", null, [
              labelled("As it arrived", { "margin-bottom": "6px" }),
              el("div", {
                class: "mono",
                style: {
                  "font-size": "var(--t-sm)", color: "var(--ink)", background: "var(--surface-2)",
                  border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
                  padding: "9px 11px", "overflow-wrap": "anywhere"
                },
                text: dq.note
              })
            ]),
            el("div", null, [
              labelled("Why it was lost", { "margin-bottom": "6px" }),
              el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" }, text: dq.why })
            ])
          ])
        ],
        foot: [
          el("span", { style: { color: "var(--ink-3)" }, text: "Read out of the email body, not the sheet the old flow produced." }),
          el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
          el("span", {
            style: { color: "var(--ink-3)" },
            text: dqDelta === 0
              ? "It was level with the keenest " + dq.cls + " already on the board, so this one cost nothing. The audit trail is the loss."
              : "Worth " + fmt.money(dqValue) + " of wheat."
          })
        ]
      });
      dqCard.style.setProperty("box-shadow", "var(--sh-md)");
      wrap.appendChild(dqCard);

      /* ------------------------------------------------------- 4 · filters */
      var state = { origin: "all", cls: "all", supplier: "all", q: "" };
      var table;   /* assigned below; the handlers only fire after that */

      function filtered() {
        var needle = state.q.trim().toLowerCase();
        return all.filter(function (q) {
          if (state.origin !== "all" && q.origin !== state.origin) return false;
          if (state.cls !== "all" && q.cls !== state.cls) return false;
          if (state.supplier !== "all" && q.supplier !== state.supplier) return false;
          if (needle) {
            var hay = (q.supplier + " " + q.cls + " " + (q.notes || "")).toLowerCase();
            if (hay.indexOf(needle) < 0) return false;
          }
          return true;
        });
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

      function apply() {
        var rows = filtered();
        if (table) table.update(rows);
        countNode.textContent = "showing " + fmt.n(rows.length, 0) + " of " + fmt.n(all.length, 0);
        var dirty = state.origin !== "all" || state.cls !== "all" ||
                    state.supplier !== "all" || state.q !== "";
        if (dirty) clearBtn.removeAttribute("disabled");
        else clearBtn.setAttribute("disabled", "");
      }

      var originKeys = ["US", "AUS", "CA"].filter(function (code) {
        return all.some(function (q) { return q.origin === code; });
      });
      var originItems = [{ key: "all", label: "All " + all.length }].concat(
        originKeys.map(function (code) {
          var n = all.filter(function (q) { return q.origin === code; }).length;
          return { key: code, label: code + " " + n };
        })
      );
      var segNode = GD.seg(originItems, "all", function (k) { state.origin = k; apply(); });

      /* Classes in the profile's own order — US, then Canada, then Australia —
         which reads better than alphabetical for someone choosing a grist. */
      var clsSel = el("select", {
        class: "field", "aria-label": "Filter by wheat class",
        onchange: function () { state.cls = clsSel.value; apply(); }
      }, [el("option", { value: "all", text: "All classes" })].concat(
        D.profile.classes.filter(function (c) {
          return all.some(function (q) { return q.cls === c.code; });
        }).map(function (c) {
          var n = all.filter(function (q) { return q.cls === c.code; }).length;
          return el("option", { value: c.code, text: c.code + "  (" + n + ")" });
        })
      ));

      var supSel = el("select", {
        class: "field", "aria-label": "Filter by supplier",
        onchange: function () { state.supplier = supSel.value; apply(); }
      }, [el("option", { value: "all", text: "All suppliers" })].concat(
        suppliers.slice().sort(function (a, b) { return tally[b] - tally[a]; })
          .map(function (s) {
            return el("option", { value: s, text: s + "  (" + tally[s] + ")" });
          })
      ));

      var textIn = el("input", {
        class: "field", type: "text", autocomplete: "off",
        placeholder: "Filter supplier, class or notes…",
        "aria-label": "Filter by supplier, class or notes",
        oninput: function () { state.q = textIn.value; apply(); }
      });
      textIn.style.setProperty("flex", "1 1 200px");
      textIn.style.setProperty("min-width", "160px");

      function clearAll() {
        state.origin = "all"; state.cls = "all"; state.supplier = "all"; state.q = "";
        clsSel.value = "all"; supSel.value = "all"; textIn.value = "";
        GD.qsa("button", segNode).forEach(function (b, i) {
          b.setAttribute("aria-pressed", String(i === 0));
        });
        apply();
      }

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
            }, [icon("filter", 14, 1.6), el("span", { class: "label", text: "Origin" })]),
            segNode,
            divider(),
            clsSel,
            supSel,
            divider(),
            textIn,
            countNode,
            clearBtn
          ])
        ]
      }));

      /* --------------------------------------------------------- 5 · table */
      var cols = [
        {
          key: "supplier", label: "Supplier", tdClass: "strong", width: "128px",
          render: function (q) {
            return el("div", null, [
              el("div", { text: q.supplier }),
              el("div", {
                style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "font-weight": "400" },
                text: originLabel(q.origin)
              })
            ]);
          }
        },
        {
          key: "received", label: "Received", width: "100px",
          render: function (q) {
            var n = el("span", { text: fmt.ago(q.received) });
            GD.tipOn(n, "<b>" + GD.esc(fmt.date(q.received) + " " + fmt.time(q.received)) + "</b>" +
              "<div class='tip-mute'>" + GD.esc(q.supplier + " · row " + q.id) + "</div>");
            return n;
          }
        },
        { key: "cls", label: "Class", width: "122px" },
        { key: "shipment", label: "Shipment", width: "136px" },
        {
          key: "qty", label: "Qty t", num: true, width: "88px",
          render: function (q) {
            if (statedQty(q) !== null) return fmt.n(q.qty, 0);
            var n = el("span", { class: "val-mute", text: "not stated" });
            GD.tipOn(n, "No quantity in the email. Per-cargo value assumes a " +
              GD.esc(fmt.n(ASSUMED_CARGO, 0)) + " t cargo.");
            return n;
          }
        },
        {
          key: "flat", label: "Flat $/t", num: true, tdClass: "strong", width: "94px",
          render: function (q) { return fmt.usd(q.flat); }
        },
        {
          key: "vs_best", label: "vs best", num: true, width: "88px",
          render: function (q) {
            if (!q.vs_best) return el("span", { class: "val-mute", text: "—" });
            return el("span", { class: GD.toneFor(q.vs_best), text: signedUsd(q.vs_best) });
          }
        },
        {
          key: "cargo_value", label: "Per cargo", num: true, width: "100px",
          render: function (q) {
            if (statedQty(q) !== null) return fmt.money(q.cargo_value);
            var n = el("span", null, [
              el("span", { text: fmt.money(q.cargo_value) }),
              el("span", { style: { color: "var(--ink-4)", "margin-left": "3px" }, text: "*" })
            ]);
            GD.tipOn(n, "Quantity not stated — assumes a " +
              GD.esc(fmt.n(ASSUMED_CARGO, 0)) + " t cargo.");
            return n;
          }
        },
        {
          key: "compo", label: "Composition", sortable: false, width: "168px",
          render: function (q) {
            var bar = GD.compo(parts(q), q.flat);
            bar.style.setProperty("height", "14px");
            /* The segments keep their labels and tooltips, but drop out of the tab
               order: three stops per row across 26 rows would bury the rows
               themselves, and every figure is readable in the row and the modal. */
            GD.qsa("span", bar).forEach(function (s) { s.setAttribute("tabindex", "-1"); });
            return bar;
          }
        },
        {
          key: "state", label: "State", width: "118px",
          sortValue: stateRank, render: stateChip
        }
      ];

      table = GD.table({
        cols: cols,
        rows: all,
        sortKey: "received",
        sortDir: "desc",
        onRow: function (q) { GD.showQuote(q); },
        emptyText: "No indication matches these filters."
      });

      wrap.appendChild(GD.card({
        title: "Indications",
        sub: "Click a row for the four-way decomposition and the drafted counter",
        actions: [GD.legend([
          { name: "Futures", color: C_FUT },
          { name: "Freight", color: C_FRT },
          { name: "Residual", color: C_RES }
        ])],
        flush: true,
        body: [table],
        foot: [
          el("span", null, [
            el("b", { text: "vs best" }),
            " is this indication against the cheapest one for the same class and the same shipment window."
          ]),
          el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
          el("span", null, [
            el("b", { text: "Composition" }),
            " is reconstructed, not quoted — only " + itemised.length + " of " + all.length +
            " emails itemise anything."
          ]),
          el("span", { style: { flex: "1", "min-width": "0" } }),
          el("span", {
            class: "nowrap",
            text: "* quantity not stated; per cargo assumes " + fmt.n(ASSUMED_CARGO, 0) + " t"
          })
        ]
      }));

      apply();
    }
  });

})();
