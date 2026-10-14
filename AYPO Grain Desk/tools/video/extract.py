"""One decode pass over a recording: 16 kHz mono WAV for Whisper, plus keyframes.

Why pure Python: the `claude-video` skill shells out to ffmpeg/ffprobe/yt-dlp, none of
which exist on this machine. PyAV bundles the same FFmpeg libraries, so decode, resample
and remux all work in-process.

Two outputs matter for different reasons. The WAV feeds transcription. The keyframes exist
because Pavin screen-shared his own spreadsheets during the walkthrough, and those frames
are the only record of the columns he actually keeps.

This script deliberately does NOT decide which frames are spreadsheets. It writes the PNGs
and cheap per-frame metrics; `tag_frames.py` classifies afterwards from the PNGs. Decoding
70 minutes of video is the expensive step and the classifier is the part that needs
tuning, so they are kept apart - retuning must not cost another decode.
"""

import argparse
import json
import pathlib
import sys
import wave

import av
import cv2
import numpy as np

# Sample the video this often for scene scoring. The source is 24 fps; 2 fps is plenty to
# catch a window switch and keeps the histogram work off 100k frames.
SAMPLE_FPS = 2.0

# Histogram distance above which two sampled frames are "a different scene". Tuned low:
# a screen-share switching spreadsheet tabs is a subtle change, and a missed Excel frame
# costs more than an extra PNG on disk.
SCENE_THRESHOLD = 0.18

# Emit a frame at least this often even with no scene change, so a long static
# screen-share is still represented.
HEARTBEAT_SECONDS = 45.0

# Suppress a second emit within this window unless the change is large, to stop a
# cross-fade or a talking head from producing a burst of near-identical frames.
MIN_GAP_SECONDS = 4.0
BIG_CHANGE = 0.45


def rule_counts(gray):
    """Count long straight near-horizontal and near-vertical rules.

    An earlier version opened an adaptive threshold with a long thin kernel. That was
    wrong: an opening preserves any region large enough to contain the kernel, so a flat
    cream wall behind a webcam scored 0.97 - higher than a real spreadsheet. Hough on an
    edge map cannot make that mistake, because a solid region contributes only its thin
    outline and a wall contributes nothing long or straight.
    """
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


def hist_distance(a, b):
    """Bhattacharyya distance between grayscale histograms. 0 = identical, 1 = disjoint."""
    ha = cv2.calcHist([a], [0], None, [64], [0, 256])
    hb = cv2.calcHist([b], [0], None, [64], [0, 256])
    cv2.normalize(ha, ha)
    cv2.normalize(hb, hb)
    return float(cv2.compareHist(ha, hb, cv2.HISTCMP_BHATTACHARYYA))


def label(seconds):
    m, s = divmod(int(seconds), 60)
    return "%02d:%02d" % (m, s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="evidence")
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--no-audio", action="store_true")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    container = av.open(args.video)
    vstream = container.streams.video[0]
    astream = container.streams.audio[0] if container.streams.audio else None

    # Multithreaded decode: 24 cores available and this is the slow part.
    vstream.thread_type = "AUTO"
    if astream is not None:
        astream.thread_type = "AUTO"

    duration = float(container.duration) / av.time_base if container.duration else 0.0
    print("source   : %s" % args.video)
    print("duration : %.1fs  (%.1f min)" % (duration, duration / 60))
    print("video    : %s %dx%d @ %.2f fps" % (
        vstream.codec_context.name,
        vstream.codec_context.width,
        vstream.codec_context.height,
        float(vstream.average_rate or 0),
    ))

    wav = None
    resampler = None
    want_audio = astream is not None and not args.no_audio
    if want_audio:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        wav = wave.open(str(out / "audio.wav"), "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)

    sample_interval = 1.0 / SAMPLE_FPS
    next_sample = 0.0
    last_emit_t = -1e9
    prev_small = None
    kept = []
    decoded = 0
    stop = False

    streams = [vstream] + ([astream] if want_audio else [])

    for packet in container.demux(*streams):
        for frame in packet.decode():
            if isinstance(frame, av.AudioFrame):
                for chunk in resampler.resample(frame):
                    wav.writeframes(chunk.to_ndarray().tobytes())
                continue

            decoded += 1
            t = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
            if t < next_sample:
                continue
            next_sample = t + sample_interval

            full = frame.to_ndarray(format="gray")
            small = cv2.resize(full, (160, 90), interpolation=cv2.INTER_AREA)

            dist = 1.0 if prev_small is None else hist_distance(prev_small, small)
            prev_small = small

            gap = t - last_emit_t
            scene = dist >= SCENE_THRESHOLD
            heartbeat = gap >= HEARTBEAT_SECONDS
            if not (heartbeat or (scene and (gap >= MIN_GAP_SECONDS or dist >= BIG_CHANGE))):
                continue

            nh, nv, ed = rule_counts(full)
            # Index first: two samples half a second apart both round to the same
            # mm:ss and silently overwrote each other in the first version.
            name = "f_%04d_%s.png" % (len(kept), label(t).replace(":", ""))
            cv2.imwrite(str(frames_dir / name), frame.to_ndarray(format="bgr24"))
            kept.append({
                "file": name,
                "t": round(t, 2),
                "label": label(t),
                "h_rules": nh,
                "v_rules": nv,
                "edges": round(ed, 5),
                "dist": round(dist, 4),
                "reason": "scene" if scene else "heartbeat",
            })
            last_emit_t = t

            if len(kept) % 25 == 0:
                print("  %s  frames kept: %d" % (label(t), len(kept)), flush=True)
            if len(kept) >= args.max_frames:
                print("  hit --max-frames %d at %s" % (args.max_frames, label(t)), flush=True)
                stop = True
                break
        if stop:
            break

    if wav is not None:
        wav.close()
    container.close()

    (out / "frames.json").write_text(
        json.dumps({"video": args.video, "duration": duration, "frames": kept}, indent=2),
        encoding="utf-8",
    )

    print("decoded video frames : %d" % decoded)
    print("frames kept          : %d" % len(kept))
    if kept:
        top = sorted(kept, key=lambda k: -(k["h_rules"] + k["v_rules"]))[:12]
        print("most line-dense      : " +
              ", ".join("%s(h%d/v%d)" % (k["label"], k["h_rules"], k["v_rules"]) for k in top))
    if wav is not None:
        print("audio written        : %s" % (out / "audio.wav"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
