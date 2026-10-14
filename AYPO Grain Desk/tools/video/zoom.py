"""Crop and upscale a region of a keyframe so small on-screen text can be read.

The recording is 720p and the shared screen occupies roughly the left two thirds of the
frame, with a webcam tile beside it. Excel cell text at that scale is a few pixels tall, so
reading it needs the region isolated and enlarged. Lanczos rather than cubic: it keeps thin
glyph strokes together instead of smearing them, which is the whole point here.

  py -3.11 tools/video/zoom.py f_0131_1752.png --region screen --scale 2.5
  py -3.11 tools/video/zoom.py f_0131_1752.png --box 0,0,700,400 --scale 3
  py -3.11 tools/video/zoom.py f_0131_1752.png --region screen --tiles 2x2 --scale 2.5
"""

import argparse
import pathlib
import sys

import cv2

REGIONS = {
    # Fractions of width/height: x0, y0, x1, y1
    "screen": (0.0, 0.0, 0.805, 1.0),   # the shared window, excluding the webcam tile
    "full": (0.0, 0.0, 1.0, 1.0),
    "left": (0.0, 0.0, 0.42, 1.0),
    "top": (0.0, 0.0, 0.805, 0.5),
    "bottom": (0.0, 0.5, 0.805, 1.0),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frame")
    ap.add_argument("--dir", default="evidence/frames")
    ap.add_argument("--out", default="evidence/zoom")
    ap.add_argument("--region", default="screen", choices=sorted(REGIONS))
    ap.add_argument("--box", help="pixel box x0,y0,x1,y1 - overrides --region")
    ap.add_argument("--scale", type=float, default=2.5)
    ap.add_argument("--tiles", default="", help="e.g. 2x2 to split the region into tiles")
    ap.add_argument("--sharpen", action="store_true")
    args = ap.parse_args()

    src = pathlib.Path(args.dir) / args.frame
    img = cv2.imread(str(src))
    if img is None:
        print("cannot read %s" % src)
        return 1
    h, w = img.shape[:2]

    if args.box:
        x0, y0, x1, y1 = (int(v) for v in args.box.split(","))
    else:
        fx0, fy0, fx1, fy1 = REGIONS[args.region]
        x0, y0, x1, y1 = int(fx0 * w), int(fy0 * h), int(fx1 * w), int(fy1 * h)
    crop = img[y0:y1, x0:x1]

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(args.frame).stem

    pieces = [("", crop)]
    if args.tiles:
        cols, rows = (int(v) for v in args.tiles.lower().split("x"))
        ch, cw = crop.shape[:2]
        pieces = []
        for r in range(rows):
            for c in range(cols):
                # 6% overlap so a row of figures is never split down the middle.
                ov_x, ov_y = int(cw / cols * 0.06), int(ch / rows * 0.06)
                a = max(0, int(c * cw / cols) - ov_x)
                b = min(cw, int((c + 1) * cw / cols) + ov_x)
                p = max(0, int(r * ch / rows) - ov_y)
                q = min(ch, int((r + 1) * ch / rows) + ov_y)
                pieces.append(("_r%dc%d" % (r, c), crop[p:q, a:b]))

    for suffix, piece in pieces:
        big = cv2.resize(piece, None, fx=args.scale, fy=args.scale,
                         interpolation=cv2.INTER_LANCZOS4)
        if args.sharpen:
            blur = cv2.GaussianBlur(big, (0, 0), 1.2)
            big = cv2.addWeighted(big, 1.6, blur, -0.6, 0)
        dst = out / ("%s_%s%s.png" % (stem, args.region if not args.box else "box", suffix))
        cv2.imwrite(str(dst), big)
        print("%s  %dx%d" % (dst, big.shape[1], big.shape[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
