"""Media retrieval (brief §7, the retrieval half of discovery/retrieval).

Discovery gives us a public URL and metadata. Getting the actual bytes is a
separate step that can fail independently, and failing it must degrade the item
rather than the run.

Media is fetched with yt-dlp from the public post URL rather than through the
managed collector's paid video-download add-on. Verified against a live TikTok
post; it keeps the collection provider responsible only for discovery and
metadata, which is what makes that provider replaceable (brief §5).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from aypo.config import get_settings

log = structlog.get_logger(__name__)


class RetrievalTier(StrEnum):
    """How much of an item we actually obtained.

    Recorded per item so "insufficient data" is backed by counts rather than
    assumed (brief §28).
    """

    FULL_VIDEO = "full_video"
    THUMBNAIL_ONLY = "thumbnail_only"
    METADATA_ONLY = "metadata_only"
    # A post with no video stream at all -- TikTok photo carousels are
    # published with only an audio track, and yt-dlp happily returns the mp3.
    NO_VIDEO_STREAM = "no_video_stream"


@dataclass
class RetrievedMedia:
    tier: RetrievalTier
    path: Path | None = None
    bytes_downloaded: int = 0
    error: str | None = None

    @property
    def analysable(self) -> bool:
        """Whether this item can support in-video claims.

        A metadata-only item must never be sent to the vision model: a
        caption-only "analysis" is exactly the caption-driven listening the
        brief rejects, and any visible_brands it produced could only be
        hallucinated.
        """
        return self.tier is RetrievalTier.FULL_VIDEO and self.path is not None


def ytdlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


async def retrieve_video(
    url: str, *, workdir: Path | None = None, timeout_s: float = 180.0
) -> RetrievedMedia:
    """Download a public post's video. Never raises; returns a degraded tier."""
    settings = get_settings()
    if not ytdlp_available():
        return RetrievedMedia(
            RetrievalTier.METADATA_ONLY, error="yt-dlp is not installed; cannot retrieve media"
        )

    target_dir = workdir or Path(tempfile.mkdtemp(prefix="aypo-media-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(target_dir / "media.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--no-progress",
        # Prefer a single muxed mp4 so no merge step (and no ffmpeg round-trip)
        # is needed before frame extraction.
        "-f",
        "mp4/best",
        "--max-filesize",
        f"{settings.aypo_max_video_download_mb}M",
        "-o",
        out_template,
        "--",
        url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(timeout_s):
            _, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return RetrievedMedia(
            RetrievalTier.METADATA_ONLY, error=f"retrieval timed out after {timeout_s}s"
        )

    if proc.returncode != 0:
        message = stderr.decode("utf-8", "replace").strip().splitlines()
        return RetrievedMedia(
            RetrievalTier.METADATA_ONLY,
            error=(message[-1] if message else "yt-dlp failed")[:300],
        )

    files = [p for p in target_dir.glob("media.*") if p.is_file() and p.stat().st_size > 0]
    if not files:
        return RetrievedMedia(RetrievalTier.METADATA_ONLY, error="no media file produced")
    path = max(files, key=lambda p: p.stat().st_size)
    size = path.stat().st_size

    # Verify a video stream is actually present. Format selection can still fall
    # back to audio, and finding out during scene scoring produces an error that
    # looks like a bug in frame sampling rather than a property of the post.
    if not await _has_video_stream(path):
        return RetrievedMedia(
            RetrievalTier.NO_VIDEO_STREAM,
            path=path,
            bytes_downloaded=size,
            error="post has no video stream (photo carousel or audio-only)",
        )

    log.info("retrieval.ok", url=url[:80], bytes=size)
    return RetrievedMedia(RetrievalTier.FULL_VIDEO, path=path, bytes_downloaded=size)


async def _has_video_stream(path: Path) -> bool:
    settings = get_settings()
    proc = await asyncio.create_subprocess_exec(
        settings.ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(30):
            stdout, _ = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return b"video" in stdout
