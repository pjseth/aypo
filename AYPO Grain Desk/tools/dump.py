"""Dump a generated document to the terminal, so its prose can actually be read.

A test can assert that a bulletin contains the word "No data"; only a person reading it can
tell whether the sentence around that is any good.

    py -3.11 tools/dump.py bulletin          # the generated market bulletin, urc
    py -3.11 tools/dump.py bulletin --ws ufm
    py -3.11 tools/dump.py brief             # the negotiation brief for the planted quote
    py -3.11 tools/dump.py brief-print       # the same, as the HTML that goes to the printer
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP = ROOT / "app"

DRIVERS = {
    "bulletin": """
        GD.go('bulletin');
        setTimeout(function () {
          var bs = document.querySelectorAll('button');
          for (var i = 0; i < bs.length; i++) {
            if (bs[i].textContent.indexOf('Generate draft') >= 0) { bs[i].click(); break; }
          }
          setTimeout(function () { out(document.querySelector('textarea').value); }, 1200);
        }, 600);
    """,
    "brief": """
        GD.go('pricecheck');
        setTimeout(function () {
          var q = GD.pricing.latestPerSupplier('green_coffee', GD.ws().quotes)
            .filter(function (x) { return x.supplier_id === 'kalinga'; })[0];
          GD.showPriceCheck(q, GD.ws());
          setTimeout(function () {
            var fs = document.querySelectorAll('.sheet-foot button');
            for (var i = 0; i < fs.length; i++) {
              if (fs[i].textContent.indexOf('Negotiation brief') >= 0) { fs[i].click(); break; }
            }
            setTimeout(function () {
              var tas = document.querySelectorAll('.scrim textarea');
              out(tas[tas.length - 1].value);
            }, 900);
          }, 700);
        }, 600);
    """,
    "brief-print": """
        GD.go('pricecheck');
        setTimeout(function () {
          var q = GD.pricing.latestPerSupplier('green_coffee', GD.ws().quotes)
            .filter(function (x) { return x.supplier_id === 'kalinga'; })[0];
          GD.showPriceCheck(q, GD.ws());
          setTimeout(function () {
            var fs = document.querySelectorAll('.sheet-foot button');
            for (var i = 0; i < fs.length; i++) {
              if (fs[i].textContent.indexOf('Negotiation brief') >= 0) { fs[i].click(); break; }
            }
            setTimeout(function () {
              var fs2 = document.querySelectorAll('.scrim .sheet-foot button');
              for (var i = 0; i < fs2.length; i++) {
                if (fs2[i].textContent.indexOf('Print / PDF') >= 0) { fs2[i].click(); break; }
              }
              setTimeout(function () {
                var f = document.querySelector('iframe');
                out(f ? f.contentWindow.document.body.innerHTML : 'no iframe');
              }, 700);
            }, 900);
          }, 700);
        }, 600);
    """,
}

HEAD = """<script>
try { localStorage.clear(); localStorage.setItem('graindesk-workspace', '%s'); } catch (e) {}
function out(text) {
  var n = document.createElement('div');
  n.id = '__dump';
  n.textContent = text;
  document.body.appendChild(n);
}
</script>
"""

TAIL = """
<script>
window.addEventListener('load', function () {
  setTimeout(function () { try { %s } catch (e) { out('ERROR ' + e.message); } }, 500);
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
    ap.add_argument("what", choices=sorted(DRIVERS))
    ap.add_argument("--ws", default="urc")
    args = ap.parse_args()

    probe = APP / "_dump.html"
    probe.write_text(HEAD % args.ws + (APP / "index.html").read_text(encoding="utf-8") +
                     TAIL % DRIVERS[args.what], encoding="utf-8")
    try:
        with serve(ROOT) as port:
            url = "http://127.0.0.1:%d/app/_dump.html" % port
            r = subprocess.run(
                [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=14000", "--window-size=1400,1000", "--dump-dom", url],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            dom = r.stdout or ""
    finally:
        probe.unlink(missing_ok=True)

    m = re.search(r'id="__dump">(.*?)</div>', dom, re.S)
    if not m:
        print("nothing dumped")
        print(dom[-2000:])
        return 1
    print(html.unescape(m.group(1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
