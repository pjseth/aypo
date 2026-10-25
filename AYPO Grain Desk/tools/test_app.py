"""End-to-end checks for the Grain Desk app, driven through headless Edge.

Walks every route in every workspace and fails on:
  · any uncaught error, unhandled rejection, or console.error
  · a route that renders the shell's error card instead of its surface
  · a route whose expected marker text is missing from the DOM
  · common correctness smells in the rendered output (NaN, undefined, [object Object])

The wheat desk is workspace `ufm` and is the default; the seven surfaces that predate
materials are checked there, exactly as before. The three new surfaces are checked in both.

    py -3.11 tools/test_app.py                 # every route, every workspace
    py -3.11 tools/test_app.py --shots         # also write screenshots to app/_shots
    py -3.11 tools/test_app.py --route quotes
    py -3.11 tools/test_app.py --ws urc
"""

import argparse
import contextlib
import functools
import html
import http.server
import pathlib
import re
import socketserver
import subprocess
import sys
import threading

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# The desk prints pesos and baht. A Windows console defaults to cp1252, which cannot encode
# either, and a test run should not die on its own output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP = ROOT / "app"

# route key -> a string that must appear in the rendered DOM for that surface
MARKERS = {
    "brief":      "Morning brief",
    "quotes":     "Quote board",
    "position":   "Position book",
    "market":     "Market",
    "cargo":      "Cargo",
    "margin":     "Blend",
    "savings":    "Savings",
    "pricecheck": "Price check",
    "spend":      "Spend cleansing",
    "bulletin":   "Market bulletin",
}

# which routes each workspace serves, mirroring the `ws` field on each surface
WORKSPACES = {
    "ufm": ["brief", "quotes", "position", "market", "cargo", "margin", "savings",
            "pricecheck", "spend", "bulletin"],
    "urc": ["pricecheck", "spend", "bulletin"],
}

TRAP = """<script>
window.__errs = [];
window.addEventListener('error', function (e) {
  window.__errs.push((e.filename ? e.filename.split('/').pop() : '?') + ':' + e.lineno + ' ' +
                     (e.message || (e.error && e.error.message) || 'error'));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__errs.push('rejection: ' + ((e.reason && e.reason.message) || e.reason));
});
(function () { var ce = console.error; console.error = function () {
  window.__errs.push('console.error: ' + Array.prototype.map.call(arguments, function (a) {
    return (a && a.message) ? a.message : String(a); }).join(' '));
  return ce.apply(console, arguments); }; })();
try {
  localStorage.setItem('graindesk-workspace', '%s');
  localStorage.removeItem('gd.spend.map.%s');
  localStorage.removeItem('gd.bulletin.last.%s');
} catch (e) {}
</script>
"""

STAMP = """<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var marker = document.createElement('div');
    marker.id = '__result';
    marker.textContent = window.__errs.length ? ('FAIL ' + window.__errs.join(' || ')) : 'OK';
    document.body.appendChild(marker);
    document.title = window.__errs.length ? 'FAIL' : 'OK';
  }, 900);
});
</script>
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


# Threaded: the page now pulls twenty-odd scripts, and a single-threaded server drops one
# under that burst. A script that silently never arrives shows up as a baffling "X is not a
# function" from whichever file loaded after it.
class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextlib.contextmanager
def serve(directory):
    handler = functools.partial(Quiet, directory=str(directory))
    with Server(("127.0.0.1", 0), handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()


def probe_for(wskey):
    body = (APP / "index.html").read_text(encoding="utf-8")
    probe = APP / ("_probe_%s.html" % wskey)
    probe.write_text(TRAP % (wskey, wskey, wskey) + body + STAMP, encoding="utf-8")
    return probe


def render(url, shot=None):
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--virtual-time-budget=9000", "--window-size=1440,960"]
    if shot:
        cmd += ["--screenshot=" + str(shot)]
    else:
        cmd += ["--dump-dom"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    return r.stdout or "", r.stderr or ""


def text_of(dom):
    """Rough visible-text extraction, enough for marker and smell checks."""
    t = re.sub(r"<script.*?</script>", " ", dom, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(re.sub(r"\s+", " ", t))


SMELLS = [
    (r"\bNaN\b", "NaN rendered"),
    (r"\bundefined\b", "'undefined' rendered"),
    (r"\[object Object\]", "[object Object] rendered"),
    (r"\$NaN|฿NaN|₱NaN", "malformed currency"),
    (r"Infinity", "Infinity rendered"),
    (r"This surface failed to render", "surface threw and hit the shell error card"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", help="only this route")
    ap.add_argument("--ws", help="only this workspace")
    ap.add_argument("--shots", action="store_true", help="write screenshots")
    args = ap.parse_args()

    spaces = {args.ws: WORKSPACES[args.ws]} if args.ws else WORKSPACES
    shots_dir = APP / "_shots"
    if args.shots:
        shots_dir.mkdir(exist_ok=True)

    probes = {}
    failures = []
    total = 0
    try:
        for wskey in spaces:
            probes[wskey] = probe_for(wskey)
        with serve(ROOT) as port:
            base = "http://127.0.0.1:%d/app" % port
            for wskey, keys in spaces.items():
                pname = probes[wskey].name
                for key in keys:
                    if args.route and key != args.route:
                        continue
                    total += 1
                    marker = MARKERS[key]
                    url = "%s/%s#%s" % (base, pname, key)
                    dom, err = render(url)
                    label = "%-5s %-11s" % (wskey, key)

                    result = ""
                    m = re.search(r'id="__result">(.*?)</div>', dom, re.S)
                    if m:
                        result = html.unescape(m.group(1)).strip()

                    probs = []
                    if not result:
                        probs.append("no result marker — page did not settle")
                    elif result.startswith("FAIL"):
                        probs += [e.strip() for e in result[4:].split("||") if e.strip()]

                    txt = text_of(dom)
                    if marker.lower() not in txt.lower():
                        probs.append("marker %r missing" % marker)
                    for pat, why in SMELLS:
                        if re.search(pat, txt):
                            probs.append(why)

                    if probs:
                        failures.append((key, wskey, probs))
                        print("  FAIL  %s  %s" % (label, probs[0]))
                        for extra in probs[1:]:
                            print("                     %s" % extra)
                    else:
                        print("  ok    %s  (%d chars rendered)" % (label, len(txt)))

                    if args.shots:
                        render(url, shot=shots_dir / ("%s-%s.png" % (wskey, key)))
    finally:
        for p in probes.values():
            p.unlink(missing_ok=True)

    print("\n%d/%d workspace-route combinations clean" % (total - len(failures), total))
    if args.shots:
        print("screenshots in %s" % shots_dir)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
