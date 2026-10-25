/* Grain Desk — getting a document out of the browser.

   Three ways out, used by the negotiation brief, the bulletin and the spend report. All of
   them are local: nothing is uploaded, nothing is mailed, and the only thing that leaves the
   page is a file the person asked for.

   "Export to PDF" is the print dialogue. The app has no server and no PDF library, and the
   browser's own print-to-PDF produces a better-looking document than a hand-rolled renderer
   would — so the honest implementation is to lay the document out for paper and hand it to
   the printer. It goes through a hidden iframe rather than a popup so a blocker cannot
   silently eat it. */
(function () {
  "use strict";

  GD.copyText = function (text, label) {
    var ok = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        var p = navigator.clipboard.writeText(text);
        if (p && p.catch) p.catch(function () { /* denied — fall through to the toast */ });
        ok = true;
      } else if (document.execCommand) {
        var ta = GD.el("textarea", { style: { position: "fixed", opacity: "0", top: "0" } });
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      }
    } catch (e) { ok = false; }
    GD.toast(ok ? (label || "Copied") + " copied" : "Could not copy — select the text and press Ctrl+C",
      { icon: ok ? "check" : "file" });
    return ok;
  };

  GD.downloadText = function (filename, text, mime) {
    try {
      var blob = new Blob([text], { type: (mime || "text/plain") + ";charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var a = GD.el("a", { href: url, download: filename, style: { display: "none" } });
      document.body.appendChild(a);
      a.click();
      setTimeout(function () {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 400);
      GD.toast("Saved " + filename, { icon: "download" });
      return true;
    } catch (e) {
      GD.toast("This browser blocked the download", { icon: "alert" });
      return false;
    }
  };

  /* Print styles for a generated document. Deliberately plain: a brief that lands on a
     supplier's desk, or in a board pack, should look like a document and not like a
     screenshot of a dashboard. */
  var PRINT_CSS = [
    "@page { size: A4; margin: 18mm 16mm; }",
    "* { box-sizing: border-box; }",
    "body { margin: 0; font: 11pt/1.5 'Inter', -apple-system, 'Segoe UI', system-ui, sans-serif;",
    "       color: #1a1d23; -webkit-print-color-adjust: exact; print-color-adjust: exact; }",
    "h1 { font-size: 17pt; letter-spacing: -0.02em; margin: 0 0 2mm; }",
    "h2 { font-size: 11.5pt; margin: 7mm 0 2mm; padding-bottom: 1.5mm;",
    "     border-bottom: 1px solid #e2e5ec; }",
    "h3 { font-size: 10.5pt; margin: 5mm 0 1.5mm; }",
    ".sub { color: #868d99; font-size: 9.5pt; margin: 0 0 5mm; }",
    "p { margin: 0 0 2.5mm; }",
    "ul { margin: 0 0 3mm; padding-left: 5mm; }",
    "li { margin-bottom: 1.5mm; }",
    "pre { white-space: pre-wrap; font: inherit; margin: 0; }",
    "table { border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 0 0 4mm; }",
    "th, td { text-align: left; padding: 1.6mm 2mm; border-bottom: 1px solid #e2e5ec; }",
    "th { color: #868d99; font-weight: 600; }",
    "td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }",
    ".foot { margin-top: 8mm; padding-top: 3mm; border-top: 1px solid #e2e5ec;",
    "        color: #868d99; font-size: 8.5pt; }",
    ".tag { display: inline-block; border: 1px solid #d2d7e1; border-radius: 4px;",
    "       padding: 0.6mm 2mm; font-size: 8pt; color: #4d535d; }"
  ].join("\n");

  /* `bodyHtml` is markup this app authored — never data interpolated raw. Callers escape
     with GD.esc on the way in. */
  GD.printDoc = function (title, bodyHtml) {
    var frame = GD.el("iframe", {
      "aria-hidden": "true",
      style: { position: "fixed", right: "0", bottom: "0", width: "1px", height: "1px",
               border: "0", opacity: "0" }
    });
    document.body.appendChild(frame);
    var doc = frame.contentWindow.document;
    doc.open();
    doc.write("<!doctype html><html><head><meta charset='utf-8'><title>" + GD.esc(title) +
      "</title><style>" + PRINT_CSS + "</style></head><body>" + bodyHtml + "</body></html>");
    doc.close();
    var done = false;
    function go() {
      if (done) return;
      done = true;
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      } catch (e) {
        GD.toast("This browser blocked the print dialogue", { icon: "alert" });
      }
      /* Long enough for the dialogue to have taken its snapshot. */
      setTimeout(function () {
        if (frame.parentNode) frame.parentNode.removeChild(frame);
      }, 60000);
    }
    if (frame.contentWindow.document.readyState === "complete") setTimeout(go, 120);
    else frame.addEventListener("load", function () { setTimeout(go, 120); });
    setTimeout(go, 900);
    return frame;
  };

  /* Markdown is the lingua franca for a draft somebody is going to paste into Outlook or
     Teams, so that is the download format for anything written as prose. */
  GD.downloadMarkdown = function (name, text) {
    GD.downloadText(name.replace(/[^\w.-]+/g, "_") + ".md", text, "text/markdown");
  };

  GD.downloadCsv = function (name, rows) {
    var out = rows.map(function (r) {
      return r.map(function (c) {
        var s = c === null || c === undefined ? "" : String(c);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      }).join(",");
    }).join("\n");
    GD.downloadText(name.replace(/[^\w.-]+/g, "_") + ".csv", out, "text/csv");
  };

  /* An editable document pane: a textarea that grows with its content, used by both the
     brief and the bulletin. Returns the node with a `.value` getter the caller can read. */
  GD.editor = function (text, opts) {
    opts = opts || {};
    var ta = GD.el("textarea", {
      "aria-label": opts.label || "Draft",
      spellcheck: "true",
      style: {
        width: "100%", "min-height": (opts.minHeight || 320) + "px", padding: "16px 18px",
        "font-family": "var(--sans)", "font-size": "var(--t-sm)", "line-height": "1.65",
        border: "1px solid var(--border)", "border-radius": "var(--r-inner)",
        background: "var(--surface)", color: "var(--ink)", resize: "vertical",
        "white-space": "pre-wrap"
      }
    });
    ta.value = text;
    if (opts.onInput) ta.addEventListener("input", opts.onInput);
    return ta;
  };

})();
