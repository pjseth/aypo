"""Screenshot the app in states that need a human eye, not just a passing assertion.

tools/test_app.py --shots captures every route as it first paints, which for the spend and
bulletin surfaces is their empty state. These scenarios drive the app to the states the demo
actually shows - a loaded spend file, a generated bulletin, the price-check sheet open on the
planted quote - and photograph those.

    py -3.11 tools/shots.py                    # all scenarios
    py -3.11 tools/shots.py --only urc-brief
    py -3.11 tools/shots.py --list

Output lands in app/_shots/, which is gitignored.
"""

import argparse
import contextlib
import functools
import http.server
import pathlib
import socketserver
import subprocess
import sys
import threading

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP = ROOT / "app"
OUT = APP / "_shots"

# Each scenario is a workspace, a route, and a snippet that drives the page into the state
# worth looking at. The snippet runs after boot and has the whole of GD available.
SCENARIOS = {
    "market": ("urc", "prices", "", 2200),
    "market-bottom": ("urc", "prices", """
        var h = document.querySelectorAll('.card-title');
        for (var i = 0; i < h.length; i++) {
          if (h[i].textContent.indexOf('What that did') >= 0) {
            h[i].closest('.card').scrollIntoView({ block: 'start' }); break;
          }
        }
    """, 2600),
    "market-coffee": ("urc", "prices", """
        var rows = document.querySelectorAll('.wrap .pal-item');
        for (var i = 0; i < rows.length; i++) {
          if (rows[i].textContent.indexOf('Robusta') >= 0) { rows[i].click(); break; }
        }
        setTimeout(function () {
          var b = document.querySelectorAll('.seg button');
          for (var i = 0; i < b.length; i++) {
            if (b[i].textContent === '1Y') { b[i].click(); break; }
          }
        }, 500);
    """, 3400),
    "pricecheck": ("urc", "pricecheck", "", 2000),
    "pricecheck-sheet": ("urc", "pricecheck", """
        var q = GD.pricing.latestPerSupplier('green_coffee', GD.ws().quotes)
          .filter(function (x) { return x.supplier_id === 'kalinga'; })[0];
        GD.showPriceCheck(q, GD.ws());
    """, 3000),
    "brief": ("urc", "pricecheck", """
        var q = GD.pricing.latestPerSupplier('green_coffee', GD.ws().quotes)
          .filter(function (x) { return x.supplier_id === 'kalinga'; })[0];
        GD.showPriceCheck(q, GD.ws());
        setTimeout(function () {
          var fs = document.querySelectorAll('.sheet-foot button');
          for (var i = 0; i < fs.length; i++) {
            if (fs[i].textContent.indexOf('Negotiation brief') >= 0) { fs[i].click(); return; }
          }
        }, 500);
    """, 4000),
    "spend-empty": ("urc", "spend", "", 1600),
    "spend-loaded": ("urc", "spend", """
        var bs = document.querySelectorAll('button');
        for (var i = 0; i < bs.length; i++) {
          if (bs[i].textContent.indexOf('Load the sample export') >= 0) { bs[i].click(); break; }
        }
    """, 3200),
    "spend-review": ("urc", "spend", """
        var bs = document.querySelectorAll('button');
        for (var i = 0; i < bs.length; i++) {
          if (bs[i].textContent.indexOf('Load the sample export') >= 0) { bs[i].click(); break; }
        }
        setTimeout(function () {
          var h = document.querySelectorAll('.card-title');
          for (var i = 0; i < h.length; i++) {
            if (h[i].textContent.indexOf('Review queue') >= 0) {
              h[i].closest('.card').scrollIntoView({ block: 'start' }); return;
            }
          }
        }, 1400);
    """, 3600),
    "spend-report": ("urc", "spend", """
        var bs = document.querySelectorAll('button');
        for (var i = 0; i < bs.length; i++) {
          if (bs[i].textContent.indexOf('Load the sample export') >= 0) { bs[i].click(); break; }
        }
        setTimeout(function () {
          var h = document.querySelectorAll('.card-title');
          for (var i = 0; i < h.length; i++) {
            if (h[i].textContent.indexOf('By material') >= 0) {
              h[i].closest('.card').scrollIntoView({ block: 'start' }); return;
            }
          }
        }, 1400);
    """, 3600),
    "bulletin-empty": ("urc", "bulletin", "", 1600),
    "bulletin-draft": ("urc", "bulletin", """
        var bs = document.querySelectorAll('button');
        for (var i = 0; i < bs.length; i++) {
          if (bs[i].textContent.indexOf('Generate draft') >= 0) { bs[i].click(); break; }
        }
    """, 3200),
    "method": ("urc", "pricecheck", """
        var bs = document.querySelectorAll('.ph-actions button');
        for (var i = 0; i < bs.length; i++) {
          if (bs[i].textContent.indexOf('How the band is built') >= 0) { bs[i].click(); break; }
        }
    """, 3000),
}

# The workspace comes from the URL, exactly as a demo link would carry it, so the entry point
# is exercised rather than simulated.
HEAD = """<script>
try { localStorage.clear(); } catch (e) {}
</script>
"""

TAIL = """
<script>
window.addEventListener('load', function () {
  setTimeout(function () { try { %s } catch (e) { console.log(e); } }, 700);
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
    ap.add_argument("--only", action="append", help="one scenario name; repeatable")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--size", default="1600,1100")
    args = ap.parse_args()

    if args.list:
        for k in SCENARIOS:
            print("  %s" % k)
        return 0

    names = args.only or list(SCENARIOS)
    OUT.mkdir(exist_ok=True)
    probe = APP / "_shot.html"
    body = (APP / "index.html").read_text(encoding="utf-8")

    try:
        with serve(ROOT) as port:
            for name in names:
                if name not in SCENARIOS:
                    print("  ?     %s is not a scenario" % name)
                    continue
                wskey, route, script, budget = SCENARIOS[name]
                probe.write_text(HEAD + body + TAIL % script, encoding="utf-8")
                url = "http://127.0.0.1:%d/app/_shot.html?ws=%s#%s" % (port, wskey, route)
                shot = OUT / (name + ".png")
                r = subprocess.run(
                    [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
                     "--virtual-time-budget=%d" % budget,
                     "--window-size=" + args.size,
                     "--screenshot=" + str(shot), url],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=180)
                size = shot.stat().st_size if shot.exists() else 0
                print("  %-22s %s  (%.0f KB)" % (name, shot.name, size / 1024))
    finally:
        probe.unlink(missing_ok=True)

    print("\n%d shots in %s" % (len(names), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
