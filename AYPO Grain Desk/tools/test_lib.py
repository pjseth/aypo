"""Run the pure-module assertions in tools/lib_tests.js through headless Edge.

There is no Node on this machine, so the only JS engine available is the browser. Same trick
as tools/jscheck.py: serve the tree over a throwaway localhost server so errors arrive with a
file and a line instead of the opaque "Script error." that file:// gives you.

    py -3.11 tools/test_lib.py            # pass/fail per assertion
    py -3.11 tools/test_lib.py --verbose  # also print the detail on passing checks

Exit status is 0 only when every assertion passed and nothing was logged to console.error.
"""

import argparse
import contextlib
import functools
import html
import http.server
import json
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


# Everything the pure modules need, and nothing that touches the DOM shell.
ORDER = [
    "app/js/data.js",
    "app/js/ws.js",
    "app/js/spend_sample.js",
    "app/js/core.js",
    "app/js/workspace.js",
    "app/js/pricing.js",
    "app/js/llm.js",
    "app/js/spendlib.js",
    "tools/lib_tests.js",
]

TRAP = """<!doctype html><meta charset="utf-8"><title>PENDING</title>
<script>
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
</script>
<body>
"""

TAIL = """
<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var n = document.createElement('div');
    n.id = '__errs';
    n.textContent = JSON.stringify(window.__errs);
    document.body.appendChild(n);
  }, 1200);
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    page = ROOT / "_libtest.html"
    page.write_text(
        TRAP + "".join('<script src="/%s"></script>\n' % p for p in ORDER) + TAIL,
        encoding="utf-8")
    try:
        with serve(ROOT) as port:
            url = "http://127.0.0.1:%d/_libtest.html" % port
            r = subprocess.run(
                [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=12000", "--window-size=1200,900", "--dump-dom", url],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
            dom = r.stdout or ""
    finally:
        page.unlink(missing_ok=True)

    errs = []
    m = re.search(r'id="__errs">(.*?)</div>', dom, re.S)
    if m:
        try:
            errs = json.loads(html.unescape(m.group(1)))
        except Exception:
            errs = [html.unescape(m.group(1))]

    m = re.search(r'id="__lib_results">(.*?)</div>', dom, re.S)
    if not m:
        print("no results marker — the page did not finish.")
        for e in errs:
            print("  error: %s" % e)
        if not errs:
            print(dom[-2500:])
        return 1

    payload = json.loads(html.unescape(m.group(1)))
    width = max(len(r["name"]) for r in payload["results"]) + 2
    for r in payload["results"]:
        if r["pass"]:
            if args.verbose:
                print("  ok    %-*s %s" % (width, r["name"], r["detail"]))
            else:
                print("  ok    %s" % r["name"])
        else:
            print("  FAIL  %-*s %s" % (width, r["name"], r["detail"]))

    for e in errs:
        print("  ERROR %s" % e)

    bad = payload["failed"] + len(errs)
    print("\n%d/%d assertions passed%s"
          % (payload["total"] - payload["failed"], payload["total"],
             "" if not errs else ", %d page errors" % len(errs)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
