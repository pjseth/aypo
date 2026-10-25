/* Assertions for the pure modules — pricing.js, spendlib.js and the number check in llm.js.

   There is no Node on this machine, so these run in headless Edge through tools/test_lib.py.
   Everything here is a property of the code, not of the seed: where a planted story is
   asserted it is asserted through the general function, so special-casing the demo data to
   make a test pass would not work. */
(function () {
  "use strict";

  var results = [];
  function ok(name, cond, detail) {
    results.push({ name: name, pass: !!cond, detail: detail === undefined ? "" : String(detail) });
  }
  function close(a, b, tol) {
    return Math.abs(a - b) <= (tol === undefined ? 0.011 : tol);
  }

  var P = GD.pricing, S = GD.spendlib;
  var urc = null, ufm = null;
  GD.wsAll().forEach(function (w) {
    if (w.key === "urc") urc = w;
    if (w.key === "ufm") ufm = w;
  });

  ok("the workspaces loaded", !!urc && !!ufm);
  ok("the wheat desk is the default workspace", GD.wsAll()[0].key === "ufm",
     GD.wsAll().map(function (w) { return w.key; }).join(","));
  ok("the demo workspace is labelled as demo data", urc.demo === true && ufm.demo === false);
  ok("every material declares a category, a unit and a currency",
     urc.materials.concat(ufm.materials).every(function (m) {
       return (m.category === "raw" || m.category === "packaging") && m.unit && m.currency;
     }));
  ok("every material declares at least one price driver",
     urc.materials.concat(ufm.materials).every(function (m) {
       return (m.drivers || []).length > 0;
     }));
  ok("every driver declares its source, unit and conversion",
     urc.materials.concat(ufm.materials).every(function (m) {
       return m.drivers.every(function (d) {
         return d.source && d.unit && d.currency &&
           typeof d.per_unit === "number" && typeof d.offset === "number" &&
           d.dates.length === d.values.length && d.dates.length > 0;
       });
     }));
  ok("every quote names a material and a supplier that exist",
     [urc, ufm].every(function (ws) {
       return ws.quotes.every(function (q) {
         return !!GD.material(q.material_id, ws) && !!GD.supplier(q.supplier_id, ws);
       });
     }));
  ok("every material has between two and four suppliers quoting it",
     urc.materials.every(function (m) {
       var n = {};
       urc.quotes.forEach(function (q) { if (q.material_id === m.id) n[q.supplier_id] = 1; });
       var c = Object.keys(n).length;
       return c >= 2 && c <= 4;
     }),
     urc.materials.map(function (m) {
       var n = {};
       urc.quotes.forEach(function (q) { if (q.material_id === m.id) n[q.supplier_id] = 1; });
       return m.id + ":" + Object.keys(n).length;
     }).join(" "));
  ok("the demo carries twelve months of quotes",
     (function () {
       var months = {};
       urc.quotes.forEach(function (q) { months[q.date.slice(0, 7)] = 1; });
       return Object.keys(months).length === 12;
     })());
  ok("every fx pair a material needs is in the table",
     urc.materials.concat(ufm.materials).every(function (m) {
       return m.drivers.every(function (d) { return GD.fx(d.currency, m.currency) !== null; });
     }));
  ok("the home currency is PHP in the demo workspace", urc.home_currency === "PHP");

  /* ============================================================== pricing: primitives */

  ok("percentile p25 of [1,2,3,4]", close(P.percentile([1, 2, 3, 4], 0.25), 1.75, 1e-9),
     P.percentile([1, 2, 3, 4], 0.25));
  ok("percentile p75 of [1,2,3,4]", close(P.percentile([1, 2, 3, 4], 0.75), 3.25, 1e-9),
     P.percentile([1, 2, 3, 4], 0.75));
  ok("percentile p50 of [10,20,30]", close(P.percentile([10, 20, 30], 0.5), 20, 1e-9));
  ok("percentile of one value", P.percentile([7], 0.25) === 7);
  ok("percentile of nothing is null", P.percentile([], 0.5) === null);

  /* 1.125 and 2.375 are exactly representable, so this tests the rounding rule rather than
     the float literal. Half-up, not banker's — the seed in tools/build_ws.py rounds the same
     way, and a disagreement there would put every quote a cent out. */
  ok("round2 rounds a half away from zero",
     P.round2(1.125) === 1.13 && P.round2(2.375) === 2.38 && P.round2(-1.125) === -1.13,
     P.round2(1.125) + " / " + P.round2(2.375) + " / " + P.round2(-1.125));
  ok("monthsBefore crosses the year", P.monthsBefore("2026-02-14", 12) === "2025-02-14",
     P.monthsBefore("2026-02-14", 12));

  /* series lookup takes the print in force, not the next one */
  (function () {
    var d = { dates: ["2026-01-01", "2026-02-01", "2026-03-01"], values: [10, 20, 30] };
    ok("valueAt picks the print in force", P.valueAt(d, "2026-02-17") === 20);
    ok("valueAt before the series starts holds the first print",
       P.valueAt(d, "2025-12-31") === 10);
    ok("valueAt after the series ends holds the last print",
       P.valueAt(d, "2030-01-01") === 30);
  })();

  /* ================================================ pricing: the components must sum */

  (function () {
    var bad = [], worst = 0;
    [urc, ufm].forEach(function (ws) {
      ws.quotes.forEach(function (q) {
        var m = GD.material(q.material_id, ws);
        if (!m) { bad.push(q.id + " has no material"); return; }
        var bd = P.breakdown(m, q, function (f, t) { return GD.fx(f, t); });
        var err = Math.abs(bd.total - q.price);
        if (err > worst) worst = err;
        if (!bd.reconciles) bad.push(q.id + " out by " + err.toFixed(4));
      });
    });
    ok("every quote's components sum to its price (" +
       (urc.quotes.length + ufm.quotes.length) + " quotes)",
       bad.length === 0, bad.slice(0, 3).join("; ") + " worst " + worst.toFixed(6));
  })();

  /* A hand-worked leg, so the arithmetic is pinned and not just self-consistent. */
  (function () {
    var m = {
      id: "t", name: "Test", category: "raw", unit: "MT", currency: "PHP",
      budget_price: 1000,
      drivers: [
        { key: "A", name: "Index A", source: "x", unit: "USD/bbl", currency: "USD",
          per_unit: 7.4, offset: 395, kind: "index",
          dates: ["2026-01-01"], values: [71.4] },
        { key: "B", name: "Freight B", source: "y", unit: "USD/MT", currency: "USD",
          per_unit: 1, offset: 2, kind: "freight",
          dates: ["2026-01-01"], values: [46] }
      ]
    };
    var fx = function (f, t) { return (f === "USD" && t === "PHP") ? 58.2 : (f === t ? 1 : null); };
    var legs = P.legs(m, "2026-02-01", fx);
    /* 71.4 * 7.4 + 395 = 923.36 ; * 58.2 = 53,739.55 */
    ok("leg A arithmetic", close(legs[0].value, 53739.55), legs[0].value);
    /* 46 + 2 = 48 ; * 58.2 = 2,793.60 */
    ok("leg B arithmetic", close(legs[1].value, 2793.6), legs[1].value);
    var cost = P.driverCost(m, "2026-02-01", fx);
    ok("driver cost is the sum of the legs", close(cost, 56533.15), cost);
    var bd = P.breakdown(m, { price: 59156.00, date: "2026-02-01" }, fx);
    ok("premium is quote minus driver cost", close(bd.premium, 2622.85), bd.premium);
    ok("hand-worked breakdown reconciles", bd.reconciles);
    ok("fx summary groups the foreign legs",
       bd.fx.length === 1 && bd.fx[0].currency === "USD" && close(bd.fx[0].base, 971.36),
       JSON.stringify(bd.fx));
  })();

  /* ============================================================== pricing: the band */

  (function () {
    var m = { id: "t", unit: "MT", currency: "USD", budget_price: null,
              drivers: [{ key: "A", name: "A", source: "s", unit: "USD/MT", currency: "USD",
                          per_unit: 1, offset: 0, kind: "index",
                          dates: ["2026-01-01"], values: [100] }] };
    var fx = function (f, t) { return f === t ? 1 : null; };
    var qs = [10, 20, 30, 40].map(function (p, i) {
      return { id: "q" + i, material_id: "t", supplier_id: "s" + i,
               date: "2026-0" + (i + 2) + "-01", price: 100 + p };
    });
    /* GD.fx is what P.band reaches for internally; swap it for the duration. */
    var realFx = GD.fx;
    GD.fx = fx;
    var band = P.band(m, qs, "2026-06-01", "2026-06-01");
    ok("band is established at four observations", band.established && band.n === 4, band.n);
    ok("band low is cost + p25", close(band.low, 117.5), band.low);
    ok("band high is cost + p75", close(band.high, 132.5), band.high);
    ok("a quote under the band reads below", P.position(110, band) === "below");
    ok("a quote in the band reads inside", P.position(125, band) === "inside");
    ok("a quote over the band reads above", P.position(140, band) === "above");
    ok("how far outside is measured from the edge", close(P.outside(140, band), 7.5),
       P.outside(140, band));
    ok("inside the band is zero outside", P.outside(125, band) === 0);

    var thin = P.band(m, qs.slice(0, 3), "2026-06-01", "2026-06-01");
    ok("three observations is not a band", !thin.established && thin.n === 3);
    ok("an unestablished band has no verdict", P.position(125, thin) === "unknown");
    ok("an unestablished band reports no edge", P.outside(125, thin) === null);
    GD.fx = realFx;
  })();

  /* ======================================================== pricing: wheat is unchanged

     The generic premium on a wheat quote has to be the number the Quote Board already shows,
     or the two screens would be telling the mill different things about the same email. */

  (function () {
    var bad = [];
    window.DESK.quotes.forEach(function (dq) {
      var q = null;
      ufm.quotes.forEach(function (x) { if (x.id === dq.id) q = x; });
      if (!q) { bad.push(dq.id + " missing"); return; }
      var m = GD.material(q.material_id, ufm);
      var bd = P.breakdown(m, q);
      if (!close(bd.premium, dq.residual_usd_mt)) {
        bad.push(dq.id + ": " + bd.premium + " vs " + dq.residual_usd_mt);
      }
      if (!close(bd.driverCost, dq.futures_usd_mt + dq.freight_usd_mt)) {
        bad.push(dq.id + " driver cost mismatch");
      }
    });
    ok("every wheat premium equals the Quote Board's residual (" +
       window.DESK.quotes.length + " quotes)", bad.length === 0, bad.slice(0, 3).join("; "));
  })();

  (function () {
    var m = GD.material(ufm.materials[0].id, ufm);
    ok("a material with no budget reports no gap", P.budgetGap(m, 400) === null);
    var urcM = GD.material("pet_resin", urc);
    var g = P.budgetGap(urcM, urcM.budget_price * 1.1);
    ok("budget gap is amount and percent", g && close(g.pct, 0.1, 1e-6) && g.over, g && g.pct);
  })();

  /* ===================================================== pricing: the planted stories

     Asserted through the same functions the screen calls. If the seed drifts, these fail. */

  (function () {
    var coffee = GD.material("green_coffee", urc);
    var latest = P.latestPerSupplier("green_coffee", urc.quotes).filter(function (q) {
      return q.supplier_id === "kalinga";
    })[0];
    ok("the planted coffee quote exists", !!latest);
    if (latest) {
      var a = P.assess(coffee, latest, urc.quotes, GD.wsAsOf());
      ok("the planted coffee quote sits above the band", a.position === "above",
         latest.price + " vs high " + (a.band && a.band.high));
      ok("the planted coffee quote is over budget", a.budget && a.budget.over,
         a.budget && a.budget.amount);
      var t = P.premiumTrend(coffee, urc.quotes, "kalinga", GD.wsAsOf());
      ok("the planted coffee premium widened three quarters running", t.risingRun >= 3,
         "run " + t.risingRun + " over " + t.quarters.length + " quarters");
      ok("premiumTrend reports the widening as money", t.change > 0, t.change);
    }
    var ps = P.premiumSeries(coffee, urc.quotes, "kalinga", GD.wsAsOf());
    ok("premium history has twelve months", ps.months.length === 12, ps.months.length);
    ok("premium history carries both series",
       ps.mine.length === ps.months.length && ps.median.length === ps.months.length);
  })();

  (function () {
    var resin = GD.material("pet_resin", urc);
    var latest = P.latestPerSupplier("pet_resin", urc.quotes).filter(function (q) {
      return q.supplier_id === "pacpoly";
    })[0];
    ok("the planted resin quote exists", !!latest);
    if (latest) {
      var a = P.assess(resin, latest, urc.quotes, GD.wsAsOf());
      ok("the planted resin quote sits inside the band", a.position === "inside",
         latest.price + " in " + a.band.low + ".." + a.band.high);
      ok("the planted resin quote is under budget", a.budget && !a.budget.over,
         a.budget && a.budget.amount);
    }
  })();

  /* ============================================================== spendlib: primitives */

  ok("number with separators", S.num("1,234.56") === 1234.56);
  ok("number in parentheses is negative", S.num("(1,234.56)") === -1234.56);
  ok("negative number", S.num("-99") === -99);
  ok("not a number is null", S.num("see attachment") === null);
  ok("iso date", S.date("2026-03-21") === "2026-03-21");
  ok("german date", S.date("10.03.2026") === "2026-03-10");
  ok("slashed date", S.date("3/4/2026") === "2026-04-03");
  ok("unparseable date is null", S.date("Q3") === null);
  ok("uom aliases fold", S.uom("TO") === "MT" && S.uom("T") === "MT" && S.uom("kgs") === "kg");
  ok("kg converts to MT", S.toUnit(3350, "kg", "MT") === 3.35);

  ok("vendor normalisation drops legal suffixes",
     S.normalizeVendor("ABC Trading Corp.") === S.normalizeVendor("A.B.C. TRADING") &&
     S.normalizeVendor("abc trading corporation") === S.normalizeVendor("ABC Trading Corp."),
     S.normalizeVendor("A.B.C. TRADING"));

  ok("prefix tokens match", S.similarity(["corrugated", "cartons"], ["corr", "carton"]) > 0.8,
     S.similarity(["corrugated", "cartons"], ["corr", "carton"]));
  ok("vowel-free abbreviations match",
     S.skeleton("pkg", "packaging") && S.skeleton("trdg", "trading") &&
     !S.skeleton("pet", "polyethylene") && !S.skeleton("cat", "carton"));
  ok("a vendor spelling the master has never seen still resolves",
     S.matchSupplier("VISAYAS PKG MILLS", S.index(urc)).id === "visayas",
     JSON.stringify(S.matchSupplier("VISAYAS PKG MILLS", S.index(urc))));
  ok("a genuinely ambiguous abbreviation is left for a person",
     S.matchSupplier("B.P. Commodities Corp.", S.index(urc)).id === null,
     JSON.stringify(S.matchSupplier("B.P. Commodities Corp.", S.index(urc))));

  /* ============================================================ spendlib: the whole run */

  var parsed = S.parseDelimited(window.SPEND_SAMPLE.text);
  ok("sample parses to the row count the builder wrote",
     parsed.rows.length === window.SPEND_SAMPLE.rows,
     parsed.rows.length + " vs " + window.SPEND_SAMPLE.rows);
  var cols = S.mapColumns(parsed.header);
  ok("every required column is found", cols.missing.length === 0, cols.missing.join(","));

  var learned = { vendor: {}, material: {} };
  var run = S.process({ ws: urc, parsed: parsed, learned: learned });
  ok("every row became a line", run.lines.length === parsed.rows.length);

  var autoPct = run.counts.auto / run.counts.total;
  var groups = S.reviewGroups(run);
  ok("most lines classify without a human (" + Math.round(autoPct * 100) + "%)",
     autoPct > 0.9,
     run.counts.auto + " of " + run.counts.total + " · queue: " +
     groups.slice(0, 6).map(function (g) {
       return g.count + "x " + g.sample.vendorRaw + " / " + g.sample.descRaw +
              " [" + g.sample.reviewWhy + "]";
     }).join("  |  "));
  ok("the queue collapses to a handful of decisions",
     run.counts.decisions > 0 && run.counts.decisions <= 12,
     run.counts.decisions + " decisions over " + run.counts.review + " lines");
  ok("duplicates are found", run.counts.duplicates >= 6, run.counts.duplicates);
  ok("something is left for review", run.counts.review > 0, run.counts.review);
  ok("the counters add up",
     run.counts.auto + run.counts.review + run.counts.duplicates === run.counts.total,
     JSON.stringify(run.counts));

  (function () {
    var junk = run.lines.filter(function (l) {
      return /MISC CONSUMABLES|ROUNDING DIFFERENCE|GR\/IR|SUNDRY|ACCRUAL|RECLASS|PALLET DEPOSIT|THIRD PARTY/
        .test(l.descRaw);
    });
    ok("unclassifiable lines are not guessed at",
       junk.length > 0 && junk.every(function (l) { return l.material_id === null; }),
       junk.length + " junk lines, " +
       junk.filter(function (l) { return l.material_id; }).length + " wrongly classified");
  })();

  (function () {
    var dups = run.lines.filter(function (l) { return l.duplicate; });
    var originals = {};
    run.lines.forEach(function (l) { if (!l.duplicate) originals[l.i] = true; });
    ok("every duplicate points at a line that was kept",
       dups.every(function (l) { return originals[l.duplicateOf]; }), dups.length);
  })();

  /* --------------------------------------------------------------- the reconciliation */

  var rep = S.report(run);
  ok("the report reconciles across all four groupings", rep.reconciles,
     rep.checks.map(function (c) { return c.name + " " + c.total; }).join(" | ") +
     " vs total " + rep.total);

  (function () {
    var manual = 0;
    run.lines.forEach(function (l) {
      if (l.duplicate || l.amountHome === null) return;
      manual += l.amountHome;
    });
    manual = Math.round(manual * 100) / 100;
    ok("the grand total is the sum of the non-duplicate lines", close(rep.total, manual),
       rep.total + " vs " + manual);
  })();

  ok("an Unclassified bucket exists rather than lines being dropped",
     rep.byCategory.some(function (g) { return g.key === "unclassified"; }),
     rep.byCategory.map(function (g) { return g.key; }).join(","));

  ok("price paid per unit is reported against budget",
     rep.perUnit.length === urc.materials.length &&
     rep.perUnit.every(function (p) { return p.budget !== null && p.gap !== null; }),
     rep.perUnit.length);

  (function () {
    var credits = run.lines.filter(function (l) {
      return !l.duplicate && l.amountHome !== null && l.amountHome < 0;
    });
    ok("credit notes net off rather than being dropped", credits.length > 0, credits.length);
  })();

  /* ------------------------------------------------- corrections are learned and reused */

  (function () {
    var target = null;
    run.lines.forEach(function (l) {
      if (target) return;
      if (l.needsReview && !l.duplicate && l.supplier_id && l.descRaw) target = l;
    });
    if (!target) { ok("a line was available to correct", false); return; }
    var pick = urc.materials[0].id;
    var touched = S.resolve(run, target, { material_id: pick, supplier_id: target.supplier_id },
                            learned);
    ok("resolving one line resolves every line that said the same thing", touched >= 1, touched);
    ok("the resolved line no longer needs review", !target.needsReview);
    ok("the correction was remembered", S.countLearned(learned) > 0, S.countLearned(learned));

    var again = S.process({ ws: urc, parsed: parsed, learned: learned });
    var same = null;
    again.lines.forEach(function (l) { if (l.i === target.i) same = l; });
    ok("on re-upload the same text resolves without being asked",
       same && same.material_id === pick && !same.needsReview,
       same && (same.material_id + " review=" + same.needsReview));
    ok("re-upload leaves fewer lines in the queue", again.counts.review <= run.counts.review,
       again.counts.review + " vs " + run.counts.review);
    ok("the re-run still reconciles", S.report(again).reconciles);
  })();

  /* ======================================================= llm: the number check bites */

  (function () {
    var facts = { band_high: 262682, quote: 269405, budget: 262000, supplier: "Kalinga" };
    var good = "The quote of 269,405 sits above the band top of 262,682 and the budget of 262,000.";
    var bad = "The quote of 269,405 sits above the band top of 262,682, and the market is " +
              "expected to fall 14% by March.";
    ok("a grounded draft passes the number check", GD.llm.verifyNumbers(good, facts).ok);
    var v = GD.llm.verifyNumbers(bad, facts);
    ok("an invented figure is caught", !v.ok && v.unverified.indexOf("14") >= 0,
       JSON.stringify(v.unverified));
    ok("rounding a fact is allowed",
       GD.llm.verifyNumbers("about 269,400 against 262,700", facts).ok);
    ok("with no key, draft falls back to the template", !GD.llm.available() ||
       GD.llm.keySource() !== null);
  })();

  /* ------------------------------------------------- the model path, with a stub transport

     There is no API key in a test run and there should not be, but the wiring around the call
     is the part that matters: what happens when the model answers well, when it answers with
     a figure nobody gave it, and when the call fails outright. A stubbed fetch exercises all
     three without spending anything or depending on a network. */

  var realFetch = window.fetch;
  var realKey = GD.llm.key;

  function stub(reply) {
    GD.llm.key = function () { return "sk-ant-test"; };
    window.fetch = function (url, opts) {
      stub.lastUrl = url;
      stub.lastOpts = opts;
      if (reply instanceof Error) return Promise.reject(reply);
      return Promise.resolve({
        ok: reply.ok !== false,
        status: reply.status || 200,
        json: function () { return Promise.resolve(reply.body); },
        text: function () { return Promise.resolve(JSON.stringify(reply.body || {})); }
      });
    };
  }
  function unstub() {
    window.fetch = realFetch;
    GD.llm.key = realKey;
  }
  function message(text) {
    return { body: { content: [{ type: "thinking", thinking: "" },
                               { type: "text", text: text }], stop_reason: "end_turn" } };
  }

  var FACTS = "- quote: 269,405\n- band top: 262,682\n- budget: 262,000";
  var TEMPLATE = "The app wrote this. The quote is 269,405 against a band top of 262,682.";

  function draftCase(name, reply, check) {
    return function () {
      stub(reply);
      return GD.llm.draft({
        facts: FACTS, template: TEMPLATE, system: "s", instruction: "Rewrite it."
      }).then(function (d) {
        unstub();
        check(d);
      }).catch(function (e) {
        unstub();
        ok(name, false, e && e.message);
      });
    };
  }

  var cases = [
    draftCase("a grounded model reply is used", message(
      "The quote of 269,405 is above the band top of 262,682 and the budget of 262,000."
    ), function (d) {
      ok("a grounded model reply is used", d.source === "model", d.source + " / " + d.note);
      ok("the request went to the Messages API",
         String(stub.lastUrl).indexOf("api.anthropic.com/v1/messages") >= 0, stub.lastUrl);
      var body = JSON.parse(stub.lastOpts.body);
      ok("it asked for the model the skill names", body.model === "claude-opus-5", body.model);
      ok("the guardrail went in the system prompt",
         body.system.indexOf("Every number you write must appear") >= 0);
      ok("the fact pack went in the user message, assembled by GD.llm itself",
         body.messages[0].content.indexOf("269,405") >= 0 &&
         body.messages[0].content.indexOf("FACTS") >= 0,
         body.messages[0].content.slice(0, 90));
      ok("the app's own draft went in as the starting point",
         body.messages[0].content.indexOf(TEMPLATE) >= 0);
      ok("the browser-origin header is set",
         stub.lastOpts.headers["anthropic-dangerous-direct-browser-access"] === "true");
    }),

    draftCase("a reply with an untraceable figure is discarded", message(
      "The quote of 269,405 is above the band, and the market will fall 37.4% by March."
    ), function (d) {
      ok("a reply with an untraceable figure is discarded",
         d.source === "template" && d.rejected === true, d.source + " / " + d.note);
      ok("the app's own wording is what comes back", d.text === TEMPLATE);
      ok("the note names the stray figure", d.note.indexOf("37.4") >= 0, d.note);
    }),

    draftCase("a refusal falls back to the template",
      { body: { content: [], stop_reason: "refusal",
                stop_details: { type: "refusal", category: "other" } } },
      function (d) {
        ok("a refusal falls back to the template", d.source === "template", d.source);
        ok("the refusal is reported rather than swallowed",
           (d.error || "").indexOf("declined") >= 0, d.error);
      }),

    draftCase("an HTTP error falls back to the template",
      { ok: false, status: 429, body: { error: { message: "rate limited" } } },
      function (d) {
        ok("an HTTP error falls back to the template", d.source === "template", d.source);
        ok("the status is reported", (d.error || "").indexOf("429") >= 0, d.error);
      }),

    draftCase("a network failure falls back to the template", new Error("network down"),
      function (d) {
        ok("a network failure falls back to the template", d.source === "template", d.source);
        ok("every failure path still returns usable text", d.text === TEMPLATE);
      })
  ];

  (function run(i) {
    if (i >= cases.length) {
      /* and finally the no-key path, which is the one the demo actually runs on */
      GD.llm.draft({
        facts: FACTS, template: TEMPLATE, system: "s", instruction: "Rewrite it."
      }).then(function (d) {
        ok("with no key the app writes it itself",
           d.source === "template" && d.text === TEMPLATE && !d.error, d.note);
        finish();
      }).catch(function (e) {
        ok("with no key the app writes it itself", false, e && e.message);
        finish();
      });
      return;
    }
    cases[i]().then(function () { run(i + 1); });
  })(0);

  function finish() {
    var failed = results.filter(function (r) { return !r.pass; });
    var out = document.createElement("div");
    out.id = "__lib_results";
    out.textContent = JSON.stringify({
      total: results.length, failed: failed.length, results: results
    });
    document.body.appendChild(out);
    document.title = failed.length ? ("FAIL " + failed.length) : "OK";
  }

})();
