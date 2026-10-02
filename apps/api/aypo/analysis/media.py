"""FFmpeg media processing: probe, scene scoring, frame selection, audio extraction.

Frame selection is the quality floor of the whole product. The headline claim --
*which brand is visible and is it actually being used* -- depends on landing a
frame on the shot where the object is held to the lens. Neither pure strategy
gets there:

* Uniform sampling lands frames mid-cut and systematically misses that shot.
* Pure scene-change detection returns 40-80 candidates on a fast-cut edit and
  **zero** on a single-take talking clip, and ranks transitions rather than
  informativeness.

So selection is a hybrid: a cheap decimated scoring pass, then one frame per
equal-width time bucket, taking the highest-scoring frame in the bucket when the
bucket actually contains a cut and the bucket midpoint when it does not.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field
from statistics import median

import structlog

from aypo.config import get_settings

log = structlog.get_logger(__name__)

# Long edge chosen so both common platform aspect ratios cost the same ~700
# image tokens: Claude tokenises at roughly one token per 28x28 patch, and
# 960 lands on a clean grid for both 9:16 and 16:9.
LONG_EDGE = 960
JPEG_QSCALE = 3

# Decimated rate for the scoring pass. Full-rate scene detection costs ~6x more
# decode work for no ranking benefit, since scores are only compared within a
# bucket.
SCORE_FPS = 6
SCORE_WIDTH = 160

# scdet reports on a 0-100 scale (the filter's own threshold option defaults to
# 10) -- NOT the 0-1 scale of the older select='gt(scene,T)' idiom. Measured on
# a clip with known hard cuts, real cuts scored 28-35 while non-cut frames
# stayed below 2, so cuts separate by more than an order of magnitude.
#
# A bucket is treated as containing a cut when its best frame beats both a
# median-relative threshold (invariant to how motion-heavy the item is overall)
# and an absolute floor (which keeps a genuinely static clip, median = 0, on the
# midpoint branch). Both constants are unmeasured priors.
SCENE_MEDIAN_MULTIPLE = 3.0
SCENE_ABSOLUTE_FLOOR = 1.0

_SCORE_RE = re.compile(r"lavfi\.scd\.score=([0-9.]+)")
_TIME_RE = re.compile(r"lavfi\.scd\.time=([0-9.]+)")


class MediaError(RuntimeError):
    pass


@dataclass
class MediaProbe:
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    rotation: int = 0
    has_audio: bool = False
    codec_name: str | None = None
    nb_frames: int | None = None

    @property
    def display_size(self) -> tuple[int, int]:
        """Width/height after rotation.

        Vertical platform video is frequently stored 1920x1080 with a 90-degree
        display matrix; reading width/height raw produces wrongly-oriented
        output.
        """
        if self.rotation in (90, 270, -90, -270):
            return self.height, self.width
        return self.width, self.height

    @property
    def is_vertical(self) -> bool:
        w, h = self.display_size
        return h > w


@dataclass
class SelectedFrame:
    index: int
    timestamp_s: float
    scene_score: float
    selection_reason: str  # bucket_scene_max | bucket_midpoint | thumbnail
    gap_violated: bool = False
    data: bytes | None = field(default=None, repr=False)


def frame_budget(duration_s: float) -> int:
    """Frames per item, excluding the platform thumbnail.

    Tiers are evaluated with `<` on the upper bound, so exactly 60.0s gets 12.
    A missing or zero duration (common with truncated downloads) is treated as
    the modal short-form tier rather than failing the item.
    """
    if not duration_s or duration_s <= 0:
        return 8
    if duration_s < 5:
        return 3
    if duration_s < 20:
        return 6
    if duration_s < 60:
        return 8
    if duration_s < 180:
        return 12
    if duration_s < 600:
        return 16
    return 20


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


async def _run(cmd: list[str], *, timeout_s: float = 180.0) -> tuple[int, bytes, bytes]:
    """Run a subprocess, killing it if it overruns.

    The timeout is not just cancellation: an ffmpeg process left running after
    its caller gives up would keep consuming CPU and holding the download, so
    it is explicitly killed and reaped.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(timeout_s):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError(f"timed out after {timeout_s}s: {cmd[0]}") from None
    return proc.returncode or 0, stdout, stderr


def ffmpeg_available() -> bool:
    settings = get_settings()
    return bool(shutil.which(settings.ffmpeg_binary) and shutil.which(settings.ffprobe_binary))


async def probe(path: str) -> MediaProbe:
    settings = get_settings()
    code, stdout, stderr = await _run(
        [
            settings.ffprobe_binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "--",
            path,
        ],
        timeout_s=60.0,
    )
    if code != 0:
        raise MediaError(f"ffprobe failed: {stderr.decode('utf-8', 'replace')[:300]}")
    data = json.loads(stdout or b"{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    rotation = 0
    if video:
        for side in video.get("side_data_list") or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
                break
        if not rotation:
            rotation = int(float((video.get("tags") or {}).get("rotate", 0) or 0))

    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if not duration and video:
        duration = float(video.get("duration") or 0.0)

    nb = video.get("nb_frames") if video else None
    return MediaProbe(
        duration_s=duration,
        width=int(video.get("width") or 0) if video else 0,
        height=int(video.get("height") or 0) if video else 0,
        rotation=rotation,
        has_audio=audio is not None,
        codec_name=(video or {}).get("codec_name"),
        nb_frames=int(nb) if nb and str(nb).isdigit() else None,
    )


async def score_scenes(path: str) -> list[tuple[float, float]]:
    """Cheap decimated scoring pass -> [(timestamp_s, scene_score)]."""
    settings = get_settings()
    code, stdout, stderr = await _run(
        [
            settings.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            path,
            "-an",
            "-vf",
            f"fps={SCORE_FPS},scale={SCORE_WIDTH}:-2:flags=fast_bilinear,"
            "scdet=threshold=0,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        timeout_s=240.0,
    )
    # `metadata=print:file=-` writes to stdout; ffmpeg's own logging goes to
    # stderr. Both are scanned so this keeps working if a future ffmpeg routes
    # it differently.
    text = stdout.decode("utf-8", "replace") + "\n" + stderr.decode("utf-8", "replace")
    if code != 0 and "lavfi.scd" not in text:
        raise MediaError(f"scene scoring failed: {stderr.decode('utf-8', 'replace')[:300]}")

    # scdet prints each frame's metadata as mafd, then score, then time -- the
    # score arrives BEFORE its timestamp, so the score is held until the
    # matching time line closes the pair.
    out: list[tuple[float, float]] = []
    pending_score: float | None = None
    for line in text.splitlines():
        if (m := _SCORE_RE.search(line)) is not None:
            pending_score = float(m.group(1))
        elif (m := _TIME_RE.search(line)) is not None and pending_score is not None:
            out.append((float(m.group(1)), pending_score))
            pending_score = None
    return out


def select_timestamps(duration_s: float, scores: list[tuple[float, float]]) -> list[SelectedFrame]:
    """Bucketed scene-max selection.

    Deterministic by construction: the same inputs always produce the same
    timestamps, because the frame set feeds an artefact cache key.
    """
    n = frame_budget(duration_s)
    if duration_s <= 0:
        # Unknown duration: fall back to evenly spaced guesses over a nominal
        # window; the caller records how many actually extracted.
        return [SelectedFrame(i, (i + 0.5) * (30.0 / n), 0.0, "bucket_midpoint") for i in range(n)]

    # Trim head and tail: openings are often a platform/black/logo frame or a
    # fade-in, closings often an end card -- both score high and carry little
    # information, so without trimming the scene-max rule reliably picks them.
    t_lo = _clamp(0.03 * duration_s, 0.20, 1.0)
    t_hi = duration_s - _clamp(0.04 * duration_s, 0.25, 2.0)
    if t_hi <= t_lo:
        t_lo, t_hi = 0.0, max(duration_s, 0.1)

    span = (t_hi - t_lo) / n
    all_scores = [s for _, s in scores]
    med = median(all_scores) if all_scores else 0.0
    threshold = max(SCENE_ABSOLUTE_FLOOR, SCENE_MEDIAN_MULTIPLE * med)

    chosen: list[SelectedFrame] = []
    for i in range(n):
        lo, hi = t_lo + i * span, t_lo + (i + 1) * span
        in_bucket = [(t, s) for t, s in scores if lo <= t < hi]
        midpoint = (lo + hi) / 2.0
        if in_bucket:
            best_t, best_s = max(in_bucket, key=lambda ts: ts[1])
            if best_s >= threshold:
                chosen.append(SelectedFrame(i, best_t, best_s, "bucket_scene_max"))
                continue
        chosen.append(SelectedFrame(i, midpoint, 0.0, "bucket_midpoint"))

    # Enforce a minimum gap in one deterministic left-to-right pass. Near
    # identical frames cost ~700 tokens each and add nothing.
    min_gap = max(0.5, 0.25 * duration_s / n)
    for i in range(1, len(chosen)):
        prev, cur = chosen[i - 1], chosen[i]
        if cur.timestamp_s - prev.timestamp_s < min_gap:
            lo, hi = t_lo + i * span, t_lo + (i + 1) * span
            shifted = _clamp(prev.timestamp_s + min_gap, lo, hi)
            if shifted - prev.timestamp_s < min_gap:
                cur.gap_violated = True
            cur.timestamp_s = shifted
    return chosen


def midpoint_fraction(frames: list[SelectedFrame]) -> float:
    """Share of buckets that fell back to the midpoint.

    The cheapest available proxy for static single-take versus cut-heavy
    production, which correlates with how reliably a brand can be read off the
    frames -- so it is recorded alongside the analysis rather than discarded.
    """
    sampled = [f for f in frames if f.selection_reason != "thumbnail"]
    if not sampled:
        return 0.0
    return sum(1 for f in sampled if f.selection_reason == "bucket_midpoint") / len(sampled)


async def extract_frames(path: str, frames: list[SelectedFrame]) -> list[SelectedFrame]:
    """Extract each selected timestamp as a JPEG, populating `.data`.

    Seeks per frame rather than using one select= expression: a per-frame seek
    is robust to variable frame rates and to timestamps that a select filter
    would silently drop, and a frame that fails to extract leaves the others
    intact instead of losing the whole item.
    """
    settings = get_settings()
    out: list[SelectedFrame] = []
    for frame in frames:
        code, stdout, stderr = await _run(
            [
                settings.ffmpeg_binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{frame.timestamp_s:.3f}",
                "-i",
                path,
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-frames:v",
                "1",
                "-vf",
                f"scale=w={LONG_EDGE}:h={LONG_EDGE}:force_original_aspect_ratio=decrease:"
                "force_divisible_by=2:flags=lanczos",
                "-q:v",
                str(JPEG_QSCALE),
                "-f",
                "image2",
                "-c:v",
                "mjpeg",
                "-",
            ],
            timeout_s=90.0,
        )
        if code != 0 or not stdout:
            log.debug(
                "media.frame_extract_failed",
                t=frame.timestamp_s,
                error=stderr.decode("utf-8", "replace")[:160],
            )
            continue
        frame.data = stdout
        out.append(frame)
    return out


async def extract_audio(path: str, *, max_seconds: int | None = None) -> bytes | None:
    """Mono 16 kHz MP3 for transcription, or None when the item has no audio."""
    settings = get_settings()
    cmd = [
        settings.ffmpeg_binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
    ]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-f", "mp3", "-"]
    code, stdout, stderr = await _run(cmd, timeout_s=240.0)
    if code != 0 or not stdout:
        log.debug("media.audio_extract_failed", error=stderr.decode("utf-8", "replace")[:160])
        return None
    return stdout


async def sample_video(path: str) -> tuple[MediaProbe, list[SelectedFrame]]:
    """Probe, score, select and extract in one call."""
    meta = await probe(path)
    scores = await score_scenes(path)
    selected = select_timestamps(meta.duration_s, scores)
    extracted = await extract_frames(path, selected)
    log.info(
        "media.sampled",
        duration_s=round(meta.duration_s, 2),
        requested=len(selected),
        extracted=len(extracted),
        midpoint_fraction=round(midpoint_fraction(extracted), 2),
    )
    return meta, extracted
