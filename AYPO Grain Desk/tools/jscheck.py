"""Syntax- and load-check JavaScript without Node, and console-check the whole app.

There is no node/npm on this machine, so the only real JS engine available is the browser.
The first version loaded the scripts over `file://` and every failure came back as the
useless `"Script error."` - file:// scripts are an opaque origin, so the browser withholds
the message, the file and the line. Serving the tree over a throwaway localhost HTTP server
makes everything same-origin, so errors arrive complete with stack.

    py -3.11 tools/jscheck.py app/js/surfaces/brief.js   # one file, on top of core
    py -3.11 tools/jscheck.py --app                      # every file in load order
    py -3.11 tools/jscheck.py --page app/index.html      # the real page, console included

Exit status is 0 only when nothing threw and nothing was logged to console.error.
"""

import argparse
import contextlib
import functools
import html
import http.server
import pathlib
import re
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# The desk prints pesos and baht. A Windows console defaults to cp1252, which cannot encode
# either, and a test run should not die on its own output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


APP_ORDER = [
    "app/js/ws.js",
    "app/js/spend_sample.js",
    "app/js/env.js",
    "app/js/core.js",
    "app/js/workspace.js",
    "app/js/export.js",
    "app/js/pricing.js",
    "app/js/spendlib.js",
    "app/js/llm.js",
    "app/js/surfaces/prices.js",
    "app/js/surfaces/pricecheck.js",
    "app/js/surfaces/spend.js",
    "app/js/surfaces/bulletin.js",
    "app/js/app.js",
]

# Trap everything a broken surface can do: throw at parse, throw at run, reject a promise,
# or quietly console.error. The result is stamped into <title> for --dump-dom to read.
HARNESS_HEAD = """<!doctype html><meta charset="utf-8"><title>PENDING</title>
<script>
window.__errs = [];
window.addEventListener('error', function (e) {
  var where = (e.filename ? e.filename.split('/').pop() : '?') + ':' + e.lineno + ':' + e.colno;
  window.__errs.push(where + ' ' + (e.message || (e.error && e.error.message) || 'error'));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__errs.push('unhandled rejection: ' + ((e.reason && e.reason.message) || e.reason));
});
(function () {
  var ce = console.error;
  console.error = function () {
    window.__errs.push('console.error: ' + Array.prototype.map.call(arguments, function (a) {
      return (a && a.message) ? a.message : String(a);
    }).join(' '));
    return ce.apply(console, arguments);
  };
})();
</script>
"""

HARNESS_TAIL = """
<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    document.title = window.__errs.length ? ('FAIL ' + window.__errs.join(' || ')) : 'OK';
  }, %d);
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
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            yield port
        finally:
            httpd.shutdown()


def render(url, timeout=180):
    r = subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=8000", "--dump-dom", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    dom = r.stdout or ""
    m = re.search(r"<title>(.*?)</title>", dom, re.S)
    return (html.unescape(m.group(1).strip()) if m else ""), dom, (r.stderr or "")


def verdict(title, stderr):
    if title == "OK":
        return 0, []
    if title.startswith("FAIL"):
        return 1, [e.strip() for e in title[4:].split("||") if e.strip()]
    return 3, ["could not read a result (title=%r)" % title] + stderr.strip().splitlines()[-4:]


def check_scripts(files, settle_ms):
    tags = []
    for f in files:
        p = (ROOT / f) if not pathlib.Path(f).is_absolute() else pathlib.Path(f)
        if not p.exists():
            return 2, ["missing file: %s" % f]
        try:
            rel = p.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            # outside the tree — copy it in so it is reachable over HTTP
            dst = ROOT / "app" / ("_tmp_" + p.name)
            dst.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            rel = dst.relative_to(ROOT).as_posix()
        tags.append('<script src="/%s"></script>' % rel)

    page = ROOT / "_jscheck.html"
    page.write_text(HARNESS_HEAD + "\n".join(tags) + (HARNESS_TAIL % settle_ms), encoding="utf-8")
    try:
        with serve(ROOT) as port:
            title, _dom, err = render("http://127.0.0.1:%d/_jscheck.html" % port)
        return verdict(title, err)
    finally:
        page.unlink(missing_ok=True)
        for leftover in (ROOT / "app").glob("_tmp_*"):
            leftover.unlink(missing_ok=True)


def check_page(rel_page, settle_ms):
    """Load the real page and report anything it logs or throws."""
    src = (ROOT / rel_page).resolve()
    if not src.exists():
        return 2, ["missing page: %s" % rel_page], ""
    body = src.read_text(encoding="utf-8")
    probe = src.parent / ("_probe_" + src.name)
    probe.write_text(HARNESS_HEAD + body + (HARNESS_TAIL % settle_ms), encoding="utf-8")
    try:
        with serve(ROOT) as port:
            url = "http://127.0.0.1:%d/%s" % (port, probe.relative_to(ROOT).as_posix())
            title, dom, err = render(url)
        code, errs = verdict(title, err)
        return code, errs, dom
    finally:
        probe.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--app", action="store_true", help="every app script, in load order")
    ap.add_argument("--page", help="load a real HTML page and check its console")
    ap.add_argument("--settle", type=int, default=500)
    ap.add_argument("--expect", help="substring that must appear in the rendered DOM")
    args = ap.parse_args()

    if args.page:
        code, errs, dom = check_page(args.page, args.settle)
        print("page: %s" % args.page)
        if code == 0 and args.expect:
            if args.expect not in dom:
                print("\nFAILED - expected text not found in the DOM: %r" % args.expect)
                return 4
            print("  found %r in the rendered DOM" % args.expect)
        if code == 0:
            print("\nOK - page loaded with no errors and nothing on console.error")
        else:
            print("\nFAILED (%d)" % code)
            for e in errs:
                print("  ! %s" % e)
        return code

    files = APP_ORDER if args.app else list(args.files)
    if not files:
        ap.error("give files, or --app, or --page")
    if not args.app:
        base = [d for d in ("app/js/data.js", "app/js/core.js")
                if not any(pathlib.Path(f).name == pathlib.Path(d).name for f in files)]
        files = base + files

    code, errs = check_scripts(files, args.settle)
    for f in files:
        print("  loaded %s" % f)
    if code == 0:
        print("\nOK - %d file(s) parsed and executed cleanly" % len(files))
    else:
        print("\nFAILED (%d)" % code)
        for e in errs:
            print("  ! %s" % e)
    return code


if __name__ == "__main__":
    sys.exit(main())
