/* Grain Desk — spend cleansing.

   URC's head of procurement spends about five days a fortnight cleaning an S/4HANA spend
   export before anybody can look at it. The work is real: the same supplier appears under
   four spellings, the material is free text somebody typed into a PO, the currency changes
   line to line, and a handful of documents got posted twice.

   This module is that cleanup, written down. It is deliberately dull and deliberately
   general: no table in here knows anything about potato flakes. Vendors are matched by
   normalised alias and then by token similarity; materials are classified by token overlap
   against the material names the workspace already holds, with the supplier's own catalogue
   as a prior. A line the model of the data cannot resolve confidently goes to a human, and
   what the human decides is remembered.

   Pure functions and no DOM, so the reconciliation can be asserted in a test rather than
   eyeballed in a report. */
(function () {
  "use strict";

  var S = {};
  GD.spendlib = S;

  /* Anything below this goes to a person. Set where it is because a wrong auto-classification
     is far more expensive than a review click: it lands silently in a report somebody then
     negotiates against. */
  var THRESHOLD = 0.72;
  S.THRESHOLD = THRESHOLD;

  /* ===================================================================== text handling */

  function strip(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/﻿/g, "")
      .trim();
  }
  S.strip = strip;

  /* Lower-case, punctuation to spaces, legal suffixes dropped. "A.B.C. TRADING" and
     "abc trading corporation" have to land on the same string or nothing else works. */
  var LEGAL = {
    inc: 1, incorporated: 1, corp: 1, corporation: 1, co: 1, company: 1, ltd: 1, limited: 1,
    llc: 1, plc: 1, bv: 1, nv: 1, sa: 1, sdn: 1, bhd: 1, gmbh: 1, ag: 1, pte: 1, pty: 1,
    trdg: 1, "&": 1, and: 1, the: 1
  };

  function words(s) {
    return strip(s).toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .split(" ")
      .filter(Boolean);
  }
  S.words = words;

  /* "A.B.C. TRADING" splits into a, b, c, trading. A run of single characters is an
     initialism that punctuation pulled apart, so it is pushed back together — which is what
     makes "A.B.C. TRADING" and "ABC Trading Corp" the same vendor, and lets "B.V." reach the
     legal-suffix list it would otherwise walk straight past. */
  function fuseInitials(toks) {
    var out = [], run = [];
    function flush() {
      if (run.length > 1) out.push(run.join(""));
      else if (run.length === 1) out.push(run[0]);
      run = [];
    }
    toks.forEach(function (t) {
      if (t.length === 1) run.push(t);
      else { flush(); out.push(t); }
    });
    flush();
    return out;
  }
  S.fuseInitials = fuseInitials;

  function normalizeVendor(s) {
    return fuseInitials(words(s)).filter(function (w) { return !LEGAL[w]; }).join(" ");
  }
  S.normalizeVendor = normalizeVendor;

  /* Material descriptions keep their numbers out: "25KG", "IV0.80" and "GR2" are pack sizes
     and grades that differ between two lines for the same thing. */
  var DESC_STOP = { the: 1, and: 1, of: 1, for: 1, per: 1, kg: 1, mt: 1, ea: 1, pc: 1, pcs: 1,
                    bag: 1, bags: 1, bulk: 1, std: 1, credit: 1, note: 1 };

  function descTokens(s) {
    return words(s).filter(function (w) {
      if (DESC_STOP[w]) return false;
      if (/^\d/.test(w)) return false;        /* 25kg, 50, iv0 — sizes, not identity */
      return w.length > 2;                    /* "gr", "ir", "b" carry nothing */
    });
  }
  S.descTokens = descTokens;

  /* Two tokens match if one is a prefix of the other and the shorter is at least three
     characters. That is what turns "corr" into "corrugated" and "pot" into "potato" without
     a hand-written abbreviation table — which would be the demo special-casing itself. */
  function tokenHit(a, b) {
    if (a === b) return 1;
    var sh = a.length <= b.length ? a : b, lo = a.length <= b.length ? b : a;
    if (sh.length >= 3 && lo.indexOf(sh) === 0) return 0.9;
    if (skeleton(sh, lo)) return 0.8;
    return 0;
  }

  /* The other way people abbreviate: knock the vowels out. "pkg" is "packaging", "trdg" is
     "trading", "mfg" is "manufacturing". Tight on purpose — the short form must be
     vowel-free, start with the same letter, and appear in order inside the long form — so
     that "pet" cannot quietly become "polyethylene". */
  function skeleton(sh, lo) {
    if (sh.length < 3 || lo.length < sh.length + 2) return false;
    if (sh.charAt(0) !== lo.charAt(0)) return false;
    if (/[aeiou]/.test(sh)) return false;
    var at = 0;
    for (var i = 0; i < sh.length; i++) {
      at = lo.indexOf(sh.charAt(i), at);
      if (at < 0) return false;
      at += 1;
    }
    return true;
  }
  S.skeleton = skeleton;

  function overlap(aTokens, bTokens) {
    var used = {}, hits = 0;
    aTokens.forEach(function (a) {
      var best = 0, bi = -1;
      bTokens.forEach(function (b, i) {
        if (used[i]) return;
        var h = tokenHit(a, b);
        if (h > best) { best = h; bi = i; }
      });
      if (bi >= 0) { used[bi] = true; hits += best; }
    });
    return hits;
  }
  S.overlap = overlap;

  function similarity(aTokens, bTokens) {
    if (!aTokens.length || !bTokens.length) return 0;
    var hits = overlap(aTokens, bTokens);
    return (2 * hits) / (aTokens.length + bTokens.length);
  }
  S.similarity = similarity;

  /* Dice alone is the wrong measure for a company name, because the two strings are rarely
     the same length: "NORDPOORT B V" against "Nordpoort Food Ingredients BV" scores 0.5 on
     Dice and is obviously the same company. What matters is how much of the *shorter* name
     is accounted for. Dice stays in the blend to stop a single shared word carrying a match
     on its own. */
  function nameScore(aTokens, bTokens) {
    if (!aTokens.length || !bTokens.length) return 0;
    var hits = overlap(aTokens, bTokens);
    var dice = (2 * hits) / (aTokens.length + bTokens.length);
    var contain = hits / Math.min(aTokens.length, bTokens.length);
    return Math.min(1, contain * 0.72 + dice * 0.28);
  }
  S.nameScore = nameScore;

  /* ===================================================================== value handling */

  /* "1,234.56", "(1,234.56)" and "-1,234.56" are all the same number in a spend export. */
  function num(v) {
    var s = strip(v);
    if (!s) return null;
    var neg = /^\(.*\)$/.test(s);
    s = s.replace(/[()]/g, "").replace(/,/g, "").replace(/\s/g, "");
    if (!/^-?\d*\.?\d+$/.test(s)) return null;
    var n = parseFloat(s);
    if (isNaN(n)) return null;
    return neg ? -n : n;
  }
  S.num = num;

  /* ISO, or the DD.MM.YYYY and DD/MM/YYYY an SAP export produces depending on who ran it. */
  function date(v) {
    var s = strip(v);
    var m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return m[1] + "-" + m[2] + "-" + m[3];
    m = s.match(/^(\d{1,2})[./](\d{1,2})[./](\d{4})$/);
    if (m) {
      return m[3] + "-" + String(m[2]).padStart(2, "0") + "-" + String(m[1]).padStart(2, "0");
    }
    return null;
  }
  S.date = date;

  var UOM = {
    mt: "MT", t: "MT", to: "MT", ton: "MT", tons: "MT", tonne: "MT", tonnes: "MT",
    kg: "kg", kgs: "kg", kilogram: "kg", kilograms: "kg",
    l: "L", lt: "L", ltr: "L", ea: "EA", pc: "EA", pcs: "EA"
  };
  S.uom = function (v) { return UOM[strip(v).toLowerCase()] || strip(v).toUpperCase() || null; };

  /* Quantity in the material's own unit, so "3,350 KG" and "3.35 MT" are one number. */
  S.toUnit = function (qty, from, to) {
    if (qty === null || !from || !to) return null;
    if (from === to) return qty;
    if (from === "kg" && to === "MT") return qty / 1000;
    if (from === "MT" && to === "kg") return qty * 1000;
    return null;
  };

  /* ====================================================================== file reading */

  /* Delimiter sniffed from the header line: an S/4HANA export is comma, semicolon or tab
     depending on the locale of whoever pressed the button. */
  function parseDelimited(text) {
    var src = String(text).replace(/﻿/g, "");
    var firstLine = src.slice(0, src.indexOf("\n") < 0 ? src.length : src.indexOf("\n"));
    var counts = [[",", 0], [";", 0], ["\t", 0], ["|", 0]];
    var inQ = false;
    for (var i = 0; i < firstLine.length; i++) {
      var ch = firstLine[i];
      if (ch === '"') inQ = !inQ;
      else if (!inQ) counts.forEach(function (c) { if (ch === c[0]) c[1]++; });
    }
    counts.sort(function (a, b) { return b[1] - a[1]; });
    var d = counts[0][1] ? counts[0][0] : ",";

    var rows = [], row = [], field = "", q = false;
    for (i = 0; i < src.length; i++) {
      var c = src[i];
      if (q) {
        if (c === '"') {
          if (src[i + 1] === '"') { field += '"'; i++; }
          else q = false;
        } else field += c;
      } else if (c === '"') q = true;
      else if (c === d) { row.push(field); field = ""; }
      else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
      else if (c === "\r") { /* swallowed; \n closes the row */ }
      else field += c;
    }
    if (field.length || row.length) { row.push(field); rows.push(row); }
    rows = rows.filter(function (r) {
      return r.length > 1 || (r.length === 1 && strip(r[0]));
    });
    var header = (rows.shift() || []).map(strip);
    return { header: header, rows: rows, delimiter: d };
  }
  S.parseDelimited = parseDelimited;

  /* ------------------------------------------------------------------------ xlsx
     A spend export is an Excel file, and telling a buyer to re-save it as CSV is exactly the
     manual step this feature exists to remove. An .xlsx is a zip of XML, and the platform
     ships an inflater, so reading one needs no library — just the zip central directory and
     DecompressionStream. Stored (method 0) and deflated (method 8) entries both occur. */

  function dv(buf) { return new DataView(buf); }

  function findEOCD(buf) {
    var v = dv(buf), n = buf.byteLength;
    for (var i = n - 22; i >= Math.max(0, n - 70000); i--) {
      if (v.getUint32(i, true) === 0x06054b50) return i;
    }
    return -1;
  }

  function zipEntries(buf) {
    var v = dv(buf);
    var eocd = findEOCD(buf);
    if (eocd < 0) throw new Error("not a zip file");
    var count = v.getUint16(eocd + 10, true);
    var at = v.getUint32(eocd + 16, true);
    var out = [];
    for (var i = 0; i < count; i++) {
      if (v.getUint32(at, true) !== 0x02014b50) break;
      var method = v.getUint16(at + 10, true);
      var csize = v.getUint32(at + 20, true);
      var nameLen = v.getUint16(at + 28, true);
      var extraLen = v.getUint16(at + 30, true);
      var commentLen = v.getUint16(at + 32, true);
      var local = v.getUint32(at + 42, true);
      var name = new TextDecoder("utf-8").decode(new Uint8Array(buf, at + 46, nameLen));
      out.push({ name: name, method: method, csize: csize, local: local });
      at += 46 + nameLen + extraLen + commentLen;
    }
    return out;
  }

  function readEntry(buf, e) {
    var v = dv(buf);
    if (v.getUint32(e.local, true) !== 0x04034b50) throw new Error("bad local header");
    var nameLen = v.getUint16(e.local + 26, true);
    var extraLen = v.getUint16(e.local + 28, true);
    var start = e.local + 30 + nameLen + extraLen;
    var bytes = new Uint8Array(buf, start, e.csize);
    if (e.method === 0) return Promise.resolve(new TextDecoder("utf-8").decode(bytes));
    if (e.method !== 8) return Promise.reject(new Error("unsupported compression " + e.method));
    if (typeof DecompressionStream === "undefined") {
      return Promise.reject(new Error(
        "This browser cannot open .xlsx here (no DecompressionStream). " +
        "The CSV export of the same file works."));
    }
    var ds = new DecompressionStream("deflate-raw");
    var stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Response(stream).text();
  }

  function colIndex(ref) {
    var m = String(ref).match(/^([A-Z]+)/);
    if (!m) return 0;
    var n = 0;
    for (var i = 0; i < m[1].length; i++) n = n * 26 + (m[1].charCodeAt(i) - 64);
    return n - 1;
  }

  function parseXlsx(buf) {
    var entries;
    try { entries = zipEntries(buf); } catch (e) { return Promise.reject(e); }
    function pick(test) {
      for (var i = 0; i < entries.length; i++) if (test(entries[i].name)) return entries[i];
      return null;
    }
    var sheet = pick(function (n) { return /^xl\/worksheets\/sheet1\.xml$/i.test(n); })
      || pick(function (n) { return /^xl\/worksheets\/.*\.xml$/i.test(n); });
    if (!sheet) return Promise.reject(new Error("no worksheet in this workbook"));
    var shared = pick(function (n) { return /^xl\/sharedStrings\.xml$/i.test(n); });

    return Promise.all([
      readEntry(buf, sheet),
      shared ? readEntry(buf, shared) : Promise.resolve(null)
    ]).then(function (parts) {
      var parser = new DOMParser();
      var strs = [];
      if (parts[1]) {
        var sdoc = parser.parseFromString(parts[1], "application/xml");
        Array.prototype.forEach.call(sdoc.getElementsByTagName("si"), function (si) {
          var ts = si.getElementsByTagName("t");
          var txt = "";
          for (var i = 0; i < ts.length; i++) txt += ts[i].textContent;
          strs.push(txt);
        });
      }
      var doc = parser.parseFromString(parts[0], "application/xml");
      var rows = [];
      Array.prototype.forEach.call(doc.getElementsByTagName("row"), function (r) {
        var cells = [];
        Array.prototype.forEach.call(r.getElementsByTagName("c"), function (c) {
          var idx = colIndex(c.getAttribute("r") || "");
          var t = c.getAttribute("t");
          var val = "";
          if (t === "inlineStr") {
            var is = c.getElementsByTagName("t");
            for (var i = 0; i < is.length; i++) val += is[i].textContent;
          } else {
            var vEl = c.getElementsByTagName("v")[0];
            val = vEl ? vEl.textContent : "";
            if (t === "s") val = strs[Number(val)] || "";
          }
          while (cells.length < idx) cells.push("");
          cells[idx] = val;
        });
        rows.push(cells);
      });
      var header = (rows.shift() || []).map(strip);
      return { header: header, rows: rows, delimiter: "xlsx" };
    });
  }
  S.parseXlsx = parseXlsx;

  S.readFile = function (file) {
    var name = file.name || "upload";
    if (/\.xlsx$/i.test(name)) {
      return file.arrayBuffer().then(parseXlsx).then(function (p) {
        p.name = name; return p;
      });
    }
    return file.text().then(function (t) {
      var p = parseDelimited(t);
      p.name = name;
      return p;
    });
  };

  /* =================================================================== column mapping

     Column names drift between exports and between the people who configure them. Each field
     is found by matching the header against a list of things it is plausibly called, so a
     renamed column does not take the whole upload down. */

  var FIELDS = [
    ["date", ["posting date", "document date", "post date", "date", "belegdatum"]],
    ["doc", ["document no", "document number", "doc no", "document", "invoice no", "belegnr"]],
    ["vendor", ["vendor name", "supplier name", "vendor", "supplier", "creditor", "lifnr"]],
    ["desc", ["material description", "description", "material text", "item text", "material"]],
    ["plant", ["plant", "werks", "site"]],
    ["plantName", ["plant name", "plant description", "site name"]],
    ["qty", ["quantity", "qty", "menge", "invoice quantity"]],
    ["uom", ["uom", "unit", "unit of measure", "base unit", "meins"]],
    ["amount", ["net amount", "amount", "value", "net value", "amount in doc curr", "wrbtr"]],
    ["currency", ["currency", "curr", "doc currency", "waers"]],
    ["po", ["purchase order", "po", "po number", "ebeln"]]
  ];

  S.mapColumns = function (header) {
    var norm = header.map(function (h) { return strip(h).toLowerCase().replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim(); });
    var map = {};
    FIELDS.forEach(function (f) {
      var key = f[0], names = f[1];
      for (var ni = 0; ni < names.length; ni++) {
        var at = norm.indexOf(names[ni]);
        if (at >= 0) { map[key] = at; return; }
      }
      /* second pass: a header that contains the name rather than equalling it */
      for (ni = 0; ni < names.length; ni++) {
        for (var i = 0; i < norm.length; i++) {
          if (norm[i].indexOf(names[ni]) >= 0 && map[key] === undefined) { map[key] = i; return; }
        }
      }
    });
    var missing = ["date", "vendor", "desc", "amount"].filter(function (k) {
      return map[k] === undefined;
    });
    return { map: map, missing: missing, header: header };
  };

  /* ====================================================================== the matchers */

  S.index = function (ws) {
    var materials = (ws.materials || []).map(function (m) {
      return { id: m.id, name: m.name, category: m.category, unit: m.unit,
               tokens: descTokens(m.name) };
    });

    /* A token that appears in exactly one material's name is that material's own word.
       "corrugated" names only the cartons; "resin" only the resin. Finding one of those in a
       free-text description is much better evidence than counting how many of the name's
       other words happen to be there — a buyer writing "Corrugated box - RSC" has told you
       precisely which material it is while matching only half the name. */
    var df = {};
    materials.forEach(function (m) {
      var seen = {};
      m.tokens.forEach(function (t) {
        if (seen[t]) return;
        seen[t] = true;
        df[t] = (df[t] || 0) + 1;
      });
    });
    materials.forEach(function (m) {
      m.ident = m.tokens.filter(function (t) { return df[t] === 1; });
    });

    return {
      suppliers: (ws.suppliers || []).map(function (s) {
        var keys = [normalizeVendor(s.name)];
        (s.aliases || []).forEach(function (a) {
          var k = normalizeVendor(a);
          if (k && keys.indexOf(k) < 0) keys.push(k);
        });
        return { id: s.id, name: s.name, keys: keys, materials: s.materials || [],
                 tokens: words(normalizeVendor(s.name)) };
      }),
      materials: materials,
      df: df
    };
  };

  S.matchSupplier = function (raw, idx, learned) {
    var key = normalizeVendor(raw);
    if (!key) return { id: null, score: 0, why: "no vendor name on the line" };
    if (learned && learned[key]) {
      return { id: learned[key], score: 1, why: "a correction you made earlier" };
    }
    for (var i = 0; i < idx.suppliers.length; i++) {
      if (idx.suppliers[i].keys.indexOf(key) >= 0) {
        return { id: idx.suppliers[i].id, score: 1, why: "exact match on a known spelling" };
      }
    }
    var toks = words(key), best = null, bestScore = 0, second = 0;
    idx.suppliers.forEach(function (s) {
      var sc = 0;
      s.keys.forEach(function (k) {
        var v = nameScore(toks, words(k));
        if (v > sc) sc = v;
      });
      if (sc > bestScore) { second = bestScore; bestScore = sc; best = s; }
      else if (sc > second) second = sc;
    });
    if (!best || bestScore < 0.55) {
      return {
        id: null, score: bestScore,
        why: best ? "no known supplier matches this name; the closest is " + best.name +
                    " at " + Math.round(bestScore * 100) + "%"
                  : "no known supplier is close to this name",
        /* Rejected, but still the best thing there is. Offering it as the pre-filled answer
           turns a review item into one click without deciding anything on anyone's behalf —
           the person still has to agree. */
        suggestion: best ? { id: best.id, name: best.name, score: bestScore } : null
      };
    }
    /* A near-tie is not a match. Two suppliers that both look right is precisely the case a
       person should settle, and it is cheap for them to settle. */
    var margin = bestScore - second;
    var score = margin < 0.08 ? Math.min(bestScore, 0.6) : bestScore;
    return {
      id: best.id, score: score,
      why: "name is " + Math.round(bestScore * 100) + "% similar to " + best.name,
      suggestion: { id: best.id, name: best.name, score: bestScore }
    };
  };

  S.classify = function (desc, idx, supplierId, learned) {
    var key = descTokens(desc).join(" ");
    if (!key) return { id: null, score: 0, why: "no description on the line" };
    if (learned && learned[key]) {
      return { id: learned[key], score: 1, why: "a correction you made earlier" };
    }
    var toks = descTokens(desc);
    var sup = null;
    idx.suppliers.forEach(function (s) { if (s.id === supplierId) sup = s; });
    function inCatalogue(m) { return !!(sup && sup.materials.indexOf(m.id) >= 0); }

    /* ---- first pass: does the text use a word that names exactly one material? */
    var named = idx.materials.map(function (m) {
      var hits = [];
      (m.ident || []).forEach(function (t) {
        for (var i = 0; i < toks.length; i++) {
          if (tokenHit(t, toks[i])) { hits.push(t); return; }
        }
      });
      return { m: m, n: hits.length, hits: hits, cat: inCatalogue(m) };
    }).filter(function (x) { return x.n > 0; })
      .sort(function (a, b) {
        if (b.n !== a.n) return b.n - a.n;
        return (b.cat ? 1 : 0) - (a.cat ? 1 : 0);
      });

    if (named.length) {
      var top = named[0], next = named[1];
      var decisive = !next || top.n > next.n || (top.cat && !next.cat);
      if (decisive) {
        var cover = top.m.tokens.length
          ? overlap(top.m.tokens, toks) / top.m.tokens.length : 0;
        return {
          id: top.m.id,
          category: top.m.category,
          /* Floor of 0.78 for a decisive naming word, rising with how much of the full name
             the text also carried. */
          score: Math.min(1, 0.78 + 0.22 * cover),
          why: '"' + top.hits.join('", "') + '" name' + (top.hits.length === 1 ? "s" : "") +
               " only " + top.m.name + " in this material list"
        };
      }
    }

    /* ---- second pass: plain token overlap, with the supplier's catalogue as a prior. */
    var scored = idx.materials.map(function (m) {
      var hits = overlap(m.tokens, toks);
      var cover = m.tokens.length ? hits / m.tokens.length : 0;
      var precision = toks.length ? hits / toks.length : 0;
      var score = cover * 0.72 + precision * 0.28;
      /* The supplier's own catalogue is real evidence: a vendor that only ever sold us resin
         is unlikely to have invoiced cartons. It is a nudge, never a decision. */
      if (inCatalogue(m)) score = Math.min(1, score + 0.15);
      return { m: m, score: score, cover: cover };
    }).sort(function (a, b) { return b.score - a.score; });

    var best = scored[0], second = scored[1];
    if (!best || best.score < 0.45) {
      return {
        id: null, score: best ? best.score : 0,
        why: "nothing in the material list matches this text",
        /* Below 0.25 there is nothing worth putting in front of someone: a line reading
           "ROUNDING DIFFERENCE" should open with no answer filled in. */
        suggestion: (best && best.score >= 0.25)
          ? { id: best.m.id, name: best.m.name, score: best.score } : null
      };
    }
    var margin = best.score - (second ? second.score : 0);
    var score = margin < 0.10 ? Math.min(best.score, 0.6) : best.score;
    return {
      id: best.m.id, category: best.m.category, score: score,
      why: margin < 0.10 && second
        ? "could be " + best.m.name + " or " + second.m.name
        : Math.round(best.cover * 100) + "% of the words in " + best.m.name + " appear here",
      suggestion: { id: best.m.id, name: best.m.name, score: best.score }
    };
  };

  /* ========================================================================= the run */

  S.process = function (cfg) {
    var ws = cfg.ws;
    var parsed = cfg.parsed;
    var learned = cfg.learned || { vendor: {}, material: {} };
    var fx = cfg.fx || GD.fx;
    var home = ws.home_currency;
    var idx = S.index(ws);
    var cols = S.mapColumns(parsed.header);
    var matById = {};
    (ws.materials || []).forEach(function (m) { matById[m.id] = m; });

    var lines = [];
    var seen = {};

    parsed.rows.forEach(function (r, i) {
      function at(k) { return cols.map[k] === undefined ? "" : strip(r[cols.map[k]]); }
      var vendorRaw = at("vendor");
      var descRaw = at("desc");
      var amount = num(at("amount"));
      var cur = (at("currency") || home).toUpperCase();
      var rate = fx(cur, home);
      var sup = S.matchSupplier(vendorRaw, idx, learned.vendor);
      var mat = S.classify(descRaw, idx, sup.id, learned.material);
      var m = mat.id ? matById[mat.id] : null;
      var uom = S.uom(at("uom"));
      var qty = num(at("qty"));
      var qtyUnit = m ? S.toUnit(qty, uom, m.unit) : null;

      var problems = [];
      if (!sup.id) problems.push("supplier");
      if (!mat.id) problems.push("material");
      if (amount === null) problems.push("amount");
      if (rate === null) problems.push("currency");

      var conf = Math.min(sup.score, mat.score);
      var line = {
        i: i,
        row: r,
        date: date(at("date")),
        month: null,
        doc: at("doc"),
        po: at("po"),
        plant: at("plant"),
        plantName: at("plantName"),
        vendorRaw: vendorRaw,
        descRaw: descRaw,
        qty: qty,
        uom: uom,
        qtyUnit: qtyUnit,
        amount: amount,
        currency: cur,
        rate: rate,
        amountHome: (amount === null || rate === null) ? null : Math.round(amount * rate * 100) / 100,
        supplier_id: sup.id,
        supplierScore: sup.score,
        supplierWhy: sup.why,
        supplierSuggestion: sup.suggestion || null,
        material_id: mat.id,
        materialScore: mat.score,
        materialWhy: mat.why,
        materialSuggestion: mat.suggestion || null,
        category: m ? m.category : null,
        confidence: conf,
        problems: problems,
        duplicate: false,
        duplicateOf: null,
        resolved: false
      };
      line.month = line.date ? line.date.slice(0, 7) : null;
      line.needsReview = problems.length > 0 || conf < THRESHOLD;
      line.reviewWhy = problems.length
        ? "could not resolve the " + problems.join(" and ")
        : "confidence " + Math.round(conf * 100) + "% is under the " +
          Math.round(THRESHOLD * 100) + "% bar";
      lines.push(line);
    });

    /* Duplicates: the same document number twice, or the same vendor, text, date, quantity
       and amount posted under two numbers. The first occurrence stands; the rest are flagged
       and left out of every total. */
    lines.forEach(function (l) {
      var keys = [];
      if (l.doc) keys.push("doc:" + l.doc.toLowerCase());
      keys.push(["sig", normalizeVendor(l.vendorRaw), descTokens(l.descRaw).join("_"),
                 l.date, l.qty, l.amount].join("|"));
      for (var k = 0; k < keys.length; k++) {
        var key = keys[k];
        if (seen[key] !== undefined) {
          l.duplicate = true;
          l.duplicateOf = seen[key];
          l.duplicateWhy = k === 0 ? "document " + l.doc + " is already in this file"
                                   : "same vendor, text, date, quantity and amount as an earlier line";
          return;
        }
      }
      keys.forEach(function (key) { seen[key] = l.i; });
    });

    return S.stats({ lines: lines, columns: cols, ws: ws, name: parsed.name,
                     delimiter: parsed.delimiter });
  };

  /* Two lines that say the same thing are one decision, not two — and a decision is about
     one unresolved thing, not about the whole line. Seventy lines that all say
     "B.P. Commodities Corp." are a single question about a vendor spelling, whatever
     materials they carry. Grouping on what is actually being asked is what turns a queue of
     two hundred rows into a handful of clicks. */
  function asks(l) {
    var vendorOpen = !l.supplier_id || l.supplierScore < THRESHOLD;
    var materialOpen = !l.material_id || l.materialScore < THRESHOLD;
    return { vendor: vendorOpen, material: materialOpen };
  }
  S.asks = asks;

  S.signature = function (l) {
    var a = asks(l);
    var v = normalizeVendor(l.vendorRaw);
    var d = descTokens(l.descRaw).join(" ");
    if (a.vendor && a.material) return "both:" + v + "|" + d;
    if (a.vendor) return "vendor:" + v;
    if (a.material) return "material:" + d;
    return "line:" + l.i;
  };

  S.reviewGroups = function (run) {
    var map = {}, out = [];
    run.lines.forEach(function (l) {
      if (l.duplicate || !l.needsReview) return;
      var k = S.signature(l);
      var g = map[k];
      if (!g) {
        g = map[k] = {
          key: k, kind: k.split(":")[0], lines: [], amount: 0, sample: l,
          spellings: [], wordings: []
        };
        out.push(g);
      }
      g.lines.push(l);
      g.amount += l.amountHome || 0;
      if (g.spellings.indexOf(l.vendorRaw) < 0) g.spellings.push(l.vendorRaw);
      if (g.wordings.indexOf(l.descRaw) < 0) g.wordings.push(l.descRaw);
    });
    out.forEach(function (g) {
      g.count = g.lines.length;
      g.amount = Math.round(g.amount * 100) / 100;
      g.asks = asks(g.sample);
    });
    out.sort(function (a, b) { return b.count - a.count; });
    return out;
  };

  S.stats = function (run) {
    var lines = run.lines;
    var dupes = 0, review = 0, auto = 0;
    lines.forEach(function (l) {
      if (l.duplicate) { dupes++; return; }
      if (l.needsReview && !l.resolved) review++; else auto++;
    });
    run.counts = {
      total: lines.length,
      duplicates: dupes,
      review: review,
      auto: auto,
      decisions: S.reviewGroups(run).length
    };
    return run;
  };

  /* A reviewer's decision. Applied to this line, remembered for the next upload, and — this
     is the part that turns five days into minutes — applied to every other line in the file
     that said the same thing. */
  S.resolve = function (run, line, choice, learned) {
    var touched = 0;
    var vKey = normalizeVendor(line.vendorRaw);
    var dKey = descTokens(line.descRaw).join(" ");
    if (choice.supplier_id !== undefined && choice.supplier_id !== null && vKey) {
      learned.vendor[vKey] = choice.supplier_id;
    }
    if (choice.material_id !== undefined && choice.material_id !== null && dKey) {
      learned.material[dKey] = choice.material_id;
    }
    var matById = {};
    (run.ws.materials || []).forEach(function (m) { matById[m.id] = m; });

    run.lines.forEach(function (l) {
      var sameV = vKey && normalizeVendor(l.vendorRaw) === vKey;
      var sameD = dKey && descTokens(l.descRaw).join(" ") === dKey;
      var hit = false;
      if (choice.supplier_id !== undefined && sameV && l.supplier_id !== choice.supplier_id) {
        l.supplier_id = choice.supplier_id;
        l.supplierScore = 1;
        l.supplierWhy = "confirmed by you";
        hit = true;
      } else if (choice.supplier_id !== undefined && sameV) {
        l.supplierScore = 1; l.supplierWhy = "confirmed by you"; hit = true;
      }
      if (choice.material_id !== undefined && sameD) {
        l.material_id = choice.material_id;
        l.materialScore = 1;
        l.materialWhy = "confirmed by you";
        var m = matById[choice.material_id];
        l.category = m ? m.category : null;
        l.qtyUnit = m ? S.toUnit(l.qty, l.uom, m.unit) : null;
        hit = true;
      }
      if (!hit) return;
      l.problems = [];
      if (!l.supplier_id) l.problems.push("supplier");
      if (!l.material_id) l.problems.push("material");
      if (l.amount === null) l.problems.push("amount");
      if (l.rate === null) l.problems.push("currency");
      l.confidence = Math.min(l.supplierScore, l.materialScore);
      l.needsReview = l.problems.length > 0 || l.confidence < THRESHOLD;
      l.resolved = !l.needsReview;
      touched++;
    });
    S.stats(run);
    return touched;
  };

  /* Mark a line as genuinely unclassifiable. It stays in the file and in the grand total —
     dropping it would make the report stop reconciling with the ledger, which is the one
     thing a finance team will check. */
  S.setUnclassified = function (run, line) {
    var vKey = normalizeVendor(line.vendorRaw);
    var dKey = descTokens(line.descRaw).join(" ");
    run.lines.forEach(function (l) {
      if (normalizeVendor(l.vendorRaw) !== vKey) return;
      if (descTokens(l.descRaw).join(" ") !== dKey) return;
      l.material_id = null;
      l.category = null;
      l.materialScore = 1;
      l.materialWhy = "marked unclassifiable by you";
      l.problems = l.problems.filter(function (p) { return p !== "material"; });
      l.confidence = l.supplierScore;
      l.needsReview = l.problems.length > 0;
      l.resolved = !l.needsReview;
    });
    S.stats(run);
  };

  /* ========================================================================= the report

     Four groupings and a per-unit table. The grand total is the sum of every non-duplicate
     line, and each grouping is asserted to add back up to it — including an explicit
     Unclassified bucket, because a bucket that is quietly dropped is how a report stops
     matching the ledger. */

  S.report = function (run) {
    var ws = run.ws;
    var home = ws.home_currency;
    var matById = {};
    (ws.materials || []).forEach(function (m) { matById[m.id] = m; });
    var supById = {};
    (ws.suppliers || []).forEach(function (s) { supById[s.id] = s; });

    var included = run.lines.filter(function (l) {
      return !l.duplicate && l.amountHome !== null;
    });
    var dropped = run.lines.filter(function (l) {
      return !l.duplicate && l.amountHome === null;
    });

    function group(keyFn, labelFn) {
      var map = {}, out = [];
      included.forEach(function (l) {
        var k = keyFn(l);
        var g = map[k];
        if (!g) {
          g = map[k] = { key: k, label: labelFn(l, k), amount: 0, qty: 0, lines: 0 };
          out.push(g);
        }
        g.amount += l.amountHome;
        g.lines += 1;
        if (l.qtyUnit !== null && l.qtyUnit !== undefined) g.qty += l.qtyUnit;
      });
      out.forEach(function (g) { g.amount = Math.round(g.amount * 100) / 100; });
      out.sort(function (a, b) { return b.amount - a.amount; });
      return out;
    }

    var total = 0;
    included.forEach(function (l) { total += l.amountHome; });
    total = Math.round(total * 100) / 100;

    var byCategory = group(
      function (l) { return l.category || "unclassified"; },
      function (l, k) {
        return k === "raw" ? "Raw materials" : k === "packaging" ? "Packaging" : "Unclassified";
      });
    var byMaterial = group(
      function (l) { return l.material_id || "unclassified"; },
      function (l, k) { return matById[k] ? matById[k].name : "Unclassified"; });
    var bySupplier = group(
      function (l) { return l.supplier_id || "unknown"; },
      function (l, k) { return supById[k] ? supById[k].name : "Unmatched vendor"; });
    var byMonth = group(
      function (l) { return l.month || "unknown"; },
      function (l, k) { return k; });
    byMonth.sort(function (a, b) { return a.key < b.key ? -1 : 1; });

    /* Price paid per unit against the budget rate — the question the whole report exists to
       answer, and the one a spend cube normally cannot because it has no budget in it. */
    var perUnit = [];
    (ws.materials || []).forEach(function (m) {
      var amt = 0, qty = 0, n = 0;
      included.forEach(function (l) {
        if (l.material_id !== m.id) return;
        if (l.qtyUnit === null || l.qtyUnit === undefined || !l.qtyUnit) return;
        amt += l.amountHome; qty += l.qtyUnit; n++;
      });
      if (!n || !qty) return;
      var paid = amt / qty;
      perUnit.push({
        material: m,
        amount: Math.round(amt * 100) / 100,
        qty: Math.round(qty * 1000) / 1000,
        paid: Math.round(paid * 100) / 100,
        budget: m.budget_price,
        gap: m.budget_price === null || m.budget_price === undefined
          ? null : Math.round((paid - m.budget_price) * 100) / 100,
        gapPct: m.budget_price ? (paid - m.budget_price) / m.budget_price : null,
        lines: n
      });
    });
    perUnit.sort(function (a, b) {
      return (b.gapPct === null ? -9 : b.gapPct) - (a.gapPct === null ? -9 : a.gapPct);
    });

    function sums(g) {
      var s = 0;
      g.forEach(function (x) { s += x.amount; });
      return Math.round(s * 100) / 100;
    }
    var checks = [
      { name: "by category", total: sums(byCategory) },
      { name: "by material", total: sums(byMaterial) },
      { name: "by supplier", total: sums(bySupplier) },
      { name: "by month", total: sums(byMonth) }
    ];
    checks.forEach(function (c) { c.ok = Math.abs(c.total - total) < 0.011; });

    return {
      currency: home,
      total: total,
      included: included.length,
      duplicates: run.counts.duplicates,
      dropped: dropped.length,
      byCategory: byCategory,
      byMaterial: byMaterial,
      bySupplier: bySupplier,
      byMonth: byMonth,
      perUnit: perUnit,
      checks: checks,
      reconciles: checks.every(function (c) { return c.ok; })
    };
  };

  /* ---------------------------------------------------------------- learned mappings */

  S.loadLearned = function (wsKey) {
    try {
      var raw = window.localStorage.getItem("gd.spend.map." + wsKey);
      var o = raw ? JSON.parse(raw) : null;
      if (o && o.vendor && o.material) return o;
    } catch (e) { /* fall through */ }
    return { vendor: {}, material: {} };
  };
  S.saveLearned = function (wsKey, learned) {
    try {
      window.localStorage.setItem("gd.spend.map." + wsKey, JSON.stringify(learned));
      return true;
    } catch (e) { return false; }
  };
  S.clearLearned = function (wsKey) {
    try { window.localStorage.removeItem("gd.spend.map." + wsKey); } catch (e) { /* ok */ }
  };
  S.countLearned = function (learned) {
    return Object.keys(learned.vendor).length + Object.keys(learned.material).length;
  };

})();
