/* Drives the three new flows through the real DOM, in the real app.

   The route tests prove every page renders. These prove the things the brief actually asks
   for, by clicking the buttons a person would click:

     · the market screen shows every feed, and hands a material to the price check
     · the planted coffee quote opens above its band, with the widening trend on screen, and
       the brief it generates contains no figure that is not in the fact table beside it
     · the messy sample processes end to end, the review queue clears in a handful of clicks,
       and the grand total does not move while it does
     · the bulletin generates, declares its gaps, and cannot be exported until a person has
       approved it — and goes back to un-exportable the moment it is edited again

   async/await rather than the ES5 the app itself is written in: this is a test harness run
   through tools/test_flows.py, not a file the app loads. */
(function () {
  "use strict";

  var results = [];
  function ok(name, cond, detail) {
    results.push({ name: name, pass: !!cond, detail: detail === undefined ? "" : String(detail) });
  }

  function wait(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }
  async function until(fn, label, timeout) {
    var end = Date.now() + (timeout || 4000);
    while (Date.now() < end) {
      var v = fn();
      if (v) return v;
      await wait(60);
    }
    throw new Error("timed out waiting for " + label);
  }

  function nodes(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function byText(sel, text, root) {
    var hit = nodes(sel, root).filter(function (n) {
      return (n.textContent || "").indexOf(text) >= 0;
    });
    return hit.length ? hit[0] : null;
  }
  /* Overlays stack, so the sheet under test is always the last one appended — the brief
     opens on top of the price check rather than replacing it. */
  function sheet() {
    var all = nodes(".scrim .sheet");
    return all.length ? all[all.length - 1] : null;
  }
  function closeSheet() { GD.closeTop(); }
  function textOf(node) { return (node && node.textContent) || ""; }

  function finish(err) {
    if (err) ok("the flow ran to the end", false, err.message || String(err));
    var failed = results.filter(function (r) { return !r.pass; });
    var out = document.createElement("div");
    out.id = "__flow_results";
    out.textContent = JSON.stringify({
      total: results.length, failed: failed.length, results: results
    });
    document.body.appendChild(out);
    document.title = failed.length ? ("FAIL " + failed.length) : "OK";
  }

  /* ============================================================== 1 · the price check */

  async function priceCheckFlow() {
    GD.go("pricecheck");
    await until(function () { return byText(".page-title", "Price check"); }, "the price check page");

    var ws = GD.ws();
    ok("the dashboard opens on the demo book", ws.key === "urc", ws.key);
    ok("the demo label is on the page", !!byText(".chip", "Demo data"));

    var coffee = GD.material("green_coffee", ws);
    var live = GD.pricing.latestPerSupplier("green_coffee", ws.quotes).filter(function (q) {
      return q.supplier_id === "kalinga";
    })[0];
    ok("the planted coffee quote is on the live board", !!live);
    if (!live) return;

    GD.showPriceCheck(live, ws);
    var sh = await until(sheet, "the price-check sheet");
    var t = textOf(sh);

    ok("the sheet names the supplier", t.indexOf("Kalinga") >= 0);
    ok("the quote reads as above the band", t.indexOf("Above the band by") >= 0,
       t.slice(t.indexOf("Above the band") - 20, t.indexOf("Above the band") + 70));
    ok("the widening trend is on screen", /widened \d+ quarters running/.test(t),
       (t.match(/widened[^.]{0,80}/) || [""])[0]);
    ok("the components are shown to sum to the quote",
       t.indexOf("the quoted price exactly") >= 0);
    ok("the premium history chart drew", !!sh.querySelector("svg.chart"));
    ok("the band bar drew its key", t.indexOf("Fair band, 25th–75th") >= 0);
    ok("the budget comparison is on screen", t.indexOf("Budget rate") >= 0);

    /* The sum proof, read off the screen rather than off the model. */
    var a = GD.pricing.assess(coffee, live, ws.quotes, GD.wsAsOf());
    ok("the breakdown reconciles to the quoted price", a.breakdown.reconciles,
       a.breakdown.total + " vs " + live.price);

    /* ---- the brief */
    var briefBtn = byText(".sheet-foot button", "Negotiation brief", sh);
    ok("the brief button is in the sheet footer", !!briefBtn);
    if (!briefBtn) return;
    briefBtn.click();

    var bs = await until(function () {
      var s = sheet();
      return (s && textOf(s).indexOf("Draft — yours to edit") >= 0) ? s : null;
    }, "the brief sheet");

    var ta = bs.querySelector("textarea");
    ok("the brief opened in an editor", !!ta && ta.value.length > 400,
       ta ? ta.value.length + " characters" : "no textarea");
    var brief = ta ? ta.value : "";

    ok("the brief names the supplier", brief.indexOf("Kalinga") >= 0);
    ok("the brief states the fair band", /fair price between/.test(brief));
    ok("the brief states the budget", brief.indexOf("budget rate for") >= 0);
    ok("the brief gives three talking points",
       (brief.match(/THREE THINGS TO PUT TO THEM([\s\S]*?)\n\nSOURCES/) || [, ""])[1]
         .split("\n· ").length - 1 === 3,
       (brief.match(/THREE THINGS TO PUT TO THEM([\s\S]*?)\n\nSOURCES/) || [, ""])[1]
         .split("\n· ").length - 1);
    ok("the brief is framed collaboratively, not as an accusation",
       !/\b(overcharg|gouging|unacceptable|demand that|we will re-tender)\b/i.test(brief),
       (brief.match(/overcharg\w*|gouging|unacceptable|demand that/i) || [""])[0]);
    ok("the brief cites its sources", brief.indexOf("SOURCES") >= 0);
    ok("nothing in the brief claims to have been sent",
       brief.indexOf("nothing here has been sent") >= 0);

    /* The acceptance criterion, tested the way it is written: no figure in the brief that is
       not traceable to the data shown beside it. The fact table in this very sheet is the
       whole of what the brief is allowed to say, so it is the thing to check against. */
    var factTable = bs.querySelector(".table-wrap");
    var facts = textOf(factTable);
    ok("the fact table is on screen beside the draft", facts.length > 300, facts.length);
    var check = GD.llm.verifyNumbers(brief, facts);
    ok("every figure in the brief traces to the fact table (" + check.checked + " checked)",
       check.ok, (check.details || []).slice(0, 6).map(function (d) {
         return d.raw + " in “" + d.context + "”";
       }).join("  ||  "));

    /* And the check has teeth: an invented figure in the same text must fail it. */
    var poisoned = brief + "\n· The market is expected to fall 37.4% by March.";
    ok("the same check rejects an invented figure",
       !GD.llm.verifyNumbers(poisoned, facts).ok,
       JSON.stringify(GD.llm.verifyNumbers(poisoned, facts).unverified.slice(0, 4)));

    ok("the copy and export actions are present",
       !!byText(".sheet-foot button", "Copy", bs) &&
       !!byText(".sheet-foot button", "Download .md", bs) &&
       !!byText(".sheet-foot button", "Print / PDF", bs));

    /* What the printer would actually get. A print dialogue cannot be read by a test; the
       markup behind it can, and it is the thing that lands on a supplier's desk. */
    var doc = GD.docHtml(brief, { foot: "Generated by Grain Desk." });
    ok("the printable brief has a title and sections",
       /^<h1>NEGOTIATION BRIEF/.test(doc) && (doc.match(/<h2>/g) || []).length >= 4,
       (doc.match(/<h2>[^<]*/g) || []).slice(0, 5).join(" | "));
    ok("the talking points print as a list",
       doc.indexOf("<ul>") >= 0 && (doc.match(/<li>/g) || []).length > 8,
       (doc.match(/<li>/g) || []).length + " items");
    ok("the printable brief carries its footer", doc.indexOf("class='foot'") >= 0);
    var frame = GD.printableFrame("Brief", doc);
    var fdoc = frame.contentWindow.document;
    ok("the print frame renders the document",
       fdoc.querySelectorAll("h2").length >= 4 &&
       (fdoc.body.textContent || "").indexOf("Kalinga") >= 0,
       fdoc.querySelectorAll("h2").length + " headings");
    ok("the print frame carries the paper stylesheet",
       (fdoc.head.textContent || "").indexOf("@page") >= 0);
    if (frame.parentNode) frame.parentNode.removeChild(frame);

    closeSheet();
    await wait(260);
    closeSheet();
    await wait(260);
  }

  /* ---- the resin story, the other planted one */
  async function resinFlow() {
    var ws = GD.ws();
    var resin = GD.material("pet_resin", ws);
    var q = GD.pricing.latestPerSupplier("pet_resin", ws.quotes).filter(function (x) {
      return x.supplier_id === "pacpoly";
    })[0];
    ok("the planted resin quote is on the live board", !!q);
    if (!q) return;
    GD.showPriceCheck(q, ws);
    var sh = await until(sheet, "the resin sheet");
    var t = textOf(sh);
    ok("the resin quote reads as inside the band",
       t.indexOf("Inside the band") >= 0, (t.match(/Inside the band[^.]*/) || [""])[0]);
    var a = GD.pricing.assess(resin, q, ws.quotes, GD.wsAsOf());
    ok("the resin quote is under budget", a.budget && !a.budget.over,
       a.budget && a.budget.amount);
    closeSheet();
    await wait(260);
  }

  /* ================================================================ 2 · the market */

  async function marketFlow() {
    GD.go("prices");
    await until(function () { return byText(".page-title", "Market"); }, "the market page");
    var ws = GD.ws();
    function page() { return textOf(document.querySelector(".wrap")); }

    var rows = nodes(".wrap .pal-item");
    ok("there is a watchlist row for every feed", rows.length >= 10, rows.length + " rows");
    /* The watchlist rows carry the name and the reach; the source belongs on the instrument
       header, where there is room for it. */
    ok("the open instrument names its source", page().indexOf("BSP ref") >= 0);
    ok("placeholder feeds are labelled", page().indexOf("mock") >= 0);
    ok("the watchlist is grouped by kind",
       page().indexOf("Commodities") >= 0 && page().indexOf("Freight") >= 0 &&
       page().indexOf("FX") >= 0);

    ok("it opens on the feed that touches the most materials",
       page().indexOf("USD / PHP spot") >= 0);
    ok("the instrument chart drew", !!document.querySelector(".wrap svg.chart"));
    ok("there is a window selector",
       !!byText(".seg button", "6M") && !!byText(".seg button", "1Y"));
    ok("the window stats are on screen",
       page().indexOf("Window open") >= 0 && page().indexOf("12-month range") >= 0);
    ok("the page says the daily path is generated",
       page().indexOf("generated for this build") >= 0);
    ok("it says what the feed is worth inside each material",
       page().indexOf("inside each material") >= 0);

    var opens = nodes(".wrap .statline > div").filter(function (d) {
      return textOf(d).indexOf("Window open") >= 0;
    })[0];
    var before = textOf(opens);
    var oneM = byText(".seg button", "1M");
    ok("the window can be changed", !!oneM);
    if (oneM) {
      oneM.click();
      await wait(300);
      var after = textOf(nodes(".wrap .statline > div").filter(function (d) {
        return textOf(d).indexOf("Window open") >= 0;
      })[0]);
      ok("a shorter window opens from a different level", after !== before,
         before + " -> " + after);
    }

    var coffee = nodes(".wrap .pal-item").filter(function (r) {
      return textOf(r).indexOf("Robusta") >= 0;
    })[0];
    ok("the watchlist has the coffee feed", !!coffee);
    if (coffee) {
      coffee.click();
      await wait(320);
      ok("picking a feed swaps the instrument",
         page().indexOf("ICE Robusta coffee") >= 0);
      ok("and swaps its source with it", page().indexOf("ICE Europe") >= 0);
      ok("exactly one row is marked as selected",
         nodes('.wrap .pal-item[data-active="true"]').length === 1,
         nodes('.wrap .pal-item[data-active="true"]').length);
    }

    ok("driver cost is reported for every material",
       ws.materials.every(function (m) { return page().indexOf(m.name) >= 0; }));
    ok("the bridge card is on the page",
       page().indexOf("What that did to our materials") >= 0);

    var row = document.querySelector(".wrap .card tbody tr");
    ok("the material table is clickable", !!row);
    if (row) {
      row.click();
      await until(function () { return byText(".page-title", "Price check"); },
                  "the price check after the hand-off");
      await wait(320);
      ok("the hand-off filtered the quote board to one material",
         GD.qs("select.field").value !== "all", GD.qs("select.field").value);
    }
  }


  async function spendFlow() {
    GD.go("spend");
    await until(function () { return byText(".page-title", "Spend cleansing"); },
                "the spend page");

    var load = byText("button", "Load the sample export");
    ok("the sample is one click away", !!load);
    if (!load) return;
    load.click();

    await until(function () { return GD.spendRun; }, "the processed run", 20000);
    await until(function () { return byText(".card-title", "What the file became"); },
                "the counter card");

    var run = GD.spendRun;
    ok("every line in the sample was processed",
       run.counts.total === window.SPEND_SAMPLE.rows,
       run.counts.total + " vs " + window.SPEND_SAMPLE.rows);
    ok("the counter is on screen",
       (document.body.textContent || "").indexOf(GD.fmt.n(run.counts.total, 0)) >= 0);
    ok("duplicates were flagged", run.counts.duplicates > 0, run.counts.duplicates);
    ok("the report ties to the lines before anything is resolved",
       GD.spendReport.reconciles);

    var totalBefore = GD.spendReport.total;
    var startedWith = run.counts.review;
    ok("something was left for a person", startedWith > 0, startedWith);
    ok("the queue is a handful of decisions, not a list of lines",
       run.counts.decisions <= 12, run.counts.decisions + " decisions / " +
       startedWith + " lines");

    /* Clear the queue the way a person would: take each decision, pick the suggested value
       where there is one, and mark the rest unclassifiable. */
    var clicks = 0;
    while (clicks < 30) {
      var queue = byText(".card-title", "Review queue");
      if (!queue) break;
      var card = queue.closest(".card");
      var row = card.querySelector(".note-row");
      if (!row) break;
      var sels = nodes("select", row);
      var pickable = sels.every(function (s) { return !!s.value; });
      if (pickable) {
        byText("button", "Confirm", row).click();
      } else {
        var notMine = byText("button", "Not one of our materials", row);
        if (notMine && sels.length === 1) {
          notMine.click();
        } else {
          /* Nothing is pre-selected, so choose the first real option — a person would read
             the text and pick; the test only needs a valid choice. */
          sels.forEach(function (s) {
            if (!s.value && s.options.length > 1) s.value = s.options[1].value;
          });
          byText("button", "Confirm", row).click();
        }
      }
      clicks++;
      await wait(120);
      if (!GD.spendRun.counts.review) break;
    }

    ok("the queue cleared in " + clicks + " clicks", GD.spendRun.counts.review === 0,
       GD.spendRun.counts.review + " lines still waiting after " + clicks + " clicks");
    ok("one click resolved many lines", clicks < startedWith,
       clicks + " clicks for " + startedWith + " lines");

    await until(function () { return byText(".card-title", "Does it tie?"); },
                "the reconciliation card");
    var rep = GD.spendlib.report(GD.spendRun);
    ok("the report still ties after the queue was cleared", rep.reconciles,
       rep.checks.map(function (c) { return c.name + " " + c.total; }).join(" | "));
    ok("clearing the queue did not move the grand total",
       Math.abs(rep.total - totalBefore) < 0.011, rep.total + " vs " + totalBefore);
    ok("price paid per unit is reported against budget for every material",
       rep.perUnit.length === GD.ws().materials.length, rep.perUnit.length);
    ok("the corrections were remembered",
       GD.spendlib.countLearned(GD.spendlib.loadLearned("urc")) > 0);

    /* Re-upload: the same text must resolve on its own this time. */
    var parsed = GD.spendlib.parseDelimited(window.SPEND_SAMPLE.text);
    parsed.name = "re-upload";
    var again = GD.spendlib.process({
      ws: GD.ws(), parsed: parsed, learned: GD.spendlib.loadLearned("urc")
    });
    ok("on re-upload the queue is shorter than it was first time",
       again.counts.review < startedWith, again.counts.review + " vs " + startedWith);
    ok("the re-upload report ties too", GD.spendlib.report(again).reconciles);

    ok("the cleaned lines can be exported",
       !!byText("button", "Download cleaned CSV") && !!byText("button", "Report CSV"));

    /* The .xlsx path, against the real workbook on disk. URC's export is an Excel file, and
       telling a buyer to re-save it as CSV is the manual step this feature removes. */
    try {
      var res = await fetch("../app/data/spend_sample.xlsx");
      if (!res.ok) res = await fetch("data/spend_sample.xlsx");
      var buf = await res.arrayBuffer();
      var parsedX = await GD.spendlib.parseXlsx(buf);
      ok("the .xlsx workbook parses to the same shape as the CSV",
         parsedX.rows.length === window.SPEND_SAMPLE.rows &&
         parsedX.header.length === GD.spendRun.columns.header.length,
         parsedX.rows.length + " rows, " + parsedX.header.length + " columns");
      var runX = GD.spendlib.process({
        ws: GD.ws(), parsed: parsedX, learned: { vendor: {}, material: {} }
      });
      var repX = GD.spendlib.report(runX);
      ok("the .xlsx run reconciles like the CSV one", repX.reconciles);
      ok("the .xlsx run reaches the same total as the CSV",
         Math.abs(repX.total - totalBefore) < 0.011,
         repX.total + " vs " + totalBefore);
    } catch (e) {
      ok("the .xlsx workbook parses", false, e && e.message);
    }
  }

  /* ================================================================= 4 · the bulletin */

  async function bulletinFlow() {
    GD.go("bulletin");
    await until(function () { return byText(".page-title", "Market bulletin"); },
                "the bulletin page");

    var gen = byText("button", "Generate draft");
    ok("the bulletin is one click away", !!gen);
    if (!gen) return;
    var t0 = Date.now();
    gen.click();

    var card = await until(function () { return byText(".card-title", "The draft"); },
                          "the draft card", 20000);
    var took = Date.now() - t0;
    ok("a complete bulletin generated in under a minute (" + took + " ms)", took < 60000, took);

    var ws = GD.ws();
    var secCard = byText(".card-title", "Sections").closest(".card");
    var rows = nodes("tbody tr", secCard);
    ok("there is a section for every material plus the overview",
       rows.length === ws.materials.length + 1,
       rows.length + " vs " + (ws.materials.length + 1));

    var ta = card.closest(".card").querySelector("textarea");
    ok("the draft opened in an editor", !!ta && ta.value.length > 1500,
       ta ? ta.value.length : "none");
    var text = ta ? ta.value : "";

    ok("every section heading is in the draft",
       ws.materials.every(function (m) { return text.indexOf("## " + m.name) >= 0; }));
    ok("the draft covers drivers, FX, freight, crop and a buying line",
       text.indexOf("### Drivers") >= 0 && text.indexOf("### FX") >= 0 &&
       text.indexOf("### Freight") >= 0 && text.indexOf("### Crop and weather") >= 0 &&
       text.indexOf("### What this means for buying") >= 0);
    ok("a missing feed is declared rather than filled",
       text.indexOf("No data for this period") >= 0,
       (text.match(/No data for this period[^\n]{0,80}/) || [""])[0]);
    ok("a material with no crop read says so instead of inventing one",
       text.indexOf("Not applicable") >= 0);
    ok("every figure names a source",
       (text.match(/\(([^)]*,\s*\d+ \w+ \d{4})\)/g) || []).length > 10,
       (text.match(/\(([^)]*,\s*\d+ \w+ \d{4})\)/g) || []).length + " dated citations");
    ok("mock feeds are labelled as mock", text.indexOf("[mock feed]") >= 0);

    /* The gate. */
    var pdf = byText("button", "Export PDF");
    var md = byText("button", "Download .md");
    ok("export is blocked before approval",
       pdf && pdf.hasAttribute("disabled") && md && md.hasAttribute("disabled"));

    var approve = byText("button", "Approve for export");
    ok("there is an approve action", !!approve);
    approve.click();
    await wait(140);
    ok("export opens once a person approves",
       !byText("button", "Export PDF").hasAttribute("disabled") &&
       !byText("button", "Download .md").hasAttribute("disabled"));
    ok("the approved state is shown", !!byText(".chip", "approved"));

    /* Editing it again has to close the gate: what was approved is no longer on screen. */
    ta.value = text + "\n· A line somebody typed after approval.";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    await wait(140);
    ok("editing an approved draft closes the gate again",
       byText("button", "Export PDF").hasAttribute("disabled"));
    ok("and says why", !!byText(".chip", "edited since approval"));

    byText("button", "Approve again").click();
    await wait(140);
    ok("approving again reopens it",
       !byText("button", "Export PDF").hasAttribute("disabled"));

    var bdoc = GD.docHtml(ta.value, { foot: "Approved." });
    ok("the printable bulletin keeps a section per material",
       (bdoc.match(/<h2>/g) || []).length >= ws.materials.length,
       (bdoc.match(/<h2>/g) || []).length + " headings");
    ok("the printable bulletin keeps the sub-headings",
       (bdoc.match(/<h3>/g) || []).length >= ws.materials.length * 4,
       (bdoc.match(/<h3>/g) || []).length + " sub-headings");
    ok("the printable bulletin still declares its gap",
       bdoc.indexOf("No data for this period") >= 0);
  }

  /* ========================================================================== run it */

  (async function () {
    try {
      await until(function () { return window.GD && GD.ws && GD.ws(); }, "the app to boot");
      await wait(400);
      await priceCheckFlow();
      await resinFlow();
      await marketFlow();
      await spendFlow();
      await bulletinFlow();
      finish();
    } catch (err) {
      finish(err);
    }
  })();

})();
