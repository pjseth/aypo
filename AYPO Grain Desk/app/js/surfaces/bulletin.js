/* Grain Desk — Market bulletin.

   It goes out every fortnight and it is assembled by hand: commodity prices, FX, freight,
   the El Niño and crop picture, and a read on what it all means for buying. The data is
   already on this desk — the market strip's feeds, the driver series behind every material,
   the live quote board — so the assembly is the only part that should take a person's day,
   and it shouldn't.

   Two rules make it worth sending rather than just fast to produce:

     · every figure names where it came from, on the page, in the exported PDF
     · where a feed has no print for the period the bulletin says so, in words, instead of
       reaching for the nearest number

   And the human gate: a draft cannot be exported until somebody has read it and approved it.
   Editing an approved draft un-approves it, because the thing that was approved is no longer
   the thing on screen. */
(function () {
  "use strict";

  var el = GD.el, icon = GD.icon, fmt = GD.fmt, P = GD.pricing;

  var CADENCE = 14;

  /* -------------------------------------------------------------------- the clock */

  function lastIssued(wsKey) {
    try {
      var v = window.localStorage.getItem("gd.bulletin.last." + wsKey);
      if (v && /^\d{4}-\d{2}-\d{2}$/.test(v)) return v;
    } catch (e) { /* private mode */ }
    return null;
  }
  function setLastIssued(wsKey, date) {
    try { window.localStorage.setItem("gd.bulletin.last." + wsKey, date); } catch (e) { /* ok */ }
  }

  function addDays(iso, n) {
    var d = fmt.parseDate(iso);
    if (!d) return iso;
    d.setDate(d.getDate() + n);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0");
  }

  /* The window the bulletin covers: from the last one that went out to today. With no record
     of a previous issue, one cadence back — which is the right default and says so. */
  function window_(ws) {
    var asOf = GD.wsAsOf();
    var cadence = ws.bulletin_cadence_days || CADENCE;
    var last = lastIssued(ws.key);
    var assumed = !last;
    if (!last) last = addDays(asOf, -cadence);
    return {
      from: last, to: asOf, cadence: cadence, assumed: assumed,
      due: addDays(last, cadence)
    };
  }

  /* Read by the market strip, so the countdown in the chrome and the page agree. */
  GD.bulletinDue = function () {
    var ws = GD.ws();
    if (!ws || !GD.routes().bulletin) return null;
    var w = window_(ws);
    return {
      date: w.due, last: w.from, cadence: w.cadence,
      days: fmt.daysBetween(GD.wsAsOf(), w.due)
    };
  };

  /* ================================================================== the generator */

  function instrumentFor(key) {
    var list = GD.wsInstruments() || [];
    for (var i = 0; i < list.length; i++) if (list[i].key === key) return list[i];
    return null;
  }

  /* The move a driver made over the bulletin's window, preferring a daily feed where the
     workspace has one and falling back to the driver's own monthly prints. Either way the
     answer carries the two dates it was measured between. */
  function move(driver, w) {
    var ins = instrumentFor(driver.key);
    if (ins && ins.dates && ins.dates.length) {
      var a = valueOn(ins.dates, ins.history, w.from);
      var b = valueOn(ins.dates, ins.history, w.to);
      if (a !== null && b !== null) {
        return {
          /* The venue alone: the line that prints this already names the driver, and
             "ICE · Brent crude, front month" next to "Brent crude, front month:" is the
             citation saying the thing twice. */
          kind: "daily", source: ins.venue,
          unit: ins.unit, from: a.date, to: b.date,
          was: a.value, now: b.value,
          change: P.round2(b.value - a.value),
          pct: a.value ? (b.value - a.value) / a.value : null,
          fresh: true
        };
      }
    }
    var i0 = P.indexAt(driver, w.from), i1 = P.indexAt(driver, w.to);
    var v0 = driver.values[i0], v1 = driver.values[i1];
    if (v0 === undefined || v1 === undefined) return null;
    return {
      kind: "monthly", source: driver.source, unit: driver.unit,
      from: driver.dates[i0], to: driver.dates[i1],
      was: v0, now: v1,
      change: P.round2(v1 - v0),
      pct: v0 ? (v1 - v0) / v0 : null,
      /* A monthly index with no new print inside the window has nothing to report, and that
         is a fact about the period rather than a gap in the data. */
      fresh: i1 !== i0
    };
  }

  /* True when a driver's own series does not change across the window — the leg is pegged,
     whatever the instrument behind it did. */
  function held(driver, w) {
    var a = P.valueAt(driver, w.from), b = P.valueAt(driver, w.to);
    return a !== null && b !== null && a === b;
  }

  function valueOn(dates, values, date) {
    var at = -1;
    for (var i = 0; i < dates.length; i++) {
      if (dates[i] <= date) at = i; else break;
    }
    if (at < 0) return null;
    return { date: dates[at], value: values[at] };
  }

  function fmtLevel(v, unit, currency) {
    if (v === null || v === undefined) return "—";
    if (currency) return GD.cur(v, currency, Math.abs(v) >= 1000 ? 0 : 2);
    return fmt.n(v, Math.abs(v) >= 1000 ? 0 : 2);
  }

  /* One section per material, plus a market overview built from the workspace's own feeds. */
  function build(ws) {
    var w = window_(ws);
    var asOf = w.to;
    var sections = [];
    var gaps = 0;

    /* ---- overview, from the market strip's instruments */
    var ticks = (GD.wsInstruments() || []).map(function (ins) {
      var a = valueOn(ins.dates, ins.history, w.from);
      var b = valueOn(ins.dates, ins.history, w.to);
      return {
        key: ins.key, label: ins.label, venue: ins.venue, unit: ins.unit,
        was: a ? a.value : null, now: b ? b.value : null,
        pct: (a && b && a.value) ? (b.value - a.value) / a.value : null,
        from: a ? a.date : null, to: b ? b.date : null
      };
    });

    sections.push({
      id: "overview",
      title: "The fortnight in one paragraph",
      kind: "overview",
      ticks: ticks,
      gap: !ticks.length,
      lines: ticks.length ? ticks.map(function (t) {
        return "· " + t.label + " at " + fmtLevel(t.now, t.unit) +
          (/\//.test(t.unit) ? " " + t.unit : "") +
          (t.pct === null ? ", no comparable print in the window"
            : ", " + fmt.signedPct(t.pct, 1) + " over the fortnight") +
          "  (" + t.venue + ", " + fmt.date(t.to) + ")";
      }) : ["· No market feeds are configured for this workspace, so there is no overview " +
            "to give. The material sections below still carry their own drivers."]
    });
    if (!ticks.length) gaps++;

    /* ---- one per material */
    ws.materials.forEach(function (m) {
      var legs = P.legs(m, asOf);
      var costNow = P.driverCost(m, asOf);
      var costThen = P.driverCost(m, w.from);
      var band = P.band(m, ws.quotes, asOf, asOf);
      var live = P.latestPerSupplier(m.id, ws.quotes);
      var keen = live.length ? live[0] : null;
      var keenA = keen ? P.assess(m, keen, ws.quotes, asOf) : null;
      var sectionGaps = [];

      var driverLines = [];
      /* Freight gets its own section below, so listing it here too would print the same
         sentence twice under two headings. */
      (m.drivers || []).filter(function (d) { return d.kind !== "freight"; }).forEach(function (d) {
        var mv = move(d, w);
        if (!mv) {
          sectionGaps.push(d.name + " has no series in this build");
          driverLines.push("· " + d.name + ": no data for this period. Nothing is assumed " +
            "in its place, so the driver cost below is carried at its last known print.");
          return;
        }
        if (!mv.fresh) {
          driverLines.push("· " + d.name + ": no new print since " + fmt.date(mv.to) +
            ". Held at " + fmtLevel(mv.now, mv.unit) + " " + mv.unit +
            ".  (" + mv.source + ")");
          return;
        }
        /* A spread that is held constant has not "moved 0.0% from itself" — it is held, and
           saying so is both shorter and the thing a reader needs to know about it. */
        if (mv.was === mv.now) {
          driverLines.push("· " + d.name + ": held flat at " + fmtLevel(mv.now, mv.unit) +
            " " + mv.unit + ".  (" + mv.source + ")" + (d.mocked ? "  [mock feed]" : ""));
          return;
        }
        driverLines.push("· " + d.name + ": " + fmtLevel(mv.now, mv.unit) + " " + mv.unit +
          ", " + (mv.pct === null ? "level" : fmt.signedPct(mv.pct, 1)) + " from " +
          fmtLevel(mv.was, mv.unit) + " on " + fmt.date(mv.from) +
          ".  (" + mv.source + ", print of " + fmt.date(mv.to) + ")" +
          (d.mocked ? "  [mock feed]" : "") +
          /* The market moved and the model did not. That happens when this desk holds the
             leg at one observed settlement — true of every wheat class, where the Quote
             Board reconstructs against a single print — and leaving it unsaid would put a
             falling index next to an unchanged driver cost with no explanation. */
          (held(d, w) ? "  The leg in the price model is held at the " +
            fmt.date(d.dates[P.indexAt(d, w.to)]) + " print, so driver cost below does not " +
            "move with it." : ""));
      });

      /* FX — a section of its own because it is half the move on an imported material. */
      var fxLines = [];
      var foreign = {};
      legs.forEach(function (l) {
        if (l.currency !== m.currency) foreign[l.currency] = l.rate;
      });
      var fxKeys = Object.keys(foreign);
      if (!fxKeys.length) {
        fxLines.push("· Nothing to convert — every driver for " + m.name + " is published " +
          "in " + m.currency + ".");
      } else {
        fxKeys.forEach(function (c) {
          var ins = instrumentFor(c + m.currency) || instrumentFor(m.currency + c);
          var mv = ins ? (function () {
            var a = valueOn(ins.dates, ins.history, w.from);
            var b = valueOn(ins.dates, ins.history, w.to);
            return (a && b && a.value) ? (b.value - a.value) / a.value : null;
          })() : null;
          fxLines.push("· " + c + "/" + m.currency + " at " + fmt.n(foreign[c], 4) +
            (mv === null
              ? ". No rate history is wired up for this pair, so the move over the " +
                "fortnight is not stated."
              : ", " + fmt.signedPct(mv, 1) + " over the fortnight" +
                (mv > 0 ? " — a weaker " + m.currency + " raises the landed cost of every " +
                          c + " leg above." : " — a stronger " + m.currency +
                          " lowers the landed cost of every " + c + " leg above.")) +
            "  (indicative mid, " + fmt.date(asOf) + ")");
          if (mv === null) sectionGaps.push(c + "/" + m.currency + " has no rate history");
        });
      }

      /* Freight — named separately because it is the leg a buyer can actually act on. */
      var frt = legs.filter(function (l) { return l.kind === "freight"; });
      var freightLines = frt.length ? frt.map(function (l) {
        var d = null;
        (m.drivers || []).forEach(function (x) { if (x.key === l.key) d = x; });
        var mv = d ? move(d, w) : null;
        /* The move is stated between the dates it was actually measured between. A monthly
           index read on the 29th and again on the 13th moved between two monthly prints, not
           "over the fortnight", and saying the latter would be a small lie. */
        return "· " + l.name + " contributes " + GD.curUnit(l.value, m) + "/" + m.unit +
          (mv && mv.fresh && mv.pct !== null
            ? ", " + fmt.signedPct(mv.pct, 1) + " since its " + fmt.dateShort(mv.from) + " print"
            : mv && !mv.fresh ? ", unchanged since " + fmt.date(mv.to) : "") +
          ".  (" + l.source + ")";
      }) : ["· No freight leg on this material — it is bought delivered from a domestic " +
            "supplier, so freight sits inside the supplier's own number."];

      /* Crop and weather — the section most likely to have nothing in it. */
      var cropLines, cropState;
      if (!m.crop) {
        cropState = "n/a";
        /* "Not applicable" is only true of something that is not grown. Saying it of a raw
           material would be the bulletin asserting something false about the material rather
           than about the feed. */
        cropLines = [m.category === "packaging"
          ? "· Not applicable — " + m.name + " is not an agricultural input, so there is no " +
            "crop or weather read to give."
          : "· No crop or weather feed is configured for " + m.name + ". Nothing is assumed " +
            "in its place."];
      } else if (m.crop.status === "unavailable") {
        cropState = "gap";
        sectionGaps.push("crop outlook unavailable");
        gaps++;
        cropLines = ["· No data for this period. " + m.crop.reason];
      } else if (m.crop.status === "elsewhere") {
        /* Not a gap: the read exists, it just is not this document's job to restate it.
           Counting it as missing data would inflate the one number on this page that is
           supposed to mean something. */
        cropState = "elsewhere";
        cropLines = ["· " + m.crop.reason];
      } else {
        cropState = "ok";
        cropLines = ["· " + m.crop.headline + ". " + m.crop.body +
          "  (" + m.crop.source + ")"];
      }

      /* What it means for buying — assembled from the numbers above, never a flourish. */
      var costMove = (costNow !== null && costThen !== null)
        ? P.round2(costNow - costThen) : null;
      var costPct = (costMove !== null && costThen) ? costMove / costThen : null;
      /* The driver-cost move is the section's own headline a few lines above; repeating it
         here verbatim is the kind of padding that makes a bulletin feel generated. This
         section starts with what a buyer can do about it, and only speaks about driver cost
         when there is none to report. */
      var meaning = [];
      if (costMove === null) {
        meaning.push("· Driver cost cannot be computed for this period, so there is no " +
          "buying read to give.");
      }
      if (!keen) {
        meaning.push("· No live quote is on the board for " + m.name + ", so there is " +
          "nothing to compare the market against this fortnight.");
      } else {
        var kp = GD.curUnit(keen.price, m);
        meaning.push("· The keenest live offer is " + kp + "/" + m.unit + " from " +
          GD.supplierName(keen.supplier_id, ws) + ", quoted " + fmt.date(keen.date) + ".");
        if (keenA.band.established) {
          meaning.push("· That is " +
            (keenA.position === "inside" ? "inside the fair band of " +
                GD.curUnit(keenA.band.low, m) + "–" + GD.curUnit(keenA.band.high, m)
              : keenA.position === "above" ? "above the fair band by " +
                GD.curUnit(keenA.outside, m) + "/" + m.unit
              : "below the fair band by " +
                GD.curUnit(Math.abs(keenA.outside), m) + "/" + m.unit) + ".");
        } else {
          meaning.push("· No fair band is established for " + m.name + " yet — " +
            keenA.band.n + " quotes against the " + keenA.band.min_obs + " needed.");
        }
        if (keenA.budget) {
          meaning.push("· Against the budget rate of " +
            GD.curUnit(keenA.budget.budget, m) + "/" + m.unit + " it is " +
            fmt.signedPct(keenA.budget.pct, 1) + ".");
        }
        meaning.push("· " + advice(keenA, costPct, m));
      }

      sections.push({
        id: m.id,
        title: m.name,
        kind: "material",
        material: m,
        cropState: cropState,
        gap: sectionGaps.length > 0,
        gapNotes: sectionGaps,
        costNow: costNow, costMove: costMove, costPct: costPct,
        keen: keen, assessment: keenA,
        groups: [
          { heading: "Drivers", lines: driverLines },
          { heading: "FX", lines: fxLines },
          { heading: "Freight", lines: freightLines },
          { heading: "Crop and weather", lines: cropLines },
          { heading: "What this means for buying", lines: meaning }
        ]
      });
    });

    return { window: w, sections: sections, gaps: gaps, ws: ws };
  }

  /* The one judgement line per material. Rule-based on purpose: a buying recommendation
     generated by a language model is the thing URC said they would not trust, and this one
     can be read back to the numbers it came from. */
  function advice(a, costPct, m) {
    var rising = costPct !== null && costPct > 0.015;
    var falling = costPct !== null && costPct < -0.015;
    if (!a.band.established) {
      return "Read this against driver cost rather than against a spread until there are " +
        "more quotes on the material. " +
        (rising ? "Drivers are rising, so an early drawdown is worth considering."
          : falling ? "Drivers are falling, so there is no hurry."
          : "Nothing in the drivers argues for moving early.");
    }
    if (a.position === "above") {
      return "Worth a call before the next drawdown — the premium is above where the rest " +
        "of the book sits" + (rising ? ", and rising drivers will make it worse." : ".");
    }
    if (a.position === "below" && (a.budget ? !a.budget.over : true)) {
      if (falling) {
        return "Keen, but drivers are still falling — cover the near requirement and leave " +
          "the rest open.";
      }
      return a.budget
        ? "Keen against both the band and the budget. Worth covering more of the " +
          "requirement at this level."
        : "Keen against the band. No budget rate is held for this material, so the band is " +
          "the only yardstick — on it, this is worth taking more of.";
    }
    if (a.budget && a.budget.over) {
      return "The premium is normal, so the gap to budget is a product-cost conversation " +
        "rather than a supplier one.";
    }
    return rising
      ? "Priced where it should be, with drivers rising — cover on schedule rather than late."
      : "Priced where it should be. Nothing to do this fortnight.";
  }

  /* ===================================================================== the document */

  function toMarkdown(doc) {
    var ws = doc.ws, w = doc.window;
    var L = [];
    L.push("MARKET BULLETIN — " + ws.name);
    L.push("Period " + fmt.date(w.from) + " to " + fmt.date(w.to) +
      " · issued every " + w.cadence + " days" +
      (w.assumed ? " · no previous issue on record, so the period is one cadence back" : ""));
    L.push("Prepared by Grain Desk from the feeds and quote history on this desk. " +
      (ws.demo ? "DEMONSTRATION DATA — every figure below is fictional. " : "") +
      "Draft for review.");
    L.push("");

    doc.sections.forEach(function (s) {
      L.push("## " + s.title);
      if (s.kind === "overview") {
        s.lines.forEach(function (l) { L.push(l); });
        L.push("");
        return;
      }
      if (s.costNow !== null) {
        L.push("· Driver cost " + GD.curUnit(s.costNow, s.material) + "/" + s.material.unit +
          (s.costMove === null ? ""
            : s.costMove === 0 ? ", unchanged over the period"
            : ", " + (s.costMove < 0 ? "down " : "up ") +
              GD.curUnit(Math.abs(s.costMove), s.material) +
              (s.costPct === null ? "" : " (" + fmt.signedPct(s.costPct, 1) + ")") +
              " over the period"));
      }
      s.groups.forEach(function (g) {
        L.push("");
        L.push("### " + g.heading);
        g.lines.forEach(function (l) { L.push(l); });
      });
      L.push("");
    });

    L.push("## Sources and caveats");
    L.push("· Every figure above names the feed it came from and the date of the print used.");
    L.push("· Feeds marked [mock feed] are placeholder series for this build, not licensed " +
      "data.");
    L.push("· " + GD.fxNote());
    if (doc.gaps) {
      L.push("· " + doc.gaps + " section" + (doc.gaps === 1 ? "" : "s") + " of this " +
        "bulletin had no data for the period. They say so rather than carrying an estimate.");
    }
    L.push("· Driver cost is the published drivers only. The premium each supplier carries " +
      "is on the price-check screen, quote by quote.");
    return L.join("\n");
  }

  function factPack(doc) {
    var L = [];
    L.push("Bulletin period: " + fmt.date(doc.window.from) + " to " + fmt.date(doc.window.to));
    doc.sections.forEach(function (s) {
      L.push("");
      L.push("[" + s.title + "]");
      if (s.kind === "overview") {
        s.ticks.forEach(function (t) {
          L.push("- " + t.label + ": " + fmtLevel(t.now, t.unit) + " " + t.unit +
            (t.pct === null ? " (no comparable print)" : " " + fmt.signedPct(t.pct, 1)) +
            "   [source: " + t.venue + ", " + fmt.date(t.to) + "]");
        });
        return;
      }
      if (s.costNow !== null) {
        L.push("- driver cost: " + GD.curUnit(s.costNow, s.material) + "/" +
          s.material.unit + (s.costMove === null ? "" :
          ", move " + GD.curUnitSigned(s.costMove, s.material) +
          (s.costPct === null ? "" : " / " + fmt.signedPct(s.costPct, 1))) +
          "   [source: driver legs on this desk]");
      }
      s.groups.forEach(function (g) {
        g.lines.forEach(function (l) { L.push("- " + g.heading + ": " + l.replace(/^· /, "")); });
      });
    });
    return L.join("\n");
  }

  function printable(doc, text) {
    return GD.docHtml(text, {
      foot: "Approved " + fmt.date(GD.wsAsOf()) + " · Grain Desk · " + doc.ws.name +
            (doc.ws.demo ? " · demonstration data, every figure fictional" : "")
    });
  }

  var BULLETIN_SYSTEM = [
    "You tidy the prose of a fortnightly commodity market bulletin for a food manufacturer's",
    "procurement team. The readers are buyers and the plant planners they supply.",
    "",
    "Keep every heading and every section. Keep each bullet as a bullet. Keep the source in",
    "brackets at the end of each bullet exactly as given. Tighten the wording, nothing else.",
    "",
    "Where a bullet says there is no data for the period, keep saying that. Do not substitute",
    "a number, do not reach for a typical value, and do not soften it into a guess."
  ].join("\n");

  /* ====================================================================== the surface */

  /* The draft lives here for the session. It is deliberately not written to storage: a
     half-edited bulletin surviving a browser restart, with an approval stamp on it, is a
     liability rather than a feature. What does persist is the date of the last approved
     issue, because that is what the next period is measured from. */
  var draft = null;    /* { wsKey, doc, text, approved, source, note, approvedText } */

  function statusOf() {
    if (!draft) return "none";
    if (!draft.approved) return "draft";
    return draft.text === draft.approvedText ? "approved" : "edited";
  }

  GD.route("bulletin", {
    label: "Market bulletin",
    icon: "file",
    group: "Procurement",
    ws: ["ufm", "urc"],
    badge: function () {
      var due = GD.bulletinDue();
      if (!due) return null;
      if (draft && draft.approved && draft.text === draft.approvedText) return null;
      return due.days <= 0 ? 1 : null;
    },
    badgeTone: "warn",

    render: function (wrap) {
      var ws = GD.ws();
      var w = window_(ws);
      var due = GD.bulletinDue();
      if (draft && draft.wsKey !== ws.key) draft = null;

      function redraw() { GD.go("bulletin"); }

      /* ----------------------------------------------------------------- 1 · head */
      wrap.appendChild(el("div", { class: "page-head" }, [
        el("div", { class: "ph-text" }, [
          el("h1", { class: "page-title", text: "Market bulletin" }),
          el("div", {
            class: "page-sub",
            text: "One page per material every " + w.cadence + " days: what the drivers did, " +
                  "FX and freight, the crop read, and a plain line on what it means for buying"
          })
        ]),
        el("div", { class: "ph-actions" }, [
          GD.demoChip(),
          GD.chip("every " + w.cadence + " days", "outline", "clock"),
          GD.chip(due.days <= 0 ? "due now" : "due in " + due.days + " days",
            due.days <= 0 ? "warn" : "outline", "calendar")
        ])
      ]));

      /* ---------------------------------------------------------------- 2 · stats */
      var state = statusOf();
      wrap.appendChild(el("div", { class: "grid g-4" }, [
        GD.stat({
          label: "Last issued", icon: "calendar",
          value: lastIssued(ws.key) ? fmt.dateShort(lastIssued(ws.key)) : "no record",
          unit: lastIssued(ws.key) ? fmt.date(lastIssued(ws.key)).slice(-4) : "",
          delta: [el("span", {
            text: w.assumed
              ? "Period assumed as one cadence back, to " + fmt.date(w.from)
              : "This draft covers " + fmt.date(w.from) + " onward"
          })]
        }),
        GD.stat({
          label: "Sections", icon: "layers",
          value: draft ? String(draft.doc.sections.length) : String(ws.materials.length + 1),
          unit: "in the bulletin",
          delta: [
            el("b", { text: String(ws.materials.length) }),
            el("span", { class: "nowrap", text: "materials, plus the market overview" })
          ]
        }),
        GD.stat({
          label: "Gaps declared", icon: "alert",
          value: draft ? String(draft.doc.gaps) : "—",
          unit: draft ? "sections with no data" : "generate to see",
          delta: [el("span", {
            text: draft && draft.doc.gaps
              ? "Each one says so in words rather than carrying an estimate"
              : "A feed with no print for the period is stated, never filled"
          })]
        }),
        GD.stat({
          label: "State", icon: state === "approved" ? "check" : "eye",
          hero: state === "approved",
          value: state === "none" ? "not drafted" : state === "draft" ? "draft"
            : state === "edited" ? "edited" : "approved",
          delta: [el("span", {
            text: state === "approved"
              ? "Cleared for export"
              : state === "edited" ? "Changed since it was approved — approve it again"
              : "Export is blocked until a person approves it"
          })]
        })
      ]));

      /* ------------------------------------------------------- 3 · generate / status */
      if (!draft) {
        wrap.appendChild(GD.card({
          title: "Generate this fortnight's bulletin",
          sub: "Built from the market feeds, the driver series behind every material, and " +
               "the live quote board",
          body: [
            el("div", { style: { display: "grid", gap: "15px" } }, [
              el("p", { style: { color: "var(--ink-2)", "font-size": "var(--t-sm)" },
                text: "Every figure will name its feed and the date of the print used. " +
                      "Where a feed has no print inside " + fmt.date(w.from) + " to " +
                      fmt.date(w.to) + ", the bulletin will say so instead of reaching for " +
                      "the nearest number." }),
              el("div", { class: "flex items-center gap-8", style: { "flex-wrap": "wrap" } }, [
                el("button", {
                  class: "btn btn-primary", type: "button",
                  onclick: function (ev) { generate(ev.currentTarget); }
                }, [icon("sparkle", 14, 2), "Generate draft"]),
                el("span", { style: { "font-size": "var(--t-xs)", color: "var(--ink-3)" },
                  text: GD.llm.statusLine() })
              ])
            ])
          ],
          foot: [
            el("span", { style: { color: "var(--ink-3)" },
              text: "The buying line in each section is written by a rule, not by a model — " +
                    "it is the one sentence a reader will act on, so it has to be traceable " +
                    "to the band, the budget and the driver move behind it." })
          ]
        }));
        return;
      }

      function generate(btn) {
        if (btn) { btn.setAttribute("disabled", ""); btn.textContent = "Building…"; }
        var doc = build(ws);
        var text = toMarkdown(doc);
        draft = {
          wsKey: ws.key, doc: doc, text: text, approved: false, approvedText: null,
          source: "template", note: "Written by the app from the feeds and quote history " +
            "on this desk. No model was called."
        };
        if (!GD.llm.available()) {
          redraw();
          GD.toast("Draft built — " + doc.sections.length + " sections", { icon: "check" });
          return;
        }
        GD.llm.draft({
          facts: factPack(doc), template: text, maxTokens: 12000,
          system: BULLETIN_SYSTEM,
          instruction: "Tidy the prose of the DRAFT below using only the FACTS. Keep every " +
                       "heading, every bullet and every bracketed source exactly as given."
        }).then(function (d) {
          draft.text = d.text;
          draft.source = d.source;
          draft.note = d.note;
          draft.rejected = !!d.rejected;
          redraw();
          GD.toast(d.source === "model" ? "Draft written and number-checked"
                                        : "Draft built by the app", { icon: "check" });
        });
      }

      /* ------------------------------------------------------- 4 · section status */
      wrap.appendChild(GD.card({
        title: "Sections",
        sub: "What each one found, and what it could not",
        actions: [
          draft.doc.gaps
            ? GD.chip(draft.doc.gaps + " declared gap" + (draft.doc.gaps === 1 ? "" : "s"),
                "warn", "alert")
            : GD.chip("no gaps", "good", "check")
        ],
        flush: true,
        body: [GD.table({
          cols: [
            {
              key: "title", label: "Section", tdClass: "strong", width: "180px",
              render: function (s) {
                return el("div", null, [
                  el("div", { text: s.title }),
                  el("div", {
                    style: { "font-size": "var(--t-xs)", color: "var(--ink-3)",
                             "font-weight": "400" },
                    text: s.kind === "overview" ? "market overview"
                      : (s.material.category === "packaging" ? "Packaging" : "Raw material")
                  })
                ]);
              }
            },
            {
              key: "cost", label: "Driver cost", num: true, width: "120px",
              sortValue: function (s) { return s.costNow || -1; },
              render: function (s) {
                if (s.kind === "overview" || s.costNow === null) {
                  return el("span", { class: "val-mute", text: "—" });
                }
                return GD.curUnit(s.costNow, s.material) + "/" + s.material.unit;
              }
            },
            {
              key: "move", label: "Move", num: true, width: "104px",
              sortValue: function (s) { return s.costPct === null ? -9 : s.costPct; },
              render: function (s) {
                if (s.kind === "overview" || s.costPct === null) {
                  return el("span", { class: "val-mute", text: "—" });
                }
                return el("span", { class: GD.toneFor(s.costPct),
                  text: fmt.signedPct(s.costPct, 1) });
              }
            },
            {
              key: "crop", label: "Crop read", width: "132px",
              render: function (s) {
                if (s.kind === "overview") return el("span", { class: "val-mute", text: "—" });
                if (s.cropState === "ok") return GD.chip("outlook in", "good", "check");
                if (s.cropState === "gap") return GD.chip("no data", "warn", "alert");
                if (s.cropState === "elsewhere") return GD.chip("read elsewhere", "info");
                return GD.chip("not applicable", "outline");
              }
            },
            {
              key: "quote", label: "Keenest live offer", num: true, width: "160px",
              sortValue: function (s) { return s.keen ? s.keen.price : -1; },
              render: function (s) {
                if (s.kind === "overview") return el("span", { class: "val-mute", text: "—" });
                if (!s.keen) return el("span", { class: "val-mute", text: "none on the board" });
                var m = s.material;
                var n = el("span", null, [
                  el("span", { text: GD.curUnit(s.keen.price, m) }),
                  el("span", {
                    style: { color: "var(--ink-4)", "margin-left": "6px",
                             "font-size": "var(--t-xs)" },
                    text: GD.supplierName(s.keen.supplier_id, ws)
                  })
                ]);
                return n;
              }
            },
            {
              key: "verdict", label: "vs band", width: "118px",
              sortValue: function (s) {
                return s.assessment && s.assessment.outside !== null
                  ? s.assessment.outside : -1e9;
              },
              render: function (s) {
                if (s.kind === "overview" || !s.assessment) {
                  return el("span", { class: "val-mute", text: "—" });
                }
                var a = s.assessment;
                if (!a.band.established) {
                  return el("span", { class: "val-mute", text: "no band yet" });
                }
                return GD.chip(a.position, a.position === "above" ? "bad" : "good",
                  a.position === "above" ? "up" : a.position === "below" ? "down" : "check");
              }
            }
          ],
          rows: draft.doc.sections,
          sortKey: "title",
          onRow: function (s) {
            if (s.kind === "material" && s.keen && GD.showPriceCheck) {
              GD.showPriceCheck(s.keen, ws);
            }
          },
          emptyText: "No sections."
        })],
        foot: [
          el("span", { style: { color: "var(--ink-3)" },
            text: "Click a material to open the price check behind its keenest offer." }),
          el("span", { style: { color: "var(--ink-4)" }, text: "·" }),
          el("span", { style: { color: "var(--ink-3)" },
            text: "A gap is a feed with no print inside " + fmt.date(w.from) + " to " +
                  fmt.date(w.to) + ". It is stated in the bulletin, not filled." })
        ]
      }));

      /* ------------------------------------------------------- 5 · the draft itself */
      var editor = GD.editor(draft.text, {
        label: "Market bulletin draft", minHeight: 520,
        onInput: function () {
          draft.text = editor.value;
          /* Editing an approved bulletin un-approves it: what was read is no longer what is
             on screen, and the export gate has to mean something. */
          syncGate();
        }
      });

      var approveBtn, exportPdf, exportMd, gateNote, stateChip;

      function syncGate() {
        var st = statusOf();
        var cleared = st === "approved";
        [exportPdf, exportMd].forEach(function (b) {
          if (!b) return;
          if (cleared) b.removeAttribute("disabled");
          else b.setAttribute("disabled", "");
        });
        if (approveBtn) {
          GD.clear(approveBtn);
          approveBtn.appendChild(icon(cleared ? "check" : "shield", 14, 2));
          approveBtn.appendChild(document.createTextNode(
            cleared ? "Approved" : st === "edited" ? "Approve again" : "Approve for export"));
          if (cleared) approveBtn.setAttribute("disabled", "");
          else approveBtn.removeAttribute("disabled");
        }
        if (gateNote) {
          gateNote.textContent = cleared
            ? "Approved. Export is open — and it exports exactly the text above."
            : st === "edited"
              ? "This draft has changed since it was approved. Export is closed until it is " +
                "approved again."
              : "Export is closed until somebody approves this. Nothing generated here " +
                "leaves the desk without a person reading it first.";
        }
        if (stateChip && stateChip.parentNode) {
          var next = cleared ? GD.chip("approved", "good", "check")
            : st === "edited" ? GD.chip("edited since approval", "warn", "alert")
            : GD.chip("draft — not approved", "outline", "eye");
          stateChip.parentNode.replaceChild(next, stateChip);
          stateChip = next;
        }
      }

      stateChip = GD.chip("draft — not approved", "outline", "eye");
      approveBtn = el("button", {
        class: "btn btn-accent", type: "button",
        onclick: function () {
          draft.approved = true;
          draft.approvedText = draft.text;
          setLastIssued(ws.key, GD.wsAsOf());
          syncGate();
          GD.toast("Bulletin approved — export is open", { icon: "check" });
        }
      }, [icon("shield", 14, 2), "Approve for export"]);

      exportPdf = el("button", {
        class: "btn", type: "button", disabled: true,
        onclick: function () {
          GD.printDoc("Market bulletin — " + ws.name + " — " + fmt.date(w.to),
            printable(draft.doc, draft.text));
        }
      }, [icon("file", 14, 1.75), "Export PDF"]);

      exportMd = el("button", {
        class: "btn", type: "button", disabled: true,
        onclick: function () {
          GD.downloadMarkdown("market-bulletin-" + ws.key + "-" + w.to, draft.text);
        }
      }, [icon("download", 14, 1.75), "Download .md"]);

      gateNote = el("p", {
        style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" }
      });

      var draftCard = GD.card({
        title: "The draft",
        sub: "Edit it, then approve it. Export is blocked until you do.",
        actions: [
          stateChip,
          draft.source === "model" ? GD.chip(GD.llm.model, "accent", "sparkle")
            : draft.rejected ? GD.chip("model draft rejected", "warn", "alert")
            : GD.chip("written by the app", "outline", "layers")
        ],
        body: [
          editor,
          el("p", {
            style: { "font-size": "var(--t-xs)", color: "var(--ink-3)", "margin-top": "9px" },
            text: draft.note
          }),
          gateNote
        ],
        foot: [
          approveBtn,
          exportPdf,
          exportMd,
          el("button", {
            class: "btn btn-ghost", type: "button",
            onclick: function () { GD.copyText(editor.value, "Bulletin"); }
          }, [icon("file", 14, 1.75), "Copy"]),
          el("button", {
            class: "btn btn-ghost", type: "button",
            onclick: function () { draft = null; redraw(); }
          }, [icon("x", 14, 1.75), "Discard"]),
          el("span", { style: { flex: "1", "min-width": "0" } }),
          el("span", { class: "nowrap", style: { color: "var(--ink-3)" },
            text: "A person approves. The desk never sends." })
        ]
      });
      wrap.appendChild(draftCard);
      syncGate();
    }
  });

})();
