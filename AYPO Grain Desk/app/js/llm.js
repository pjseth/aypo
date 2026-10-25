/* Grain Desk — the one place the app talks to a model.

   URC named hallucination and over-automation as the two things that would stop them trusting
   this. So the contract here is narrow and it is enforced in code, not in a prompt:

     1. The model is given a **fact pack** and nothing else. It never sees the database, it is
        never asked to recall anything, and it is told in the system prompt that every figure
        it writes must come from the pack.
     2. Whatever comes back is **checked number by number** against that pack before a human
        sees it. One figure that is not traceable and the model's draft is discarded in favour
        of the deterministic template — visibly, with a note saying why.
     3. There is always a template. With no API key — which is the default, and how the demo
        runs — every feature still produces its output. The model is an improvement to the
        prose, never a dependency for the facts.
     4. Nothing is sent anywhere. The brief and the bulletin are drafts a person edits.

   The key is read from `window.GD_ENV.ANTHROPIC_API_KEY` (written by tools/make_env.py from
   the environment, and gitignored) or from localStorage. Calling the API straight from a page
   means shipping the key to the browser, which is a demo affordance and not a design: a
   production build puts this behind the customer's own server, and the UI says so. */
(function () {
  "use strict";

  var L = {};
  GD.llm = L;

  var ENDPOINT = "https://api.anthropic.com/v1/messages";
  var MODEL = "claude-opus-5";
  var LS_KEY = "gd.anthropic_key";

  L.model = MODEL;

  function fromEnv() {
    var e = window.GD_ENV;
    return (e && e.ANTHROPIC_API_KEY) || null;
  }
  function fromLocal() {
    try { return window.localStorage.getItem(LS_KEY) || null; } catch (e) { return null; }
  }

  L.key = function () { return fromEnv() || fromLocal(); };
  L.keySource = function () {
    if (fromEnv()) return "env";
    if (fromLocal()) return "browser";
    return null;
  };
  L.available = function () { return !!L.key(); };
  L.setKey = function (k) {
    try {
      if (k) window.localStorage.setItem(LS_KEY, k);
      else window.localStorage.removeItem(LS_KEY);
      return true;
    } catch (e) { return false; }
  };

  /* ------------------------------------------------------------------------ the call */

  L.complete = function (cfg) {
    var key = L.key();
    if (!key) return Promise.reject(new Error("no API key"));
    var body = {
      model: MODEL,
      max_tokens: cfg.maxTokens || 8000,
      system: cfg.system,
      messages: [{ role: "user", content: cfg.prompt }],
      output_config: { effort: "low" }
    };
    return fetch(ENDPOINT, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        /* Required for a browser-origin call. Demo affordance — see the header comment. */
        "anthropic-dangerous-direct-browser-access": "true"
      },
      body: JSON.stringify(body)
    }).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (t) {
          throw new Error("HTTP " + res.status + " " + t.slice(0, 200));
        });
      }
      return res.json();
    }).then(function (data) {
      if (data.stop_reason === "refusal") throw new Error("the model declined this request");
      var text = (data.content || []).filter(function (b) { return b.type === "text"; })
        .map(function (b) { return b.text; }).join("").trim();
      if (!text) throw new Error("empty response");
      return text;
    });
  };

  /* -------------------------------------------------------------- the number check

     Every figure in a generated document has to be traceable to the pack it was generated
     from. This pulls the numbers out of both and compares them.

     Rounding is allowed — a model writing "₱269,405" for 269405.12 is being helpful, not
     inventing — so a written figure matches if it is within 1% of a fact, or if it is the
     fact rounded to any number of places. Bare small integers (list numbering, quarters,
     month counts, the 25 and 75 of the quartiles) are allowed through unconditionally,
     because requiring the pack to contain "3" to permit "3." in a numbered list would make
     the check useless noise. */

  var FREE = {};
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 25, 50, 75, 100].forEach(function (n) {
    FREE[n] = true;
  });

  function numbersIn(text) {
    var out = [];
    var re = /-?\d[\d,]*(?:\.\d+)?/g;
    var m;
    while ((m = re.exec(String(text))) !== null) {
      var v = parseFloat(m[0].replace(/,/g, ""));
      if (!isNaN(v)) out.push({ raw: m[0], value: v, at: m.index });
    }
    return out;
  }
  L.numbersIn = numbersIn;

  function factNumbers(facts) {
    var seen = {}, out = [];
    numbersIn(typeof facts === "string" ? facts : JSON.stringify(facts)).forEach(function (n) {
      if (!seen[n.value]) { seen[n.value] = true; out.push(n.value); }
    });
    return out;
  }
  L.factNumbers = factNumbers;

  function matches(v, facts) {
    for (var i = 0; i < facts.length; i++) {
      var f = facts[i];
      if (v === f) return true;
      if (Math.abs(f) > 1e-9 && Math.abs((v - f) / f) <= 0.01) return true;
      /* the fact rounded to the places the writer used */
      for (var dp = 0; dp <= 4; dp++) {
        var p = Math.pow(10, dp);
        if (Math.round(f * p) / p === v) return true;
      }
    }
    return false;
  }

  L.verifyNumbers = function (text, facts) {
    var allowed = factNumbers(facts);
    var found = numbersIn(text);
    var bad = [], details = [];
    found.forEach(function (n) {
      if (FREE[Math.abs(n.value)] && Math.abs(n.value) < 101 && n.value === Math.round(n.value)) return;
      if (matches(n.value, allowed)) return;
      bad.push(n.raw);
      /* The surrounding words, so a rejection can say *where* the stray figure was rather
         than just that there was one. */
      details.push({
        raw: n.raw,
        context: String(text).slice(Math.max(0, n.at - 48), n.at + n.raw.length + 48)
          .replace(/\s+/g, " ").trim()
      });
    });
    return { ok: bad.length === 0, unverified: bad, details: details, checked: found.length };
  };

  /* --------------------------------------------------------------------- the one door

     Surfaces call this and nothing else. It always resolves — never rejects — with a draft
     and an honest account of where the draft came from.

     It also *builds* the prompt, from the instruction, the fact pack and the app's own draft.
     That is deliberate: "the model is given the fact pack and nothing else" is the whole
     claim, and a claim that depends on every call site remembering to paste the facts in is
     not a claim, it is a hope. Assembling it here means the pack that is checked against is
     by construction the pack that was sent. */

  function factsText(facts) {
    return typeof facts === "string" ? facts : JSON.stringify(facts, null, 1);
  }

  L.buildPrompt = function (cfg) {
    return [
      cfg.instruction || "Rewrite the DRAFT below more cleanly, using only the FACTS.",
      "",
      "FACTS",
      factsText(cfg.facts),
      "",
      "DRAFT",
      cfg.template
    ].join("\n");
  };

  L.draft = function (cfg) {
    var fallback = {
      text: cfg.template,
      source: "template",
      note: "Written by the app from the figures on screen. No model was called."
    };
    if (!L.available()) {
      return Promise.resolve(fallback);
    }
    return L.complete({
      system: cfg.system + "\n\n" + GUARDRAIL,
      prompt: L.buildPrompt(cfg),
      maxTokens: cfg.maxTokens
    }).then(function (text) {
      var check = L.verifyNumbers(text, cfg.facts);
      if (!check.ok) {
        return {
          text: cfg.template,
          source: "template",
          rejected: true,
          note: "The model's draft contained " + check.unverified.length + " figure" +
                (check.unverified.length === 1 ? "" : "s") + " that could not be traced to the " +
                "data on screen (" + check.unverified.slice(0, 4).join(", ") +
                "). It was discarded and the app wrote this instead."
        };
      }
      return {
        text: text,
        source: "model",
        checked: check.checked,
        note: MODEL + " wrote this from the fact pack below. All " + check.checked +
              " figures in it trace back to that pack."
      };
    }).catch(function (err) {
      return {
        text: cfg.template,
        source: "template",
        error: String(err && err.message || err),
        note: "The model call failed (" + String(err && err.message || err).slice(0, 90) +
              "). The app wrote this instead — every figure is still from the data on screen."
      };
    });
  };

  var GUARDRAIL = [
    "HARD RULES, which override anything else:",
    "· Every number you write must appear in the FACTS block of the user message. Do not",
    "  compute new figures, do not round to a different magnitude, and do not estimate.",
    "· If a fact you want is not in the FACTS block, say it is not available rather than",
    "  supplying it from memory. You have no knowledge of this company or its suppliers.",
    "· Name the source next to each figure, using the source strings given in the FACTS.",
    "· This is a draft for a human to edit and send. Never address the supplier as though",
    "  the message is already going out, and never claim anything has been agreed."
  ].join("\n");
  L.guardrail = GUARDRAIL;

  /* A short, honest description of what is running, for the footer of anything generated. */
  L.statusLine = function () {
    var src = L.keySource();
    if (!src) {
      return "No API key present — every draft on this desk is written by the app itself " +
             "from the figures on screen.";
    }
    return "An API key is present (" + (src === "env" ? "build environment" : "this browser") +
           "), so drafts are written by " + MODEL + " from a fact pack and then checked " +
           "number by number against it.";
  };

})();
