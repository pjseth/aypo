/* Grain Desk — Spend cleansing.

   "The data cleansing alone takes about five days." That is a fortnight's reporting cycle
   where a third of it is spent making the export usable before anyone can read it: the same
   supplier under four spellings, the material as whatever somebody typed into the PO, three
   currencies, and a handful of documents posted twice.

   None of that is analysis. All of it is arithmetic and string matching, which is what this
   surface does — and then it asks about the few lines it genuinely cannot resolve, remembers
   the answers, and never asks again.

   The report at the end reconciles to the line items, and says so on screen, because the
   first thing a finance team does with a procurement report is check that it ties. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, S = GD.spendlib;

  var C_RAW = "var(--c-basis)",
      C_PACK = "var(--c-futures)",
      C_UNCL = "var(--ink-4)";

  function catColor(key) {
    return key === "raw" ? C_RAW : key === "packaging" ? C_PACK : C_UNCL;
  }

  /* One run at a time, kept on GD so the rail's agent badge and the sidebar count can read
     the same numbers the screen is showing. */
  GD.spendRun = null;
  GD.spendReport = null;

  function labelled(text, style) {
    var s = { "margin-bottom": "8px" };
    if (style) Object.keys(style).forEach(function (k) { s[k] = style[k]; });
    return el("div", { class: "label", style: s, text: text });
  }
  function panel(kids, style) {
    var s = {
      border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
      background: "var(--surface-2)", padding: "13px 14px"
    };
    if (style) Object.keys(style).forEach(function (k) { s[k] = style[k]; });
    return el("div", { style: s }, kids);
  }
  function statCell(k, v, tone) {
    return el("div", null, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v " + (tone || ""), text: v })
    ]);
  }
  function money(v, cur) { return GD.curM(v, cur); }

  /* ===================================================================== the pipeline */

  function steps(run, rep) {
    var lines = run.lines;
    var resolvedV = lines.filter(function (l) { return l.supplier_id; }).length;
    var resolvedM = lines.filter(function (l) { return l.material_id; }).length;
    var converted = lines.filter(function (l) {
      return l.currency !== run.ws.home_currency && l.amountHome !== null;
    }).length;
    var currencies = {};
    lines.forEach(function (l) { if (l.currency) currencies[l.currency] = true; });
    var spellings = {};
    lines.forEach(function (l) {
      if (l.supplier_id) {
        (spellings[l.supplier_id] = spellings[l.supplier_id] || {})[S.normalizeVendor(l.vendorRaw)] = 1;
      }
    });
    var extra = 0;
    Object.keys(spellings).forEach(function (k) {
      extra += Math.max(0, Object.keys(spellings[k]).length - 1);
    });

    return [
      {
        name: "Read the file",
        detail: fmt.n(lines.length, 0) + " lines, " + run.columns.header.length +
                " columns, delimiter " +
                (run.delimiter === "xlsx" ? "n/a (xlsx)" :
                 run.delimiter === "\t" ? "tab" : '"' + run.delimiter + '"'),
        n: lines.length
      },
      {
        name: "Resolve the vendor names",
        detail: fmt.n(resolvedV, 0) + " of " + fmt.n(lines.length, 0) + " matched to a " +
                "supplier in the master; " + extra + " alternative spellings folded in",
        n: resolvedV
      },
      {
        name: "Classify the material and category",
        detail: fmt.n(resolvedM, 0) + " of " + fmt.n(lines.length, 0) + " classified from " +
                "free text to one of " + run.ws.materials.length + " materials",
        n: resolvedM
      },
      {
        name: "Convert to " + run.ws.home_currency,
        detail: fmt.n(converted, 0) + " lines in " +
                (Object.keys(currencies).length - 1) + " other currencies (" +
                Object.keys(currencies).filter(function (c) {
                  return c !== run.ws.home_currency;
                }).join(", ") + ") converted at the indicative mid",
        n: converted
      },
      {
        name: "Flag the duplicates",
        detail: run.counts.duplicates + " lines are a repeat of an earlier one, worth " +
                money(dupeValue(run), run.ws.home_currency) +
                " — left out of every total and listed below",
        n: run.counts.duplicates
      }
    ];
  }

  function dupeValue(run) {
    var v = 0;
    run.lines.forEach(function (l) { if (l.duplicate && l.amountHome) v += l.amountHome; });
    return v;
  }

  /* Sub-millisecond is a real answer on a file this size, and rounding it up to a
     suspiciously round "1 ms" makes a true number look like a placeholder. */
  function msText(ms) {
    if (ms === null || ms === undefined) return "no timing";
    /* A headless browser running on virtual time measures this as zero, and "0.0 ms" reads
       as a broken clock rather than a fast one. */
    if (ms < 1) return "under a millisecond";
    return fmt.n(ms, ms < 10 ? 1 : 0) + " ms";
  }

  /* ======================================================================= the loader */

  function loader(ws, onRun) {
    var learned = S.loadLearned(ws.key);
    var nLearned = S.countLearned(learned);
    var statusNode = el("div", {
      style: { "font-size": "var(--t-sm)", color: "var(--ink-3)", "min-height": "20px" }
    });

    function run(parsed, source) {
      statusNode.textContent = "Reading " + parsed.name + "…";
      var t0 = (window.performance && performance.now) ? performance.now() : Date.now();
      var r;
      try {
        r = S.process({ ws: ws, parsed: parsed, learned: S.loadLearned(ws.key) });
      } catch (err) {
        statusNode.textContent = "";
        GD.toast("Could not process that file: " + (err && err.message), { icon: "alert" });
        return;
      }
      var t1 = (window.performance && performance.now) ? performance.now() : Date.now();
      r.ms = Math.round((t1 - t0) * 10) / 10;
      r.source = source;
      onRun(r);
    }

    var fileIn = el("input", {
      type: "file", accept: ".csv,.tsv,.txt,.xlsx", style: { display: "none" },
      "aria-label": "Choose a spend export",
      onchange: function () {
        var f = fileIn.files && fileIn.files[0];
        if (!f) return;
        statusNode.textContent = "Reading " + f.name + "…";
        S.readFile(f).then(function (parsed) { run(parsed, f.name); })
          .catch(function (err) {
            statusNode.textContent = "";
            GD.toast(String(err && err.message || err), { icon: "alert", duration: 5000 });
          });
        fileIn.value = "";
      }
    });

    function loadSample() {
      var s = window.SPEND_SAMPLE;
      if (!s) { GD.toast("No sample is bundled for this workspace", { icon: "alert" }); return; }
      var parsed = S.parseDelimited(s.text);
      parsed.name = s.name;
      run(parsed, s.name);
    }

    var drop = el("div", {
      style: {
        border: "1px dashed var(--border-2)", "border-radius": "var(--r-card)",
        padding: "30px 22px", display: "grid", gap: "13px", "justify-items": "center",
        "text-align": "center", background: "var(--surface-2)",
        "transition-property": "border-color, background",
        "transition-duration": "var(--d-fast)", "transition-timing-function": "var(--ease)"
      },
      ondragover: function (ev) {
        ev.preventDefault();
        drop.style.setProperty("border-color", "var(--accent)");
        drop.style.setProperty("background", "var(--accent-soft)");
      },
      ondragleave: function () {
        drop.style.setProperty("border-color", "var(--border-2)");
        drop.style.setProperty("background", "var(--surface-2)");
      },
      ondrop: function (ev) {
        ev.preventDefault();
        drop.style.setProperty("border-color", "var(--border-2)");
        drop.style.setProperty("background", "var(--surface-2)");
        var f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
        if (!f) return;
        S.readFile(f).then(function (parsed) { run(parsed, f.name); })
          .catch(function (err) {
            GD.toast(String(err && err.message || err), { icon: "alert", duration: 5000 });
          });
      }
    }, [
      el("span", { style: { color: "var(--ink-4)" } }, [icon("file", 26, 1.5)]),
      el("div", { class: "card-title", text: "Drop an S/4HANA spend export here" }),
      el("div", {
        style: { "font-size": "var(--t-sm)", color: "var(--ink-3)", "max-width": "440px" },
        text: "CSV, tab-separated or .xlsx. The columns it looks for are posting date, " +
              "document number, vendor name, material description, plant, quantity, unit, " +
              "net amount, currency and purchase order — under any of the names SAP gives " +
              "them."
      }),
      el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
        ws.has_spend_sample ? el("button", {
          class: "btn btn-primary", type: "button", onclick: loadSample
        }, [icon("zap", 14, 2), "Load the sample export"]) : null,
        el("button", {
          class: "btn", type: "button", onclick: function () { fileIn.click(); }
        }, [icon("download", 14, 1.75), "Choose a file"]),
        fileIn
      ]),
      statusNode
    ]);

    var memory = el("div", {
      class: "flex items-center gap-8",
      style: { "flex-wrap": "wrap", "font-size": "var(--t-sm)", color: "var(--ink-3)" }
    }, [
      icon(nLearned ? "check" : "clock", 14, 1.75),
      el("span", {
        text: nLearned
          ? nLearned + " correction" + (nLearned === 1 ? "" : "s") + " remembered from " +
            "earlier uploads — they will be applied before anything is guessed at."
          : "No corrections remembered yet. Anything you confirm below is kept, and the " +
            "same text resolves on its own next time."
      }),
      nLearned ? el("button", {
        class: "btn btn-sm", type: "button",
        onclick: function () {
          S.clearLearned(ws.key);
          GD.toast("Remembered corrections cleared", { icon: "x" });
          GD.go("spend");
        }
      }, [icon("x", 12, 1.75), "Forget them"]) : null
    ]);

    return GD.card({
      title: "Load a spend file",
      sub: "Nothing is uploaded anywhere — the file is read in this browser and stays there",
      body: [el("div", { style: { display: "grid", gap: "16px" } }, [drop, memory])],
      foot: ws.has_spend_sample ? [
        el("span", { style: { color: "var(--ink-3)" } }, [
          el("b", { text: window.SPEND_SAMPLE ? window.SPEND_SAMPLE.name : "The sample" }),
          " is " + (window.SPEND_SAMPLE ? fmt.n(window.SPEND_SAMPLE.rows, 0) : "") +
          " lines of deliberately messy export: four spellings per vendor, free-text " +
          "material descriptions, four currencies, mixed date formats, credit notes, " +
          "duplicate postings and a dozen lines nothing can classify."
        ]),
        el("span", { style: { flex: "1", "min-width": "0" } }),
        el("span", { class: "nowrap", style: { color: "var(--ink-3)" },
          text: "Also on disk at app/data/spend_sample.csv / .xlsx" })
      ] : null
    });
  }

  /* ================================================================== the review queue */

  function reviewCard(run, redraw) {
    var ws = run.ws;
    var groups = S.reviewGroups(run);
    if (!groups.length) {
      return GD.card({
        title: "Review queue",
        sub: "Nothing is waiting on a person",
        body: [panel([
          el("div", { class: "flex items-center gap-8" }, [
            GD.dot("good"),
            el("b", { text: "Every line is resolved." }),
            el("span", {
              style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
              text: "Each one has a supplier, a material and a converted amount."
            })
          ])
        ], { background: "var(--accent-soft)" })]
      });
    }

    var learned = S.loadLearned(ws.key);

    function matSelect(selected) {
      return el("select", {
        class: "field", "aria-label": "Material",
        style: { "min-width": "190px" }
      }, [el("option", { value: "", text: "— pick a material —" })].concat(
        ws.materials.map(function (m) {
          return el("option", {
            value: m.id, selected: m.id === selected ? "selected" : null,
            text: m.name + " (" + (m.category === "packaging" ? "packaging" : "raw") + ")"
          });
        })
      ));
    }
    function supSelect(selected) {
      return el("select", {
        class: "field", "aria-label": "Supplier",
        style: { "min-width": "190px" }
      }, [el("option", { value: "", text: "— pick a supplier —" })].concat(
        ws.suppliers.map(function (s) {
          return el("option", {
            value: s.id, selected: s.id === selected ? "selected" : null, text: s.name
          });
        })
      ));
    }

    var rows = groups.map(function (g) {
      var l = g.sample;
      var controls = [];
      var supSel = null, matSel = null;

      if (g.asks.vendor) {
        /* Pre-fill with the closest candidate even when it was
           rejected: a review item that opens with the right answer already selected is one
           click, and the person still has to agree to it. */
        supSel = supSelect(l.supplier_id ||
          (l.supplierSuggestion && l.supplierSuggestion.id));
        controls.push(el("div", { style: { display: "grid", gap: "5px" } }, [
          labelled("Vendor on the file", { "margin-bottom": "2px" }),
          el("div", {
            class: "mono",
            style: {
              "font-size": "var(--t-sm)", background: "var(--surface-2)",
              border: "1px solid var(--border)", "border-radius": "var(--r-chip)",
              padding: "6px 9px", "overflow-wrap": "anywhere"
            },
            text: g.spellings.slice(0, 3).join("   ·   ") +
                  (g.spellings.length > 3 ? "   · +" + (g.spellings.length - 3) + " more" : "")
          }),
          el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
            supSel,
            el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: l.supplierWhy })
          ])
        ]));
      }

      if (g.asks.material) {
        matSel = matSelect(l.material_id ||
          (l.materialSuggestion && l.materialSuggestion.id));
        controls.push(el("div", { style: { display: "grid", gap: "5px" } }, [
          labelled("Material text on the file", { "margin-bottom": "2px" }),
          el("div", {
            class: "mono",
            style: {
              "font-size": "var(--t-sm)", background: "var(--surface-2)",
              border: "1px solid var(--border)", "border-radius": "var(--r-chip)",
              padding: "6px 9px", "overflow-wrap": "anywhere"
            },
            text: g.wordings.slice(0, 2).join("   ·   ") +
                  (g.wordings.length > 2 ? "   · +" + (g.wordings.length - 2) + " more" : "")
          }),
          el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
            matSel,
            el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: l.materialWhy })
          ])
        ]));
      }

      function confirm() {
        var choice = {};
        if (supSel) {
          if (!supSel.value) { GD.toast("Pick a supplier first", { icon: "alert" }); return; }
          choice.supplier_id = supSel.value;
        }
        if (matSel) {
          if (!matSel.value) { GD.toast("Pick a material first", { icon: "alert" }); return; }
          choice.material_id = matSel.value;
        }
        var n = S.resolve(run, l, choice, learned);
        S.saveLearned(ws.key, learned);
        GD.toast(n === 1 ? "1 line resolved" : n + " lines resolved in one go", { icon: "check" });
        redraw();
      }

      function notMaterial() {
        S.setUnclassified(run, l);
        var dKey = S.descTokens(l.descRaw).join(" ");
        if (dKey) learned.material[dKey] = null;
        GD.toast(g.count + " line" + (g.count === 1 ? "" : "s") + " marked unclassifiable",
          { icon: "x" });
        redraw();
      }

      return el("div", { class: "note-row", style: { "align-items": "flex-start" } }, [
        el("span", {
          style: {
            "min-width": "46px", height: "24px", padding: "0 7px", "border-radius": "7px",
            flex: "none", background: g.count > 10 ? "var(--warn-soft)" : "var(--surface-3)",
            color: g.count > 10 ? "var(--warn)" : "var(--ink-2)", display: "grid",
            "place-items": "center", "font-size": "var(--t-xs)", "font-weight": "600",
            "font-variant-numeric": "tabular-nums"
          },
          text: "×" + fmt.n(g.count, 0)
        }),
        el("div", { class: "nr-body", style: { display: "grid", gap: "11px" } }, [
          el("div", { class: "nr-head" }, [
            el("h4", {
              text: g.kind === "vendor" ? "Which supplier is this?"
                : g.kind === "material" ? "Which material is this?"
                : "Which supplier and which material?"
            }),
            GD.chip(money(g.amount, ws.home_currency), "outline"),
            el("span", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: g.count === 1 ? "one line" :
                    "one decision, " + fmt.n(g.count, 0) + " lines"
            })
          ]),
          el("div", { class: "grid g-2", style: { gap: "13px" } }, controls),
          el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
            el("button", { class: "btn btn-accent btn-sm", type: "button", onclick: confirm },
              [icon("check", 12, 2), "Confirm"]),
            g.asks.material ? el("button", {
              class: "btn btn-sm", type: "button", onclick: notMaterial
            }, [icon("x", 12, 1.75), "Not one of our materials"]) : null,
            el("span", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: "Kept for next time — the same text will resolve on its own."
            })
          ])
        ])
      ]);
    });

    return GD.card({
      title: "Review queue",
      sub: run.counts.decisions + " decision" + (run.counts.decisions === 1 ? "" : "s") +
           " covering " + fmt.n(run.counts.review, 0) + " line" +
           (run.counts.review === 1 ? "" : "s"),
      actions: [GD.chip(fmt.n(run.counts.review, 0) + " lines", "warn", "alert")],
      flush: true,
      body: [el("div", null, rows)],
      foot: [
        el("span", { style: { color: "var(--ink-3)" },
          text: "A line comes here when the match is under " +
                Math.round(S.THRESHOLD * 100) + "% confident, or when a field could not be " +
                "resolved at all. Lines that say the same thing are one decision." })
      ]
    });
  }

  /* ======================================================================= the report */

  function reportCards(run, rep) {
    var ws = run.ws;
    var cur = rep.currency;
    var out = [];

    /* --- the counter line, which is the whole pitch in one row */
    var counter = GD.card({
      title: "What the file became",
      sub: run.source + " · processed in " + msText(run.ms),
      actions: [
        rep.reconciles ? GD.chip("report ties to the lines", "good", "check")
                       : GD.chip("does not reconcile", "bad", "alert")
      ],
      flush: true,
      body: [
        el("div", { class: "statline" }, [
          statCell("Lines processed", fmt.n(run.counts.total, 0)),
          statCell("Auto-classified", fmt.n(run.counts.auto, 0),
            run.counts.auto === run.counts.total ? "val-down" : ""),
          statCell("Need review", fmt.n(run.counts.review, 0),
            run.counts.review ? "val-up" : "val-mute"),
          statCell("Duplicates flagged", fmt.n(run.counts.duplicates, 0),
            run.counts.duplicates ? "val-up" : "val-mute"),
          statCell("Total spend", GD.cur(rep.total, cur, 0))
        ]),
        el("div", { style: { padding: "14px 16px", "border-top": "1px solid var(--border)" } }, [
          el("div", { style: { display: "grid", gap: "9px" } },
            steps(run, rep).map(function (st, i) {
              return el("div", {
                class: "flex items-center gap-8",
                style: { "align-items": "flex-start", gap: "10px" }
              }, [
                el("span", {
                  style: {
                    width: "18px", height: "18px", "border-radius": "6px", flex: "none",
                    background: "var(--accent-soft)", color: "var(--accent-2)",
                    display: "grid", "place-items": "center", "margin-top": "1px"
                  }
                }, [icon("check", 11, 2.25)]),
                el("div", null, [
                  el("div", {
                    style: { "font-size": "var(--t-sm)", "font-weight": "600" },
                    text: st.name
                  }),
                  el("div", {
                    style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
                    text: st.detail
                  })
                ])
              ]);
            }))
        ])
      ],
      foot: [
        el("span", { style: { color: "var(--ink-3)" },
          text: "Five days of spreadsheet work is the baseline this replaces. The part that " +
                "needs a person is the " + run.counts.decisions + " decision" +
                (run.counts.decisions === 1 ? "" : "s") + " in the queue." }),
        el("span", { style: { flex: "1", "min-width": "0" } }),
        el("span", { class: "nowrap", style: { color: "var(--ink-3)" }, text: GD.fxNote() })
      ]
    });
    counter.style.setProperty("box-shadow", "var(--sh-md)");
    out.push(counter);

    /* The by-category and by-month cards that used to sit here are gone, and by-supplier
       with them. Every spend cube produces those; they are table stakes, and URC already has
       an initiative in flight for exactly that. All three are still in the exported report
       CSV for anybody who wants to pivot on them. What is left on screen is the figure a
       spend cube cannot produce, because it has no budget in it. */

    /* --- by material, with price paid against budget */
    var perUnitBy = {};
    rep.perUnit.forEach(function (p) { perUnitBy[p.material.id] = p; });

    var catBar = GD.compo(rep.byCategory.map(function (g) {
      return { name: g.label + " · " + GD.cur(g.amount, cur, 0), value: g.amount,
               color: catColor(g.key) };
    }).filter(function (p) { return p.value > 0; }), rep.total);
    catBar.style.setProperty("height", "10px");
    catBar.style.setProperty("width", "190px");

    out.push(GD.card({
      title: "By material",
      sub: "Spend, volume, and the price actually paid per unit against the budget rate",
      actions: [
        el("div", { style: { display: "grid", gap: "5px", "justify-items": "end" } }, [
          catBar,
          GD.legend(rep.byCategory.filter(function (g) { return g.amount > 0; })
            .map(function (g) {
              return { name: g.label + " " + fmt.pct(rep.total ? g.amount / rep.total : 0, 0),
                       color: catColor(g.key) };
            }))
        ])
      ],
      flush: true,
      body: [GD.table({
        cols: [
          {
            key: "label", label: "Material", tdClass: "strong", width: "170px",
            render: function (g) {
              var p = perUnitBy[g.key];
              return el("div", null, [
                el("div", { text: g.label }),
                el("div", {
                  style: { "font-size": "var(--t-xs)", color: "var(--ink-3)",
                           "font-weight": "400" },
                  text: p ? (p.material.category === "packaging" ? "Packaging" : "Raw material") +
                            " · per " + p.material.unit
                          : "not classified"
                })
              ]);
            }
          },
          {
            key: "amount", label: "Spend", num: true, tdClass: "strong", width: "120px",
            render: function (g) { return GD.cur(g.amount, cur, 0); }
          },
          {
            key: "share", label: "Share", num: true, width: "80px",
            sortValue: function (g) { return g.amount; },
            render: function (g) { return fmt.pct(rep.total ? g.amount / rep.total : 0, 1); }
          },
          {
            key: "qty", label: "Volume", num: true, width: "110px",
            render: function (g) {
              var p = perUnitBy[g.key];
              if (!p) return el("span", { class: "val-mute", text: "—" });
              return fmt.n(p.qty, p.material.unit === "kg" ? 0 : 1) + " " + p.material.unit;
            }
          },
          {
            key: "paid", label: "Paid / unit", num: true, width: "110px",
            sortValue: function (g) {
              var p = perUnitBy[g.key];
              return p ? p.paid : -1;
            },
            render: function (g) {
              var p = perUnitBy[g.key];
              if (!p) return el("span", { class: "val-mute", text: "—" });
              return GD.cur(p.paid, cur, p.paid >= 1000 ? 0 : 2);
            }
          },
          {
            key: "budget", label: "Budget", num: true, width: "110px",
            sortValue: function (g) {
              var p = perUnitBy[g.key];
              return p && p.budget !== null ? p.budget : -1;
            },
            render: function (g) {
              var p = perUnitBy[g.key];
              if (!p || p.budget === null || p.budget === undefined) {
                return el("span", { class: "val-mute", text: "none set" });
              }
              return GD.cur(p.budget, cur, p.budget >= 1000 ? 0 : 2);
            }
          },
          {
            key: "gap", label: "vs budget", num: true, width: "116px",
            sortValue: function (g) {
              var p = perUnitBy[g.key];
              return p && p.gapPct !== null ? p.gapPct : -9;
            },
            render: function (g) {
              var p = perUnitBy[g.key];
              if (!p || p.gapPct === null) return el("span", { class: "val-mute", text: "—" });
              var n = el("span", { class: GD.toneFor(p.gap) }, [
                el("span", { text: fmt.signedPct(p.gapPct, 1) })
              ]);
              GD.tipOn(n, "<b>Price paid against budget</b>" +
                "<div class='tip-row'><span class='tip-mute'>paid</span><b>" +
                GD.esc(GD.cur(p.paid, cur, 2)) + "</b></div>" +
                "<div class='tip-row'><span class='tip-mute'>budget</span><b>" +
                GD.esc(GD.cur(p.budget, cur, 2)) + "</b></div>" +
                "<div class='tip-row'><span class='tip-mute'>on " +
                GD.esc(fmt.n(p.qty, 1) + " " + p.material.unit) + "</span><b>" +
                GD.esc(money(p.gap * p.qty, cur)) + "</b></div>");
              return n;
            }
          },
          {
            key: "lines", label: "Lines", num: true, width: "78px",
            render: function (g) { return fmt.n(g.lines, 0); }
          }
        ],
        rows: rep.byMaterial,
        sortKey: "amount",
        sortDir: "desc",
        emptyText: "Nothing classified yet."
      })],
      foot: [
        el("span", null, [
          el("b", { text: "Paid / unit" }),
          " is spend divided by volume across every line of that material, so it is the " +
          "weighted average actually invoiced — not an offer price."
        ]),
        el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
        el("span", { style: { color: "var(--ink-3)" },
          text: "Lines with no usable quantity contribute to spend but not to volume." })
      ]
    }));

    /* --- reconciliation, stated rather than assumed */
    out.push(GD.card({
      title: "Does it tie?",
      sub: "Each grouping added back up against the cleaned line items",
      body: [
        el("div", { class: "statline", style: {
          border: "1px solid var(--border)", "border-radius": "var(--r-ctl)",
          overflow: "hidden", "margin-bottom": "13px"
        } }, rep.checks.map(function (c) {
          return el("div", null, [
            el("div", { class: "k", text: "Total " + c.name }),
            el("div", { class: "v" }, [
              el("span", { class: "flex items-center gap-8" }, [
                GD.dot(c.ok ? "good" : "bad"),
                el("span", { text: GD.cur(c.total, cur, 0) })
              ])
            ])
          ]);
        })),
        GD.kv([
          ["Lines included", fmt.n(rep.included, 0) + " of " + fmt.n(run.counts.total, 0)],
          ["Duplicates excluded", fmt.n(rep.duplicates, 0) + " lines, " +
            money(dupeValue(run), cur)],
          rep.dropped ? ["Lines with no usable amount", fmt.n(rep.dropped, 0)] : null,
          ["Grand total", el("b", { text: GD.cur(rep.total, cur, 2) })]
        ])
      ],
      foot: [
        el("span", { style: { color: "var(--ink-3)" },
          text: rep.reconciles
            ? "All four groupings equal the sum of the included line items to the centavo."
            : "A grouping does not match the line items. That is a bug, not a rounding " +
              "difference, and the report should not be used until it is fixed." })
      ]
    }));

    return out;
  }

  /* ------------------------------------------------------------------- line items */

  var LINE_CAP = 300;

  function linesCard(run, rep) {
    var ws = run.ws;
    var cur = ws.home_currency;
    var state = { view: "all", q: "" };
    var table, countNode;

    function pool() {
      return run.lines.filter(function (l) {
        if (state.view === "review") return l.needsReview && !l.duplicate;
        if (state.view === "dupes") return l.duplicate;
        if (state.view === "converted") return l.currency !== cur;
        return true;
      });
    }
    function filtered() {
      var needle = state.q.trim().toLowerCase();
      var rows = pool();
      if (needle) {
        rows = rows.filter(function (l) {
          return (l.vendorRaw + " " + l.descRaw + " " + (l.doc || "") + " " +
            (l.plantName || "")).toLowerCase().indexOf(needle) >= 0;
        });
      }
      return rows;
    }
    function shown() { return filtered().slice(0, LINE_CAP); }

    function sync() {
      var all = filtered().length;
      countNode.textContent = all > LINE_CAP
        ? "showing the first " + LINE_CAP + " of " + fmt.n(all, 0)
        : "showing " + fmt.n(all, 0) + " of " + fmt.n(run.lines.length, 0);
    }
    function apply() { table.update(shown()); sync(); }

    countNode = el("span", {
      "aria-live": "polite",
      style: {
        "font-size": "var(--t-xs)", color: "var(--ink-3)",
        "font-variant-numeric": "tabular-nums", "white-space": "nowrap"
      }
    });

    var viewSeg = GD.seg([
      { key: "all", label: "All " + run.lines.length },
      { key: "review", label: "Review " + run.counts.review },
      { key: "dupes", label: "Duplicates " + run.counts.duplicates },
      { key: "converted", label: "Converted" }
    ], "all", function (k) { state.view = k; apply(); });

    var textIn = el("input", {
      class: "field", type: "text", autocomplete: "off",
      placeholder: "Filter vendor, text, document or plant…",
      "aria-label": "Filter the line items",
      oninput: function () { state.q = textIn.value; apply(); }
    });
    textIn.style.setProperty("flex", "1 1 200px");
    textIn.style.setProperty("min-width", "160px");

    table = GD.table({
      cols: [
        { key: "date", label: "Posted", width: "92px",
          render: function (l) {
            return l.date ? fmt.dateShort(l.date) + " " + l.date.slice(2, 4)
                          : el("span", { class: "val-mute", text: "unreadable" });
          } },
        { key: "doc", label: "Document", width: "96px",
          render: function (l) { return el("span", { class: "mono", text: l.doc || "—" }); } },
        { key: "vendorRaw", label: "Vendor on the file", width: "190px",
          render: function (l) {
            return el("div", null, [
              el("div", { text: l.vendorRaw || "—" }),
              el("div", {
                style: { "font-size": "var(--t-xs)", color: l.supplier_id
                  ? "var(--accent-2)" : "var(--up)" },
                text: l.supplier_id ? "→ " + GD.supplierName(l.supplier_id, ws)
                                    : "no supplier matched"
              })
            ]);
          } },
        { key: "descRaw", label: "Material on the file", width: "200px",
          render: function (l) {
            return el("div", null, [
              el("div", { text: l.descRaw || "—" }),
              el("div", {
                style: { "font-size": "var(--t-xs)", color: l.material_id
                  ? "var(--accent-2)" : "var(--ink-3)" },
                text: l.material_id ? "→ " + GD.materialName(l.material_id, ws)
                                    : "unclassified"
              })
            ]);
          } },
        { key: "plantName", label: "Plant", width: "110px",
          render: function (l) { return l.plantName || l.plant || "—"; } },
        { key: "qty", label: "Qty", num: true, width: "92px",
          render: function (l) {
            if (l.qty === null) return el("span", { class: "val-mute", text: "—" });
            return fmt.n(l.qty, l.qty % 1 ? 2 : 0) + " " + (l.uom || "");
          } },
        { key: "amount", label: "Amount", num: true, width: "118px",
          render: function (l) {
            if (l.amount === null) return el("span", { class: "val-mute", text: "unreadable" });
            return GD.cur(l.amount, l.currency, 2);
          } },
        { key: "amountHome", label: "In " + cur, num: true, tdClass: "strong", width: "120px",
          render: function (l) {
            if (l.amountHome === null) return el("span", { class: "val-mute", text: "—" });
            var n = el("span", { text: GD.cur(l.amountHome, cur, 0) });
            if (l.currency !== cur) {
              GD.tipOn(n, "<b>" + GD.esc(GD.cur(l.amount, l.currency, 2)) + " × " +
                GD.esc(fmt.n(l.rate, 4)) + "</b><div class='tip-mute'>" +
                GD.esc(l.currency + "/" + cur + ", indicative mid") + "</div>");
            }
            return n;
          } },
        { key: "state", label: "State", width: "128px",
          sortValue: function (l) {
            return l.duplicate ? 0 : l.needsReview ? 1 : l.resolved ? 2 : 3;
          },
          render: function (l) {
            if (l.duplicate) {
              var c = GD.chip("duplicate", "bad", "x");
              GD.tipOn(c, GD.esc(l.duplicateWhy || ""));
              return c;
            }
            if (l.needsReview) {
              var r = GD.chip("review", "warn", "alert");
              GD.tipOn(r, GD.esc(l.reviewWhy || ""));
              return r;
            }
            if (l.resolved) return GD.chip("confirmed", "accent", "check");
            var a = GD.chip(Math.round(l.confidence * 100) + "%", "good", "check");
            GD.tipOn(a, "<b>Classified automatically</b><div class='tip-mute'>" +
              GD.esc(l.supplierWhy) + "</div><div class='tip-mute'>" +
              GD.esc(l.materialWhy) + "</div>");
            return a;
          } }
      ],
      rows: shown(),
      sortKey: "date",
      sortDir: "desc",
      emptyText: "No line matches this filter."
    });

    sync();

    return GD.card({
      title: "Cleaned line items",
      sub: "Every line, what it was, and what it became",
      actions: [
        el("button", {
          class: "btn btn-sm", type: "button",
          onclick: function () { exportLines(run); }
        }, [icon("download", 12, 1.75), "Download cleaned CSV"])
      ],
      flush: true,
      body: [
        el("div", {
          style: {
            padding: "13px 16px", "border-bottom": "1px solid var(--border)",
            display: "flex", "align-items": "center", gap: "10px", "flex-wrap": "wrap"
          }
        }, [viewSeg, textIn, countNode]),
        table
      ],
      foot: [
        el("span", { style: { color: "var(--ink-3)" },
          text: "At most " + LINE_CAP + " rows are drawn at a time — the report above is " +
                "built from all " + fmt.n(run.lines.length, 0) + "." })
      ]
    });
  }

  function exportLines(run) {
    var ws = run.ws, cur = ws.home_currency;
    var rows = [[
      "posting_date", "document_no", "vendor_on_file", "supplier", "material_on_file",
      "material", "category", "plant", "plant_name", "quantity", "uom",
      "quantity_in_material_unit", "amount", "currency", "fx_to_" + cur, "amount_" + cur,
      "purchase_order", "state", "confidence", "note"
    ]];
    run.lines.forEach(function (l) {
      rows.push([
        l.date, l.doc, l.vendorRaw,
        l.supplier_id ? GD.supplierName(l.supplier_id, ws) : "",
        l.descRaw,
        l.material_id ? GD.materialName(l.material_id, ws) : "",
        l.category || "", l.plant, l.plantName,
        l.qty, l.uom, l.qtyUnit === null ? "" : l.qtyUnit,
        l.amount, l.currency, l.rate, l.amountHome, l.po,
        l.duplicate ? "duplicate" : l.needsReview ? "review" : l.resolved ? "confirmed" : "auto",
        l.confidence === null ? "" : Math.round(l.confidence * 100) / 100,
        l.duplicate ? (l.duplicateWhy || "") : l.needsReview ? (l.reviewWhy || "")
          : (l.supplierWhy + "; " + l.materialWhy)
      ]);
    });
    GD.downloadCsv("spend_cleaned_" + GD.wsAsOf(), rows);
  }

  function exportReport(run, rep) {
    var cur = rep.currency;
    var rows = [["Spend report", run.ws.name, "as of " + GD.wsAsOf()], []];
    function block(title, groups, extra) {
      rows.push([title]);
      rows.push(["key", "label", "amount_" + cur, "share", "lines"].concat(extra || []));
      groups.forEach(function (g) {
        rows.push([g.key, g.label, g.amount,
          rep.total ? Math.round(g.amount / rep.total * 10000) / 10000 : 0, g.lines]);
      });
      rows.push([]);
    }
    block("By category", rep.byCategory);
    block("By material", rep.byMaterial);
    block("By supplier", rep.bySupplier);
    block("By month", rep.byMonth);
    rows.push(["Price paid per unit against budget"]);
    rows.push(["material", "unit", "volume", "spend_" + cur, "paid_per_unit", "budget",
               "gap", "gap_pct", "lines"]);
    rep.perUnit.forEach(function (p) {
      rows.push([p.material.name, p.material.unit, p.qty, p.amount, p.paid,
        p.budget === null ? "" : p.budget, p.gap === null ? "" : p.gap,
        p.gapPct === null ? "" : Math.round(p.gapPct * 10000) / 10000, p.lines]);
    });
    rows.push([]);
    rows.push(["Reconciliation"]);
    rep.checks.forEach(function (c) { rows.push([c.name, c.total, c.ok ? "ok" : "MISMATCH"]); });
    rows.push(["grand total", rep.total]);
    rows.push(["lines included", rep.included]);
    rows.push(["duplicates excluded", rep.duplicates]);
    GD.downloadCsv("spend_report_" + GD.wsAsOf(), rows);
  }

  /* ========================================================================= method */

  function showMethod(ws) {
    GD.sheet({
      title: "What it does to a file",
      sub: "Five passes, none of which knows anything about any particular material",
      body: function () {
        var rows = [
          ["Read", "The delimiter is sniffed from the header line and the columns are found " +
            "by matching the header against the names SAP gives them — posting date, " +
            "belegdatum, document date, and so on. A renamed column does not take the " +
            "upload down; a missing required one is reported."],
          ["Vendor", "Names are lower-cased, punctuation becomes spaces, initialisms are " +
            "pushed back together (A.B.C. → abc) and legal suffixes dropped (Inc, Corp, " +
            "B.V., Sdn Bhd). An exact match on the supplier master wins. Otherwise the " +
            "tokens are compared, with prefixes (Ind → Industries) and vowel-free " +
            "abbreviations (Pkg → Packaging) counting as hits. Two suppliers that both " +
            "look right is a question for a person, not a coin toss."],
          ["Material", "A word that appears in exactly one material's name is that " +
            "material's own word — \"corrugated\", \"resin\", \"olein\". Finding one is " +
            "strong evidence even when the rest of the name is absent, which is why " +
            "\"Corrugated box - RSC\" classifies and \"MISC CONSUMABLES\" does not. " +
            "Failing that, plain token overlap, nudged by what that supplier has sold us " +
            "before."],
          ["Currency", "Every line is converted to " + ws.home_currency + " at the rate in " +
            "the workspace's table, and the rate used is on each line. The table is " +
            "indicative mids for this build, not a live feed."],
          ["Duplicates", "The same document number twice, or the same vendor, text, date, " +
            "quantity and amount under two numbers. The first stands; the rest are flagged " +
            "and left out of every total."]
        ];
        return [
          el("div", {
            style: {
              border: "1px solid var(--border)", "border-radius": "var(--r-card)",
              overflow: "hidden", "margin-bottom": "18px"
            }
          }, rows.map(function (r, i) {
            return el("div", { class: "note-row" }, [
              el("span", {
                style: {
                  width: "22px", height: "22px", "border-radius": "7px", flex: "none",
                  background: "var(--surface-3)", color: "var(--ink-2)", display: "grid",
                  "place-items": "center", "font-size": "var(--t-xs)", "font-weight": "600"
                },
                text: String(i + 1)
              }),
              el("div", { class: "nr-body" }, [
                el("div", { class: "nr-head" }, [el("h4", { text: r[0] })]),
                el("p", { text: r[1] })
              ])
            ]);
          })),
          panel([
            el("div", { class: "card-title", style: { "margin-bottom": "9px" },
              text: "What a person is for" }),
            el("p", { style: { "font-size": "var(--t-sm)", color: "var(--ink-2)" },
              text: "Anything under " + Math.round(S.THRESHOLD * 100) + "% confident, and " +
                    "anything that could not be resolved at all. The threshold is set where " +
                    "it is because a wrong auto-classification is far more expensive than a " +
                    "review click: it lands silently in a report somebody then negotiates " +
                    "against. Decisions are remembered per workspace in this browser, so " +
                    "the same text never asks twice." })
          ])
        ];
      },
      foot: function (close) {
        return [
          el("button", { class: "btn btn-primary", type: "button", "data-autofocus": true,
            onclick: close, text: "Got it" }),
          el("span", { style: { flex: "1" } }),
          el("span", { class: "card-sub", text: "Nothing in here is specific to this file." })
        ];
      }
    });
  }

  /* ======================================================================= the surface */

  GD.route("spend", {
    label: "Spend",
    icon: "dollar",
    group: "Desk",
    ws: ["urc"],
    badge: function () {
      if (!GD.spendRun) return null;
      return GD.spendRun.counts.review;
    },
    badgeTone: "warn",

    render: function (wrap) {
      var ws = GD.ws();

      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Spend cleansing" }),
          el("div", {
            class: "page-sub",
            text: "An S/4HANA export, cleaned in the browser: vendor names resolved, " +
                  "free-text materials classified, currencies converted, duplicates flagged " +
                  "— then a report that ties to the line items"
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.demoChip(),
          GD.chip("as of " + fmt.date(GD.wsAsOf()), "outline", "calendar"),
          el("button", {
            class: "btn", type: "button", onclick: function () { showMethod(ws); }
          }, [icon("layers", 14, 1.75), "What it does to a file"])
        ])
      ]));

      function redraw() { GD.go("spend"); }

      if (!GD.spendRun || GD.spendRun.ws.key !== ws.key) {
        GD.spendRun = null;
        GD.spendReport = null;
        wrap.appendChild(loader(ws, function (r) {
          GD.spendRun = r;
          GD.spendReport = S.report(r);
          redraw();
        }));
        return;
      }

      var run = GD.spendRun;
      var rep = S.report(run);
      GD.spendReport = rep;

      /* The file in hand, and the way back to another one. */
      wrap.appendChild(GD.card({
        body: [el("div", {
          style: { display: "flex", "align-items": "center", gap: "11px", "flex-wrap": "wrap" }
        }, [
          el("span", { style: { color: "var(--ink-3)", flex: "none" } }, [icon("file", 15, 1.75)]),
          el("div", { style: { flex: "1", "min-width": "180px" } }, [
            el("div", { style: { "font-weight": "600" }, text: run.source }),
            el("div", {
              style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
              text: fmt.n(run.counts.total, 0) + " lines · " + run.columns.header.length +
                    " columns · read in this browser, never uploaded"
            })
          ]),
          el("button", {
            class: "btn btn-sm", type: "button",
            onclick: function () { exportReport(run, rep); }
          }, [icon("download", 12, 1.75), "Report CSV"]),
          el("button", {
            class: "btn btn-sm", type: "button",
            onclick: function () {
              GD.spendRun = null; GD.spendReport = null; redraw();
            }
          }, [icon("x", 12, 1.75), "Load another file"])
        ])]
      }));

      /* The counter first, then whatever is waiting on a person, then the report. Built
         once: the review queue sits between the first card and the rest. */
      var cards = reportCards(run, rep);
      wrap.appendChild(cards[0]);
      wrap.appendChild(reviewCard(run, redraw));
      cards.slice(1).forEach(function (c) { wrap.appendChild(c); });
      wrap.appendChild(linesCard(run, rep));
    }
  });

})();
