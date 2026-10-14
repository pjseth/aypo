/* app/js/surfaces/cargo.js — Cargo pipeline and the document pack.
   The Clerk's surface: three cargoes on the water or nominated, the merged PDF
   pack that arrives per vessel, the certificates issued per hold, and the spec
   variance that becomes a GAFTA claim if nobody reads it in time.

   This file owns GD.showCargo — the cargo detail modal, also opened from the
   command palette in app.js. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, D = window.DESK;

  /* Certificates state pounds. Tonnes on this screen are converted, never quoted. */
  var LB_PER_MT = 2204.62;

  var STATUS = {
    discharging: { label: "Discharging", tone: "accent", icon: "cargo" },
    sailing:     { label: "Sailing",     tone: "info",   icon: "ship" },
    nominated:   { label: "Nominated",   tone: "outline", icon: "calendar" }
  };

  /* Every document state carries a dot AND a worded chip — never hue alone. */
  var DOCST = {
    received: { label: "Received",   dot: "good", tone: "good",    ico: "check", when: "Issued",   order: 0 },
    overdue:  { label: "Overdue",    dot: "bad",  tone: "bad",     ico: "alert", when: "Expected", order: 1 },
    expected: { label: "Expected",   dot: "",     tone: "",        ico: "clock", when: "Expected", order: 2 },
    none:     { label: "Not issued", dot: "",     tone: "outline", ico: "clock", when: "",         order: 3 }
  };

  /* UFM is the only coloured segment in a co-load; the partners are greys.
     The order alternates dark/light so adjacent partners stay separable, and
     every value clears the bar's own `--surface-3` track — `--border-2` does
     not, which is why it is absent. */
  var GREYS = ["var(--ink-2)", "var(--border-strong)", "var(--ink-3)",
               "var(--ink-4)", "var(--c-residual)"];

  /* ------------------------------------------------------------------ lookups */

  function originLabel(code) {
    var o = D.profile.origins.filter(function (x) { return x.code === code; })[0];
    return o ? o.label : code;
  }
  function portFull(code) {
    var p = D.profile.ports.filter(function (x) { return x.code === code; })[0];
    return p ? p.name + " (" + p.code + ")" : code;
  }
  function docState(d) {
    return (d && DOCST[d.status]) || DOCST.none;
  }
  function statusOf(c) {
    return STATUS[c.status] || { label: c.status, tone: "", icon: "ship" };
  }
  function claimsOf(c) {
    return (c.variance || []).filter(function (v) { return v.severity === "claim"; });
  }
  function ufmPoolMt(c) {
    var t = 0;
    (c.pool || []).forEach(function (p) { if (p[0] === "UFM") t += p[1]; });
    return t;
  }
  function poolTotal(c) {
    var t = 0;
    (c.pool || []).forEach(function (p) { t += p[1]; });
    return t;
  }
  function outstandingOf(c) { return Math.max(0, c.docs_total - c.docs_received); }
  function plural(n, one, many) { return fmt.n(n) + " " + (n === 1 ? one : many); }

  /* One cargo in the set has a real merged pack behind it; every provenance
     note points at it, so the vessel and the page count are read off the data
     rather than written into the sentence. */
  function realPack() {
    return D.cargoes.filter(function (c) { return c.pack; })[0] || null;
  }

  /* ETA in plain words, measured from the dataset's own build date. */
  function etaPhrase(eta) {
    var d = fmt.daysBetween(D.built, eta);
    if (d === null) return "";
    if (d === 0) return "arrives today";
    var n = Math.abs(d);
    return (d > 0 ? "in " : "") + fmt.n(n) + " day" + (n === 1 ? "" : "s") + (d > 0 ? "" : " ago");
  }

  /* A chip has to stand on its own: "ETA 6 days ago" reads as a contradiction
     on a vessel that is already discharging, so the date carries the phrase. */
  function etaChipText(eta) {
    var p = etaPhrase(eta);
    return "ETA " + fmt.dateShort(eta) + (p ? " · " + p : "");
  }

  /* ------------------------------------------------------------- small pieces */

  function labelled(text) { return el("span", { class: "label", text: text }); }

  /* `basis` narrows the flex basis so three money cells still share one row
     inside a ~320px card instead of wrapping into an orphan. */
  function statCell(k, v, basis) {
    return el("div", { style: basis ? { flex: "1 1 " + basis } : null }, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v", text: v })
    ]);
  }

  /* A flat panel: a card without the elevation, for use inside a sheet. */
  function panel(cfg) {
    var node = GD.card(cfg);
    node.style.setProperty("box-shadow", "none");
    return node;
  }

  function emptyBox(ico, head, sub) {
    return el("div", { class: "empty" }, [
      icon(ico, 20, 1.5),
      el("div", { style: { color: "var(--ink-2)", "font-weight": "600" }, text: head }),
      sub ? el("div", { class: "card-sub", text: sub }) : null
    ]);
  }

  /* The co-load bar. GD.compo labels its segments in dollars, which is wrong for
     tonnes, so each segment is replaced by a clone of itself — a clone carries no
     listeners — and the tonne tooltip is then the only one attached, instead of a
     second one firing after compo's on every pointer move. The clone keeps the
     width, the colour and the tab stop. */
  function poolBar(c) {
    var g = 0;
    var parts = (c.pool || []).filter(function (p) { return p[1] > 0; }).map(function (p) {
      var isUfm = p[0] === "UFM";
      return {
        name: p[0],
        value: p[1],
        color: isUfm ? "var(--accent)" : GREYS[(g++) % GREYS.length]
      };
    });
    var total = parts.reduce(function (s, p) { return s + p.value; }, 0) || 1;
    var bar = GD.compo(parts, total);

    Array.prototype.slice.call(bar.children).forEach(function (seg, i) {
      var p = parts[i];
      if (!p) return;
      var fresh = seg.cloneNode(true);
      bar.replaceChild(fresh, seg);
      fresh.setAttribute("aria-label",
        p.name + ", " + fmt.n(p.value) + " tonnes, " + fmt.pct(p.value / total, 0) + " of the pool");
      GD.tipOn(fresh,
        "<b>" + GD.esc(p.name) + "</b>" +
        "<div class='tip-row'><span class='tip-mute'>tonnes</span><b>" + GD.esc(fmt.n(p.value)) + " t</b></div>" +
        "<div class='tip-row'><span class='tip-mute'>share</span><b>" + GD.esc(fmt.pct(p.value / total, 0)) + "</b></div>");
    });
    return { bar: bar, parts: parts, total: total };
  }

  /* received / total, reusing the sidebar meter markup. */
  function docMeter(c) {
    var out = outstandingOf(c);
    var tone = c.docs_overdue > 0 ? "warn" : null;
    var pct = Math.max(0, Math.min(100, (c.completeness || 0) * 100));

    /* A chip is inline-flex; inside a grid it would stretch, so the caption
       always rides in its own flex row. */
    var caption = el("div", { class: "flex items-center", style: { gap: "7px" } },
      c.docs_overdue > 0
        ? [GD.chip(fmt.n(c.docs_overdue) + " overdue", "warn", "alert"),
           el("span", { class: "meter-cap", text: fmt.n(out) + " still to arrive" })]
        : out > 0
          ? [GD.chip("On schedule", "outline", "clock"),
             el("span", { class: "meter-cap", text: fmt.n(out) + " still to arrive" })]
          : [GD.chip("Pack complete", "good", "check")]);

    return el("div", { style: { display: "grid", gap: "8px" } }, [
      el("div", { class: "meter-top" }, [
        labelled("Document pack"),
        el("span", { class: "meter-val", text: fmt.n(c.docs_received) + " / " + fmt.n(c.docs_total) })
      ]),
      el("div", { class: "meter-track" }, [
        el("div", {
          class: "meter-fill", "data-tone": tone,
          style: { width: pct.toFixed(1) + "%" }
        })
      ]),
      caption
    ]);
  }

  /* Hover elevation on a whole-card target. `.card.is-hoverable` already carries
     the shadow and border-colour transition, so only the chevron nudge is wired
     here — on focus as well as hover, so a keyboard user sees the same cue. The
     focus ring itself is the global :focus-visible outline. */
  function liftOn(node, chev) {
    node.classList.add("is-hoverable");
    node.style.setProperty("cursor", "pointer");
    if (!chev) return;
    chev.style.setProperty("transition-property", "transform, color");
    chev.style.setProperty("transition-duration", "var(--d-fast)");
    chev.style.setProperty("transition-timing-function", "var(--ease)");

    function on() {
      chev.style.setProperty("transform", "translateX(2px)");
      chev.style.setProperty("color", "var(--accent)");
    }
    function off() {
      chev.style.removeProperty("transform");
      chev.style.removeProperty("color");
    }
    node.addEventListener("pointerenter", on);
    node.addEventListener("pointerleave", off);
    /* focus/blur do not bubble, so these fire for the card itself only. */
    node.addEventListener("focus", on);
    node.addEventListener("blur", off);
  }

  /* ------------------------------------------------------------- cargo card */

  function cargoCard(c) {
    var st = statusOf(c);
    var pool = poolBar(c);
    var ufmPool = ufmPoolMt(c);
    var share = pool.total ? ufmPool / pool.total : 0;
    var claims = claimsOf(c);
    var mismatch = ufmPool !== c.ufm_mt;

    var stack = el("div", {
      style: { padding: "14px 16px", display: "grid", gap: "14px", "align-content": "start", flex: "1 1 auto" }
    }, [
      GD.kv([
        ["Laycan", c.laycan],
        ["ETA", el("span", null, [
          fmt.date(c.eta),
          el("span", { class: "meter-cap", style: { "margin-left": "6px" }, text: etaPhrase(c.eta) })
        ])],
        ["Discharge", portFull(c.port)],
        ["Volume", fmt.n(c.mt) + " t cargo · UFM " + fmt.n(c.ufm_mt) + " t"]
      ]),

      el("div", { class: "hr", style: { margin: "0 -16px" } }),

      el("div", { style: { display: "grid", gap: "9px" } }, [
        el("div", { class: "flex items-center between", style: { gap: "8px" } }, [
          labelled(mismatch ? "UFM share of the declared pool" : "UFM share of cargo"),
          share < 0.5 && pool.parts.length > 1
            ? GD.chip("Minority of a " + fmt.n(pool.parts.length) + "-mill co-load", "outline", "users")
            : null
        ]),
        pool.bar,
        GD.legend(pool.parts.map(function (p) { return { name: p.name, color: p.color }; })),
        el("div", { class: "card-sub" }, [
          "UFM holds ",
          el("b", { style: { color: "var(--ink)" }, text: fmt.pct(share, 0) }),
          /* When the pool split and the contracted volume disagree, saying "of
             this cargo" would contradict the Volume row three lines above. */
          mismatch ? " of the declared pool" : " of this cargo"
        ]),
        /* a <p> so the global text-wrap: pretty keeps the last line off an orphan */
        mismatch ? el("p", { class: "card-sub" },
          "Contracted volume is " + fmt.n(c.ufm_mt) + " t; the declared pool split gives UFM " +
          fmt.n(ufmPool) + " t.") : null
      ]),

      el("div", { class: "hr", style: { margin: "0 -16px" } }),

      docMeter(c)
    ]);

    var chev = icon("chevR", 14, 2);

    var card = GD.card({
      title: c.vessel,
      sub: originLabel(c.origin) + " · " + c.supplier,
      actions: [
        GD.chip(st.label, st.tone, st.icon),
        claims.length ? GD.chip(plural(claims.length, "claim", "claims"), "bad", "alert") : null
      ],
      flush: true,
      body: [
        stack,
        el("div", { class: "statline", style: { "border-top": "1px solid var(--border)" } }, [
          statCell("Per tonne", fmt.usd(c.usd_mt), "92px"),
          statCell("Cargo value", fmt.money(c.value_usd), "92px"),
          statCell("UFM value", fmt.money(c.ufm_value_usd), "92px")
        ])
      ],
      foot: [
        el("span", { class: "flex items-center", style: { gap: "6px" } }, [
          icon(c.pack ? "file" : "clock", 13, 1.6),
          el("span", {
            text: c.pack ? fmt.n(c.pages) + "-page pack received" : "Pack not issued yet"
          })
        ]),
        el("span", { style: { flex: "1" } }),
        el("span", {
          class: "nowrap",
          style: { "font-weight": "600", color: "var(--ink-2)" },
          text: "Open pack"
        }),
        chev
      ]
    });

    /* equal-height cards with the money row pinned to the bottom edge */
    card.style.setProperty("display", "flex");
    card.style.setProperty("flex-direction", "column");
    var bodyNode = GD.qs(".card-body", card);
    if (bodyNode) {
      bodyNode.style.setProperty("flex", "1 1 auto");
      bodyNode.style.setProperty("display", "flex");
      bodyNode.style.setProperty("flex-direction", "column");
    }

    /* The whole card is the control. `role="group"` keeps the named <section>
       out of the landmark list while leaving its contents readable — role=button
       would hide every figure inside it from a screen reader. */
    card.setAttribute("role", "group");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-label",
      c.vessel + ", " + st.label.toLowerCase() + ". " +
      fmt.n(c.docs_received) + " of " + fmt.n(c.docs_total) + " documents received" +
      (c.docs_overdue > 0 ? ", " + plural(c.docs_overdue, "overdue", "overdue") : "") +
      (claims.length ? ", " + plural(claims.length, "claim", "claims") : "") +
      ". Open the document pack.");
    card.addEventListener("click", function () { GD.showCargo(c); });
    card.addEventListener("keydown", function (ev) {
      if (ev.target !== card) return;
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); GD.showCargo(c); }
    });
    liftOn(card, chev);
    return card;
  }

  /* ----------------------------------------------------------- doc matrix */

  function matrixCell(d) {
    var s = docState(d);
    if (!d) return el("span", { class: "val-mute", text: s.label });
    return el("span", { class: "flex items-center", style: { gap: "6px" } }, [
      GD.dot(s.dot),
      el("span", { text: s.label })
    ]);
  }

  function docMatrix() {
    /* Nothing in this table sorts: the rows are already in the order a pack is
       assembled, and GD.table has no table-level switch — `sortable` lives on
       the column, so every column has to say so. */
    var cols = [{
      key: "label", label: "Document", width: "34%", sortable: false,
      render: function (r) {
        return el("div", null, [
          el("div", { style: { "font-weight": "600", color: "var(--ink)" }, text: r.label }),
          el("div", { class: "card-sub", text: r.issuer })
        ]);
      }
    }];

    D.cargoes.forEach(function (c, i) {
      cols.push({
        key: "c" + i, label: c.vessel, sortable: false,
        render: function (r) { return matrixCell(r.docs[i]); }
      });
    });

    var rows = D.doc_types.map(function (t) {
      return {
        label: t.label,
        issuer: t.issuer,
        docs: D.cargoes.map(function (c) {
          return c.docs.filter(function (d) { return d.key === t.key; })[0] || null;
        })
      };
    });

    var received = D.cargoes.reduce(function (s, c) { return s + c.docs_received; }, 0);
    var total = D.cargoes.reduce(function (s, c) { return s + c.docs_total; }, 0);
    var out = Math.max(0, total - received);
    var overdue = D.cargoes.reduce(function (s, c) { return s + c.docs_overdue; }, 0);
    var perVessel = D.cargoes.map(function (c) {
      return c.vessel + " " + fmt.n(c.docs_received) + "/" + fmt.n(c.docs_total);
    }).join(" · ");

    var real = realPack();

    return GD.card({
      title: "Document pack matrix",
      sub: plural(D.doc_types.length, "document type", "document types") + " per vessel · " + perVessel,
      actions: [
        overdue > 0 ? GD.chip(fmt.n(overdue) + " overdue", "warn", "alert") : null,
        GD.chip(fmt.n(out) + " outstanding", out > 0 ? "outline" : "good", out > 0 ? "clock" : "check")
      ],
      flush: true,
      body: [GD.table({ cols: cols, rows: rows })],
      foot: [
        el("span", { class: "flex items-center", style: { gap: "6px" } }, [
          icon("layers", 13, 1.6),
          el("span", { text: "Certificates of weight and quality are issued per hold, so one line here can stand for several certificate numbers." })
        ]),
        el("span", { style: { flex: "1" } }),
        real ? GD.tipOn(
          el("span", { class: "flex items-center", style: { gap: "6px", color: "var(--ink-4)" } }, [
            icon("alert", 13, 1.6),
            el("span", { text: "Only the " + real.vessel + " pack is a real document set" })
          ]),
          "Document dates on the other cargoes are generated, not supplied — the " +
          GD.esc(real.vessel) + " pack, its " + GD.esc(fmt.n(real.pages)) +
          " pages and its certificate numbers are the real ones."
        ) : null
      ]
    });
  }

  /* ----------------------------------------------------- the detail modal */

  function docRow(d, t) {
    var s = docState(d);
    var label = d ? d.label : t.label;
    var issuer = d ? d.issuer : t.issuer;
    var when = d && d.date ? s.when + " " + fmt.date(d.date) : "No date yet";
    var pages = d && d.pages ? plural(d.pages, "page", "pages") : "—";

    /* Dimmed, not hidden — and not so far that the issuer line drops below a
       readable contrast. The "Not issued" chip carries the state on its own. */
    return el("div", { class: "note-row", style: d ? null : { opacity: "0.85" } }, [
      el("span", { style: { "padding-top": "6px", flex: "none" } }, [GD.dot(s.dot)]),
      el("div", { class: "nr-body" }, [
        el("div", { class: "nr-head" }, [
          el("h4", { text: label }),
          GD.chip(s.label, s.tone, s.ico)
        ]),
        el("div", { class: "nr-meta", text: issuer })
      ]),
      el("div", { style: { "text-align": "right", flex: "none" } }, [
        el("div", {
          style: {
            "font-size": "var(--t-sm)", color: "var(--ink-2)",
            "font-variant-numeric": "tabular-nums"
          },
          text: when
        }),
        el("div", { class: "nr-meta", text: pages })
      ])
    ]);
  }

  function checklistPanel(c) {
    /* Every document type appears, so a pack that is simply missing nine of them
       reads as nine missing rows rather than a short list. Received first. */
    var items = D.doc_types.map(function (t) {
      return { type: t, doc: c.docs.filter(function (d) { return d.key === t.key; })[0] || null };
    });
    c.docs.forEach(function (d) {
      var known = D.doc_types.filter(function (t) { return t.key === d.key; })[0];
      if (!known) items.push({ type: { label: d.label, issuer: d.issuer }, doc: d });
    });
    items.sort(function (a, b) { return docState(a.doc).order - docState(b.doc).order; });

    return panel({
      title: "Document checklist",
      sub: fmt.n(c.docs_received) + " of " + fmt.n(c.docs_total) + " received" +
           (c.docs_overdue > 0 ? " · " + fmt.n(c.docs_overdue) + " overdue" : ""),
      actions: [GD.chip(fmt.pct(c.completeness, 0) + " complete",
        c.docs_overdue > 0 ? "warn" : c.completeness >= 1 ? "good" : "outline")],
      flush: true,
      body: items.map(function (it) { return docRow(it.doc, it.type); })
    });
  }

  function holdsPanel(c) {
    var holds = c.holds || [];
    var body;

    if (!holds.length) {
      body = [emptyBox("layers", "No stowage plan yet",
        "Holds and certificate numbers appear once the vessel is loaded.")];
    } else {
      var totalLb = holds.reduce(function (s, h) { return s + h.lb; }, 0);
      var certs = holds.filter(function (h) { return h.cert; }).length;

      var head = el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "Hold" }),
        el("th", { scope: "col", text: "Class" }),
        el("th", { scope: "col", class: "num", text: "Pounds" }),
        el("th", { scope: "col", class: "num", text: "Tonnes" }),
        el("th", { scope: "col", text: "Certificate" })
      ])]);

      var tbody = el("tbody", null, holds.map(function (h) {
        return el("tr", null, [
          el("td", { class: "strong", text: "No. " + fmt.n(h.no) }),
          el("td", { text: h.cls }),
          el("td", { class: "num", text: fmt.n(h.lb) }),
          el("td", { class: "num", text: fmt.n(h.lb / LB_PER_MT, 0) }),
          h.cert
            ? el("td", null, [el("span", { class: "mono", style: { "font-size": "11px" }, text: h.cert })])
            : el("td", null, [el("span", { class: "val-mute", text: "Not issued" })])
        ]);
      }));

      var totalStyle = { "border-top": "1px solid var(--border-2)", background: "var(--surface-2)" };
      tbody.appendChild(el("tr", null, [
        el("td", { class: "strong", style: totalStyle, text: "Total" }),
        el("td", { style: totalStyle, text: plural(holds.length, "hold", "holds") }),
        el("td", { class: "num strong", style: totalStyle, text: fmt.n(totalLb) }),
        el("td", { class: "num strong", style: totalStyle, text: fmt.n(totalLb / LB_PER_MT, 0) }),
        el("td", { style: totalStyle }, [
          el("span", { class: "card-sub", text: fmt.n(certs) + " of " + fmt.n(holds.length) + " certificated" })
        ])
      ]));

      body = [el("div", { class: "table-wrap" }, [el("table", null, [head, tbody])])];
    }

    var foot = [];
    if (holds.length) {
      /* The dataset carries its own rounded figure; quote that rather than a
         second conversion of the same pounds. */
      var holdsMt = c.holds_mt === undefined || c.holds_mt === null
        ? holds.reduce(function (s, h) { return s + h.lb; }, 0) / LB_PER_MT
        : c.holds_mt;
      foot.push(el("span", { class: "flex items-center", style: { gap: "6px" } }, [
        icon("scale", 13, 1.6),
        el("span", {
          text: "Certificates state pounds; tonnes are converted at " + fmt.n(LB_PER_MT, 2) +
                " lb/t. These holds total " + fmt.n(holdsMt, 0) + " t of the " + fmt.n(c.mt) +
                " t cargo" + (c.ufm_mt === c.mt ? "." : ", on which UFM holds " + fmt.n(c.ufm_mt) + " t.")
        })
      ]));
    }

    return panel({
      title: "Holds and certificates",
      sub: "A certificate is issued per hold, not per cargo — each one has to be reconciled on its own.",
      actions: [GD.chip(plural(holds.length, "hold", "holds"), "outline", "layers")],
      flush: true,
      body: body,
      foot: foot.length ? foot : null
    });
  }

  function varianceRow(v) {
    var isClaim = v.severity === "claim";
    return el("div", {
      class: "note-row",
      style: isClaim ? { background: "var(--up-soft)" } : null
    }, [
      el("span", {
        style: { "padding-top": "3px", flex: "none", color: isClaim ? "var(--up)" : "var(--accent)" }
      }, [icon(isClaim ? "alert" : "check", 15, 2)]),
      el("div", { class: "nr-body" }, [
        el("div", { class: "nr-head" }, [
          el("h4", { text: v.field }),
          GD.chip(isClaim ? "Claim" : "Within tolerance", isClaim ? "bad" : "good",
            isClaim ? "alert" : "check")
        ]),
        el("div", { style: { display: "flex", gap: "22px", "flex-wrap": "wrap", "margin-top": "7px" } }, [
          el("div", null, [
            labelled("Contract requires"),
            el("div", {
              style: { "font-size": "var(--t-sm)", "font-weight": "600", "font-variant-numeric": "tabular-nums" },
              text: v.contract
            })
          ]),
          el("div", null, [
            labelled("Certificate says"),
            el("div", {
              class: isClaim ? "val-up" : "val-down",
              style: { "font-size": "var(--t-sm)", "font-weight": "600", "font-variant-numeric": "tabular-nums" },
              text: v.certificate
            })
          ])
        ]),
        el("p", { text: v.note })
      ])
    ]);
  }

  function variancePanel(c) {
    var claims = claimsOf(c);
    var vs = c.variance || [];
    var foot = [];

    if (claims.length) {
      foot.push(el("span", { class: "flex items-center", style: { gap: "6px", color: "var(--up)" } }, [
        icon("clock", 13, 2),
        el("span", {
          style: { color: "var(--ink-2)", "font-weight": "600" },
          text: "A claim is time-limited under GAFTA — the notice window runs from the certificate date, so it has to be raised now and settled later."
        })
      ]));
    }
    if (vs.length) {
      foot.push(GD.tipOn(
        el("span", { class: "flex items-center", style: { gap: "6px", color: "var(--ink-4)" } }, [
          icon("eye", 13, 1.6),
          el("span", { text: "Reconstructed — carried as an illustration of reconciliation" })
        ]),
        "The hold-level spec variance is modelled to show how reconciliation reads. The document pack, the certificate numbers and the hold weights are real."
      ));
    }

    return panel({
      title: "Spec variance against contract",
      sub: "Certificate values read back against what the contract required.",
      actions: [claims.length
        ? GD.chip(plural(claims.length, "claim", "claims"), "bad", "alert")
        : GD.chip("Clean", "good", "check")],
      flush: true,
      body: vs.length
        ? vs.map(function (v) { return varianceRow(v); })
        : [emptyBox("check", "Nothing to reconcile yet",
            "The certificates of weight and quality for this cargo have not arrived, so there is nothing to read back against the contract.")],
      foot: foot.length ? foot : null
    });
  }

  function packPanel(c) {
    var box = {
      border: "1px solid var(--border)",
      "border-radius": "var(--r-card)",
      background: "var(--surface-2)",
      padding: "14px 16px",
      display: "grid",
      gap: "9px"
    };

    if (!c.pack) {
      return el("div", { style: box }, [
        el("div", { class: "flex items-center between", style: { gap: "8px" } }, [
          labelled("Merged document pack"),
          GD.chip("Not issued", "outline", "clock")
        ]),
        el("p", {
          style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
          text: "Nothing has arrived for this vessel yet. When it does it arrives the same way every " +
                "time: one merged PDF per vessel, not a folder of attachments — so the work is " +
                "splitting and classifying the pages inside the single file."
        })
      ]);
    }

    return el("div", { style: box }, [
      el("div", { class: "flex items-center between", style: { gap: "8px" } }, [
        labelled("Merged document pack"),
        GD.chip(fmt.n(c.pages) + " pages", "accent", "file")
      ]),
      el("div", { class: "flex items-center", style: { gap: "8px" } }, [
        icon("file", 15, 1.6),
        el("span", {
          class: "mono",
          style: { "font-size": "12px", color: "var(--ink)", "word-break": "break-all" },
          text: c.pack
        })
      ]),
      el("p", {
        style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
        text: "One PDF for the whole shipment. The job is splitting and classifying the " +
              fmt.n(c.pages) + " pages inside this pack — there are no separate attachments to file, " +
              "which is exactly why a missing certificate is easy to miss."
      })
    ]);
  }

  GD.showCargo = function (c) {
    if (!c) return;
    var st = statusOf(c);
    var claims = claimsOf(c);
    var ufmPool = ufmPoolMt(c);
    var total = poolTotal(c);
    var real = realPack();

    GD.sheet({
      title: c.vessel,
      sub: originLabel(c.origin) + " · " + c.supplier + " · ETA " + fmt.date(c.eta) + " · " + portFull(c.port),
      body: function () {
        return [el("div", { style: { display: "grid", gap: "18px" } }, [

          el("div", { class: "flex items-center", style: { gap: "8px", "flex-wrap": "wrap" } }, [
            GD.chip(st.label, st.tone, st.icon),
            GD.chip(etaChipText(c.eta), "outline", "clock"),
            claims.length
              ? GD.chip(plural(claims.length, "claim open", "claims open"), "bad", "alert")
              : null,
            c.docs_overdue > 0
              ? GD.chip(plural(c.docs_overdue, "document overdue", "documents overdue"), "warn", "file")
              : null
          ]),

          el("div", {
            style: {
              border: "1px solid var(--border)", "border-radius": "var(--r-card)",
              background: "var(--surface-2)", overflow: "hidden"
            }
          }, [
            el("div", { class: "statline" }, [
              statCell("Cargo tonnes", fmt.n(c.mt) + " t"),
              statCell("UFM tonnes", fmt.n(c.ufm_mt) + " t"),
              statCell("Per tonne", fmt.usd(c.usd_mt)),
              statCell("Cargo value", fmt.money(c.value_usd)),
              statCell("UFM value", fmt.money(c.ufm_value_usd))
            ])
          ]),

          GD.kv([
            ["Laycan", c.laycan],
            ["Exporter", c.exporter],
            ["Discharge port", portFull(c.port)],
            ["Co-load", plural((c.pool || []).length, "mill", "mills") +
              " · UFM " + fmt.n(ufmPool) + " t of the pool (" +
              fmt.pct(total ? ufmPool / total : 0, 0) + ")" +
              (ufmPool !== c.ufm_mt ? " · contracted " + fmt.n(c.ufm_mt) + " t" : "")]
          ]),

          packPanel(c),
          checklistPanel(c),
          holdsPanel(c),
          variancePanel(c),

          !c.pack && real ? el("p", { class: "card-sub" },
            "Document dates on this cargo are generated. Only the " + real.vessel + " pack — its " +
            fmt.n(real.pages) + " pages and its certificate numbers — is a real document set.") : null
        ])];
      },
      foot: function (close) {
        return [
          claims.length ? el("button", {
            class: "btn btn-primary", type: "button",
            onclick: function () {
              GD.toast("Claim raised for review — never filed automatically", { icon: "shield" });
            }
          }, [icon("alert", 14, 2), "Raise claim for review"]) : null,
          el("button", { class: "btn", type: "button", text: "Close", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", {
            class: "card-sub",
            text: claims.length
              ? "The Clerk reconciles and drafts. Pavin files."
              : "The Clerk watches the pack. Nothing leaves the desk on its own."
          })
        ];
      }
    });
  };

  /* ----------------------------------------------------------------- route */

  GD.route("cargo", {
    label: "Cargo & docs",
    icon: "cargo",
    group: "Operations",
    badge: function () {
      return D.cargoes.reduce(function (s, c) {
        return s + c.docs_overdue + claimsOf(c).length;
      }, 0);
    },
    badgeTone: "warn",

    render: function (wrap) {
      var cargoes = D.cargoes;

      var tonnes = cargoes.reduce(function (s, c) { return s + c.ufm_mt; }, 0);
      var afloat = cargoes.reduce(function (s, c) { return s + c.mt; }, 0);
      var value = cargoes.reduce(function (s, c) { return s + c.value_usd; }, 0);
      var ufmValue = cargoes.reduce(function (s, c) { return s + c.ufm_value_usd; }, 0);
      var received = cargoes.reduce(function (s, c) { return s + c.docs_received; }, 0);
      var docsTotal = cargoes.reduce(function (s, c) { return s + c.docs_total; }, 0);
      var outstanding = cargoes.reduce(function (s, c) { return s + outstandingOf(c); }, 0);
      var overdue = cargoes.reduce(function (s, c) { return s + c.docs_overdue; }, 0);
      var claimCount = cargoes.reduce(function (s, c) { return s + claimsOf(c).length; }, 0);
      var overdueCargoes = cargoes.filter(function (c) { return c.docs_overdue > 0; });

      /* --- page head */
      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Cargo & paperwork" }),
          el("p", {
            class: "page-sub",
            text: "Three cargoes on the water or nominated. Documents arrive as one merged pack " +
                  "per vessel, and certificates are issued per hold."
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.chip("As of " + fmt.date(D.built), "outline", "calendar"),
          overdue + claimCount > 0
            ? GD.chip(fmt.n(overdue) + " overdue · " + plural(claimCount, "claim", "claims"),
                "warn", "alert")
            : GD.chip("Nothing outstanding", "good", "check")
        ])
      ]));

      /* --- stat row */
      var statusCounts = ["discharging", "sailing", "nominated"].map(function (k) {
        return { k: k, n: cargoes.filter(function (c) { return c.status === k; }).length };
      }).filter(function (x) { return x.n > 0; });

      var mixNodes = [];
      statusCounts.forEach(function (x, i) {
        if (i) mixNodes.push(el("span", { style: { color: "var(--ink-4)" }, text: "·" }));
        mixNodes.push(el("b", { text: fmt.n(x.n) }));
        mixNodes.push(el("span", { text: (STATUS[x.k] || { label: x.k }).label.toLowerCase() }));
      });

      var docsStat = GD.stat({
        label: "Documents outstanding",
        value: fmt.n(outstanding),
        unit: "of " + fmt.n(docsTotal),
        icon: "file",
        aside: overdue > 0
          ? GD.chip(fmt.n(overdue) + " overdue", "warn", "alert")
          : GD.chip("On track", "good", "check"),
        delta: [
          GD.dot(overdue > 0 ? "warn" : "good"),
          el("span", null, [
            el("b", { text: fmt.n(received) }),
            " received · ",
            el("b", { text: fmt.n(outstanding) }),
            " still to arrive"
          ])
        ],
        onClick: overdueCargoes.length === 1 ? function () { GD.showCargo(overdueCargoes[0]); } : null
      });

      if (overdue > 0) {
        var ico = GD.qs(".stat-ico", docsStat);
        if (ico) {
          ico.style.setProperty("background", "var(--warn-soft)");
          ico.style.setProperty("color", "var(--warn)");
        }
        if (overdueCargoes.length === 1) {
          /* The tile is a real button here, so its name has to say what pressing
             it does — otherwise a screen reader just reads the figures back. */
          docsStat.setAttribute("aria-label",
            fmt.n(outstanding) + " of " + fmt.n(docsTotal) + " documents outstanding, " +
            fmt.n(overdue) + " overdue on " + overdueCargoes[0].vessel +
            ". Open its document pack.");
          GD.tipOn(docsStat, "<b>" + GD.esc(overdueCargoes[0].vessel) + "</b><div class='tip-mute'>" +
            GD.esc(overdueCargoes[0].docs.filter(function (d) { return d.status === "overdue"; })
              .map(function (d) { return d.label; }).join(", ")) + " flagged overdue</div>");
        }
      }

      wrap.appendChild(el("div", { class: "grid g-4" }, [
        GD.stat({
          label: "Cargoes in flight",
          value: fmt.n(cargoes.length),
          unit: cargoes.length === 1 ? "vessel" : "vessels",
          icon: "ship",
          /* No dot: this row is a breakdown, not a state, and a green dot would
             read as one. It wraps rather than overflowing the tile — .stat
             clips its overflow, so an unwrapped flex row loses its last word. */
          delta: [el("span", {
            class: "flex items-center",
            style: { gap: "4px", "flex-wrap": "wrap", "row-gap": "2px" }
          }, mixNodes)]
        }),
        GD.stat({
          label: "UFM tonnes committed",
          value: fmt.n(tonnes),
          unit: "t",
          icon: "wheat",
          delta: [
            el("span", null, [
              el("b", { text: fmt.pct(afloat ? tonnes / afloat : 0, 0) }),
              " of ",
              el("b", { text: fmt.n(afloat) }),
              " t booked across " + plural(cargoes.length, "cargo", "cargoes")
            ])
          ]
        }),
        GD.stat({
          label: "Combined cargo value",
          value: fmt.money(value),
          icon: "dollar",
          delta: [
            el("span", null, ["UFM's share ", el("b", { text: fmt.money(ufmValue) })])
          ]
        }),
        docsStat
      ]));

      /* --- one card per cargo */
      wrap.appendChild(el("div", { class: "grid g-2" }, cargoes.map(cargoCard)));

      /* --- the matrix */
      wrap.appendChild(docMatrix());
    }
  });

})();
