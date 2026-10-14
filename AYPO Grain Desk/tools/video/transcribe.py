"""Timestamped transcript of the extracted audio, via faster-whisper.

The accuracy problem here is not the audio, it is the vocabulary. This is accented English
carrying dense commodity jargon - WASDE, Panamax, laycan, basis, CWRS - and a general model
will cheerfully turn "WASDE" into "wasted" and "laycan" into "lay can". So the domain terms
are fed in twice: as `initial_prompt` (biases the decoder's language model) and as
`hotwords` where the installed version supports it.

Writes both machine-readable JSONL and a readable Markdown transcript with [mm:ss] anchors,
because every claim in the design doc has to be checkable against a timestamp.
"""

import argparse
import json
import pathlib
import sys
import time

# Fed to the decoder as context. Phrased as prose rather than a word list: the prompt is
# conditioning a language model, so a sentence in the register of the actual speech
# steers better than comma-separated tokens.
DOMAIN_PROMPT = (
    "A procurement walkthrough about buying wheat for a Thai flour mill. Terms used: "
    "WASDE, USDA, CBOT, MGEX, Chicago SRW, Kansas City HRW, futures, basis, flat price, "
    "freight and insurance, supplier margin, Panamax vessel, 55,000 tonnes, cargo, laycan, "
    "shipment period, protein, Hard Red Winter, Northern Spring, Hard Red Spring, "
    "Western White, CWRS, Australian Standard White, origin, PNW, Vancouver, Kwinana, "
    "Chao Phraya, barge, Bangkok, bill of lading, fumigation, halal certificate, "
    "phytosanitary, Mitsui, Cargill, Viterra, Louis Dreyfus, Marubeni, GrainCorp, Bunge, "
    "AgroValues, United Flour Mill, hedging, take profit, long position, short position, "
    "bushel, metric ton, bunker fuel, Black Sea, Strait of Hormuz, export demand."
)

HOTWORDS = (
    "WASDE Panamax laycan CWRS CBOT MGEX SRW HRW basis flat price bushel Chao Phraya "
    "Kwinana Mitsui Viterra Marubeni GrainCorp AgroValues United Flour Mill phytosanitary"
)


def hhmmss(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return "%02d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default="evidence/audio.wav")
    ap.add_argument("--out", default="evidence")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("model   : %s (cpu, int8, %d threads)" % (args.model, args.threads), flush=True)
    print("audio   : %s" % args.audio, flush=True)
    t0 = time.time()

    model = WhisperModel(
        args.model, device="cpu", compute_type="int8", cpu_threads=args.threads
    )
    print("loaded in %.1fs" % (time.time() - t0), flush=True)

    kwargs = dict(
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        initial_prompt=DOMAIN_PROMPT,
        condition_on_previous_text=True,
    )
    try:
        segments, info = model.transcribe(args.audio, hotwords=HOTWORDS, **kwargs)
    except TypeError:
        # Older faster-whisper has no `hotwords`; initial_prompt alone still helps.
        print("note: this faster-whisper has no hotwords support, using initial_prompt only")
        segments, info = model.transcribe(args.audio, **kwargs)

    print("detected language: %s (p=%.2f)" % (info.language, info.language_probability),
          flush=True)
    print("audio duration   : %.1fs" % info.duration, flush=True)

    jsonl = out / ("transcript%s.jsonl" % args.suffix)
    md = out / ("transcript%s.md" % args.suffix)

    rows = []
    with jsonl.open("w", encoding="utf-8") as jf, md.open("w", encoding="utf-8") as mf:
        mf.write("# Transcript - Pavin procurement walkthrough\n\n")
        mf.write("Model `%s`. Timestamps are [mm:ss] from the start of the recording.\n"
                 "No speaker diarization: attribution comes from context and from the\n"
                 "name labels burned into the video (see `speakers.md`).\n\n" % args.model)
        last_print = 0.0
        for seg in segments:
            row = {"start": round(seg.start, 2), "end": round(seg.end, 2),
                   "text": seg.text.strip()}
            rows.append(row)
            jf.write(json.dumps(row, ensure_ascii=False) + "\n")
            jf.flush()
            mf.write("**[%s]** %s\n\n" % (hhmmss(seg.start), row["text"]))
            mf.flush()
            if seg.start - last_print >= 120:
                last_print = seg.start
                el = time.time() - t0
                rate = seg.start / el if el else 0
                print("  %s transcribed (%.1fx realtime, %.0fs elapsed)"
                      % (hhmmss(seg.start), rate, el), flush=True)

    el = time.time() - t0
    words = sum(len(r["text"].split()) for r in rows)
    print("\nsegments : %d" % len(rows))
    print("words    : %d" % words)
    print("elapsed  : %.0fs (%.1fx realtime)" % (el, (info.duration / el) if el else 0))
    print("written  : %s, %s" % (jsonl, md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
