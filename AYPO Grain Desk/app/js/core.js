/* Grain Desk — core: DOM helpers, icons, formatting, charts, overlays, router.
   Classic script, one global `GD`. No modules and no fetch(): the app has to run
   from file:// as well as from a host, and both of those break ES modules /
   same-origin JSON loading. */
(function () {
  "use strict";

  var GD = {};
  window.GD = GD;

  /* ------------------------------------------------------------------ DOM */

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k === "text") node.textContent = v;
        else if (k === "style" && typeof v === "object") {
          Object.keys(v).forEach(function (p) { node.style.setProperty(p, v[p]); });
        } else if (k.slice(0, 2) === "on" && typeof v === "function") {
          node.addEventListener(k.slice(2).toLowerCase(), v);
        } else if (v === true) node.setAttribute(k, "");
        else node.setAttribute(k, v);
      });
    }
    append(node, kids);
    return node;
  }

  function append(node, kids) {
    if (kids === null || kids === undefined || kids === false) return;
    if (Array.isArray(kids)) { kids.forEach(function (k) { append(node, k); }); return; }
    if (kids instanceof Node) { node.appendChild(kids); return; }
    node.appendChild(document.createTextNode(String(kids)));
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  GD.el = el; GD.clear = clear; GD.qs = qs; GD.qsa = qsa;

  /* ---------------------------------------------------------------- icons
     One 24x24 outline set, stroke 1.5 beside regular text and 2 beside semibold,
     coloured by `currentColor` so every state is CSS. */

  var PATHS = {
    brief:    "M4 5h16M4 10h10M4 15h16M4 20h7",
    quotes:   "M4 5h16v11H8l-4 4V5z",
    position: "M3 17l6-6 4 4 7-8M21 7h-5m5 0v5",
    cargo:    "M3 17l1.6-5.4a1 1 0 01.96-.72h12.88a1 1 0 01.96.72L21 17M3 17h18M3 17v3h18v-3M8 11V6h8v5",
    market:   "M3 20V10M9 20V4M15 20v-7M21 20V8",
    margin:   "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
    savings:  "M12 2v20M17 6H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6",
    search:   "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3",
    bell:     "M18 8a6 6 0 10-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M13.7 21a2 2 0 01-3.4 0",
    sun:      "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
    moon:     "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
    chevR:    "M9 18l6-6-6-6",
    chevD:    "M6 9l6 6 6-6",
    chevU:    "M18 15l-6-6-6 6",
    check:    "M20 6L9 17l-5-5",
    x:        "M18 6L6 18M6 6l12 12",
    alert:    "M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01",
    up:       "M7 17L17 7M9 7h8v8",
    down:     "M7 7l10 10M17 9v8H9",
    clock:    "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2",
    file:     "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6M9 13h6M9 17h6",
    download: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
    plus:     "M12 5v14M5 12h14",
    filter:   "M22 3H2l8 9.5V19l4 2v-8.5L22 3z",
    sparkle:  "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z",
    ship:     "M3 17l1.6-5.4a1 1 0 01.96-.72h12.88a1 1 0 01.96.72L21 17M12 11V4M8 4h8M3 20c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1",
    calendar: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z",
    users:    "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM23 21v-2a4 4 0 00-3-3.87M16 3.13A4 4 0 0119 7a4 4 0 01-3 3.87",
    zap:      "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
    shield:   "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
    eye:      "M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7zM12 15a3 3 0 100-6 3 3 0 000 6z",
    mail:     "M4 4h16a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V6a2 2 0 012-2zM22 6l-10 7L2 6",
    send:     "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z",
    layers:   "M12 2l9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5",
    sort:     "M7 4v16M4 17l3 3 3-3M17 20V4M14 7l3-3 3 3",
    menu:     "M4 6h16M4 12h16M4 18h16",
    panel:    "M3 5h18v14H3zM9 5v14",
    dollar:   "M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6",
    scale:    "M12 3v18M3 7h18M7 7l-4 7h8L7 7zM17 7l-4 7h8l-4-7z",
    globe:    "M12 21a9 9 0 100-18 9 9 0 000 18zM3 12h18M12 3a14 14 0 014 9 14 14 0 01-4 9 14 14 0 01-4-9 14 14 0 014-9z",
    wheat:    "M12 22V8M12 8c0-2 1.6-3.6 3.6-3.6M12 8c0-2-1.6-3.6-3.6-3.6M12 13c0-2 1.6-3.6 3.6-3.6M12 13c0-2-1.6-3.6-3.6-3.6M12 18c0-2 1.6-3.6 3.6-3.6M12 18c0-2-1.6-3.6-3.6-3.6",
    ext:      "M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3"
  };

  function icon(name, size, stroke) {
    var d = PATHS[name] || PATHS.brief;
    var s = size || 16;
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", s);
    svg.setAttribute("height", s);
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", stroke || 1.5);
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "ico");
    var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
    return svg;
  }
  GD.icon = icon;
  GD.hasIcon = function (n) { return Object.prototype.hasOwnProperty.call(PATHS, n); };

  /* ------------------------------------------------------------- formatting */

  function nf(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toLocaleString("en-US", {
      minimumFractionDigits: dp === undefined ? 0 : dp,
      maximumFractionDigits: dp === undefined ? 0 : dp
    });
  }
  function usd(v, dp) { return v === null || v === undefined || isNaN(v) ? "—" : "$" + nf(v, dp === undefined ? 2 : dp); }
  function usd0(v) { return v === null || v === undefined || isNaN(v) ? "—" : "$" + nf(v, 0); }

  /* $1.23M / $275k / $842 — the unit a cargo decision is actually read in. */
  function money(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var a = Math.abs(v), sign = v < 0 ? "-" : "";
    if (a >= 1e9) return sign + "$" + (a / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return sign + "$" + (a / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return sign + "$" + Math.round(a / 1e3) + "k";
    return sign + "$" + a.toFixed(0);
  }
  function signed(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + nf(Math.abs(v), dp === undefined ? 2 : dp);
  }
  function signedMoney(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + money(Math.abs(v)).replace("-", "");
  }
  function pct(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return nf(v * 100, dp === undefined ? 1 : dp) + "%";
  }
  function signedPct(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return (v > 0 ? "+" : v < 0 ? "−" : "") + nf(Math.abs(v) * 100, dp === undefined ? 1 : dp) + "%";
  }
  function thb(v, dp) { return v === null || v === undefined || isNaN(v) ? "—" : "฿" + nf(v, dp === undefined ? 2 : dp); }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function parseDate(s) {
    if (!s) return null;
    var m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]),
      m[4] ? Number(m[4]) : 0, m[5] ? Number(m[5]) : 0);
  }
  function fmtDate(s) {
    var d = parseDate(s); if (!d) return "—";
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  }
  function fmtDateShort(s) {
    var d = parseDate(s); if (!d) return "—";
    return d.getDate() + " " + MONTHS[d.getMonth()];
  }
  function fmtTime(s) {
    var d = parseDate(s); if (!d) return "";
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  /* "today 09:23" / "3 days ago" — relative to the dataset's build date, never to
     the viewer's clock, so the app reads consistently whenever it is opened. */
  function ago(s, nowStr) {
    var d = parseDate(s), now = parseDate(nowStr || (window.DESK && window.DESK.built) || "2026-10-13");
    if (!d || !now) return "—";
    var days = Math.floor((now - d) / 86400000);
    if (days <= 0) return "today " + fmtTime(s);
    if (days === 1) return "yesterday";
    if (days < 7) return days + " days ago";
    if (days < 14) return "last week";
    if (days < 60) return Math.round(days / 7) + " weeks ago";
    return fmtDateShort(s);
  }
  function daysBetween(a, b) {
    var x = parseDate(a), y = parseDate(b);
    if (!x || !y) return null;
    return Math.round((y - x) / 86400000);
  }

  GD.fmt = {
    n: nf, usd: usd, usd0: usd0, money: money, signed: signed, signedMoney: signedMoney,
    pct: pct, signedPct: signedPct, thb: thb, date: fmtDate, dateShort: fmtDateShort,
    time: fmtTime, ago: ago, daysBetween: daysBetween, parseDate: parseDate, months: MONTHS
  };

  GD.toneFor = function (v, invert) {
    if (v === 0 || v === null || v === undefined || isNaN(v)) return "val-mute";
    var good = invert ? v > 0 : v < 0;
    return good ? "val-down" : "val-up";
  };

  /* ------------------------------------------------------------------ charts
     Hand-rolled SVG. No chart library: the shapes needed here are few and the
     control over tooltips/theming is worth more than the bytes saved. */

  var SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }
  function extent(vals) {
    var lo = Infinity, hi = -Infinity;
    vals.forEach(function (v) { if (v < lo) lo = v; if (v > hi) hi = v; });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (lo === hi) { lo -= 1; hi += 1; }
    return [lo, hi];
  }
  function niceMax(v) {
    if (v <= 0) return 1;
    var mag = Math.pow(10, Math.floor(Math.log(v) / Math.LN10));
    var n = v / mag;
    var step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return step * mag;
  }

  /* Line sparkline with a soft area fill and an emphasised endpoint. */
  function spark(values, opts) {
    opts = opts || {};
    var w = opts.w || 76, h = opts.h || 26, pad = 2;
    var vals = values.slice(-(opts.take || 60));
    var e = extent(vals), lo = e[0], hi = e[1];
    var dx = vals.length > 1 ? (w - pad * 2) / (vals.length - 1) : 0;
    var y = function (v) { return h - pad - ((v - lo) / (hi - lo)) * (h - pad * 2); };
    var pts = vals.map(function (v, i) { return (pad + i * dx).toFixed(2) + "," + y(v).toFixed(2); });
    var color = opts.color || "var(--ink-3)";
    var svg = svgEl("svg", {
      viewBox: "0 0 " + w + " " + h, width: w, height: h, class: "spark",
      "aria-hidden": "true", preserveAspectRatio: "none"
    });
    if (opts.fill !== false) {
      var area = svgEl("path", {
        d: "M" + pts.join("L") + "L" + (pad + (vals.length - 1) * dx).toFixed(2) + "," + (h - pad) +
           "L" + pad + "," + (h - pad) + "Z",
        fill: color, opacity: 0.1
      });
      svg.appendChild(area);
    }
    svg.appendChild(svgEl("path", {
      d: "M" + pts.join("L"), fill: "none", stroke: color,
      "stroke-width": opts.weight || 1.5, "stroke-linecap": "round", "stroke-linejoin": "round"
    }));
    if (opts.endpoint !== false && vals.length) {
      svg.appendChild(svgEl("circle", {
        cx: (pad + (vals.length - 1) * dx).toFixed(2), cy: y(vals[vals.length - 1]).toFixed(2),
        r: 2, fill: color
      }));
    }
    return svg;
  }

  /* The SPN motif: many thin bars, one highlighted, with a dot and a label above it.
     Used for price history where "where are we now against the range" is the question. */
  function barSpark(values, opts) {
    opts = opts || {};
    var vals = values.slice(-(opts.take || 28));
    var w = opts.w || 112, h = opts.h || 38;
    var hi = opts.highlight === undefined ? vals.length - 1 : opts.highlight;
    var e = extent(vals), lo = e[0], top = e[1];
    var n = vals.length;
    var gap = opts.gap || 2;
    var bw = Math.max(1.5, (w - gap * (n - 1)) / n);
    var labelH = opts.label ? 13 : 0;
    var svg = svgEl("svg", {
      viewBox: "0 0 " + w + " " + h, width: w, height: h, class: "spark", "aria-hidden": "true"
    });
    vals.forEach(function (v, i) {
      var bh = Math.max(1.5, ((v - lo) / (top - lo || 1)) * (h - labelH - 4) + 2);
      var isHi = i === hi;
      svg.appendChild(svgEl("rect", {
        x: (i * (bw + gap)).toFixed(2), y: (h - bh).toFixed(2),
        width: bw.toFixed(2), height: bh.toFixed(2), rx: Math.min(1.5, bw / 2),
        fill: isHi ? (opts.color || "var(--accent)") : (opts.muted || "var(--surface-3)")
      }));
      if (isHi) {
        var cx = i * (bw + gap) + bw / 2;
        svg.appendChild(svgEl("circle", {
          cx: cx.toFixed(2), cy: (h - bh - 3.5).toFixed(2), r: 2.2,
          fill: opts.color || "var(--accent)"
        }));
        if (opts.label) {
          var tx = Math.max(10, Math.min(w - 10, cx));
          var t = svgEl("text", {
            x: tx.toFixed(2), y: 9, "text-anchor": "middle",
            fill: opts.color || "var(--accent)",
            "font-size": "9.5", "font-weight": "600", "font-family": "var(--sans)"
          });
          t.textContent = opts.label;
          svg.appendChild(t);
        }
      }
    });
    return svg;
  }

  /* Multi-series line chart with axis, grid and a shared crosshair tooltip. */
  function lineChart(opts) {
    var series = opts.series || [];
    var dates = opts.dates || [];
    var w = opts.w || 720, h = opts.h || 230;
    var padL = opts.padL === undefined ? 44 : opts.padL, padR = 12, padT = 10, padB = 24;
    var all = [];
    series.forEach(function (s) { all = all.concat(s.values.filter(function (v) { return v !== null; })); });
    var e = extent(all);
    var lo = opts.zero ? 0 : e[0] - (e[1] - e[0]) * 0.08;
    var hi = e[1] + (e[1] - e[0]) * 0.08;
    var n = dates.length || (series[0] ? series[0].values.length : 0);
    var X = function (i) { return padL + (n > 1 ? (i / (n - 1)) * (w - padL - padR) : 0); };
    var Y = function (v) { return padT + (1 - (v - lo) / (hi - lo || 1)) * (h - padT - padB); };

    var svg = svgEl("svg", {
      viewBox: "0 0 " + w + " " + h, class: "chart",
      role: "img", "aria-label": opts.label || "Line chart"
    });
    var ticks = 4, i;
    for (i = 0; i <= ticks; i++) {
      var v = lo + (hi - lo) * (i / ticks);
      var yy = Y(v);
      svg.appendChild(svgEl("line", {
        x1: padL, x2: w - padR, y1: yy.toFixed(2), y2: yy.toFixed(2), class: "grid-l"
      }));
      var t = svgEl("text", { x: padL - 7, y: (yy + 3.2).toFixed(2), "text-anchor": "end", class: "axis-l" });
      t.textContent = opts.fmtY ? opts.fmtY(v) : nf(v, 0);
      svg.appendChild(t);
    }
    var step = Math.max(1, Math.floor(n / 6));
    /* `fmtX` is optional: the default is right for a chart spanning weeks, and wrong for one
       spanning a year, where "1 Oct" at both ends means two different Octobers. */
    var fmtX = opts.fmtX || fmtDateShort;
    for (i = 0; i < n; i += step) {
      var tx = svgEl("text", { x: X(i).toFixed(2), y: h - 7, "text-anchor": "middle", class: "axis-l" });
      tx.textContent = fmtX(dates[i]);
      svg.appendChild(tx);
    }
    /* A dashed reference level — the price at the start of the window, so the whole chart
       reads as "above or below where this period opened" at a glance. */
    if (opts.baseline !== undefined && opts.baseline !== null) {
      svg.appendChild(svgEl("line", {
        x1: padL, x2: w - padR, y1: Y(opts.baseline).toFixed(2), y2: Y(opts.baseline).toFixed(2),
        stroke: "var(--border-strong)", "stroke-width": 1, "stroke-dasharray": "4 4"
      }));
    }

    series.forEach(function (s, si) {
      var d = "", started = false, firstX = null, lastX = null;
      s.values.forEach(function (v, idx) {
        if (v === null || v === undefined) return;
        d += (started ? "L" : "M") + X(idx).toFixed(2) + "," + Y(v).toFixed(2);
        if (firstX === null) firstX = X(idx);
        lastX = X(idx);
        started = true;
      });
      if (!d) return;
      /* Area under the first series only. More than one filled region is mud. */
      if (opts.area && si === 0) {
        svg.appendChild(svgEl("path", {
          d: d + "L" + lastX.toFixed(2) + "," + (h - padB) +
             "L" + firstX.toFixed(2) + "," + (h - padB) + "Z",
          fill: s.color, opacity: 0.09, stroke: "none"
        }));
      }
      svg.appendChild(svgEl("path", {
        d: d, fill: "none", stroke: s.color, "stroke-width": s.weight || 1.75,
        "stroke-linecap": "round", "stroke-linejoin": "round"
      }));
    });

    var cross = svgEl("line", {
      x1: 0, x2: 0, y1: padT, y2: h - padB, stroke: "var(--border-strong)",
      "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0
    });
    svg.appendChild(cross);
    var dots = series.map(function (s) {
      var c = svgEl("circle", { r: 3.2, fill: s.color, stroke: "var(--surface)", "stroke-width": 1.5, opacity: 0 });
      svg.appendChild(c);
      return c;
    });
    var hit = svgEl("rect", { x: padL, y: padT, width: Math.max(0, w - padL - padR), height: Math.max(0, h - padT - padB), fill: "transparent" });
    svg.appendChild(hit);

    function at(ev) {
      var r = svg.getBoundingClientRect();
      if (!r.width) return;
      var px = (ev.clientX - r.left) / r.width * w;
      var idx = Math.round(((px - padL) / Math.max(1, w - padL - padR)) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      cross.setAttribute("x1", X(idx).toFixed(2));
      cross.setAttribute("x2", X(idx).toFixed(2));
      cross.setAttribute("opacity", 1);
      var rows = series.map(function (s, si) {
        var v = s.values[idx];
        if (v === null || v === undefined) { dots[si].setAttribute("opacity", 0); return null; }
        dots[si].setAttribute("cx", X(idx).toFixed(2));
        dots[si].setAttribute("cy", Y(v).toFixed(2));
        dots[si].setAttribute("opacity", 1);
        return "<div class='tip-row'><span class='tip-mute'>" + esc(s.name) + "</span><b>" +
          esc(opts.fmtTip ? opts.fmtTip(v, s) : nf(v, 2)) + "</b></div>";
      }).filter(Boolean);
      /* A caller that wants the scrubbed value in its own header takes `onHover` and sets
         `tip: false`; two readouts of the same number following the cursor is one too many. */
      if (opts.onHover) opts.onHover(idx, dates[idx]);
      if (opts.tip !== false) {
        tip.show(ev, "<b>" + esc(fmtDate(dates[idx])) + "</b>" + rows.join(""));
      }
    }
    hit.addEventListener("pointermove", at);
    hit.addEventListener("pointerleave", function () {
      cross.setAttribute("opacity", 0);
      dots.forEach(function (d) { d.setAttribute("opacity", 0); });
      tip.hide();
      if (opts.onLeave) opts.onLeave();
    });
    return svg;
  }

  /* Grouped zero-based bars — SPN's energy chart. Zero-based on purpose: a
     truncated baseline would exaggerate a few percent of carry. */
  function groupedBars(opts) {
    var groups = opts.groups || [];      // [{label, values:[n,n]}]
    var names = opts.names || [];        // [{name,color}]
    var w = opts.w || 460, h = opts.h || 220;
    var padL = 36, padR = 8, padT = 10, padB = 26;
    var max = 0;
    groups.forEach(function (g) { g.values.forEach(function (v) { if (v > max) max = v; }); });
    max = niceMax(max);
    var gw = (w - padL - padR) / Math.max(1, groups.length);
    var bw = Math.min(14, (gw - 8) / Math.max(1, names.length));
    var Y = function (v) { return padT + (1 - v / max) * (h - padT - padB); };
    var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, class: "chart", role: "img", "aria-label": opts.label || "Bar chart" });
    var i;
    for (i = 0; i <= 4; i++) {
      var v = max * i / 4, yy = Y(v);
      svg.appendChild(svgEl("line", { x1: padL, x2: w - padR, y1: yy.toFixed(2), y2: yy.toFixed(2), class: "grid-l" }));
      var t = svgEl("text", { x: padL - 6, y: (yy + 3.2).toFixed(2), "text-anchor": "end", class: "axis-l" });
      t.textContent = opts.fmtY ? opts.fmtY(v) : nf(v, 0);
      svg.appendChild(t);
    }
    groups.forEach(function (g, gi) {
      var x0 = padL + gi * gw + (gw - bw * names.length) / 2;
      g.values.forEach(function (v, vi) {
        var yy = Y(v), bh = Math.max(1, (h - padB) - yy);
        var r = svgEl("rect", {
          x: (x0 + vi * bw).toFixed(2), y: yy.toFixed(2),
          width: Math.max(1, bw - 2).toFixed(2), height: bh.toFixed(2),
          rx: 2.5, fill: names[vi] ? names[vi].color : "var(--ink-4)"
        });
        r.style.cursor = "default";
        r.addEventListener("pointerenter", function (ev) {
          tip.show(ev, "<b>" + esc(g.label) + "</b><div class='tip-row'><span class='tip-mute'>" +
            esc(names[vi] ? names[vi].name : "") + "</span><b>" +
            esc(opts.fmtTip ? opts.fmtTip(v) : nf(v, 2)) + "</b></div>");
        });
        r.addEventListener("pointerleave", function () { tip.hide(); });
        svg.appendChild(r);
      });
      var lt = svgEl("text", { x: (padL + gi * gw + gw / 2).toFixed(2), y: h - 8, "text-anchor": "middle", class: "axis-l" });
      lt.textContent = g.label;
      svg.appendChild(lt);
    });
    return svg;
  }

  /* Candles. `bars` is [[date, open, high, low, close], …] already aggregated to whatever
     interval the caller wants — this draws what it is given and does not resample, because
     the caller is the only one who knows how many candles fit.

     Direction is the buyer's, like everything else on this desk: a session that closed lower
     is emerald, because the cheaper tonne is the good outcome. That is the opposite of a
     trading screen and it is deliberate. */
  function candles(opts) {
    var bars = opts.bars || [];
    var w = opts.w || 1000, h = opts.h || 290;
    var padL = opts.padL === undefined ? 44 : opts.padL, padR = 12, padT = 10, padB = 24;
    var all = [];
    bars.forEach(function (b) { all.push(b[2], b[3]); });
    var e = extent(all);
    var lo = e[0] - (e[1] - e[0]) * 0.06;
    var hi = e[1] + (e[1] - e[0]) * 0.06;
    var n = bars.length;
    var span = Math.max(1, w - padL - padR);
    var step = n ? span / n : span;
    var body = Math.max(1, Math.min(13, step * 0.62));
    var X = function (i) { return padL + step * (i + 0.5); };
    var Y = function (v) { return padT + (1 - (v - lo) / (hi - lo || 1)) * (h - padT - padB); };

    var svg = svgEl("svg", {
      viewBox: "0 0 " + w + " " + h, class: "chart",
      role: "img", "aria-label": opts.label || "Candlestick chart"
    });
    var i, v, yy, t;
    for (i = 0; i <= 4; i++) {
      v = lo + (hi - lo) * (i / 4);
      yy = Y(v);
      svg.appendChild(svgEl("line", {
        x1: padL, x2: w - padR, y1: yy.toFixed(2), y2: yy.toFixed(2), class: "grid-l"
      }));
      t = svgEl("text", { x: padL - 7, y: (yy + 3.2).toFixed(2), "text-anchor": "end", class: "axis-l" });
      t.textContent = opts.fmtY ? opts.fmtY(v) : nf(v, 0);
      svg.appendChild(t);
    }
    if (opts.baseline !== undefined && opts.baseline !== null) {
      svg.appendChild(svgEl("line", {
        x1: padL, x2: w - padR, y1: Y(opts.baseline).toFixed(2), y2: Y(opts.baseline).toFixed(2),
        stroke: "var(--border-strong)", "stroke-width": 1, "stroke-dasharray": "4 4"
      }));
    }
    var fmtX = opts.fmtX || fmtDateShort;
    var xStep = Math.max(1, Math.floor(n / 6));
    for (i = 0; i < n; i += xStep) {
      t = svgEl("text", { x: X(i).toFixed(2), y: h - 7, "text-anchor": "middle", class: "axis-l" });
      t.textContent = fmtX(bars[i][0]);
      svg.appendChild(t);
    }

    var group = svgEl("g", {});
    bars.forEach(function (b, idx) {
      var o = b[1], hiV = b[2], loV = b[3], c = b[4];
      var down = c < o;
      var color = down ? "var(--down)" : c > o ? "var(--up)" : "var(--ink-3)";
      var x = X(idx);
      group.appendChild(svgEl("line", {
        x1: x.toFixed(2), x2: x.toFixed(2),
        y1: Y(hiV).toFixed(2), y2: Y(loV).toFixed(2),
        stroke: color, "stroke-width": Math.max(1, Math.min(1.6, body * 0.18))
      }));
      var top = Y(Math.max(o, c)), bot = Y(Math.min(o, c));
      group.appendChild(svgEl("rect", {
        x: (x - body / 2).toFixed(2), y: top.toFixed(2),
        width: body.toFixed(2), height: Math.max(1, bot - top).toFixed(2),
        rx: Math.min(1.5, body / 4), fill: color
      }));
    });
    svg.appendChild(group);

    var cross = svgEl("line", {
      x1: 0, x2: 0, y1: padT, y2: h - padB, stroke: "var(--border-strong)",
      "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0
    });
    svg.appendChild(cross);
    var hit = svgEl("rect", {
      x: padL, y: padT, width: span, height: Math.max(0, h - padT - padB), fill: "transparent"
    });
    svg.appendChild(hit);

    function at(ev) {
      var r = svg.getBoundingClientRect();
      if (!r.width || !n) return;
      var px = (ev.clientX - r.left) / r.width * w;
      var idx = Math.max(0, Math.min(n - 1, Math.floor((px - padL) / step)));
      cross.setAttribute("x1", X(idx).toFixed(2));
      cross.setAttribute("x2", X(idx).toFixed(2));
      cross.setAttribute("opacity", 1);
      if (opts.onHover) opts.onHover(idx, bars[idx]);
      if (opts.tip !== false) {
        var b = bars[idx];
        var f = opts.fmtY || function (v) { return nf(v, 2); };
        tip.show(ev, "<b>" + esc(fmtDate(b[0])) + "</b>" +
          ["open", "high", "low", "close"].map(function (k, j) {
            return "<div class='tip-row'><span class='tip-mute'>" + k + "</span><b>" +
              esc(f(b[j + 1])) + "</b></div>";
          }).join(""));
      }
    }
    hit.addEventListener("pointermove", at);
    hit.addEventListener("pointerleave", function () {
      cross.setAttribute("opacity", 0);
      tip.hide();
      if (opts.onLeave) opts.onLeave();
    });
    return svg;
  }

  /* Roll daily bars up into weekly or monthly ones. Three hundred daily candles in a
     thousand pixels are three hundred vertical lines; the caller picks the interval that
     fits and this does the arithmetic. */
  function aggregate(bars, interval) {
    if (!interval || interval === "d") return bars.slice();
    var out = [], cur = null, key = null;
    bars.forEach(function (b) {
      var k;
      if (interval === "m") k = b[0].slice(0, 7);
      else {
        /* ISO-ish week bucket: the date of the Monday on or before this bar. */
        var d = parseDate(b[0]);
        if (!d) return;
        var day = (d.getDay() + 6) % 7;
        d.setDate(d.getDate() - day);
        k = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" +
            String(d.getDate()).padStart(2, "0");
      }
      if (k !== key) {
        if (cur) out.push(cur);
        key = k;
        cur = [b[0], b[1], b[2], b[3], b[4]];
      } else {
        cur[2] = Math.max(cur[2], b[2]);
        cur[3] = Math.min(cur[3], b[3]);
        cur[4] = b[4];
      }
    });
    if (cur) out.push(cur);
    return out;
  }

  GD.chart = {
    spark: spark, barSpark: barSpark, line: lineChart, groupedBars: groupedBars,
    candles: candles, aggregate: aggregate, niceMax: niceMax, extent: extent
  };

  /* --------------------------------------------------------------- tooltip */

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  GD.esc = esc;

  var tip = (function () {
    var node = null;
    function ensure() {
      if (!node) { node = el("div", { class: "tip", role: "tooltip" }); document.body.appendChild(node); }
      return node;
    }
    return {
      show: function (ev, html) {
        var n = ensure();
        n.innerHTML = html;
        n.setAttribute("data-open", "true");
        var r = n.getBoundingClientRect();
        var x = ev.clientX + 14, y = ev.clientY + 16;
        if (x + r.width > window.innerWidth - 10) x = ev.clientX - r.width - 12;
        if (y + r.height > window.innerHeight - 10) y = ev.clientY - r.height - 12;
        n.style.left = Math.max(8, x) + "px";
        n.style.top = Math.max(8, y) + "px";
      },
      hide: function () { if (node) node.setAttribute("data-open", "false"); }
    };
  })();
  GD.tip = tip;

  /* Attach a tooltip to any element without wiring listeners by hand. */
  GD.tipOn = function (node, html) {
    node.addEventListener("pointerenter", function (ev) { tip.show(ev, typeof html === "function" ? html() : html); });
    node.addEventListener("pointermove", function (ev) { tip.show(ev, typeof html === "function" ? html() : html); });
    node.addEventListener("pointerleave", function () { tip.hide(); });
    node.addEventListener("blur", function () { tip.hide(); });
    return node;
  };

  /* ---------------------------------------------------------------- toasts */

  var toastHost = null;
  GD.toast = function (msg, opts) {
    opts = opts || {};
    if (!toastHost) { toastHost = el("div", { class: "toasts", "aria-live": "polite" }); document.body.appendChild(toastHost); }
    var t = el("div", { class: "toast", role: "status" }, [
      icon(opts.icon || "check", 15, 2), el("span", { text: msg })
    ]);
    toastHost.appendChild(t);
    var life = opts.duration || 2600;
    setTimeout(function () {
      t.setAttribute("data-leaving", "true");
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 160);
    }, life);
    return t;
  };

  /* -------------------------------------------------------------- overlays */

  var openLayers = [];

  function trapFocus(container, ev) {
    var f = qsa('a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])', container)
      .filter(function (n) { return n.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  }

  function openOverlay(build, opts) {
    opts = opts || {};
    var prevFocus = document.activeElement;
    var scrim = el("div", { class: "scrim", "data-open": "false" });
    var panel = build(close);
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    if (opts.labelledBy) panel.setAttribute("aria-labelledby", opts.labelledBy);
    scrim.appendChild(panel);
    scrim.addEventListener("pointerdown", function (ev) { if (ev.target === scrim) close(); });
    document.body.appendChild(scrim);
    /* next frame so the transition has a start value to animate from */
    requestAnimationFrame(function () { scrim.setAttribute("data-open", "true"); });

    function onKey(ev) {
      if (ev.key === "Escape") { ev.preventDefault(); close(); }
      else if (ev.key === "Tab") trapFocus(panel, ev);
    }
    document.addEventListener("keydown", onKey);

    var entry = { close: close };
    openLayers.push(entry);
    setTimeout(function () {
      var target = qs("[data-autofocus]", panel) || qs("button,input,[tabindex]", panel);
      if (target) target.focus();
    }, 20);

    function close() {
      if (!scrim.parentNode) return;
      document.removeEventListener("keydown", onKey);
      openLayers = openLayers.filter(function (e) { return e !== entry; });
      scrim.setAttribute("data-open", "false");
      setTimeout(function () { if (scrim.parentNode) scrim.parentNode.removeChild(scrim); }, 190);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      if (opts.onClose) opts.onClose();
    }
    return close;
  }
  GD.openOverlay = openOverlay;
  GD.closeTop = function () { if (openLayers.length) openLayers[openLayers.length - 1].close(); };

  /* A standard sheet: head / body / foot. */
  GD.sheet = function (cfg) {
    return openOverlay(function (close) {
      var id = "sheet-title-" + Math.floor(performance.now() * 1000 % 1e9);
      var head = el("div", { class: "sheet-head" }, [
        el("div", { class: "ch-text", style: { flex: "1" } }, [
          el("div", { class: "card-title", id: id, text: cfg.title }),
          cfg.sub ? el("div", { class: "card-sub", text: cfg.sub }) : null
        ]),
        el("button", { class: "icon-btn", "aria-label": "Close", onclick: close }, [icon("x", 16, 1.75)])
      ]);
      var body = el("div", { class: "sheet-body" }, cfg.body ? cfg.body(close) : null);
      var foot = cfg.foot ? el("div", { class: "sheet-foot" }, cfg.foot(close)) : null;
      var sheet = el("div", { class: "sheet " + (cfg.class || ""), "aria-labelledby": id }, [head, body, foot]);
      return sheet;
    }, { onClose: cfg.onClose });
  };

  /* ---------------------------------------------------------------- router */

  var routes = {}, current = null, onChange = null;
  GD.route = function (key, def) { routes[key] = def; };
  GD.routes = function () { return routes; };
  GD.current = function () { return current; };
  GD.onRoute = function (fn) { onChange = fn; };

  GD.go = function (key, replace) {
    if (!routes[key]) key = Object.keys(routes)[0];
    if (replace) location.replace("#" + key); else location.hash = key;
    if (current === key) render(key);
  };

  function render(key) {
    current = key;
    var def = routes[key];
    if (!def) {
      /* No surface registered under this key — happens if a surface script failed to
         load. Say so rather than throwing on `def.label` and taking the shell down. */
      if (window.console) console.warn("[GrainDesk] no surface registered for '" + key + "'");
      return;
    }
    if (onChange) onChange(key, def);
  }

  GD.startRouter = function () {
    function fromHash() {
      var keys = Object.keys(routes);
      if (!keys.length) {
        if (window.console) console.warn("[GrainDesk] no surfaces registered");
        return;
      }
      var k = (location.hash || "").replace(/^#/, "");
      if (!routes[k]) k = keys[0];
      render(k);
    }
    window.addEventListener("hashchange", fromHash);
    fromHash();
  };

  /* ------------------------------------------------------------ table help */

  /* Sortable table. `cols` is [{key,label,num,cls,render,sortValue,width}]. */
  GD.table = function (cfg) {
    var rows = cfg.rows.slice();
    var sortKey = cfg.sortKey || null;
    var sortDir = cfg.sortDir || "asc";
    var host = el("div", { class: "table-wrap" });
    var table = el("table");
    host.appendChild(table);

    function valueOf(row, col) {
      if (col.sortValue) return col.sortValue(row);
      return row[col.key];
    }
    function draw() {
      clear(table);
      var thead = el("thead");
      var tr = el("tr");
      cfg.cols.forEach(function (col) {
        var th = el("th", {
          class: (col.num ? "num " : "") + (col.cls || "") + (col.sortable === false ? "" : " sortable"),
          scope: "col",
          style: col.width ? { width: col.width } : null
        });
        th.appendChild(document.createTextNode(col.label));
        if (col.sortable !== false) {
          th.appendChild(icon(sortKey === col.key ? (sortDir === "asc" ? "chevU" : "chevD") : "sort", 11, 2));
          th.lastChild.classList.add("sort-ico");
          if (sortKey === col.key) th.setAttribute("aria-sort", sortDir === "asc" ? "ascending" : "descending");
          th.addEventListener("click", function () {
            if (sortKey === col.key) sortDir = sortDir === "asc" ? "desc" : "asc";
            else { sortKey = col.key; sortDir = col.num ? "desc" : "asc"; }
            draw();
          });
        }
        tr.appendChild(th);
      });
      thead.appendChild(tr);
      table.appendChild(thead);

      var body = rows.slice();
      if (sortKey) {
        var col = cfg.cols.filter(function (c) { return c.key === sortKey; })[0];
        if (col) {
          body.sort(function (a, b) {
            var x = valueOf(a, col), y = valueOf(b, col);
            if (x === null || x === undefined) return 1;
            if (y === null || y === undefined) return -1;
            if (typeof x === "number" && typeof y === "number") return sortDir === "asc" ? x - y : y - x;
            x = String(x).toLowerCase(); y = String(y).toLowerCase();
            return sortDir === "asc" ? (x < y ? -1 : x > y ? 1 : 0) : (x > y ? -1 : x < y ? 1 : 0);
          });
        }
      }
      var tbody = el("tbody");
      if (!body.length) {
        tbody.appendChild(el("tr", null, [
          el("td", { colspan: cfg.cols.length }, [
            el("div", { class: "empty" }, [icon("filter", 20, 1.5), el("div", { text: cfg.emptyText || "Nothing matches these filters." })])
          ])
        ]));
      }
      body.forEach(function (row, i) {
        var tr2 = el("tr", { class: cfg.rowClass ? cfg.rowClass(row) : null });
        if (cfg.onRow) {
          tr2.classList.add("is-link");
          tr2.setAttribute("tabindex", "0");
          tr2.addEventListener("click", function () { cfg.onRow(row); });
          tr2.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); cfg.onRow(row); }
          });
        }
        cfg.cols.forEach(function (col) {
          var td = el("td", { class: (col.num ? "num " : "") + (col.tdClass || "") });
          var v = col.render ? col.render(row, i) : row[col.key];
          if (v instanceof Node) td.appendChild(v);
          else td.appendChild(document.createTextNode(v === null || v === undefined ? "—" : String(v)));
          tr2.appendChild(td);
        });
        tbody.appendChild(tr2);
      });
      table.appendChild(tbody);
    }
    draw();
    host.update = function (next) { rows = next.slice(); draw(); };
    return host;
  };

  /* ---------------------------------------------------------- small pieces */

  GD.chip = function (text, tone, ico) {
    return el("span", { class: "chip " + (tone ? "chip-" + tone : "") }, [
      ico ? icon(ico, 11, 2) : null, el("span", { text: text })
    ]);
  };
  GD.dot = function (tone) { return el("span", { class: "dot " + (tone ? "dot-" + tone : "") }); };

  GD.card = function (cfg) {
    var head = (cfg.title || cfg.actions) ? el("div", { class: "card-head" }, [
      el("div", { class: "ch-text" }, [
        cfg.title ? el("div", { class: "card-title", text: cfg.title }) : null,
        cfg.sub ? el("div", { class: "card-sub", text: cfg.sub }) : null
      ]),
      cfg.actions ? el("div", { class: "ch-actions" }, cfg.actions) : null
    ]) : null;
    return el("section", { class: "card " + (cfg.class || "") }, [
      head,
      el("div", { class: "card-body " + (cfg.flush ? "flush" : "") }, cfg.body),
      cfg.foot ? el("div", { class: "card-foot" }, cfg.foot) : null
    ]);
  };

  GD.stat = function (cfg) {
    var node = el(cfg.onClick ? "button" : "div", {
      class: "stat " + (cfg.hero ? "hero " : "") + (cfg.onClick ? "is-link " : "") + (cfg.class || ""),
      onclick: cfg.onClick || null,
      type: cfg.onClick ? "button" : null
    }, [
      el("div", { class: "stat-top" }, [
        el("div", { class: "stat-row" }, [
          cfg.icon ? el("span", { class: "stat-ico" }, [icon(cfg.icon, 14, 1.75)]) : null,
          el("span", { class: "stat-label", text: cfg.label })
        ]),
        el("div", { class: "stat-main" }, [
          el("div", { class: "stat-value" }, [
            el("span", { text: cfg.value }),
            cfg.unit ? el("span", { class: "unit", text: cfg.unit }) : null
          ]),
          cfg.aside || null
        ])
      ]),
      cfg.delta ? el("div", { class: "stat-delta" }, cfg.delta) : null
    ]);
    return node;
  };

  GD.seg = function (items, active, onPick) {
    var host = el("div", { class: "seg", role: "group" });
    items.forEach(function (it) {
      var b = el("button", {
        type: "button", text: it.label, "aria-pressed": String(it.key === active),
        onclick: function () {
          qsa("button", host).forEach(function (n) { n.setAttribute("aria-pressed", "false"); });
          b.setAttribute("aria-pressed", "true");
          onPick(it.key);
        }
      });
      host.appendChild(b);
    });
    return host;
  };

  GD.compo = function (parts, total) {
    var bar = el("div", { class: "compo" });
    parts.forEach(function (p) {
      if (!p.value || p.value <= 0) return;
      var seg = el("span", {
        style: { width: (p.value / total * 100).toFixed(3) + "%", "background-color": p.color },
        tabindex: "0", "aria-label": p.name + " " + usd(p.value)
      });
      GD.tipOn(seg, "<b>" + esc(p.name) + "</b><div class='tip-row'><span class='tip-mute'>per tonne</span><b>" +
        esc(usd(p.value)) + "</b></div><div class='tip-row'><span class='tip-mute'>share</span><b>" +
        esc(pct(p.value / total)) + "</b></div>");
      bar.appendChild(seg);
    });
    return bar;
  };

  GD.legend = function (items) {
    return el("div", { class: "legend" }, items.map(function (i) {
      return el("span", null, [el("i", { style: { "background-color": i.color } }), el("span", { text: i.name })]);
    }));
  };

  GD.kv = function (pairs) {
    var dl = el("dl", { class: "kv" });
    pairs.forEach(function (p) {
      if (!p) return;
      dl.appendChild(el("dt", { text: p[0] }));
      var dd = el("dd");
      if (p[1] instanceof Node) dd.appendChild(p[1]); else dd.textContent = p[1];
      dl.appendChild(dd);
    });
    return dl;
  };

  /* Stagger children of a freshly-rendered surface. Infrequent by definition —
     it only runs on a route change, never on hover or filter. */
  GD.stagger = function (node) {
    node.classList.add("stagger");
    Array.prototype.forEach.call(node.children, function (c, i) {
      c.style.setProperty("--i", Math.min(i, 7));
    });
    return node;
  };

})();
