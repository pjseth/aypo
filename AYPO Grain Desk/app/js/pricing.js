/* Grain Desk — the price model, generalised off wheat.

   The wheat desk answered one question: of the flat price a supplier quoted, how much is the
   futures market, how much is freight, and how much is the supplier? That question is not
   about wheat. Any material with a published driver can be asked it, and a buyer who relies
   on his suppliers for market outlooks needs it asked most of all.

   The arithmetic, for a quote on material `m` at date `d`:

       leg_i       = (series_i(d) × per_unit_i + offset_i) × fx(currency_i → m.currency)
       driverCost  = Σ leg_i
       premium     = quote.price − driverCost

   Premium is a *subtraction*, not a quoted figure, exactly as the wheat residual is — and for
   the same reason: nobody itemises. Every screen that shows it says whose arithmetic it is.

   FX is not a fourth addend. It is a named multiplier on each leg that needs one, so the legs
   plus the premium equal the quote price by construction and not by luck.

   No DOM, no globals beyond `GD.pricing`. Everything here is a pure function of its inputs,
   which is what makes it testable without a browser automating itself. */
(function () {
  "use strict";

  var P = {};
  GD.pricing = P;

  /* Half-up to 2dp. Matches `r2()` in tools/build_ws.py, so the seed and the app agree to
     the cent rather than to the nearest banker's rounding accident. */
  function round2(v) {
    return v >= 0 ? Math.floor(v * 100 + 0.5) / 100 : -Math.floor(-v * 100 + 0.5) / 100;
  }
  P.round2 = round2;

  /* ----------------------------------------------------------------------- series read */

  /* The published print in force on `date`: the latest point at or before it. A quote is
     argued against the number that was on the screen when it was made, not today's. */
  function indexAt(driver, date) {
    var ds = driver.dates || [];
    if (!ds.length) return -1;
    var at = -1;
    for (var i = 0; i < ds.length; i++) {
      if (ds[i] <= date) at = i; else break;
    }
    return at < 0 ? 0 : at;
  }
  P.indexAt = indexAt;

  function valueAt(driver, date) {
    var i = indexAt(driver, date);
    var v = (driver.values || [])[i];
    return v === undefined ? null : v;
  }
  P.valueAt = valueAt;

  /* -------------------------------------------------------------------------- the legs */

  /* One leg per driver, in the material's currency per material unit, with every
     intermediate figure kept so the screen can show the arithmetic rather than assert it. */
  P.legs = function (material, date, fx) {
    var rate_of = fx || GD.fx;
    return (material.drivers || []).map(function (d) {
      var raw = valueAt(d, date);
      var i = indexAt(d, date);
      var rate = rate_of(d.currency, material.currency);
      var base = raw === null ? null : raw * d.per_unit + d.offset;
      var value = (base === null || rate === null) ? null : round2(base * rate);
      return {
        key: d.key,
        name: d.name,
        source: d.source,
        unit: d.unit,
        currency: d.currency,
        kind: d.kind,
        mocked: !!d.mocked,
        note: d.note,
        at: d.dates ? d.dates[i] : null,
        raw: raw,                 /* the published print, in the driver's own unit */
        per_unit: d.per_unit,
        offset: d.offset,
        base: base === null ? null : round2(base),  /* driver currency, per material unit */
        rate: rate,
        value: value              /* material currency, per material unit */
      };
    });
  };

  P.driverCost = function (material, date, fx) {
    var legs = P.legs(material, date, fx);
    var sum = 0, ok = false;
    legs.forEach(function (l) { if (l.value !== null) { sum += l.value; ok = true; } });
    return ok ? round2(sum) : null;
  };

  /* The whole split, including the one number the screen must be able to prove: that the
     components add up to what the supplier asked for. */
  P.breakdown = function (material, quote, fx) {
    var legs = P.legs(material, quote.date, fx);
    var cost = P.driverCost(material, quote.date, fx);
    var premium = cost === null ? null : round2(quote.price - cost);
    var parts = legs.map(function (l) { return { name: l.name, value: l.value, leg: l }; });
    var total = 0;
    legs.forEach(function (l) { total += (l.value || 0); });
    total = round2(total + (premium || 0));
    return {
      legs: legs,
      parts: parts,
      driverCost: cost,
      premium: premium,
      /* `total` is the legs and the premium added back up. It must equal quote.price. */
      total: total,
      reconciles: Math.abs(total - quote.price) < 0.005,
      /* Foreign-currency legs, summarised — the FX conversion as one visible step. */
      fx: fxSummary(legs, material)
    };
  };

  function fxSummary(legs, material) {
    var groups = {};
    legs.forEach(function (l) {
      if (l.currency === material.currency || l.value === null) return;
      var g = groups[l.currency] || (groups[l.currency] = {
        currency: l.currency, rate: l.rate, base: 0, value: 0, names: []
      });
      g.base = round2(g.base + l.base);
      g.value = round2(g.value + l.value);
      g.names.push(l.name);
    });
    return Object.keys(groups).map(function (k) { return groups[k]; });
  }

  /* ---------------------------------------------------------------------- the premiums */

  function monthsBefore(dateStr, n) {
    var m = String(dateStr).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return "0000-00-00";
    var y = Number(m[1]), mo = Number(m[2]) - n, d = m[3];
    while (mo <= 0) { mo += 12; y -= 1; }
    return y + "-" + String(mo).padStart(2, "0") + "-" + d;
  }
  P.monthsBefore = monthsBefore;

  /* Every supplier's premium on this material inside the trailing window. This is the
     population the fair band is drawn from — the material's own history, not a benchmark
     bought from someone with an interest in the answer. */
  P.premiums = function (material, quotes, asOf, months) {
    var from = monthsBefore(asOf, months === undefined ? 12 : months);
    var out = [];
    quotes.forEach(function (q) {
      if (q.material_id !== material.id) return;
      if (q.date < from || q.date > asOf) return;
      var cost = P.driverCost(material, q.date);
      if (cost === null) return;
      out.push({
        quote: q,
        date: q.date,
        supplier_id: q.supplier_id,
        driverCost: cost,
        premium: round2(q.price - cost),
        pct: cost ? (q.price - cost) / cost : null
      });
    });
    out.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    return out;
  };

  /* Linear-interpolated percentile on a sorted array. The same definition Excel's
     PERCENTILE uses, so a buyer who re-derives it in a spreadsheet gets our number. */
  function percentile(sorted, p) {
    if (!sorted.length) return null;
    if (sorted.length === 1) return sorted[0];
    var pos = (sorted.length - 1) * p;
    var lo = Math.floor(pos), hi = Math.ceil(pos);
    if (lo === hi) return sorted[lo];
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }
  P.percentile = percentile;

  /* Four premiums is the floor. Below that the quartiles are describing noise, and a band
     that is really noise is worse than no band, because somebody will negotiate against it. */
  var MIN_OBS = 4;
  P.MIN_OBS = MIN_OBS;

  P.band = function (material, quotes, date, asOf, months) {
    var obs = P.premiums(material, quotes, asOf || date, months);
    var cost = P.driverCost(material, date);
    var vals = obs.map(function (o) { return o.premium; }).sort(function (a, b) { return a - b; });
    if (vals.length < MIN_OBS || cost === null) {
      return {
        established: false, n: vals.length, min_obs: MIN_OBS,
        driverCost: cost, low: null, high: null, p25: null, p75: null, obs: obs
      };
    }
    var p25 = percentile(vals, 0.25), p75 = percentile(vals, 0.75);
    return {
      established: true,
      n: vals.length,
      min_obs: MIN_OBS,
      driverCost: cost,
      p25: round2(p25),
      p75: round2(p75),
      median: round2(percentile(vals, 0.5)),
      low: round2(cost + p25),
      high: round2(cost + p75),
      obs: obs
    };
  };

  /* "below" / "inside" / "above" — a word, because colour alone never carries a verdict. */
  P.position = function (price, band) {
    if (!band || !band.established) return "unknown";
    if (price < band.low) return "below";
    if (price > band.high) return "above";
    return "inside";
  };

  /* How far outside, as money and as a share of the quote. Zero when inside. */
  P.outside = function (price, band) {
    if (!band || !band.established) return null;
    if (price > band.high) return round2(price - band.high);
    if (price < band.low) return round2(price - band.low);
    return 0;
  };

  /* ----------------------------------------------------------------------- the budget */

  P.budgetGap = function (material, price) {
    if (material.budget_price === null || material.budget_price === undefined) return null;
    var amount = round2(price - material.budget_price);
    return {
      budget: material.budget_price,
      amount: amount,
      pct: material.budget_price ? amount / material.budget_price : null,
      over: amount > 0
    };
  };

  /* ------------------------------------------------------------------ premium history
     The generalisation of the wheat basis history: this supplier's premium month by month
     against the median every supplier of the material is carrying. Two lines, one question —
     is this counterparty drifting away from the rest of the book. */

  P.premiumSeries = function (material, quotes, supplierId, asOf, months) {
    var obs = P.premiums(material, quotes, asOf, months);
    var buckets = [];
    var index = {};
    obs.forEach(function (o) {
      var mk = o.date.slice(0, 7);
      if (!index[mk]) { index[mk] = { month: mk, mine: [], all: [] }; buckets.push(index[mk]); }
      index[mk].all.push(o.premium);
      if (o.supplier_id === supplierId) index[mk].mine.push(o.premium);
    });
    buckets.sort(function (a, b) { return a.month < b.month ? -1 : 1; });
    return {
      months: buckets.map(function (b) { return b.month + "-01"; }),
      mine: buckets.map(function (b) { return b.mine.length ? round2(mean(b.mine)) : null; }),
      median: buckets.map(function (b) {
        var s = b.all.slice().sort(function (x, y) { return x - y; });
        return s.length ? round2(percentile(s, 0.5)) : null;
      })
    };
  };

  function mean(a) {
    var s = 0;
    a.forEach(function (v) { s += v; });
    return a.length ? s / a.length : 0;
  }
  P.mean = mean;

  /* Quarterly averages of one supplier's premium, and the longest run of consecutive rises.
     "Widening for three quarters" is a claim; this is the test of it, and the brief quotes
     the number this returns rather than a human impression of the chart. */
  P.premiumTrend = function (material, quotes, supplierId, asOf, months) {
    var obs = P.premiums(material, quotes, asOf, months).filter(function (o) {
      return o.supplier_id === supplierId;
    });
    var buckets = [], index = {};
    obs.forEach(function (o) {
      var y = o.date.slice(0, 4), mo = Number(o.date.slice(5, 7));
      var k = y + "-Q" + (Math.floor((mo - 1) / 3) + 1);
      if (!index[k]) { index[k] = { key: k, vals: [] }; buckets.push(index[k]); }
      index[k].vals.push(o.premium);
    });
    buckets.sort(function (a, b) { return a.key < b.key ? -1 : 1; });
    var quarters = buckets.map(function (b) {
      return { key: b.key, avg: round2(mean(b.vals)), n: b.vals.length };
    });
    var run = 0, best = 0;
    for (var i = 1; i < quarters.length; i++) {
      run = quarters[i].avg > quarters[i - 1].avg ? run + 1 : 0;
      if (run > best) best = run;
    }
    var first = quarters.length ? quarters[0].avg : null;
    var last = quarters.length ? quarters[quarters.length - 1].avg : null;
    return {
      quarters: quarters,
      risingRun: best,
      widening: best >= 3,
      change: (first === null || last === null) ? null : round2(last - first),
      changePct: (first ? (last - first) / Math.abs(first) : null)
    };
  };

  /* ------------------------------------------------------------------------- the board */

  /* Everything a surface needs about one quote, in one call, so the table, the modal and the
     brief all read the same numbers from the same place. */
  P.assess = function (material, quote, quotes, asOf) {
    var bd = P.breakdown(material, quote);
    var band = P.band(material, quotes, quote.date, asOf);
    return {
      material: material,
      quote: quote,
      breakdown: bd,
      band: band,
      position: P.position(quote.price, band),
      outside: P.outside(quote.price, band),
      budget: P.budgetGap(material, quote.price),
      premiumPct: bd.driverCost ? bd.premium / bd.driverCost : null
    };
  };

  /* The latest quote per supplier for a material — the live board, which is what a buyer is
     actually looking at when he picks up the phone. */
  P.latestPerSupplier = function (materialId, quotes) {
    var best = {};
    quotes.forEach(function (q) {
      if (q.material_id !== materialId) return;
      var cur = best[q.supplier_id];
      if (!cur || q.date > cur.date) best[q.supplier_id] = q;
    });
    return Object.keys(best).map(function (k) { return best[k]; })
      .sort(function (a, b) { return a.price - b.price; });
  };

})();
