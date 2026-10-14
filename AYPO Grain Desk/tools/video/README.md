# Video analysis pipeline

Turns a recording into a timestamped transcript plus a set of readable keyframes. Built to
dissect the 71-minute procurement walkthrough with Pavin Sethbhakdi.

## Why this exists rather than the `claude-video` skill

The skill at `github.com/bradautomates/claude-video` does the same job and does it well, but
it shells out to **ffmpeg / ffprobe / yt-dlp** and wants a **JS runtime** for YouTube. None of
those are installed on this machine, and `node`/`npx` are absent too, so `npx skills add`
is not available either.

What *is* installed is PyAV, which bundles the same FFmpeg libraries (libavcodec 62, libavformat
62, libswscale 9) and calls them in-process. So the pipeline is the skill's approach
reimplemented against the libraries directly. If ffmpeg is ever installed
(`winget install Gyan.FFmpeg`), the real skill becomes the better option for future videos.

Everything runs on **`py -3.11`**. Bare `python` on this machine is the Microsoft Store stub
and exits 49.

## Steps

```bash
cd "AYPO Grain Desk"

# 1. One decode pass: 16 kHz mono WAV + scene-change keyframes + per-frame metrics
py -3.11 tools/video/extract.py "<path to .mp4>" --out evidence

# 2. Classify frames, build contact sheets and speaker strips (cheap, re-runnable)
py -3.11 tools/video/tag_frames.py --dir evidence

# 3. Transcribe (the long step - run it in the background)
py -3.11 tools/video/transcribe.py --model small.en --threads 20 --suffix _small

# 4. Enlarge any frame enough to read what is on screen
py -3.11 tools/video/zoom.py f_0187_5319.png --region screen --scale 2.6 --sharpen
```

## Design notes

**Extraction and classification are separate.** Decoding 102k frames takes minutes; the
"is this a spreadsheet" heuristic needed several attempts. Keeping them apart means retuning
the classifier costs nothing. `tag_frames.py` re-scores the PNGs that `extract.py` wrote.

**The first spreadsheet detector was wrong.** It thresholded the frame and opened it with a
long thin kernel, reasoning that cell borders are long straight runs. But a morphological
opening preserves *any* region big enough to hold the kernel, so a flat cream wall behind a
webcam scored 0.97 - higher than a real spreadsheet. The replacement counts long straight
lines with `HoughLinesP` on a Canny edge map, which a solid region cannot fake because it
contributes only its own thin outline. A second signal, the density of small connected
components, catches shared screens whose gridlines are switched off.

**Transcription is seeded with domain vocabulary.** Accented English plus commodity jargon is
where a general model fails - "WASDE" becomes "wasted", "laycan" becomes "lay can". The terms
go in twice, as `initial_prompt` and as `hotwords`. Observed throughput on 24 CPU cores with
`small.en` and `int8`: ~3.7x realtime.

**No speaker diarization.** `pyannote` is not installed. Instead `tag_frames.py` crops the
bottom-left corner of every frame, where the recording burns in the active speaker's name,
and stacks those crops into `speakers_*.png`. Reading that montage recovers a speaker
timeline without a model.

## Outputs

| Path | What |
| --- | --- |
| `evidence/audio.wav` | 16 kHz mono, gitignored (~130 MB) |
| `evidence/frames/` | Keyframe PNGs, gitignored |
| `evidence/frames.json` | Per-frame metrics and classification |
| `evidence/contact_NN.png` | 5x4 triage grids, timestamps burned in |
| `evidence/speakers_NN.png` | Name-label crops for speaker attribution |
| `evidence/transcript_*.md` | Readable transcript with `[mm:ss]` anchors |
| `evidence/transcript_*.jsonl` | `{start, end, text}` per segment |
| `evidence/zoom/` | Enlarged crops of individual frames |
