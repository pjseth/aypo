"""Render docs/design.html to a shareable PDF with the web fonts embedded.

Headless Edge would not reliably load the Google Fonts stylesheet - Archivo loads in
isolation, Source Serif 4 never does (it is served as a variable font with an `opsz` axis),
and in the full document both fell back to Times New Roman and Tahoma. Rather than depend on
the browser fetching fonts at print time, this fetches the latin subset once, base64-embeds
every face into a print-only copy of the page, and prints that. No network at render time,
so the PDF is byte-identical whoever builds it.

  py -3.11 tools/make_pdf.py
"""

import base64
import pathlib
import re
import subprocess
import sys
import urllib.request

CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Archivo:wght@500;600;700"
    "&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&display=swap"
)
# Deliberately an old Chrome. Google Fonts serves modern browsers ONE variable woff2 per
# family covering every weight; this Edge build will not apply a variable font delivered as
# a data: URI, so Archivo and Source Serif 4 silently fell back to Arial and Georgia while
# IBM Plex Mono - which is static - rendered fine. A legacy UA gets static instances, one
# file per weight, which embed and apply correctly.
UA = ("Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "design.html"
PRINT_HTML = ROOT / "docs" / "_print.html"
OUT = ROOT / "docs" / "Grain Desk - design proposal.pdf"


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = urllib.request.urlopen(req, timeout=60).read()
    return data if binary else data.decode("utf-8")


def main():
    print("fetching font stylesheet")
    css = get(CSS_URL)

    # Keep only the latin subset of each face. Google emits one @font-face per subset;
    # the latin one is identifiable by its unicode-range covering basic latin.
    blocks = re.findall(r"@font-face\s*\{[^}]*\}", css)
    keep = [b for b in blocks if "U+0000-00FF" in b]
    print("faces: %d total, %d latin" % (len(blocks), len(keep)))

    out_faces = []
    cache = {}
    for b in keep:
        m = re.search(r"src:\s*url\((https://[^)]+\.woff2)\)", b)
        if not m:
            continue
        url = m.group(1)
        if url not in cache:
            cache[url] = base64.b64encode(get(url, binary=True)).decode("ascii")
            fam = re.search(r"font-family:\s*'([^']+)'", b).group(1)
            wgt = re.search(r"font-weight:\s*([^;]+);", b)
            print("  embedded %-16s %s  %6.1f KB"
                  % (fam, (wgt.group(1).strip() if wgt else "-"),
                     len(cache[url]) * 3 / 4 / 1024))
        face = b.replace(url, "data:font/woff2;base64," + cache[url])
        # unicode-range would keep the browser looking for other subsets it cannot reach.
        face = re.sub(r"\s*unicode-range:[^;]+;", "", face)
        out_faces.append(face)

    html = SRC.read_text(encoding="utf-8")
    # Drop the network stylesheet and the preconnects; splice the embedded faces in.
    html = re.sub(r'<link rel="preconnect"[^>]*>\s*', "", html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts\.googleapis[^>]*>',
                  "<style>\n" + "\n".join(out_faces) + "\n</style>", html)
    PRINT_HTML.write_text(html, encoding="utf-8")
    print("wrote %s (%.0f KB)" % (PRINT_HTML.name, len(html) / 1024))

    uri = PRINT_HTML.as_uri()
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
           "--no-pdf-header-footer", "--print-to-pdf=%s" % OUT, uri]
    print("printing")
    r = subprocess.run(cmd, capture_output=True, text=True)
    line = [l for l in (r.stderr or "").splitlines() if "bytes written" in l]
    print(line[0] if line else (r.stderr or "")[-300:])

    data = OUT.read_bytes()
    fonts = sorted(set(f.decode() for f in
                       re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-,]+)", data)))
    pages = re.search(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", data)
    print("\n%s" % OUT.name)
    print("  pages : %s" % (pages.group(1).decode() if pages else "?"))
    print("  size  : %.0f KB" % (len(data) / 1024))
    print("  fonts : %s" % ", ".join(fonts))
    bad = [f for f in fonts if re.search(r"Times|Tahoma|Arial|Consolas", f)]
    print("  %s" % ("FALLBACKS PRESENT: " + ", ".join(bad) if bad
                    else "no fallback fonts - all faces embedded"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
