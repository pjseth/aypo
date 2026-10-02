"""Speech-to-text for collected video audio.

Language is never pinned. The target market's speech is routinely Taglish --
Tagalog and English code-switched inside a single utterance -- and forcing a
language flag degrades exactly the code-switching signal the product is meant to
surface (brief §18: language, code-switching, slang). Whisper auto-detects and
transcribes mixed speech in place.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import structlog

from aypo.config import get_settings
from aypo.llm.pricing import Ledger, Spend, transcription_cost_usd

log = structlog.get_logger(__name__)


class TranscriptionUnavailable(RuntimeError):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    segments: list[Segment] = field(default_factory=list)
    detected_language: str | None = None
    duration_seconds: float = 0.0

    @property
    def has_speech(self) -> bool:
        return bool(self.text.strip())

    def at(self, seconds: float) -> str | None:
        """The utterance spoken at a timestamp, used to anchor a visual claim
        to what was being said when the frame was captured."""
        for seg in self.segments:
            if seg.start <= seconds <= seg.end:
                return seg.text
        return None


class OpenAITranscriber:
    def __init__(self, api_key: str, model: str, ledger: Ledger | None) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self._ledger = ledger

    async def transcribe(self, audio: bytes, *, filename: str = "audio.mp3") -> Transcript:
        buf = io.BytesIO(audio)
        buf.name = filename
        resp = await self._client.audio.transcriptions.create(
            model=self.model,
            file=buf,
            response_format="verbose_json",
            # Segment timestamps let the analyser cite "what was said at 0:12"
            # as the cue for a visual or cultural claim.
            timestamp_granularities=["segment"],
        )
        segments = [
            Segment(float(s.get("start", 0.0)), float(s.get("end", 0.0)), str(s.get("text", "")))
            for s in (getattr(resp, "segments", None) or [])
            if isinstance(s, dict)
        ] or [
            Segment(float(s.start), float(s.end), s.text)
            for s in (getattr(resp, "segments", None) or [])
            if hasattr(s, "start")
        ]
        duration = float(getattr(resp, "duration", 0.0) or 0.0)
        if self._ledger is not None and duration:
            self._ledger.record(
                Spend(
                    kind="transcription",
                    model=self.model,
                    usd=transcription_cost_usd(self.model, duration),
                    label="transcribe",
                )
            )
        return Transcript(
            text=getattr(resp, "text", "") or "",
            segments=segments,
            detected_language=getattr(resp, "language", None),
            duration_seconds=duration,
        )


def get_transcriber(ledger: Ledger | None = None) -> OpenAITranscriber:
    settings = get_settings()
    key = settings.secret("openai_api_key")
    if not key:
        raise TranscriptionUnavailable(
            "OPENAI_API_KEY is not set; videos will be analysed without audio, losing "
            "spoken brand references, slang and code-switching signal"
        )
    return OpenAITranscriber(key, settings.aypo_transcription_model, ledger)
