"""Drive the three new flows through the real app, in headless Edge.

tools/test_app.py proves every page renders. This proves the flows work: the planted quote
opens above its band and produces a brief with no untraceable figure in it, the messy spend
sample processes end to end and clears its review queue without moving the grand total, and
the bulletin cannot be exported until somebody approves it.

    py -3.11 tools/test_flows.py
    py -3.11 tools/test_flows.py --verbose
    py -3.11 tools/test_flows.py --keep        # leave the probe page for manual poking

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

APP = ROOT / "app"

# The flows start from a clean slate: no remembered corrections, no previous bulletin, and
# the wheat desk as the active workspace, which is what a fresh browser gets.
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
try { localStorage.clear(); } catch (e) {}
</script>
"""

TAIL = """
<script src="/tools/flow_tests.js"></script>
<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    var n = document.createElement('div');
    n.id = '__errs';
    n.textContent = JSON.stringify(window.__errs);
    document.body.appendChild(n);
  }, 55000);
});
</script>
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


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
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    body = (APP / "index.html").read_text(encoding="utf-8")
    probe = APP / "_flow.html"
    probe.write_text(TRAP + body + TAIL, encoding="utf-8")
    try:
        with serve(ROOT) as port:
            url = "http://127.0.0.1:%d/app/_flow.html" % port
            r = subprocess.run(
                [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=90000", "--window-size=1440,1000", "--dump-dom", url],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            dom = r.stdout or ""
    finally:
        if not args.keep:
            probe.unlink(missing_ok=True)

    errs = []
    m = re.search(r'id="__errs">(.*?)</div>', dom, re.S)
    if m:
        try:
            errs = json.loads(html.unescape(m.group(1)))
        except Exception:
            errs = [html.unescape(m.group(1))]

    m = re.search(r'id="__flow_results">(.*?)</div>', dom, re.S)
    if not m:
        print("no results marker — the flows did not finish.")
        for e in errs:
            print("  error: %s" % e)
        if not errs:
            print(dom[-3000:])
        return 1

    payload = json.loads(html.unescape(m.group(1)))
    width = max(len(x["name"]) for x in payload["results"]) + 2
    for x in payload["results"]:
        if x["pass"]:
            if args.verbose:
                print("  ok    %-*s %s" % (width, x["name"], x["detail"]))
            else:
                print("  ok    %s" % x["name"])
        else:
            print("  FAIL  %-*s %s" % (width, x["name"], x["detail"]))

    for e in errs:
        print("  ERROR %s" % e)

    bad = payload["failed"] + len(errs)
    print("\n%d/%d flow assertions passed%s"
          % (payload["total"] - payload["failed"], payload["total"],
             "" if not errs else ", %d page errors" % len(errs)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
