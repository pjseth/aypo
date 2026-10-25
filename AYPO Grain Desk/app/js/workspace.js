/* Grain Desk — the workspace.

   A workspace is one customer's book: their materials, their suppliers, their quotes, their
   home currency. This dashboard shows exactly one of them — `WS.ui_workspace` names it — and
   every surface reads it through `GD.ws()` rather than reaching into the payload.

   The wheat desk this product started as is still in `ws.js` as a second workspace, but it is
   not on screen and there is no switcher. It is kept because `tools/lib_tests.js` uses it to
   assert that the generic price model reproduces, to the cent, the four-way split the
   original Quote Board computed — a cross-check worth keeping and worth nothing on screen.

   The rest of this file is currency formatting for the currencies the original `fmt` never
   needed, and the precision rule that goes with them.

   Classic script, no modules. Loads after core.js and before any surface. */
(function () {
  "use strict";

  var W = window.WS || { workspaces: [], fx: {} };
  var active = null;

  function byKey(k) {
    for (var i = 0; i < W.workspaces.length; i++) {
      if (W.workspaces[i].key === k) return W.workspaces[i];
    }
    return null;
  }

  /* `?ws=<key>` overrides, which is how the wheat fixture can be put on screen when somebody
     is checking the price model against the original Quote Board. Nothing in the product
     uses it, and there is nothing to persist: one dashboard, one book. */
  function fromQuery() {
    var m = String(location.search || "").match(/[?&]ws=([A-Za-z0-9_-]+)/);
    return m ? m[1] : null;
  }

  active = byKey(fromQuery()) || byKey(W.ui_workspace) || W.workspaces[0] || null;

  GD.wsAll = function () { return W.workspaces; };
  GD.wsKey = function () { return active ? active.key : null; };
  GD.ws = function () { return active; };
  GD.wsAsOf = function () { return W.as_of || "2026-10-13"; };

  /* The wheat fixture carries no instruments of its own — it borrows the original desk's,
     which are only loaded under the test harness. On screen there is always a list. */
  GD.wsInstruments = function () {
    if (!active) return [];
    if (active.instruments) return active.instruments;
    return (window.DESK && window.DESK.instruments) || [];
  };

  /* --------------------------------------------------------------------------- lookups */

  function find(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }
  GD.material = function (id, ws) { return find(((ws || active) || {}).materials || [], id); };
  GD.supplier = function (id, ws) { return find(((ws || active) || {}).suppliers || [], id); };
  GD.materialName = function (id, ws) { var m = GD.material(id, ws); return m ? m.name : id; };
  GD.supplierName = function (id, ws) { var s = GD.supplier(id, ws); return s ? s.name : id; };

  /* --------------------------------------------------------------------------------- fx
     One table, mocked, declared once in the payload. Every conversion on every screen comes
     through here so there is exactly one rate to argue with. */

  GD.fxTable = function () { return W.fx || {}; };
  GD.fxNote = function () { return W.fx_note || ""; };
  GD.fx = function (from, to) {
    if (from === to) return 1;
    var row = (W.fx || {})[from];
    if (row && typeof row[to] === "number") return row[to];
    /* One hop through USD covers every pair the table does not state directly. */
    var a = (W.fx || {})[from], b = (W.fx || {}).USD;
    if (a && typeof a.USD === "number" && b && typeof b[to] === "number") return a.USD * b[to];
    return null;
  };

  /* ------------------------------------------------------------------ currency display
     `fmt` knows dollars and baht because that is all the wheat desk needed. These five are
     the ones the materials actually price in. */

  var SYM = { USD: "$", PHP: "₱", EUR: "€", MYR: "RM", THB: "฿", USc: "¢" };

  GD.curSym = function (code) { return SYM[code] || (code + " "); };

  GD.cur = function (v, code, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var d = dp === undefined ? (code === "PHP" ? 0 : 2) : dp;
    return GD.curSym(code) + GD.fmt.n(v, d);
  };

  /* "₱1.24M" / "₱275k" — the unit a category total is read in. */
  GD.curM = function (v, code) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var a = Math.abs(v), sign = v < 0 ? "−" : "", s = GD.curSym(code);
    if (a >= 1e9) return sign + s + (a / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return sign + s + (a / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return sign + s + Math.round(a / 1e3) + "k";
    return sign + s + a.toFixed(0);
  };

  GD.curSigned = function (v, code, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + GD.cur(Math.abs(v), code, dp);
  };

  /* How many decimals a figure about a material wants. The decision belongs to the material,
     not to the individual number: a band that happens to be ₱872 wide should not print two
     decimal places beside a price of ₱269,405 that prints none. Peso-per-tonne is a
     five-figure number and wants none; peso-per-kilo is a three-figure one and wants two. The
     scale is read off what the material is worth, so there is no unit table to maintain. */
  var dpCache = {};
  GD.unitDp = function (m) {
    if (!m) return 2;
    var key = (active ? active.key : "?") + ":" + m.id;
    if (dpCache[key] === undefined) {
      var scale = m.budget_price;
      if (!scale && active) {
        var q = (active.quotes || []).filter(function (x) { return x.material_id === m.id; })[0];
        scale = q ? q.price : null;
      }
      dpCache[key] = Math.abs(scale || 1000) >= 1000 ? 0 : 2;
    }
    return dpCache[key];
  };

  /* A figure in the material's own currency, at the material's own precision. */
  GD.curUnit = function (v, m) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return GD.cur(v, m.currency, GD.unitDp(m));
  };
  GD.curUnitSigned = function (v, m) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + GD.curUnit(Math.abs(v), m);
  };

  /* ------------------------------------------------------------------------ demo label
     The URC workspace is invented from end to end. Every surface that renders it says so in
     the same words rather than each one inventing its own disclaimer. */

  GD.demoChip = function () {
    if (!active || !active.demo) return null;
    var c = GD.chip("Demo data", "warn", "alert");
    GD.tipOn(c, "<b>Every figure in this workspace is invented</b><div class='tip-mute'>" +
      "Suppliers, materials, prices, spend lines and index levels are all fictional, " +
      "generated for this demonstration. Nothing here came from a customer.</div>");
    return c;
  };

})();
