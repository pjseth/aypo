/* Grain Desk — shell: rail, sidebar, topbar, market strip, palette, routing.
   Runs last; every surface has registered itself with GD.route() by now.

   Four surfaces, in the order the questions get asked:

     Market        what are prices doing
     Price check   what should I be paying for this
     Spend         what did I actually pay
     Bulletin      what do I tell everyone, every fortnight

   The wheat desk that this product started as lives on the `v2-urc-essentials` branch, with
   its seven surfaces and its own data. None of it is loaded here: a dashboard that carries a
   second customer's screens around unreachably is not a simpler dashboard. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, qs = GD.qs, qsa = GD.qsa, fmt = GD.fmt;

  /* The desk is light only. The three reference designs are light, it is read in
     a lit office, and one palette is one palette to keep honest — so there is no
     theme state, no toggle and nothing stored. */

  /* ------------------------------------------------------- agents in rail

     Three agents, one question each. The rail shows what each is holding for you; the count
     on the dot is read from the badge of the agent's own surface, so the dot and the screen
     behind it cannot disagree. */

  var AGENTS = [
    {
      key: "watch", name: "The Watch", icon: "market", route: "prices",
      question: "What is the market doing?",
      folds: "The Analyst · Bulletin",
      blurb: "Owns the feeds and the fortnightly clock. Tracks every index, freight rate and " +
             "FX pair that sets the floor under a material, and assembles the bulletin from " +
             "them — saying where the data is missing rather than filling it.",
      approval: "Reports freely. Never places an order."
    },
    {
      key: "buyer", name: "The Buyer", icon: "scale", route: "pricecheck",
      question: "What should I pay?",
      folds: "Fair Value · Negotiator",
      blurb: "Splits every quote into its published drivers and the premium the supplier is " +
             "carrying, measures that premium against the whole material's history, and " +
             "drafts the brief for the call.",
      approval: "Drafts only. A person sends."
    },
    {
      key: "clerk", name: "The Clerk", icon: "dollar", route: "spend",
      question: "Did I pay what I agreed?",
      folds: "Spend Clerk",
      blurb: "Takes the raw S/4HANA export, resolves the vendor spellings and the free-text " +
             "material descriptions, converts the currencies, flags the double postings, and " +
             "hands back a report that reconciles.",
      approval: "Classifies, then asks about anything it is unsure of."
    }
  ];

  function agentState(a) {
    var def = GD.routes()[a.route];
    var n = def && def.badge ? def.badge() : 0;
    if (!n) return { state: a.key === "watch" ? "working" : "idle", n: 0 };
    return { state: "attention", n: n };
  }
  GD.AGENTS = AGENTS;
  GD.agentState = agentState;

  /* ----------------------------------------------------------------- rail */

  function buildRail() {
    var rail = el("div", { class: "rail" });
    rail.appendChild(el("div", { class: "rail-logo", title: "Grain Desk", text: "GD" }));

    AGENTS.forEach(function (a) {
      if (!GD.routes()[a.route]) return;
      var st = agentState(a);
      var btn = el("button", {
        class: "rail-btn", type: "button",
        "aria-label": a.name + " — " + a.question,
        onclick: function () { showAgent(a); }
      }, [icon(a.icon, 17, 1.6), el("span", { class: "dot", "data-state": st.state })]);
      GD.tipOn(btn, "<b>" + GD.esc(a.name) + "</b><div class='tip-mute'>" +
        GD.esc(a.question) + "</div>" +
        (st.n ? "<div class='tip-row'><span class='tip-mute'>needs you</span><b>" +
          st.n + "</b></div>" : ""));
      rail.appendChild(btn);
    });
    return rail;
  }

  function showAgent(a) {
    var st = agentState(a);
    GD.sheet({
      title: a.name,
      sub: a.question,
      body: function () {
        return [
          el("p", { style: { color: "var(--ink-2)", "margin-bottom": "16px" }, text: a.blurb }),
          GD.kv([
            ["Folds in", a.folds],
            ["State", el("span", {
              class: "chip chip-" + (st.state === "attention" ? "warn"
                : st.state === "working" ? "good" : "")
            }, [st.state === "attention" ? st.n + " need you"
                : st.state === "working" ? "Running" : "Idle"])],
            ["Approval", a.approval]
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
    var ws = GD.ws();

    side.appendChild(el("div", { class: "side-head" }, [
      el("span", { class: "avatar", text: ws.short.slice(0, 1).toUpperCase() }),
      el("div", { class: "org" }, [
        el("div", { class: "org-name", text: ws.name }),
        el("div", {
          class: "org-sub",
          text: ws.country + " · " + ws.home_currency + (ws.demo ? " · demo data" : "")
        })
      ])
    ]));

    var scroll = el("div", { class: "side-scroll" });
    var routes = GD.routes();
    var groups = {};
    Object.keys(routes).forEach(function (k) {
      (groups[routes[k].group] = groups[routes[k].group] || []).push(k);
    });

    Object.keys(groups).forEach(function (g) {
      var box = el("div", { class: "nav-group" });
      box.appendChild(el("div", { class: "nav-label", text: g }));
      groups[g].forEach(function (k) {
        var r = routes[k];
        var badge = r.badge ? r.badge() : null;
        box.appendChild(el("button", {
          class: "nav-item", type: "button", "data-route": k,
          onclick: function () { GD.go(k); }
        }, [
          icon(r.icon, 16, 1.6),
          el("span", { class: "txt", text: r.label }),
          badge === null || badge === undefined ? null
            : el("span", {
                class: "chip " + (r.badgeTone ? "chip-" + r.badgeTone : "chip-outline"),
                text: String(badge)
              })
        ]));
      });
      scroll.appendChild(box);
    });
    side.appendChild(scroll);
    side.appendChild(sideFoot(ws));
    return side;
  }

  /* The meter carries the one honest number there is: how much of the book the desk can
     actually see. A desk that covers three materials out of eight should say so. */
  function sideFoot(ws) {
    var covered = 0;
    ws.materials.forEach(function (m) {
      if (ws.quotes.some(function (q) { return q.material_id === m.id; })) covered++;
    });
    var pct = ws.materials.length ? covered / ws.materials.length : 0;
    return el("div", { class: "side-foot" }, [
      el("div", { class: "meter-card" }, [
        el("div", { class: "meter-top" }, [
          el("span", { class: "meter-val", text: covered + " of " + ws.materials.length }),
          el("span", { class: "meter-cap", text: "materials priced" })
        ]),
        el("div", { class: "meter-track" }, [
          el("div", { class: "meter-fill", style: { width: (pct * 100).toFixed(1) + "%" } })
        ]),
        el("div", {
          class: "meter-cap",
          text: fmt.n(ws.quotes.length, 0) + " quotes from " + ws.suppliers.length + " suppliers"
        })
      ]),
      el("button", {
        class: "user-row", type: "button",
        onclick: function () { GD.go("pricecheck"); }
      }, [
        el("span", { class: "avatar", text: "CP" }),
        el("div", { class: "who" }, [
          el("b", { text: "Central procurement" }),
          el("span", { text: ws.country + (ws.demo ? " · demo workspace" : "") })
        ]),
        icon("chevR", 14, 1.6)
      ])
    ]);
  }

  /* -------------------------------------------------------- market strip */

  function buildStrip() {
    var strip = el("div", { class: "strip", role: "region", "aria-label": "Market" });

    (GD.wsInstruments() || []).forEach(function (ins) {
      /* Decimals from the magnitude, not from a table of units to keep in step: a four-figure
         level reads better flat, a rate needs its places. */
      var dp = Math.abs(ins.last) >= 1000 ? 0 : 2;
      var val = fmt.n(ins.last, dp);
      /* A rising input price costs the buyer money, so up is the bad tone here. */
      var tone = GD.toneFor(ins.change);
      var btn = el("button", {
        class: "tick", type: "button", onclick: function () { GD.go("prices"); }
      }, [
        el("span", { class: "tick-badge" }, [
          icon(ins.role === "fx" ? "globe" : ins.role === "freight" ? "ship" : "market", 14, 1.6)
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
          "<div class='tip-row'><span class='tip-mute'>last</span><b>" +
          GD.esc(val + " " + ins.unit) + "</b></div>" +
          "<div class='tip-row'><span class='tip-mute'>vs month ago</span><b>" +
          GD.esc(fmt.signedPct(ins.last / ins.month_ago - 1)) + "</b></div>" +
          "<div class='tip-mute'>Open the market screen</div>";
      });
      strip.appendChild(btn);
    });

    /* The clock this desk is built around: the fortnightly bulletin. */
    var due = GD.bulletinDue ? GD.bulletinDue() : null;
    if (due) {
      var db = el("button", {
        class: "tick", type: "button", style: { "margin-left": "auto" },
        onclick: function () { GD.go("bulletin"); }
      }, [
        el("span", {
          class: "tick-badge",
          style: { background: "var(--accent-soft)", color: "var(--accent-2)" }
        }, [icon("clock", 14, 1.8)]),
        el("span", { class: "tick-body" }, [
          el("span", { class: "tick-value" }, [
            el("span", { text: due.days <= 0 ? "due now" : "in " + due.days + " days" })
          ]),
          el("span", { class: "tick-label", text: "Next bulletin" })
        ])
      ]);
      GD.tipOn(db, "<b>Market bulletin — " + GD.esc(fmt.date(due.date)) +
        "</b><div class='tip-mute'>Issued every " + due.cadence +
        " days. The last one went out " + GD.esc(fmt.date(due.last)) + ".</div>");
      strip.appendChild(db);
    }
    return strip;
  }

  /* ------------------------------------------------------------- topbar */

  function buildTopbar() {
    var ws = GD.ws();
    var crumbs = el("div", { class: "crumbs" }, [
      icon("globe", 14, 1.6),
      el("span", { text: ws.short }),
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

    var bulletinBtn = el("button", {
      class: "btn btn-primary", type: "button",
      onclick: function () { GD.go("bulletin"); }
    }, [icon("file", 14, 2), "Market bulletin"]);
    GD.tipOn(bulletinBtn, "The fortnightly bulletin — generate, edit, approve, export");

    return el("header", { class: "topbar" }, [
      el("button", {
        class: "icon-btn", type: "button", "aria-label": "Toggle sidebar",
        onclick: function () {
          var app = qs(".app");
          app.setAttribute("data-sidebar",
            app.getAttribute("data-sidebar") === "collapsed" ? "open" : "collapsed");
        }
      }, [icon("panel", 16, 1.6)]),
      crumbs,
      el("div", { class: "topbar-spacer" }),
      search,
      bulletinBtn
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

    var ws = GD.ws();
    ws.materials.forEach(function (m) {
      out.push({
        group: "Materials", label: m.name, icon: "layers",
        hint: m.category === "packaging" ? "packaging" : "raw material",
        run: function () {
          GD.go("pricecheck");
          setTimeout(function () {
            if (GD.filterPriceCheck) GD.filterPriceCheck(m.id);
          }, 80);
        }
      });
    });

    /* The live board: the latest quote from each supplier of each material, which is what
       somebody reaching for the palette is usually after. */
    if (GD.pricing) {
      ws.materials.forEach(function (m) {
        GD.pricing.latestPerSupplier(m.id, ws.quotes).forEach(function (q) {
          out.push({
            group: "Quotes", label: GD.supplierName(q.supplier_id, ws) + " · " + m.name,
            icon: "scale", hint: GD.curUnit(q.price, m) + "/" + m.unit,
            run: function () {
              GD.go("pricecheck");
              setTimeout(function () { GD.showPriceCheck(q, ws); }, 60);
            }
          });
        });
      });
    }
    return out;
  }

  function openPalette() {
    GD.openOverlay(function (close) {
      var items = paletteItems();
      var active = 0;
      var list = el("div", { class: "pal-list", role: "listbox" });
      var input = el("input", {
        class: "pal-input", type: "text", placeholder: "Search surfaces, materials, quotes…",
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
          list.appendChild(el("button", {
            class: "pal-item", type: "button", role: "option",
            "data-active": String(i === active),
            "aria-selected": String(i === active),
            onclick: function () { close(); it.run(); },
            onmouseenter: function () { active = i; refreshActive(); }
          }, [
            icon(it.icon || "chevR", 15, 1.6),
            el("span", { text: it.label }),
            it.hint ? el("span", { class: "hint", text: it.hint }) : null
          ]));
        });
      }
      function refreshActive() {
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
        if (ev.key === "ArrowDown") { ev.preventDefault(); active = Math.min(ms.length - 1, active + 1); refreshActive(); }
        else if (ev.key === "ArrowUp") { ev.preventDefault(); active = Math.max(0, active - 1); refreshActive(); }
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
              el("code", {
                class: "mono",
                style: { "font-size": "11px", color: "var(--ink-3)" },
                text: String(err && err.message || err)
              })
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
      var typing = tag === "INPUT" || tag === "TEXTAREA" ||
                   (ev.target && ev.target.isContentEditable);
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
