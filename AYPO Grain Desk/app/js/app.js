/* Grain Desk — shell: rail, sidebar, topbar, market strip, palette, routing.
   Runs last; every surface has registered itself with GD.route() by now. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, qs = GD.qs, qsa = GD.qsa, fmt = GD.fmt;
  var D = window.DESK;

  /* The desk is light only. The three reference designs are light, it is read in
     a lit office, and one palette is one palette to keep honest — so there is no
     theme state, no toggle and nothing stored. */

  /* ------------------------------------------------------- agents in rail */

  var AGENTS = [
    {
      key: "buyer", name: "The Buyer", icon: "quotes",
      question: "What should I pay?",
      folds: "Quote Clerk · Fair Value · Negotiator · Scout",
      blurb: "Reads every indication into structured rows, rebuilds the four components, " +
             "reports the residual nobody itemised, and drafts the counter.",
      route: "quotes"
    },
    {
      key: "watch", name: "The Watch", icon: "market",
      question: "When should I move?",
      folds: "Night Watch · The Analyst · Position Keeper",
      blurb: "Owns the clock — the release-second verdict, the morning brief, and the " +
             "position carried against both.",
      route: "market"
    },
    {
      key: "clerk", name: "The Clerk", icon: "cargo",
      question: "Did I get what I bought?",
      folds: "Cargo Clerk · Blender",
      blurb: "Splits the vessel pack, reconciles weights and specs against the contract, " +
             "and carries landed cost through the blend to ฿/kg.",
      route: "cargo"
    }
  ];

  function agentState(a) {
    if (a.key === "buyer") {
      var stale = D.quotes.filter(function (q) { return q.implied_margin > 3; }).length;
      return stale ? { state: "attention", n: stale } : { state: "idle", n: 0 };
    }
    if (a.key === "watch") {
      return D.position.breach ? { state: "attention", n: 1 } : { state: "working", n: 0 };
    }
    var overdue = D.cargoes.reduce(function (s, c) { return s + c.docs_overdue; }, 0);
    var claims = D.cargoes.reduce(function (s, c) {
      return s + c.variance.filter(function (v) { return v.severity === "claim"; }).length;
    }, 0);
    return (overdue + claims) ? { state: "attention", n: overdue + claims } : { state: "idle", n: 0 };
  }
  GD.AGENTS = AGENTS;
  GD.agentState = agentState;

  /* ----------------------------------------------------------------- rail */

  function buildRail() {
    var rail = el("div", { class: "rail" });
    rail.appendChild(el("div", { class: "rail-logo", title: "Grain Desk", text: "GD" }));

    AGENTS.forEach(function (a) {
      var st = agentState(a);
      var btn = el("button", {
        class: "rail-btn", type: "button",
        "aria-label": a.name + " — " + a.question,
        onclick: function () { showAgent(a); }
      }, [icon(a.icon, 17, 1.6), el("span", { class: "dot", "data-state": st.state })]);
      GD.tipOn(btn, "<b>" + GD.esc(a.name) + "</b><div class='tip-mute'>" + GD.esc(a.question) + "</div>" +
        (st.n ? "<div class='tip-row'><span class='tip-mute'>needs you</span><b>" + st.n + "</b></div>" : ""));
      rail.appendChild(btn);
    });

    return rail;
  }

  function showAgent(a) {
    var st = agentState(a);
    GD.sheet({
      title: a.name,
      sub: a.question,
      body: function (close) {
        return [
          el("p", { style: { color: "var(--ink-2)", "margin-bottom": "16px" }, text: a.blurb }),
          GD.kv([
            ["Folds in", a.folds],
            ["State", el("span", { class: "chip chip-" + (st.state === "attention" ? "warn" : st.state === "working" ? "good" : "") },
              [st.state === "attention" ? st.n + " need you" : st.state === "working" ? "Running" : "Idle"])],
            ["Approval", a.key === "buyer" ? "Drafts only. Pavin sends."
              : a.key === "watch" ? "Alerts freely. Never places an order."
              : "Raises claims for review. Never files one."]
          ])
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-primary", type: "button", "data-autofocus": "true",
            onclick: function () { close(); GD.go(a.route); }
          }, [icon("chevR", 14, 2), "Open " + a.name + "'s surface"]),
          el("button", { class: "btn", type: "button", onclick: close, text: "Close" })
        ];
      }
    });
  }

  /* -------------------------------------------------------------- sidebar */

  function buildSidebar() {
    var side = el("aside", { class: "sidebar" });

    side.appendChild(el("div", { class: "side-head" }, [
      el("span", { class: "avatar", text: "U" }),
      el("div", { class: "org" }, [
        el("div", { class: "org-name", text: D.profile.mill }),
        el("div", { class: "org-sub", text: D.profile.group.join(" · ") + " · " + D.profile.country })
      ])
    ]));

    var scroll = el("div", { class: "side-scroll" });
    var routes = GD.routes();
    var groups = {};
    Object.keys(routes).forEach(function (k) {
      var r = routes[k];
      (groups[r.group] = groups[r.group] || []).push(k);
    });

    Object.keys(groups).forEach(function (g) {
      var box = el("div", { class: "nav-group" });
      box.appendChild(el("div", { class: "nav-label", text: g }));
      groups[g].forEach(function (k) {
        var r = routes[k];
        var badge = r.badge ? r.badge() : null;
        var item = el("button", {
          class: "nav-item", type: "button", "data-route": k,
          onclick: function () { GD.go(k); }
        }, [
          icon(r.icon, 16, 1.6),
          el("span", { class: "txt", text: r.label }),
          badge === null || badge === undefined ? null :
            el("span", { class: "chip " + (r.badgeTone ? "chip-" + r.badgeTone : "chip-outline") , text: String(badge) })
        ]);
        box.appendChild(item);
      });
      scroll.appendChild(box);
    });
    side.appendChild(scroll);

    /* Savings ledger as the sidebar meter — the number that justifies renewal. */
    var saved = D.savings_total;
    var target = D.advisor.fee_per_year;
    side.appendChild(el("div", { class: "side-foot" }, [
      el("div", { class: "meter-card" }, [
        el("div", { class: "meter-top" }, [
          el("span", { class: "meter-val", text: fmt.money(saved) }),
          el("span", { class: "meter-cap", text: "saved this year" })
        ]),
        el("div", { class: "meter-track" }, [
          el("div", {
            class: "meter-fill",
            style: { width: Math.min(100, saved / target * 100).toFixed(1) + "%" }
          })
        ]),
        el("div", { class: "meter-cap", text: "vs " + fmt.money(target) + " in advisor success fees" })
      ]),
      el("button", {
        class: "user-row", type: "button",
        onclick: function () { GD.go("savings"); }
      }, [
        el("span", { class: "avatar", text: "PS" }),
        el("div", { class: "who" }, [
          el("b", { text: D.profile.buyer.name }),
          el("span", { text: D.profile.buyer.email })
        ]),
        icon("chevR", 14, 1.6)
      ])
    ]));
    return side;
  }

  /* -------------------------------------------------------- market strip */

  function buildStrip() {
    var strip = el("div", { class: "strip", role: "region", "aria-label": "Market" });

    D.instruments.forEach(function (ins) {
      var isPrice = ins.unit === "c/bu";
      var dpByUnit = ins.unit === "THB" ? 3 : ins.unit === "USD/day" ? 0 : 2;
      var val = ins.unit === "USD/day" ? fmt.n(ins.last, 0) : fmt.n(ins.last, dpByUnit);
      /* A rising input price costs the mill money, so up is the bad tone here. */
      var tone = GD.toneFor(ins.change);
      var btn = el("button", { class: "tick", type: "button", onclick: function () { GD.go("market"); } }, [
        el("span", { class: "tick-badge" }, [
          icon(ins.role === "fx" ? "globe" : ins.role === "freight" ? "ship" : "wheat", 14, 1.6)
        ]),
        el("span", { class: "tick-body" }, [
          el("span", { class: "tick-value" }, [
            el("span", { text: val }),
            el("span", { class: "tick-delta " + tone, text: fmt.signedPct(ins.change_pct / 100) })
          ]),
          el("span", { class: "tick-label", text: ins.key })
        ]),
        GD.chart.spark(ins.history, {
          w: 54, h: 22, take: 45,
          color: ins.change < 0 ? "var(--down)" : ins.change > 0 ? "var(--up)" : "var(--ink-4)"
        })
      ]);
      GD.tipOn(btn, function () {
        return "<b>" + GD.esc(ins.label) + "</b>" +
          "<div class='tip-row'><span class='tip-mute'>last</span><b>" + GD.esc(val + " " + ins.unit) + "</b></div>" +
          (isPrice ? "<div class='tip-row'><span class='tip-mute'>per tonne</span><b>" + GD.esc(fmt.usd(ins.usd_mt)) + "</b></div>" : "") +
          "<div class='tip-row'><span class='tip-mute'>vs month ago</span><b>" +
          GD.esc(fmt.signedPct(ins.last / ins.month_ago - 1)) + "</b></div>";
      });
      strip.appendChild(btn);
    });

    /* Next WASDE — the clock The Watch is built around. */
    var next = D.wasde.filter(function (w) { return w.status === "scheduled"; })[0];
    if (next) {
      var days = fmt.daysBetween(D.built, next.date);
      var nb = el("button", {
        class: "tick", type: "button", style: { "margin-left": "auto" },
        onclick: function () { GD.go("market"); }
      }, [
        el("span", { class: "tick-badge", style: { background: "var(--accent-soft)", color: "var(--accent-2)" } }, [icon("clock", 14, 1.8)]),
        el("span", { class: "tick-body" }, [
          el("span", { class: "tick-value" }, [el("span", { text: "in " + days + " days" })]),
          el("span", { class: "tick-label", text: "Next WASDE" })
        ])
      ]);
      GD.tipOn(nb, "<b>WASDE — " + GD.esc(fmt.date(next.date)) + "</b><div class='tip-mute'>Lands " +
        GD.esc(fmt.time(next.local)) + " Bangkok" + (next.dst ? " (US DST)" : "") + "</div>");
      strip.appendChild(nb);
    }
    return strip;
  }

  /* ------------------------------------------------------------- topbar */

  function buildTopbar() {
    var crumbs = el("div", { class: "crumbs" }, [
      icon("wheat", 14, 1.6),
      el("span", { text: D.profile.short }),
      el("span", { class: "sep", text: "/" }),
      el("b", { "data-crumb": "", text: "" })
    ]);

    var search = el("button", {
      class: "search", type: "button", "aria-label": "Search the desk",
      onclick: openPalette
    }, [
      icon("search", 14, 1.6),
      el("span", { text: "Search the desk" }),
      el("span", { class: "kbd-wrap" }, [el("kbd", { class: "kbd", text: "⌘K" })])
    ]);

    var nightBtn = el("button", {
      class: "btn btn-primary", type: "button",
      onclick: function () { GD.nightWatch(); }
    }, [icon("zap", 14, 2), "Night Watch"]);
    GD.tipOn(nightBtn, "Replay the 9 Oct WASDE alert exactly as it landed");

    return el("header", { class: "topbar" }, [
      el("button", {
        class: "icon-btn", type: "button", "aria-label": "Toggle sidebar",
        onclick: function () {
          var app = qs(".app");
          var next = app.getAttribute("data-sidebar") === "collapsed" ? "open" : "collapsed";
          app.setAttribute("data-sidebar", next);
        }
      }, [icon("panel", 16, 1.6)]),
      crumbs,
      el("div", { class: "topbar-spacer" }),
      search,
      el("button", {
        class: "icon-btn", type: "button", "aria-label": "Notifications",
        onclick: function () { GD.go("brief"); }
      }, [icon("bell", 16, 1.6)]),
      nightBtn
    ]);
  }

  /* ------------------------------------------------------- command palette */

  function paletteItems() {
    var out = [];
    var routes = GD.routes();
    Object.keys(routes).forEach(function (k) {
      out.push({
        group: "Go to", label: routes[k].label, icon: routes[k].icon,
        hint: routes[k].group, run: function () { GD.go(k); }
      });
    });
    out.push({
      group: "Actions", label: "Replay the WASDE alert", icon: "zap", hint: "Night Watch",
      run: function () { GD.nightWatch(); }
    });
    D.quotes.slice().sort(function (a, b) { return a.received < b.received ? 1 : -1; })
      .slice(0, 12).forEach(function (q) {
        out.push({
          group: "Quotes", label: q.supplier + " · " + q.cls, icon: "quotes",
          hint: fmt.usd(q.flat) + "/t",
          run: function () { GD.go("quotes"); setTimeout(function () { GD.showQuote(q); }, 60); }
        });
      });
    D.cargoes.forEach(function (c) {
      out.push({
        group: "Cargoes", label: c.vessel, icon: "ship", hint: c.origin + " · " + c.status,
        run: function () { GD.go("cargo"); setTimeout(function () { GD.showCargo(c); }, 60); }
      });
    });
    return out;
  }

  function openPalette() {
    GD.openOverlay(function (close) {
      var items = paletteItems();
      var active = 0;
      var list = el("div", { class: "pal-list", role: "listbox" });
      var input = el("input", {
        class: "pal-input", type: "text", placeholder: "Search surfaces, quotes, cargoes…",
        "aria-label": "Search", "data-autofocus": "true", autocomplete: "off"
      });

      function matches() {
        var q = input.value.trim().toLowerCase();
        if (!q) return items;
        return items.filter(function (i) {
          return (i.label + " " + i.group + " " + (i.hint || "")).toLowerCase().indexOf(q) >= 0;
        });
      }
      function draw() {
        GD.clear(list);
        var ms = matches();
        if (active >= ms.length) active = Math.max(0, ms.length - 1);
        if (!ms.length) {
          list.appendChild(el("div", { class: "empty" }, [
            icon("search", 20, 1.5), el("div", { text: "Nothing matches that." })
          ]));
          return;
        }
        var lastGroup = null;
        ms.forEach(function (it, i) {
          if (it.group !== lastGroup) {
            lastGroup = it.group;
            list.appendChild(el("div", { class: "pal-group label", text: it.group }));
          }
          var row = el("button", {
            class: "pal-item", type: "button", role: "option",
            "data-active": String(i === active),
            "aria-selected": String(i === active),
            onclick: function () { close(); it.run(); },
            onmouseenter: function () { active = i; refreshActive(ms); }
          }, [
            icon(it.icon || "chevR", 15, 1.6),
            el("span", { text: it.label }),
            it.hint ? el("span", { class: "hint", text: it.hint }) : null
          ]);
          list.appendChild(row);
        });
      }
      function refreshActive(ms) {
        var rows = qsa(".pal-item", list);
        rows.forEach(function (r, i) {
          r.setAttribute("data-active", String(i === active));
          r.setAttribute("aria-selected", String(i === active));
        });
        if (rows[active]) rows[active].scrollIntoView({ block: "nearest" });
      }

      input.addEventListener("input", function () { active = 0; draw(); });
      input.addEventListener("keydown", function (ev) {
        var ms = matches();
        if (ev.key === "ArrowDown") { ev.preventDefault(); active = Math.min(ms.length - 1, active + 1); refreshActive(ms); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); active = Math.max(0, active - 1); refreshActive(ms); }
        else if (ev.key === "Enter") {
          ev.preventDefault();
          if (ms[active]) { close(); ms[active].run(); }
        }
      });
      draw();
      return el("div", { class: "sheet palette" }, [input, list]);
    });
  }
  GD.openPalette = openPalette;

  /* ------------------------------------------------------------ night watch
     Not a page: an interrupt. Replays the 9 Oct release exactly as it landed. */

  GD.nightWatch = function () {
    var w = D.wasde.filter(function (x) { return x.status === "released"; })[0];
    if (!w) { GD.toast("No released report to replay"); return; }
    var cargo = D.profile.economics.cargo_mt;

    GD.sheet({
      title: "WASDE — " + fmt.date(w.date),
      sub: "Released " + fmt.time(w.local) + " Bangkok" + (w.dst ? " · US daylight saving in force" : "") +
           " · verdict in 8 seconds",
      class: "nightwatch",
      body: function () {
        var rows = w.verdicts.map(function (v) {
          var impact = v.usd_mt * cargo;
          return el("tr", null, [
            el("td", { class: "strong", text: v.cls }),
            el("td", null, [
              el("div", { text: v.surprise }),
              el("div", { class: "card-sub", text: "expected " + v.expected + " · printed " + v.actual })
            ]),
            el("td", null, [GD.chip(v.stance === "bearish" ? "BEARISH" : "BULLISH",
              v.stance === "bearish" ? "good" : "bad")]),
            el("td", { class: "num " + GD.toneFor(v.usd_mt, true) },
              fmt.signed(v.usd_mt) + "/t"),
            el("td", { class: "num " + GD.toneFor(impact, true) },
              fmt.signedMoney(impact)),
            el("td", null, [v.stance === "bearish" && v.usd_mt < -2
              ? GD.chip("Call Mitsui", "accent", "send") : GD.chip("Hold", "")])
          ]);
        });
        var table = el("table", null, [
          el("thead", null, [el("tr", null, [
            el("th", { text: "Class" }), el("th", { text: "Print vs expected" }),
            el("th", { text: "Direction" }), el("th", { class: "num", text: "Per tonne" }),
            el("th", { class: "num", text: "On your cargo" }), el("th", { text: "Action" })
          ])]),
          el("tbody", null, rows)
        ]);

        var worst = w.verdicts.slice().sort(function (a, b) { return a.usd_mt - b.usd_mt; })[0];
        var draft =
          "Khun Boom — following the WASDE print, we see " + worst.cls +
          " fundamentals softer than the trade expected (" + worst.surprise +
          "). Could you re-indicate " + worst.cls + " for 20 Dec – 20 Jan, CFR Koh Sichang, 55KMT?";

        return [
          el("div", { class: "table-wrap", style: { "margin-bottom": "18px" } }, [table]),
          el("div", { class: "label", style: { "margin-bottom": "7px" }, text: "Drafted, not sent" }),
          el("div", {
            class: "card", style: { padding: "13px 14px", background: "var(--surface-2)", "box-shadow": "none" }
          }, [
            el("div", { class: "card-sub", style: { "margin-bottom": "6px" }, text: "To: A.Chonnigarn@mitsui.com" }),
            el("div", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" }, text: draft })
          ]),
          el("p", { class: "card-sub", style: { "margin-top": "10px" } },
            "Expectation set from the Reuters trade poll, the prior WASDE and Pavin's own stated bias — " +
            "shown so a wrong call is auditable.")
        ];
      },
      foot: function (close) {
        return [
          el("button", {
            class: "btn btn-accent", type: "button", "data-autofocus": "true",
            onclick: function () { close(); GD.toast("Draft sent to Mitsui", { icon: "send" }); }
          }, [icon("send", 14, 2), "Send to Mitsui"]),
          el("button", { class: "btn", type: "button", text: "Edit draft", onclick: function () { GD.toast("Opened for editing", { icon: "file" }); } }),
          el("button", { class: "btn btn-ghost", type: "button", text: "Snooze", onclick: close }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "The agent prepares the five minutes. Pavin spends them." })
        ];
      }
    });
  };

  /* --------------------------------------------------------------- boot */

  function boot() {
    var app = el("div", { class: "app", "data-sidebar": "open" });
    var main = el("div", { class: "main" });
    var content = el("main", { class: "content", id: "content", tabindex: "-1" });

    app.appendChild(buildRail());
    app.appendChild(buildSidebar());
    main.appendChild(buildTopbar());
    main.appendChild(buildStrip());
    main.appendChild(content);
    app.appendChild(main);

    document.body.appendChild(app);

    GD.onRoute(function (key, def) {
      qsa(".nav-item").forEach(function (n) {
        if (n.getAttribute("data-route") === key) n.setAttribute("aria-current", "page");
        else n.removeAttribute("aria-current");
      });
      var crumb = qs("[data-crumb]");
      if (crumb) crumb.textContent = def.label;
      document.title = def.label + " · Grain Desk";

      GD.clear(content);
      var wrap = el("div", { class: "wrap" });
      content.appendChild(wrap);
      try {
        def.render(wrap);
      } catch (err) {
        wrap.appendChild(el("div", { class: "card" }, [
          el("div", { class: "card-body" }, [
            el("div", { class: "empty" }, [
              icon("alert", 22, 1.6),
              el("div", { text: "This surface failed to render." }),
              el("code", { class: "mono", style: { "font-size": "11px", color: "var(--ink-3)" }, text: String(err && err.message || err) })
            ])
          ])
        ]));
        if (window.console) console.error("[GrainDesk] surface '" + key + "' failed:", err);
      }
      GD.stagger(wrap);
      content.scrollTop = 0;
    });

    document.addEventListener("keydown", function (ev) {
      var tag = (ev.target && ev.target.tagName) || "";
      var typing = tag === "INPUT" || tag === "TEXTAREA" || (ev.target && ev.target.isContentEditable);
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault(); openPalette(); return;
      }
      if (typing) return;
      if (ev.key === "/") { ev.preventDefault(); openPalette(); }
    });

    GD.startRouter();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

})();
