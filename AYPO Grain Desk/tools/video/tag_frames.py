"""Classify extracted keyframes, and build the montages used to read them.

Kept separate from `extract.py` on purpose: decoding 70 minutes of video costs minutes,
and the classifier is the part that needs tuning. This reads the PNGs that extract.py
already wrote, so the heuristic can be changed as often as needed for free.

Three outputs:
  frames.json      - updated in place with a `sheet` flag per frame
  contact_*.png    - 5x4 grids with burned-in timestamps, for triage in few image reads
  speakers_*.png   - a stack of the name-label corner of each frame

That last one is the diarization substitute. The recording burns the active speaker's name
into the bottom-left of the video, so cropping that corner across all frames recovers a
speaker timeline without pyannote, which is not installed.
"""

import argparse
import json
import pathlib
import sys

import cv2
import numpy as np


def rule_counts(gray):
    """Long near-horizontal and near-vertical rules. See extract.rule_counts for why Hough."""
    h, w = gray.shape
    edges = cv2.Canny(gray, 30, 90, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=70,
        minLineLength=max(120, w // 6), maxLineGap=4,
    )
    nh = nv = 0
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            dx, dy = abs(int(x2) - int(x1)), abs(int(y2) - int(y1))
            if dy <= 2 and dx >= w // 6:
                nh += 1
            elif dx <= 2 and dy >= h // 8:
                nv += 1
    return nh, nv, float(np.count_nonzero(edges)) / float(edges.size)


def small_text_density(gray):
    """Density of small connected components - a proxy for 'a lot of little glyphs'.

    A spreadsheet is mostly numerals in cells. A webcam frame of a person has very few
    small components. This separates a shared screen from a face even when the sharer has
    gridlines turned off, which is the case the rule counter would miss entirely.
    """
    inv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 10
    )
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    if n <= 1:
        return 0.0
    wh = stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]
    area = stats[1:, cv2.CC_STAT_AREA]
    glyphs = np.count_nonzero(
        (wh[0] >= 2) & (wh[0] <= 28) & (wh[1] >= 5) & (wh[1] <= 26) & (area >= 8)
    )
    return glyphs / 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="evidence")
    ap.add_argument("--h-rules", type=int, default=5)
    ap.add_argument("--v-rules", type=int, default=3)
    ap.add_argument("--glyphs", type=float, default=0.25)
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--rows", type=int, default=4)
    args = ap.parse_args()

    root = pathlib.Path(args.dir)
    meta = json.loads((root / "frames.json").read_text(encoding="utf-8"))
    frames = meta["frames"]
    fdir = root / "frames"

    print("scoring %d frames" % len(frames), flush=True)
    for i, f in enumerate(frames):
        img = cv2.imread(str(fdir / f["file"]), cv2.IMREAD_GRAYSCALE)
        if img is None:
            f["sheet"] = False
            continue
        nh, nv, ed = rule_counts(img)
        gl = small_text_density(img)
        f["h_rules"], f["v_rules"], f["edges"], f["glyphs"] = nh, nv, round(ed, 5), round(gl, 3)
        # Either a real lattice of rules, or a dense field of small glyphs. Gridlines are
        # often switched off in a shared sheet, so requiring both would miss those.
        f["sheet"] = bool((nh >= args.h_rules and nv >= args.v_rules) or gl >= args.glyphs)
        f["screen"] = bool(gl >= 0.12 or (nh >= 3 and nv >= 2))
        if (i + 1) % 50 == 0:
            print("  %d/%d" % (i + 1, len(frames)), flush=True)

    meta["frames"] = frames
    (root / "frames.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    sheets = [f for f in frames if f["sheet"]]
    screens = [f for f in frames if f.get("screen")]
    print("\nspreadsheet-like : %d" % len(sheets))
    print("screen-share-like: %d" % len(screens))
    if sheets:
        print("  " + ", ".join(f["label"] for f in sheets[:40]))

    # Contact sheets, in time order, with the timestamp burned in so a frame spotted in a
    # grid can be opened by name afterwards.
    cw, ch = 384, 216
    per = args.cols * args.rows
    for page in range((len(frames) + per - 1) // per):
        chunk = frames[page * per:(page + 1) * per]
        sheet = np.full((ch * args.rows, cw * args.cols, 3), 24, np.uint8)
        for i, f in enumerate(chunk):
            img = cv2.imread(str(fdir / f["file"]))
            if img is None:
                continue
            thumb = cv2.resize(img, (cw, ch), interpolation=cv2.INTER_AREA)
            tag = "%s%s" % (f["label"], "  SHEET" if f["sheet"] else "")
            cv2.rectangle(thumb, (0, 0), (cw, 22), (0, 0, 0), -1)
            cv2.putText(thumb, tag, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 255, 255) if f["sheet"] else (255, 255, 255), 1, cv2.LINE_AA)
            r, c = divmod(i, args.cols)
            sheet[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] = thumb
        cv2.imwrite(str(root / ("contact_%02d.png" % page)), sheet)
    print("contact sheets   : %d" % (page + 1))

    # Name-label strips: bottom-left corner of each frame, stacked in columns.
    strip_h, strip_w = 44, 340
    per_col = 24
    cols = []
    col = []
    for f in frames:
        img = cv2.imread(str(fdir / f["file"]))
        if img is None:
            continue
        h, w = img.shape[:2]
        crop = img[h - 52:h - 8, 0:strip_w].copy()
        crop = cv2.resize(crop, (strip_w, strip_h), interpolation=cv2.INTER_AREA)
        band = np.full((strip_h, strip_w + 70, 3), 20, np.uint8)
        band[:, 70:] = crop
        cv2.putText(band, f["label"], (4, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (120, 220, 255), 1, cv2.LINE_AA)
        col.append(band)
        if len(col) == per_col:
            cols.append(np.vstack(col))
            col = []
    if col:
        pad = np.full((strip_h * (per_col - len(col)), strip_w + 70, 3), 20, np.uint8)
        cols.append(np.vstack(col + [pad]))
    for page in range(0, len(cols), 4):
        group = cols[page:page + 4]
        cv2.imwrite(str(root / ("speakers_%02d.png" % (page // 4))), np.hstack(group))
    print("speaker strips   : %d" % ((len(cols) + 3) // 4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
