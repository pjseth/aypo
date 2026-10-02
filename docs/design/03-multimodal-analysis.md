# 03 — Per-Video Multimodal Analysis

**Subsystem owner:** `apps/api/aypo/analysis/`
**Brief sections covered:** §18 (multimodal video analysis), §19 (hybrid schema), §20 (structured
output), §28 (data quality / guardrails). Downstream consumers: §21 clustering, §22 trends,
§23 competitor discovery, §27 evidence, §31.5 Content Explorer.
**Status:** design. No implementation code in this document.

---

## 0. Scope, non-scope, and the anti-seeding contract

### 0.1 What this subsystem does

Takes **one** normalised content item (a video, or in the degraded case a single still) plus its
platform metadata, and produces **one** validated, evidence-anchored structured record, stored
against an explicit `prompt_version` so results are comparable across prompt generations.

```
NormalizedContentItem
   └─> media retrieval        (S3 cache, or provider media URL)
        └─> FFmpeg            (audio track, frame set)
             └─> ASR          (whisper-1, language NOT forced)
                  └─> ONE Claude Opus 5 call (frames + transcript + platform text + context)
                       └─> Pydantic validation + anchor/cue verification
                            └─> calibrated per-claim confidence
                                 └─> content_analysis + content_analysis_claims rows
```

### 0.2 What this subsystem does **not** do

| Not owned here | Owned by |
|---|---|
| Discovery, media URL resolution, provenance | `collectors/`, `providers/` |
| Embeddings, clustering, cluster labelling | `intelligence/` (§21) |
| The word "competitor" and every competitor relationship | `intelligence/` (§23) |
| Trend classification, growth/decline | `intelligence/` (§22) |
| Findings, aggregation, sample-size statements | `intelligence/` (§27) |
| Commerce record analysis | commerce subsystem (reuses the vision primitives here — see §9.4) |
| The job queue itself | `jobs/` (Postgres-backed queue) |

**Hard rule:** the per-item analyser has *no* brand of interest, *no* category model, *no*
competitor set and *no* market context. It is a witness, not an analyst. Every comparative,
competitive, categorical or market-level judgement is made downstream from many witnesses.
This is the single most important structural defence against brief §1 / §34 — the component
with the most expressive power (a frontier multimodal model with a free-text prompt) is the
component given the least context about what answer anyone wants.

### 0.3 The anti-seeding contract (brief §1, §34)

The following are **forbidden** anywhere in this subsystem's prompts, enums, schema defaults,
taxonomy, fixtures, filters, test data or thresholds:

1. Any brand name, real or assumed, including the brand under research.
2. Any candidate-brand, candidate-competitor or candidate-flavour list.
3. Any occasion, situation, activity, social grouping, audience, daypart or location label
   offered as an option (closed enums are banned for every §19B field).
4. Any language, slang term, phrase, meme, humour style or cultural reference, including as an
   example, a hint, an ASR `prompt`, or a "for instance" in prompt text.
5. Any product category, snack/food framing, or assumption about what the product is for.
6. Any market, country or locale string in the analysis prompt (it is kept in provenance only;
   see open question OQ-1).
7. Any phrase-level denylist for ASR hallucination filtering (statistical filters only — §2.5).

Test and example data use `Brand A`, `Theme X`, `Cluster Y`, `Creator 1`, `Label L`.

**Declared exceptions (audited, not accidental).** Three closed enums survive the ban on closed
enums, and they are listed here so a reviewer can object to them explicitly rather than discover
them:

1. `ConsumptionState` (§5.2). It is an ordinal over **human–object physical interaction**
   (not-touched → touched → opened → oriented-to-lens → contents-removed → passed-to-another),
   which brief §28 ("distinguish incidental appearances from active product usage") requires and
   which free text cannot provide, because the downstream comparison needs an ordering. It does
   presume a physical, manipulable artefact; it does **not** name a category, a use, an occasion
   or a product type. `present` and `unclear` carry no assumption at all and are expected to be
   the modal values.
2. `SponsorCueKind` (§5.4). Each member names an *observable artefact* (a disclosure string, a
   platform label, a code, a pinned comment), not a judgement about the market.
3. `Modality` / `Basis` / `Legibility` / `Prominence` — structural properties of evidence, not
   content.

Everything in brief §19B remains free text. If a reviewer judges exception 1 to be category
seeding, the fallback is to emit only `contact: bool` + a free-text cue and let §23 derive the
ordering — at the cost of making the brief §28 distinction unaggregatable.

**Two sources of seeding are easy to miss and are explicitly handled:**

- **The discovery query.** If the research engine found an item by searching `"<brand> <term>"`,
  putting that query in the prompt is a direct instruction to find that brand. Default: the
  discovery query is **not** sent to the model (§3.4, OQ-1). It stays in provenance where the
  research engine already has it.
- **The caption.** Caption text naming a brand will make a vision model "see" that brand. This
  is handled structurally, not by instruction: `visible_brands` and `mentioned_brands` are
  **separate fields with disjoint admissible anchor modalities** (§5.3), and content-block
  ordering puts frames before any text (§3.3).

---

## 1. Media retrieval and FFmpeg processing

### 1.1 Media tiers and graceful degradation (brief §7)

Not every item yields a video file. Apify actors return expiring CDN URLs; `APIFY_TOKEN` and
`YOUTUBE_API_KEY` are currently absent; some platforms yield only a thumbnail. The pipeline
declares which tier it got and **never silently pretends** a lower tier is a higher one.

| `media_tier` | Available | Frames | Audio | Analysis effect |
|---|---|---|---|---|
| `full_video` | decodable video file | per §1.4 policy | yes | full schema |
| `audio_only` | audio track only (or video with unusable video stream) | thumbnail if present, else none | yes | `visible_*` fields restricted to the thumbnail or empty |
| `thumbnail_only` | one still image | 1 (the still) | no | `consumption_state` capped at `present` by validator (§6.4); all §19B temporal fields must be empty |
| `metadata_only` | caption/metadata only | 0 | no | **skipped**: status `skipped`, reason `no_media`. Not analysed, counted separately in every denominator |

`metadata_only` items are deliberately **not** sent to the model. A caption-only "analysis"
is exactly the caption-driven social listening the brief rejects (§ preamble), and it would
contaminate the corpus with items whose `visible_brands` can only ever be hallucinated.

Retrieval order: (1) S3 cache by `content_item_id`; (2) provider-supplied direct media URL;
(3) provider thumbnail URL. Download cap 200 MB and 90 s wall clock per item; on exceed,
degrade one tier rather than fail the item. Every tier transition is written to
`analysis_attempts` so §28's "identify insufficient data" is backed by counts, not vibes.

### 1.2 Probe

```
ffprobe -v error -print_format json -show_format -show_streams -- "$SRC"
```

Captured into `MediaProbe`: `duration_s`, `width`, `height`, `rotation` (from the display
matrix side data — vertical platform video is frequently 1920×1080 with a 90° rotation, and
reading `width`/`height` without it produces wrongly-oriented crops), `nb_frames`, `avg_frame_rate`,
`has_audio`, `audio_channels`, `audio_sample_rate`, `codec_name`.

### 1.3 Uniform sampling vs scene-change detection — decision

**Decision: hybrid bucketed scene-max.** Neither pure strategy is acceptable:

- **Pure uniform** at N frames over a short-form video with hard cuts lands frames mid-cut and
  mid-transition, and systematically misses the one deliberate "hold the object up to the lens"
  shot, which is precisely the frame that carries legible packaging. The product's headline
  claim — *what brand is visible and is it actually being used* — depends on hitting that frame.
- **Pure scene-change** (`select='gt(scene,0.3)'`) on short-form video returns a wildly variable
  count: a fast-cut edit yields 40–80 candidates clustered in whichever section is most heavily
  cut, while a single-take talking clip yields **zero**. Both failure modes are fatal: the first
  blows the token budget and over-represents one passage, the second returns no frames at all.
  Scene score is also a per-frame difference metric, so it ranks *transitions*, not *informativeness*.

**Hybrid algorithm** (exact):

1. Cheap scoring pass over the whole file at decimated rate and tiny resolution (§1.5), giving
   `[(t_s, scene_score)]` for every sampled frame.
2. `N = frame_budget(duration_s)` from the tier table in §1.4.
3. Partition `[t_lo, t_hi]` into `N` equal-width buckets, where
   `t_lo = clamp(0.03 * duration_s, 0.20, 1.0)` and
   `t_hi = duration_s - clamp(0.04 * duration_s, 0.25, 2.0)`.
   *Why trim the ends:* the opening frames are frequently a platform/black/logo frame or a
   fade-in and the closing frames are frequently an end card or fade-out; both are
   high-scene-score and low-information, and without trimming the scene-max rule reliably
   selects them. **Correction applied in review:** the original constants
   (`min(0.35, …)` / `min(0.25, …)`) could never exceed 0.35 s / 0.25 s, so they did not in fact
   trim the end card the rationale was written about — a typical end card runs 1–3 s. The
   clamped-proportional form above trims 0.2–1.0 s of head and 0.25–2.0 s of tail, which does.
   This is a *heuristic*, not a measurement: how much it helps (and whether it ever discards a
   real product reveal that lives in the last second) is measured by ablations A3/A5 (§6.6).
4. Within bucket `i`, select `t_i = argmax(scene_score)` over the samples in that bucket, but
   taking the frame **`lead_s = 0.30` seconds after** that argmax. *Why the offset:* the
   maximum-difference frame is the first frame of the new shot, which on real platform video is
   often still mid-dissolve, motion-blurred, or carries a transition effect; +0.30 s lands inside
   the settled shot. If `t_i + lead_s` leaves the bucket, clamp to the bucket's right edge.
5. If a bucket's maximum score is below the flat threshold, the bucket contains no cut; use the
   bucket **midpoint** instead. The threshold is **relative, with an absolute floor**:

   ```
   flat_threshold = max(SCENE_FLAT_ABS, SCENE_FLAT_REL * median(all scene scores in the item))
   SCENE_FLAT_ABS = 0.08    SCENE_FLAT_REL = 3.0
   ```

   **Correction applied in review.** The original rule used the bare absolute 0.08, justified by
   "camera-motion-only variation within one continuous shot sits under ~0.05". That figure is a
   *full-frame-rate* property. The scoring pass runs at `fps=6` (§1.5c), i.e. 167 ms between
   compared frames, so accumulated camera and subject motion inflates within-shot scores by an
   amount that depends on the clip. A handheld single-take item can therefore exceed 0.08 in
   every bucket, the "has a cut" branch is taken everywhere, and `argmax` then selects **the
   moment of greatest motion** — the most motion-blurred frame in the bucket, which is the exact
   opposite of the intent. Decimation is not a monotone rescaling of the full-rate score (it
   compares different frame pairs), so the original invariance argument does not hold. A
   median-relative rule is invariant to the global motion level of the item and to the inflation
   from decimation; the absolute floor keeps a genuinely static clip (median ≈ 0) on the midpoint
   branch. Both constants are unmeasured priors — see OQ-12.
   Record `selection_reason` as `bucket_midpoint` or `bucket_scene_max`, and record the item's
   `midpoint_fraction` (share of buckets that took the midpoint branch) on `content_analysis`: it
   is the only cheap proxy the pipeline has for "static single-take vs cut-heavy production", and
   §6.6 stratifies on it because frame-selection quality — and therefore brand-read recall — is
   correlated with it.
6. Enforce `min_gap_s = max(0.5, 0.25 * duration_s / N)` between consecutive selected timestamps
   in a **single deterministic left-to-right pass**: for `i` ascending, if
   `t_i - t_{i-1} < min_gap_s`, set `t_i = clamp(t_{i-1} + min_gap_s, bucket_i.lo, bucket_i.hi)`;
   if the clamp cannot satisfy the gap, accept the violation, keep the frame, and record
   `gap_violated=true` on it. One pass, no iteration to convergence, no reordering — determinism
   matters because the frame set is an input to the artefact cache key (§1.8). Identical or
   near-identical frames waste 700 tokens each.
7. **Additionally include the platform thumbnail / cover image** when one exists, as
   `frame_index = N` (always *in addition to* the N sampled frames, never replacing one) with
   `selection_reason='thumbnail'`. The creator chose it, so it has the highest prior in the item
   for deliberate object presentation, and it costs one frame.

   **It is not, however, a frame of the video, and the design must stop treating it as one.**
   On some platforms the cover is an arbitrary upload: a composite, a graphic, a still from a
   different shoot, with text and objects added. Three consequences are handled explicitly:
   - The manifest labels it `source=platform_thumbnail` (§4.3) **and** the system prompt states
     what that means (§4.2 rule 7a), so the model does not silently treat it as in-video evidence.
   - A claim whose frame anchors are *all* the thumbnail index is flagged
     `thumbnail_only_anchored=true` on `content_analysis_claims`. CSR-7 excludes such claims from
     every *in-video* visibility, behaviour and consumption aggregate; they remain usable for
     brand-presence-in-the-item counts, with their own denominator.
   - Whether a thumbnail existed is recorded (`platform_thumbnail_present`). Thumbnail
     availability differs systematically by platform, so without this column a cross-platform
     comparison of brand visibility is confounded by it and nothing records the confound.

This gives: guaranteed temporal coverage (uniform's property), shot-aware frame choice
(scene detection's property), and a **deterministic** frame count and timestamp set for identical
input bytes and an identical policy (neither pure method has either).

*Deterministic timestamps, not deterministic bytes.* The selected **timestamps** are a pure
function of (source bytes, policy). The encoded **JPEG bytes** are additionally a function of the
FFmpeg build, so a toolchain upgrade silently changes them. §8 therefore keys `media_fingerprint`
off the *source* bytes, not the frame bytes (§8.1), and `frame_policy_hash` includes an
`ffmpeg_build_id` so a toolchain change produces a new, visible artefact generation instead of a
corpus-wide re-analysis that nobody asked for.

### 1.4 Frame budget, resolution, JPEG quality, token cost

**Resolution is set by a single long-edge cap, and the cap is chosen to land on a clean patch
grid.** Claude tokenises images at roughly one token per 28×28 pixel patch, so

```
image_tokens ≈ ceil(W / 28) * ceil(H / 28)
```

At `LONG_EDGE = 960`, both common platform aspect ratios cost the **same** 700 tokens:

| Source | Scaled | Patch grid | Tokens |
|---|---|---|---|
| 1080×1920 (9:16) | 540×960 | 20 × 35 | **700** |
| 1920×1080 (16:9) | 960×540 | 35 × 20 | **700** |
| 1080×1080 (1:1) → use `SQUARE_LONG_EDGE = 832` | 832×832 | 30 × 30 | **900** |
| 1080×1350 (4:5) → long edge 960 | 768×960 | 28 × 35 | 980 |

Rule: if `0.8 <= W/H <= 1.25`, cap the long edge at **832**; otherwise at **960**. *Why 960:*
it is the largest cap that keeps a 16:9/9:16 frame at 700 tokens; 1024 pushes the grid to
37×21 = 777 (+11% cost for +6% linear resolution), and dropping to 896 only saves 700→672
(−4%) while losing 7% of linear resolution on packaging text, which is the thing most at risk.
Opus 5 accepts substantially larger images (the Opus 4.7+ family supports up to 2576 px on the
long edge, ≈4784 tokens), and that headroom is deliberately reserved for the §1.7 detail pass
rather than spent uniformly on every frame.

**Frame budget by duration** (`N` excludes the thumbnail):

| `duration_s` | N | Effective sampling | Rationale |
|---|---:|---|---|
| < 5 | 3 | ~1.5 s | Below 3 frames you cannot distinguish "object present" from "object used"; a sub-5 s clip has at most 2–3 distinct shots |
| 5 – 20 | 6 | ~3 s | |
| 20 – 60 | **8** | ~7 s | The modal short-form item. 8 buckets resolve the standard hook / body / payoff / end-card structure with ≥2 frames per section |
| 60 – 180 | 12 | ~13 s | |
| 180 – 600 | 16 | ~25 s | |
| > 600 | 20 + relevance window (§1.6) | — | Uniform sampling of a 20-minute video at 20 frames is one frame per minute and will miss everything; see §1.6 |

**Boundary behaviour:** the tiers are evaluated with `<` on the upper bound, so a clip of exactly
60.0 s gets 12 frames, not 8. A clip whose probe `duration_s` is missing or zero (common with
truncated downloads) is treated as the 20–60 tier and its actual frame count is recorded; if
fewer than 2 frames extract successfully, the item degrades to `thumbnail_only`.

**JPEG quality: `-q:v 3`** (mjpeg qscale, 2 = best, 31 = worst). Image **token** cost is a
function of pixel dimensions only, so quality costs nothing in tokens — it costs only bytes.
q=3 at 540×960 produces ~70–110 KB, keeps a 9-frame payload near 1 MB base64-expanded (well
under the 5 MB/image and 32 MB/request API limits), and avoids the ringing artefacts around
small high-contrast glyphs — i.e. exactly the packaging wordmarks we need to read — that appear
from about q=7. q=2 adds ~35% bytes for no legibility gain at this resolution.

**Per-video image token cost** (default config): short-form `8 × 700 = 5,600` sampled, plus
`700` when the platform supplies a thumbnail, i.e. `5,600` or `6,300`. The thumbnail is additive
and never replaces a sampled frame (§1.3 step 7) — the earlier "replaces one sampled frame"
rule made the frame count depend on thumbnail availability, which contradicted the determinism
the policy hash depends on and made two items with the same policy carry different evidence
without recording it. §7 plans on `5,600` and treats the thumbnail as an item-dependent
`+700` line (≈+$0.0035/item interactive at the observed thumbnail rate, which is recorded, not
assumed).

### 1.5 Exact FFmpeg commands

**(a) Audio extraction** — tolerant of a missing audio stream via the `?` map suffix:

```bash
ffmpeg -nostdin -hide_banner -loglevel error \
  -i "$SRC" \
  -map 0:a:0? -vn -sn -dn \
  -ac 1 -ar 16000 \
  -c:a libmp3lame -b:a 32k \
  -y "$WORK/audio.mp3"
```

`-ac 1 -ar 16000` because whisper-1 resamples to 16 kHz mono internally; sending anything richer
only inflates upload bytes. 32 kbps CBR makes size exactly predictable at 4 KB/s, so the 25 MB
whisper-1 file limit is reached at 6,250 s ≈ 104 minutes, and the chunker (§2.6) can be driven
off duration alone with no trial uploads. If the output file is absent or `< 2 KB`, there is no
usable audio track → `has_speech = false`, `asr_quality = 'unavailable'`, no whisper call.

**(b) Silence / speech-ratio pre-check** — run before paying for ASR:

```bash
ffmpeg -nostdin -hide_banner -loglevel info \
  -i "$WORK/audio.mp3" \
  -af "silencedetect=noise=-35dB:d=0.4" \
  -f null - 2>&1 | grep -E 'silence_(start|end|duration)'
```

`speech_ratio = 1 - (Σ silence_duration) / duration_s`.
**Parsing rule (bug fixed in review):** `silencedetect` emits `silence_duration` only on a
`silence_end` line. A silence that runs to EOF never produces one, so a clip that ends in 20 s of
silence was being scored as 20 s of speech. The parser must track an unterminated `silence_start`
and close it at `duration_s` before summing. `-35 dB` rather than the `-60 dB`
default because platform audio is loudness-normalised and heavily compressed, so true digital
silence is rare; `-35 dB` separates "nothing but room tone / bed music floor" from speech.
`d=0.4` so ordinary inter-word pauses (typically < 0.3 s) are not counted as silence.

**(c) Scene scoring pass** — whole file, decimated and tiny, so it is I/O-bound not CPU-bound:

```bash
ffmpeg -nostdin -hide_banner -loglevel info \
  -i "$SRC" -map 0:v:0 -an -sn -dn \
  -vf "fps=6,scale=160:-2:flags=fast_bilinear,scdet=threshold=0,metadata=print:file=-" \
  -f null - 2>&1
```

Parse `pts_time` with `lavfi.scd.score` (0–100; divide by 100 to normalise). **Implementation
note:** confirm the metadata key against `ffmpeg -h filter=scdet` on the installed FFmpeg 8 build
and parse defensively — accept `lavfi.scd.score` and fall back to `lavfi.scene_score` (set by the
older `select='gt(scene,T)'` idiom, which remains the fallback expression if `scdet` is
unavailable). Never let a parse miss become a crash: zero parsed scores ⇒ every bucket takes its
midpoint, i.e. the policy degrades cleanly to uniform sampling.

**Cost of this pass (corrected in review).** `fps=6` is a *filter*, applied after decoding:
FFmpeg still decodes every frame of the file and then discards five in six. The original claim
that this pass is "I/O-bound not CPU-bound" is wrong, and the §7.4 wall-clock estimate was built
on it. `scale=160:-2` with `fast_bilinear` does make the *filter* cost negligible, but the decode
is not avoided. For a 10-minute 1080p item a full decode is tens of seconds, and the 400
longer-form items in the §7.2 corpus are exactly where it bites.

Policy: for `duration_s > 180`, run the scoring pass with `-skip_frame nokey` (keyframe-only
decode, one to two orders of magnitude cheaper) and score the keyframes. Encoders place
keyframes at cuts, so keyframe position is itself a reasonable cut proxy; where the encode uses a
fixed GOP the scores come out flat and the policy degrades to bucket midpoints, which is the
already-designed fallback. Below 180 s, keep the full-decode pass. Both branches are recorded in
`frame_policy` and therefore in the fingerprint. The crossover and the resulting wall-clock
figures are **unmeasured** — see §7.4.

Scores are used only for **ranking within a bucket** and for the flat/not-flat test, and ranking
within a bucket is invariant to any monotone rescaling. The flat/not-flat test is **not**
invariant to the decimation, which is why §1.3 step 5 now uses a median-relative threshold.

**(d) Frame extraction — single-pass variant, used when `duration_s <= 120`:**

```bash
ffmpeg -nostdin -hide_banner -loglevel error \
  -i "$SRC" -map 0:v:0 -an -sn -dn \
  -vf "select='eq(n\,$F0)+eq(n\,$F1)+eq(n\,$F2)+...',\
scale=w=960:h=960:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,\
setsar=1" \
  -fps_mode passthrough -q:v 3 -f image2 \
  -y "$WORK/frames/f_%03d.jpg"
```

Frame numbers come from `round(t_i * avg_frame_rate)`. One decode of the whole file; for a 45 s
1080p clip that is ~2–4 s on the local machine.

**Two implementer traps, both silent, both specified here rather than left to guesswork:**

1. **This path is valid only for constant-frame-rate sources.** `round(t * avg_frame_rate)` is
   wrong for VFR, which is common on mobile platform video: the nth frame is not at
   `n / avg_frame_rate`, and the extracted frame can be seconds away from the requested
   timestamp. Gate on `r_frame_rate == avg_frame_rate` from the probe; when they differ, use the
   seek variant (e) regardless of duration.
2. **`select` + `image2` numbers output files in selection order, not by `frame_index`.** The
   files are `f_001.jpg … f_00N.jpg` in ascending time, so they must be zipped back onto the
   *sorted* `FrameChoice` list. Getting this wrong attaches every claim's frame anchor to the
   wrong image, raises no error anywhere, and corrupts the evidence trail that is the product's
   whole proposition. Therefore: after extraction, `ffprobe` each output's
   `best_effort_timestamp`, assert `|t_actual - t_requested| <= 0.25 s` (the same tolerance as
   (e)), **store `t_actual`** on `content_frames.t_s`, and put `t_actual` — not the requested
   time — in the manifest the model sees. If the assertion fails for any frame, fall back to the
   seek variant for the whole item.
`scale=w=960:h=960:force_original_aspect_ratio=decrease` is the orientation-agnostic long-edge
cap from §1.4 — one knob, correct for portrait, landscape and square sources, and it never
upscales a small source past its native size (`decrease` only shrinks). `lanczos` preserves glyph
edges better than the default bicubic at these reduction factors.

**(e) Frame extraction — seek variant, used when `duration_s > 120`:** one invocation per
timestamp, input-seek so FFmpeg does not decode the preamble.

```bash
# one invocation per timestamp; IDX is the frame_index, printf-padded by the caller
ffmpeg -nostdin -hide_banner -loglevel error \
  -ss "$T" -i "$SRC" \
  -map 0:v:0 -an -sn -dn -frames:v 1 \
  -vf "scale=w=960:h=960:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1" \
  -q:v 3 -f image2 -y "$WORK/frames/f_$(printf '%03d' "$IDX").jpg"
```

**Bug fixed in review:** the original form used a `%03d` pattern with `-f mjpeg` on a
one-invocation-per-timestamp loop. The `mjpeg` muxer does not expand the pattern, and every one
of the N invocations wrote to the same path — so the seek variant produced exactly **one** frame,
the last one, for every long-form item. Each invocation must name its own output file explicitly,
and the muxer must be `image2`.

**Why 120 s as the crossover:** a full decode scales linearly with duration (~2–4 s per 45 s of
1080p here), while N input-seeks cost roughly `N × 0.2–0.4 s` and are independent of duration.
At 120 s / 12 frames the two are both ~4–6 s; past that, seeking wins and keeps winning. At the
boundary either path is correct and produces the same frames, so a misclassification costs only
latency. For containers with sparse keyframes where plain `-ss` lands early, use the hybrid form
`-ss (T-2) -i SRC -ss 2 -frames:v 1` (coarse input seek, fine output seek) — detected by
comparing the extracted frame's `best_effort_timestamp` against the requested `T` and retrying
when the delta exceeds 0.25 s.

**(f) Zoom/detail crop** — see §1.7.

### 1.6 Short clips, long videos, thumbnail-only

- **Short clips (< 5 s):** 3 frames, bucket trim becomes proportional (`t_lo = 0.05 * duration`),
  `min_gap_s` floor of 0.5 s may force fewer than 3 distinct timestamps — in that case emit the
  distinct ones and record the real count. A 1.2 s clip legitimately yields 2 frames.
- **Long videos (> 600 s):** uniform 20 frames is one frame per minute and is close to useless.
  Policy: 20 **baseline** frames by the §1.3 hybrid, **plus** up to 12 **relevance-window** frames.
  The relevance window is derived from the *transcript* (which is cheap and complete for the whole
  video) by locating segments containing any term from the **run-supplied query lexicon**, which
  is produced by the research engine at runtime and is **never a hardcoded list** — this is the one
  place query terms touch frame selection, and they touch only *where to look*, never *what to
  report* (the terms are not sent to the model). Take up to 4 windows, 3 frames each, centred on
  the matching segment midpoints, excluding windows within 10 s of a baseline frame. If the lexicon
  is empty or matches nothing, no relevance frames are added and the item is analysed on its 20
  baseline frames. Cap total frames at 32 (22,400 image tokens ≈ \$0.112 input) and record
  `frame_policy.relevance_windows` in the fingerprint so the choice is reproducible.
- **Thumbnail-only:** the *same* system prompt and schema are used — divergent prompts per tier
  would make results incomparable across tiers. What changes is one line of the media manifest
  (`media_tier: thumbnail_only`, `frame_count: 1`, `duration_s: null`), which the prompt's
  single-still rule keys off (§4.2 rule 7), plus a hard validator (§6.4) that rejects any
  `consumption_state` above `present`, any non-empty `activities`, and any transcript-anchored
  claim. Thumbnail-only items carry a flag that excludes them from motion/behaviour aggregation
  denominators downstream (cross-subsystem requirement CSR-7).

### 1.7 The detail (zoom) pass — conditional, not default

Packaging wordmarks in background or hand-held positions are frequently sub-legible at 540×960.
Rather than pay 2576-px frames for every frame of every video (≈4,784 tokens/frame, a 6.8×
increase), spend high resolution only where the first pass says it is needed.

**Trigger:** the first-pass result contains any `visible_brands[i].legibility in {partial}`, or
any `visible_products[j].has_legible_text_region == true` with no brand resolved. Expected rate
~15% of items (planning assumption, to be measured — OQ-3).

**Localisation:** the first-pass schema requires, on each such item, a
`detail_request: {frame_index, region}` where `region` is one of nine coarse grid cells
(`top_left … center … bottom_right`). Coarse 9-cell localisation is near the limit of what a
non-grounding vision model does reliably; a bounding box would be fabricated precision.

**Crop:**

```bash
ffmpeg -nostdin -hide_banner -loglevel error \
  -ss "$T" -i "$SRC" -map 0:v:0 -an -sn -dn -frames:v 1 \
  -vf "crop=w=iw/2:h=ih/2:x=$CX:y=$CY,\
scale=w=896:h=896:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1" \
  -q:v 2 -f mjpeg -y "$WORK/frames/zoom_000.jpg"
```

The crop window is **half** the frame in each dimension (a 2×2 window centred on the nominated
3×3 cell, i.e. the cell plus 25% padding on every side) so a slightly wrong cell still contains
the target. The window must be **clamped to the frame**: for cell centre `(cx, cy)` in source
pixels, `x = clamp(cx - iw/4, 0, iw/2)` and `y = clamp(cy - ih/4, 0, ih/2)` — an unclamped centred
window on `top_left` has negative `x` and FFmpeg errors out. `iw`/`ih` here are *post-autorotation*
dimensions (FFmpeg applies the display matrix by default); the probe's `rotation_deg` is used to
assert that the post-scale dimensions have the orientation the probe predicted, and the actual
`width`/`height` are recorded per frame rather than assumed. `detail_request.frame_index` is
model-emitted and must be range-checked against the manifest before any crop is attempted. Re-extracted from the **source** at native resolution, not upscaled from the 960-px
frame — upscaling adds no information. `896` long edge ⇒ 32 × 32 = **1,024 tokens**;
`-q:v 2` here because this image exists solely to be read as text.

**Second call:** a separate, tiny, open-world transcription-of-visible-text prompt (§4.5). It
returns text as rendered and marks described; it is given **no** brand list and **no** first-pass
hypothesis, so it cannot confirm the first pass by suggestion. Its result updates
`visible_brands[i].brand_text_as_seen` and `legibility` only when it returns
`confident == true`; otherwise the brand claim is demoted to an `visible_products` entry with
`brand_resolved = false`.

### 1.8 Artefact caching

Frames: `s3://$BUCKET/frames/{content_item_id}/{frame_policy_hash}/{idx:03d}.jpg`.
Audio: `s3://$BUCKET/audio/{content_item_id}/{sha256(source_bytes)[:16]}.mp3`.
Keying frames by `frame_policy_hash` means re-analysis at an unchanged frame policy performs
**zero** downloads and **zero** FFmpeg work, while a policy change cleanly produces a new
artefact set instead of silently reusing the old one. 1,500 videos × 9 frames × ~90 KB ≈ 1.2 GB
per run — immaterial at S3 pricing, and worth keeping indefinitely because every UI evidence
view (§31.5) and every re-run depends on it.

---

## 2. Transcription

### 2.1 Call shape

```python
# aypo/analysis/transcribe.py
res = await openai.audio.transcriptions.create(
    model="whisper-1",
    file=("audio.mp3", audio_bytes, "audio/mpeg"),
    response_format="verbose_json",
    timestamp_granularities=["segment"] if duration_s > 120 else ["segment", "word"],
    temperature=0.0,
    # language:  DELIBERATELY OMITTED  — see §2.2
    # prompt:    DELIBERATELY OMITTED  — see §2.3
)
```

`response_format="verbose_json"` is required for `timestamp_granularities` and is the only format
that returns the per-segment diagnostics (`avg_logprob`, `no_speech_prob`, `compression_ratio`)
the quality filter in §2.5 depends on. Word granularity is requested only for items ≤ 120 s: word
timestamps roughly triple transcript JSON size, and their value is tight evidence anchors, which
matter most on short items where a claim points at a 0.4 s window rather than a 6 s segment.

### 2.2 Code-switching: never force a language

`language` is **not** passed. Forcing a language on code-switched speech degrades it in a
specific and damaging way: with `language` pinned, the decoder's token prior fights every
switched-in word, which both lowers accuracy on the switched words and — worse for this product —
tends to *normalise* them into the pinned language's orthography, destroying exactly the slang
and borrowed-word evidence that brief §18 (AUDIO: "language; code-switching ... slang") asks for.
Auto-detection keeps the decoder free to switch.

What is stored instead of an assumption:

- `language_detected` — **free text**, whatever the API reports, with no validation against an
  allow-list. A closed language enum is a market assumption and is forbidden by §0.3.
- `code_switch_suspected: bool` — a cheap heterogeneity signal computed post-hoc, not asserted
  by the model. **Redefined in review:** the original definition ("segments whose token set is
  disjoint from the modal segment's detected-language profile") is not implementable as written —
  `whisper-1` returns a single top-level `language`, there is no per-segment detected language,
  and a "language profile" would be a per-locale lexicon resource, i.e. exactly the market
  assumption §0.3 forbids. The implementable, locale-free definition is **Unicode-script
  heterogeneity plus token-reuse heterogeneity**:

  ```
  script_het = 1 - (modal Unicode-script share of alphabetic characters, over the whole text)
  seg_div    = share of segments whose token set overlaps the corpus-modal token set
               (top 20 tokens of this item) by Jaccard < 0.05
  code_switch_suspected = (script_het > 0.05) or (seg_div > 0.25 and n_segments >= 6)
  ```

  Both terms are computed from the item's own text; neither consults any external vocabulary.
  This is a *hint for the UI and for QA sampling only* — it gates nothing and is never published.
  The authoritative language claims come from the fusion call's `languages` field with anchors
  (§5.5), because the fusion model sees both audio transcript and on-screen text. The thresholds
  are unmeasured priors; they are tuned, if at all, against the OQ-2 Gemini sample.

**Known risk (OQ-2):** `whisper-1` on the `/transcriptions` endpoint occasionally *translates*
rather than transcribes heavily code-switched audio, returning fluent English for mixed-language
speech. There is no API flag that suppresses this. Mitigations in the design: (a) store the raw
`language` the API reports and flag items where `language == "en"` while
`code_switch_suspected == true` as `asr_review_queue`. *This single reference to English is not
a market assumption and does not violate §0.3:* whisper's translation behaviour has English as
its fixed target by construction, so "reported language is English" is a property of the **ASR
model's failure mode**, not a hypothesis about the corpus. It sets a QA flag only — it never
filters, re-weights or hides content, and it is not sent to the fusion model; (b) route a 50-clip stratified sample
through **Gemini 2.x** as an independent ASR and measure disagreement (§6.6). Do **not** attempt
to fix this with a biasing `prompt` — see §2.3.

### 2.3 Why no ASR `prompt`

`prompt` is whisper-1's only steering surface, and it steers by *vocabulary priming*. Any priming
text is, by construction, a list of words we expect to hear — i.e. a seeded hypothesis about
slang, brands and cultural references, written before the data was collected. That is a direct
violation of brief §1/§34, and it would make the resulting slang evidence circular: the system
would "discover" the terms it was primed with. The `prompt` field is left empty, permanently, and
this decision is recorded in `asr_config` so it is visible in the config fingerprint rather than
being an invisible default.

### 2.4 No speech / no usable audio

Decision ladder, cheapest test first:

1. No audio stream, or audio file < 2 KB → `has_speech=false`, `asr_quality='unavailable'`,
   **no API call**.
2. `speech_ratio < 0.08` **AND** `speech_seconds < 4.0` → `asr_quality='skipped_no_speech'`,
   **no API call**. *Why both conditions (corrected in review):* the justification for 0.08 is an
   absolute one — "< 3.6 s of non-silent audio cannot contain a usable utterance" — but
   `speech_ratio` is a *ratio*, so the bare ratio test is duration-dependent in its reasoning and
   duration-independent in its effect. On a 600 s item, `speech_ratio = 0.08` is **48 seconds of
   speech**, and the original rule silently discarded the audio channel of every long, sparsely
   narrated item — which is precisely the item class the §1.6 relevance-window policy exists to
   serve. The conjunction keeps the cheap skip where it was justified (short, near-silent clips,
   the regime where whisper-1 most reliably emits training-corpus filler) and removes it where it
   was not. `speech_seconds = speech_ratio * duration_s`.
   **At the boundary (`speech_seconds` in [4.0, 8.0), or `speech_ratio` in [0.08, 0.15)):** the call *is* made, but
   `asr_quality='low'`, and a validator (§6.4) forbids any single-anchor transcript-only claim —
   such a claim needs two independent segments or a second modality.
3. Music-only audio defeats `silencedetect` (it is loud, so `speech_ratio ≈ 1`). It is caught
   *after* the call by the statistical filter in §2.5; if the filter drops more than 80% of
   segments, set `has_speech=false`, `asr_quality='degenerate'`, store the raw response for
   audit, and render the transcript block as `(no intelligible speech detected)`.

When `has_speech=false`, the fusion call still runs — the visual channel is the primary channel
(brief §18: "do not reduce video analysis to OCR"). The transcript block is replaced with an
explicit negative statement so the model does not infer that speech existed and was withheld.

### 2.5 Segment quality filter — statistical, never phrase-based

Per segment, using the `verbose_json` diagnostics:

| Rule | Action | Why this number |
|---|---|---|
| `no_speech_prob > 0.6` **AND** `avg_logprob < -0.8` | drop segment | 0.6 and ≈-1.0 are the thresholds the reference Whisper decoder itself uses for its no-speech and low-confidence gates. `avg_logprob` is tightened to **-0.8** because code-switched speech legitimately scores lower than monolingual speech, so a logprob gate alone over-fires on exactly the content we care about; requiring **both** conditions means genuine low-confidence code-switching (low logprob, *low* no-speech-prob) survives while music and room tone (high no-speech-prob *and* low logprob) are removed. |
| `compression_ratio > 2.4` | drop segment | The reference decoder's degeneration threshold. Above it the segment text is repetitive loop output, not speech. |
| ≥ 4 consecutive segments with identical normalised text | drop all, set `asr_degenerate=true` | Repetition loops are the dominant whisper failure on music beds; 4 is low enough to catch loops early and high enough that a genuinely repeated short phrase (a chant, a count-in) is not destroyed. |
| Exactly one of the first two rules fires | **keep**, set `low_conf=true` | Cannot be the sole anchor for a published claim (§6.4) |

**Deliberately absent: a filler-phrase denylist.** The usual approach — a list of known whisper
hallucination strings — is corpus- and locale-specific. Shipping such a list is both an
assumption about what language the audio is in and a silent content filter that could delete real
speech. The statistical filters above are language-agnostic and auditable. `dropped_segment_count`
is stored so the filter's aggressiveness is measurable.

### 2.6 Chunking long audio

Required above the 25 MB whisper-1 limit (≈104 min at 32 kbps) and used earlier for latency.
Split at **900 s** targets, snapped to the nearest `silencedetect` boundary within ±20 s of the
target (falling back to a hard cut if no silence boundary exists there), with **2.0 s overlap**.
Each chunk's segment/word timestamps are re-based by the chunk's start offset before merging.
Overlap de-duplication: in the 2 s overlap region, keep the earlier chunk's segments and drop the
later chunk's segments whose start lies before the earlier chunk's last kept segment end. 900 s
keeps each chunk ~3.5 MB, well inside the limit, and keeps a single failed chunk's retry cheap.

### 2.7 What is stored

`content_transcripts` (DDL in §10.3): full `text`, `language_detected`, `duration_s`,
`speech_ratio`, `asr_quality`, `code_switch_suspected`, `dropped_segment_count`, `cost_usd`, and
`segments jsonb` as an array of
`{id, start, end, text, avg_logprob, no_speech_prob, compression_ratio, low_conf}`, plus
`words jsonb` (nullable) as `{w, start, end}`.

### 2.8 Rendering the transcript into the prompt

```
[00.00–02.40] <text>
[02.40–05.90] <text>
[05.90–09.10] (non-speech audio)
```

Rules: segments shorter than 1.0 s are merged into the following segment (timestamp overhead
would otherwise exceed the text); dropped segments are **not deleted** but rendered as
`(non-speech audio)` spanning their time range, so the model sees that the timeline is continuous
and does not infer that it has the whole utterance; total transcript block hard-capped at
12,000 characters with middle-elision marked `[… N segments elided …]` (only reachable on >30 min
items). Timestamps use the segment start/end, two decimals, because every transcript-anchored
claim must point into this rendering and the model can only cite what it can see.

---

## 3. FUSION

### 3.1 Decision: ONE Claude call per item

All four channels (frames, transcript, platform text, item context) go into a **single**
`messages` request. Justification on both axes:

**Accuracy.** The product's two load-bearing judgements are irreducibly cross-modal:

- *Incidental appearance vs active use* (brief §28) is decided by a frame showing contact/manipulation
  **together with** what is said at that timestamp. A visual-only stage emits "object held";
  a text-only stage emits "speaker describes using something"; neither can emit the joined
  claim, and a second fusion stage can only join their **lossy summaries** — by then the pixel
  evidence is gone and the frame index needed for the anchor has been discarded.
- *Sponsored vs organic* (brief §19A, §28) requires reconciling on-screen disclosure text, spoken
  disclosure, caption hashtags and production cues. These live in three different channels and a
  staged design would have to re-derive the reconciliation from summaries.

More fundamentally, brief §27 requires that every claim carry an anchor into specific evidence.
Anchors can only be emitted by the stage that can *see* the frame index and the transcript
timestamp at the same time. A staged pipeline either loses anchors or has to re-attach them by
string matching, which is exactly the hallucinated-provenance failure §28 forbids.

**Cost.** Splitting is strictly more expensive, not less:
the frames are unique per item so they can never be reused or cached across the two calls; the
system prompt would be paid twice (mitigated but not eliminated by caching); output tokens
— the dominant cost line at Opus 5's \$25/MTok, see §7 — would be paid twice, once for the
intermediate representation and once for the final object; and adaptive thinking would run twice.
Measured in the §7 model, a two-stage design costs ≈1.7× a single call for *lower* anchor fidelity.

**The one exception is conditional, not architectural:** the §1.7 detail pass. It fires on ~15%
of items, costs ~\$0.015 each, and exists because it needs a *different input* (a high-resolution
crop that cannot be chosen before the first pass), not because it needs a different fusion.

### 3.2 Model and parameters

```python
MODEL       = "claude-opus-5"
EFFORT      = "medium"        # default; see justification below
MAX_TOKENS  = 32_000          # ceiling, not a charge — see below
THINKING    = {"type": "adaptive"}
```

- **`effort="medium"`.** This is a perception-plus-extraction task, not long-horizon reasoning:
  the work is bounded by what is in 8 images and a transcript, and there is no search space to
  explore. `high` is the model default and buys thinking tokens that, on this task shape, are
  spent re-describing frames. Thinking tokens bill as output at \$25/MTok and are the single
  largest cost line (§7), so this is also the largest quality-trading lever. **This default must
  be confirmed, not assumed:** the calibration harness (§6.6) runs an effort sweep over
  `{low, medium, high}` on the 150-item labelled set and selects the lowest level that is
  **not detectably worse than `high`** — i.e. the paired per-item difference in the
  pre-registered primary metric has a Holm-corrected bootstrap interval containing zero (§6.6).
  **The original "within 2.0 macro-F1 points, because that is below the annotator-disagreement
  band" rule is replaced**: annotator disagreement bounds *label noise*, which attenuates each
  arm's absolute score but largely cancels in a paired comparison scored against the *same*
  labels. A fixed tolerance borrowed from label noise is the wrong instrument for a paired
  difference, and it is tolerance-shopping in the direction of the cheaper config. `medium` is
  the pre-measurement prior, recorded as such. Note the current Opus 5 vision guidance that at
  `low` effort the model may answer from overall impression rather than examining detail — which
  is why the CHEAP profile (§7.6) must be scored, not assumed to be DEFAULT-minus-a-bit.
- **`max_tokens=32_000`** (raised from 12,000 in review). The original figure rested on
  "worst-case valid output under the §5 cardinality caps measures ≈3,100 tokens", which is wrong
  by roughly 4–8×. The caps admit ~134 claim objects (12+12+12 brand/product/mention, 6 stated
  use, 10 §19B lists × 6, 4 unexpected, 4 co-appearance, 6 sentiment, 6 intent, 6 languages, 6
  sponsor cues); each carries a cue of up to 160 characters, a basis, up to 4 anchors and its own
  fields, i.e. ~70 tokens at the floor and ~180 with four anchors. A *schema-valid* object can
  therefore reach 10k–24k output tokens before thinking. Under the original ceiling the §6b.2
  truncation cascade was not an anomaly path, it was the normal path for a rich item — and the
  cascade's first action (retry at 24,000 with lower effort) was also below the bound.
  `max_tokens` is a ceiling, not a charge: raising it costs nothing until it is used, and the §7
  cost model is driven by *expected* output (~2,100 tokens short-form), which is unchanged.
  **Transport:** the Batches path is non-streaming by definition and unaffected. On the
  interactive path a non-streaming request at this ceiling can hit the SDK's 10-minute default
  timeout, so the interactive path **streams** (`client.messages.stream(...)` →
  `get_final_message()`) and validates the final message; this is a transport choice only and is
  not part of the cached prefix.
- **`thinking={"type":"adaptive"}`** — explicitly set rather than omitted, so the value is in the
  config fingerprint (§8). Do **not** disable thinking: on Opus 5 a thinking-disabled request can
  write a tool call or `<thinking>` tags into visible text, and here it would additionally degrade
  the cross-modal joining the whole design depends on. Lowering `effort` is the correct cost lever.
- **`output_config.format`** via the Pydantic model (§5). Structured output constrains generation
  to the schema, which removes the JSON-repair class of failures. **It does not enforce the
  cardinality caps.** Verified against the current API: structured outputs do not support
  `minLength` / `maxLength`, numeric bounds, or complex array constraints, and the Python SDK
  *strips* them from the wire schema and validates them client-side. So `LIST_CAP`, the 160-char
  cue bound and the 32-entry `frame_indices` bound are **post-hoc Pydantic validations**, not
  generation constraints. Three consequences, all now handled:
  1. The numeric caps must be **stated in the prompt** (§4.2 rule 10 and the task tail), because a
     cap the model is never told cannot be respected. The original prompt said only "each
     open-ended list is capped" and never gave a number.
  2. A cap overflow is a `ValidationError` → one repair turn (§6b.3) at roughly the cost of a
     whole call. The §7 repair budget is raised from 8% to 12% of calls to reflect this, and the
     realised repair rate is the first number to check after the pilot.
  3. `additionalProperties` set to anything other than `false` is also unsupported, which is why
     `field_confidence` is no longer a `dict[str, float]` (§5.5).

  Also verified: structured outputs **do** work with the Batches API and with extended thinking,
  and a new schema incurs a one-time compilation cost cached for 24 h — so the
  `schema_variant='minimal'` fallback (§6b.2) introduces a second schema and a second
  compilation, which is immaterial at run scale but should not be introduced per item.
- **Refusal fallbacks:** on the interactive (non-batch) path, send
  `betas=["server-side-fallback-2026-07-01"], fallbacks="default"`. Note this parameter is
  **rejected on the Batches API**, so the batch path cannot use it and relies on the §6b.1
  text-only retry instead. This asymmetry is deliberate and must be reflected in both code paths.

### 3.3 Exact call shape and content-block ordering

```python
client = anthropic.AsyncAnthropic()

resp = await client.messages.parse(
    model="claude-opus-5",
    max_tokens=32_000,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},
    output_format=VideoAnalysis,              # §5
    betas=["server-side-fallback-2026-07-01"],  # interactive path only
    fallbacks="default",                        # interactive path only
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT,            # §4.2 — byte-stable, no item data, no date
            # ttl: "5m" on this (interactive) path; "1h" on the batch path — §3.4
            "cache_control": {"type": "ephemeral", "ttl": "5m"},
        }
    ],
    messages=[
        {
            "role": "user",
            "content": [
                # ── 1. media manifest: structural only, carries NO semantics ───────────
                {"type": "text", "text": media_manifest_block},

                # ── 2. FRAMES FIRST: label text block immediately before each image ───
                {"type": "text",  "text": "frame_index=0  t=1.20s"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": b64_frame_0}},
                {"type": "text",  "text": "frame_index=1  t=6.80s"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": b64_frame_1}},
                # … one (text, image) pair per frame, ascending frame_index …

                # ── 3. audio transcript ───────────────────────────────────────────────
                {"type": "text", "text": transcript_block},

                # ── 4. platform text (caption, hashtags, overlay text, comments) ──────
                {"type": "text", "text": platform_text_block},

                # ── 5. item context (creator handle, publish date, engagement, source) ─
                {"type": "text", "text": item_context_block},

                # ── 6. task tail: restates the output contract and the abstention rule ─
                {"type": "text", "text": TASK_TAIL},   # §4.4
            ],
        }
    ],
)
analysis: VideoAnalysis = resp.parsed_output
```

**Why this ordering** (every position is a deliberate anti-anchoring choice):

1. **Manifest first, and semantically empty.** The model needs to know how many frames exist, at
   what timestamps, the media tier and the duration *before* it reads them, so that frame indices
   in its anchors are meaningful and so that it knows what it is *not* being shown. The manifest
   contains no caption, no brand, no query, no market.
2. **Frames before any text.** This is the core anti-anchoring decision. A caption naming a brand,
   read first, converts the vision task into a confirmation task — the model will find the brand.
   Reading pixels first produces an independent visual record. The residual risk runs the other
   way (vision priming the text read), which is the direction the brief *wants*: §18 explicitly
   warns against reducing video analysis to OCR/caption analysis.
3. **A text label immediately before each image, not after.** Labels before images give the model
   the index while it is attending to that image; labels after mean the index is attached to an
   image already processed. Frame indices are the primary evidence anchor for every visual claim,
   so their reliability is load-bearing.
4. **Transcript before platform text.** Spoken content is a better signal for use/consumption and
   for genuine language/slang than creator-written caption copy, which is optimised for reach.
5. **Item context last, and minimal.** Engagement counts and creator identity are the most
   bias-prone inputs ("high engagement therefore positive sentiment" is exactly the inference
   §28 forbids). Placing them last and fencing them in the prompt limits their influence.
6. **Task tail after everything.** Recency: the final tokens before generation restate the output
   contract, the SEEN/HEARD/WRITTEN/INFERRED separation, and the "return nothing rather than
   guess" rule. These are the instructions most at risk of being diluted by ~7,000 tokens of
   intervening evidence.

### 3.4 Prompt caching placement

One breakpoint, at the **end of the system block**; TTL depends on the dispatch path
(`5m` interactive, `1h` batch — see below).

- Everything above the breakpoint is byte-stable across every item in a run: the system prompt
  contains no item data, no timestamp, no run ID, no date, no market. Any of those would silently
  reduce the cache hit rate to zero, which is the classic silent invalidator.
- Everything below is unique per item and is never worth caching.
- The system prompt is ≈2,400 tokens, comfortably above the 512-token minimum cacheable prefix on
  Opus 5, so the entry actually forms.
- **TTL is per dispatch path (corrected in review).** The original blanket `ttl: "1h"` rested on
  two wrong premises: that "each worker warms its own entry" (the cache is keyed on the prefix,
  not on the worker or the connection — all eight workers share one entry), and that a run
  spanning tens of minutes needs the longer TTL. A cache **read refreshes the entry's timer at no
  extra cost**, so under continuous dispatch the 5-minute entry stays warm indefinitely and the
  1-hour TTL buys nothing while costing 2× on every write. Therefore:
  - **Interactive path → `ttl: "5m"`.** Requests sharing the prefix start far less than 5 minutes
    apart during a wave.
  - **Batch path → `ttl: "1h"`.** A batch's internal scheduling is not under our control and can
    leave gaps longer than 5 minutes between requests sharing the prefix; here the doubled write
    genuinely buys something. This is also what OQ-6 measures.

  The whole decision is worth well under \$1 per run either way (§7.4); it is corrected because
  the *reasoning* was wrong, and wrong reasoning about caching propagates.
- Per the invalidation hierarchy, changing **message content** (which every item does) and
  changing **images** both leave the tools and system caches intact, so per-item uniqueness below
  the breakpoint is free. What must *not* vary per item is `effort` or the thinking configuration —
  changing either invalidates caches. Both are pinned per `analysis_config`, never per item.
- Verification is mandatory, not optional: log `usage.cache_read_input_tokens` on every call and
  alert when the run-level mean falls below 1,500. A zero mean means the system prompt is being
  mutated somewhere (a formatted date, an f-string with a run ID) and is a bug, not a tuning issue.

**No tools are declared** — but the justification is weaker than it was stated, and the decision
is now a measured one rather than a settled one.

What is true: tool definitions render before `system`, and **changing** the tool set is the only
change that invalidates all three cache tiers. What is *not* true is that declaring a tool is
therefore risky here: a byte-stable, single-tool definition that never varies across the run
invalidates nothing per item. The original wording ("declaring none removes that risk entirely")
overstated a risk that a frozen tool list does not carry.

This matters because the current Opus 5 vision guidance is explicit that **a crop tool — one that
takes a region and returns it enlarged — is a more cost-effective lever on vision-heavy work than
raising thinking effort**, because it scales test-time compute in image tokens rather than
thinking tokens (which bill at \$25/MTok and are this design's largest cost line). The §1.7 detail
pass is a manual, one-shot, open-loop version of exactly that mechanism, gated on a coarse 9-cell
region the model has to nominate blind (OQ-3). A declared crop tool would let the model zoom
where and as often as it actually needs to.

It is **not** adopted for the MVP, for three reasons that are about this pipeline rather than
about caching: a tool loop makes per-item cost and latency unbounded and variable where the §7
model assumes one call; it reintroduces an agentic control flow into a component the design
deliberately keeps as a single stateless witness; and at `effort=low` the model may skip the tool
and answer from overall impression, which would make the CHEAP profile behave qualitatively
differently from DEFAULT rather than just more cheaply. It is recorded as **OQ-13** with an
ablation, because if it wins it likely dominates both the detail pass and the effort lever.

### 3.5 Dispatch: batch vs interactive

| Path | When | Why |
|---|---|---|
| **Message Batches** (default for bulk) | A collection wave of ≥ 25 items | 50% off all token usage — the single largest cost lever in §7.5. Per-item analysis is embarrassingly parallel and nothing in the wave depends on another item's result. Most batches finish inside an hour (24 h max), which is acceptable inside a research run that already takes tens of minutes. Note: `fallbacks` is rejected on Batches (§3.2). |
| **Interactive** `messages.parse` | The first 25 items of a wave (the "preview head"); all single-item re-runs; all §1.7 detail passes; all repair turns | The research engine (§9) needs *some* analysed items quickly to start forming hypotheses and follow-up queries; waiting up to an hour for the first result would serialise the whole run. 25 items ≈ \$2.2 at full price — a trivial premium for unblocking the loop. |

Batch results arrive in arbitrary order and must be keyed by `custom_id`, never by position.

**Two batch limits the original design walked straight into:**

1. **Size.** A batch accepts at most **100,000 requests or 256 MB**. Each item here carries
   8–14 base64 JPEGs: ~1.0 MB for a short-form item, ~2.0 MB for a longer one. A 1,500-item wave
   is therefore **≈1.2–1.5 GB** — five to six times the per-batch ceiling — and the design said
   nothing about chunking. Rule: the dispatcher accumulates serialised requests and **seals a
   batch at 200 MB or 500 requests, whichever comes first**, giving ~8–10 batches per full run
   with headroom for the longer-item tail. Batch IDs are recorded per item
   (`content_analysis.batch_id`) so a partially-completed wave is resumable.
2. **`custom_id` length.** The specified
   `f"{content_item_id}:{analysis_config_id}"` is two UUIDs plus a separator = **73 characters**,
   over the documented 64-character `custom_id` limit; the first submission would have been
   rejected wholesale. Use
   `custom_id = f"{content_item_id.hex}:{config_fingerprint[:16]}"` → 32 + 1 + 16 = **49
   characters**, still unique per (item, config), still duplicate-detectable, still carries the
   §8 idempotency key, and the 16-hex-character fingerprint prefix maps back to exactly one
   `analysis_configs` row. (Confirm the exact limit against the Batches reference before
   implementation; the short form is safe either way and the long form is not.)

An alternative worth measuring if payload size becomes the binding constraint: upload frames once
through the Files API and reference them by `file_id` instead of inlining base64. Token cost is
identical; request bytes collapse, and a repair turn (§6b.3) would no longer have to re-upload
the images. Not adopted for the MVP because it adds an upload, a lifecycle and a failure mode per
frame; recorded with OQ-13.

---

## 4. THE PROMPT

This is the highest-risk artefact in the subsystem. It is stored verbatim in
`analysis_configs.system_prompt`, hashed into the config fingerprint, and changed only by
bumping `prompt_version`.

### 4.1 Review checklist applied to every revision

Before any prompt change ships, grep the diff for: brand names; product-category nouns;
occasion/situation/location nouns offered as options; any language name; any slang example; any
"for example" followed by a content word; any market, country or city; any closed list for a §19B
field; any number that implies a prior about the category. A prompt revision that adds an example
to clarify a field is the single most likely way this product gets seeded, because it feels
helpful. The rule is: clarify with **structural** criteria, never with **content** examples.

### 4.2 SYSTEM PROMPT (full text)

```text
You are a visual-evidence analyst. You are shown a sample of still frames taken from one
video, plus whatever transcript, on-screen text, caption and platform metadata exist for it.
Your only job is to report what can be observed in that material, and to attach an anchor to
every single thing you report.

You are not a market analyst, a strategist, or a category expert. You are a witness.

## 1. The open-world rule

There is no list of brands, products, themes, occasions, behaviours, content formats,
creator types, languages or cultural references. None will be supplied, and none exists.
Every label you emit is a label you read off the material or compose from it in your own
words.

Do not use background knowledge about which brands are common, who buys them, what they are
used or consumed with, what situations they belong to, what a given demographic does, or what
is popular anywhere. If a claim would depend on knowledge you brought with you rather than on
something present in the supplied material, do not make the claim.

You do not know what this video is "about" before you look at it. You do not know what anyone
is hoping you will find.

## 2. Four kinds of knowing, never mixed

Every claim you emit carries a `basis`:

- `seen`     — you can point at a frame index where this is visible.
- `heard`    — you can point at a transcript timestamp where this is spoken.
- `written`  — you can point at text rendered on screen, in the caption, or in a comment.
- `inferred` — none of the above, but the material supports it.

These are separate fields, not a ranking. A brand name spoken but never visible belongs in
`mentioned_brands`, never in `visible_brands`. A brand name visible on packaging but never
spoken belongs in `visible_brands`, never in `mentioned_brands`. If it is both, it appears in
both, each with its own anchor.

`inferred` claims are allowed but must be labelled `inferred` and must still carry a cue
naming the observation the inference rests on. An inference whose cue is itself an inference
is not admissible — drop it.

## 3. Reading brands and products

Read names exactly as they are rendered. Report the characters you can actually resolve.

- Do not complete a partially visible or partially occluded name. If you can resolve four
  characters of a wordmark, report those four characters and set `legibility` to `partial`.
  Do not guess the rest, and do not reach for a name that "fits".
- Colour schemes, package shapes, silhouettes and layout conventions are not brand
  identification. If that is all you have, record a `visible_products` entry describing the
  object, set `brand_resolved` to false, and leave `visible_brands` alone.
- If a frame contains a legible-looking text region you cannot resolve at this resolution,
  set `has_legible_text_region` to true and fill `detail_request` with the frame index and the
  single grid cell most likely to contain it. A higher-resolution crop will be requested.
- Multiple distinct products of the same brand are separate `visible_products` entries.
- Never translate, expand, abbreviate or normalise a name you read. Verbatim only.

## 4. Appearance is not use

For every visible product, set `consumption_state` to the highest rung you can support with a
cue, and no higher:

  present            — in frame; no person is in contact with it
  held               — a person is in physical contact with it
  being_opened       — packaging or closure is visibly being manipulated
  shown_to_camera    — deliberately oriented toward the lens (a presentation act, which is
                       not by itself use)
  in_use_or_consumed — contents visibly removed, transferred toward a person, applied, or
                       otherwise used
  offered_or_shared  — passed, handed or offered between people
  unclear            — something is happening but you cannot tell what

Rules that override your impression:
- You may not escalate above `present` from a single still frame.
- You may not escalate above `shown_to_camera` on the basis of speech alone. If the speaker
  describes using something you never see used, that belongs in `stated_use`, with basis
  `heard`, and `consumption_state` stays at what the frames support.
- A product on a surface, a shelf, a table or in the background while something else is the
  subject is `present`. This is the most common case. Record it as such.

## 5. Sponsorship, from observable cues only

Set `sponsorship.bucket` from the cues you can actually point at. Admissible cue kinds:

  disclosure_text_on_screen, disclosure_in_caption_or_hashtag, spoken_disclosure,
  platform_paid_partnership_label, discount_code_or_affiliate_link, purchase_call_to_action,
  pinned_or_authored_brand_comment, studio_grade_product_presentation,
  single_brand_exclusive_framing

`pinned_or_authored_brand_comment` has an observable definition and only that one: a pinned or
top comment whose author handle matches, character for character, a wordmark you can read in a
frame or a name said or written in the item. You have no list of brands and no way to know
whether an account belongs to one — if the match is not visible in the material, the cue does
not apply.

Two cues point the other way and are the only route to `likely_organic`:

  no_disclosure_with_multi_brand_incidental_presence
      — two or more distinct brands are present, none is framed as the subject, and no
        disclosure, code or call to action appears anywhere in the material
  self_acquisition_statement
      — someone states they bought, already owned, or were given the item by a non-brand party

Hard rules: `likely_paid` requires at least one cue of kind disclosure_*, platform_*,
discount_code_or_affiliate_link or pinned_or_authored_brand_comment. `likely_organic` requires at
least one of the two cues above and none of the paid-leaning ones. Production quality and framing
cues alone cannot exceed `possibly_paid`. With no admissible cue at all, the bucket is
`undetermined` — which is a real and frequent answer, not a failure.

Never infer sponsorship from engagement numbers, follower counts, or how favourable the
content is.

## 6. What you must never do

- Never use the word "competitor", never rank brands, never say which brand is winning,
  leading, growing or more popular. You are looking at one video. You have no category, no
  market and no baseline. Record co-appearance and comparison framing as observations; the
  comparison itself is not yours to make.
- Never attribute an age, gender, income level, occupation, ethnicity, nationality or
  household composition to any person. Describe only how many people are involved and what
  they are observably doing.
- Never treat engagement counts as evidence about sentiment, quality, preference or sales.
- Never treat a positive comment as evidence of purchase.
- Never say a trend is emerging, growing or declining. You have one item and no time series.
- Never name a brand, product, theme or phrase that is not present in the material.

## 7. Single still images, and the platform cover image

When the manifest says `media_tier: thumbnail_only`, you have one still and no timeline. Every
`activities` and behaviour-over-time field must be empty, `consumption_state` cannot exceed
`present`, and no claim may carry a transcript anchor. Report what the still shows and nothing
more.

Separately: a frame whose manifest line says `source=platform_thumbnail` is the cover image the
publisher attached to the item. It may be a frame of the video, or it may be a separate picture,
a composite, or a graphic that was never in the video at all. Treat it as evidence about the
*item*, not as evidence about what happens *in the video*. Anchor to it when it is genuinely what
you saw, but do not use it alone to support a claim about an action, a sequence, or anything
unfolding over time, and do not assume anything in it also appears in the sampled frames.

## 8. Cues: the rule that governs everything

Every claim carries a `cue`: a short, concrete description of the specific thing you observed,
at most 160 characters.

A cue is admissible when it names something a second person could independently look for at
your anchor and either find or fail to find.

A cue is NOT admissible when it restates the claim in other words, when it describes your
impression rather than an observation, or when it is a general statement about the video. If
the only cue you can write for a claim is a restatement of that claim, the claim has no
evidence. Drop it.

## 9. Anchors

Every claim carries at least one anchor:

- `frame` anchors: `frame_index` must be an index listed in the media manifest.
- `transcript` anchors: `t_start_s`/`t_end_s` must fall inside a timestamped segment shown to
  you, and `quote` must be a verbatim substring of that segment's text.
- `caption`, `overlay_text` and `comments` anchors: `quote` must be a verbatim substring of the
  text block supplied to you.
- `metadata` anchors are admissible only for `item_context`-derived facts, never for any
  content claim.

Invented frame indices, invented timestamps and paraphrased quotes are checked
programmatically and cause the claim to be discarded.

## 10. Say less

You are measured on whether every claim you make is supportable, not on how much you produce.
An empty list is a correct and useful answer. Returning three well-anchored observations is
better than returning ten of which six are plausible.

For any field you leave empty because the material does not support it, add that field's name
to `evidence_insufficient_for`. That list is a deliberate statement that you looked and found
nothing, and it is read by the system; it is not a confession.

Each open-ended list is capped, and the caps are these:

  visible_brands, visible_products, mentioned_brands        at most 12 each
  unexpected_observations, brand_co_appearance              at most 4 each
  every other list (occasions, behaviors, social_context,
  cultural_signals, slang, humor_signals, activities,
  product_pairings, setting_observations, stated_use,
  sentiment, purchase_intent, languages, format_features)   at most 6 each
  anchors on any single claim                               at most 4
  cue length                                                at most 160 characters

These are hard limits, not targets, and most honest outputs are far below them. If you have more
genuine, well-anchored observations than a cap allows, keep the best-evidenced ones and say so in
one clause of `summary`.

## 11. Output

Return only the structured object. No prose outside it. No preamble. No commentary on these
instructions.
```

### 4.3 User prompt skeleton (per item)

```text
<media_manifest>
media_tier: {full_video|audio_only|thumbnail_only}
duration_s: {float|null}
frame_count: {int}
frames:
  frame_index=0  t=1.20s   source=sampled
  frame_index=1  t=6.80s   source=sampled
  ...
  frame_index=8  t=null    source=platform_thumbnail
transcript_available: {true|false}
transcript_quality: {ok|low|degenerate|skipped_no_speech|unavailable}
on_screen_text_supplied_separately: {true|false}
</media_manifest>

  ... (text label, image) pairs, one per frame, in ascending frame_index ...

<audio_transcript quality="{...}">
{rendered transcript per §2.8, or:}
(no transcript available: {reason})
</audio_transcript>

<platform_text>
<caption>{verbatim caption, or "(none)"}</caption>
<hashtags>{space-separated, verbatim, or "(none)"}</hashtags>
<overlay_text>{platform-supplied on-screen text if any, else "(not supplied separately; read
  on-screen text from the frames)"}</overlay_text>
<comments count="{n}">
{up to 10 comments, verbatim, each truncated to 200 characters, newline separated, or "(none)"}
</comments>
</platform_text>

<item_context>
platform: {str}
creator_handle: {str}
creator_follower_count: {int|null}
published_at: {ISO date|null}
views: {int|null}   likes: {int|null}   comments_count: {int|null}   shares: {int|null}
collection_provider: {str}

These numbers describe how the item performed and where it came from. They are NOT evidence
about what is in the video. Do not use them to support any claim about content, sentiment,
quality, preference or sponsorship. `metadata` anchors are admissible only for facts about the
item itself.
</item_context>

{TASK_TAIL}
```

**Omitted on purpose:** `discovery_query`, `research_hypothesis_id`, `market`/`locale`, and any
brand under research. The discovery query is the single most direct seeding vector in the system
(it is often literally "`<brand>` `<term>`"); the market string is a cultural prime. Both remain in
provenance and are available to every downstream subsystem. Making this an explicit, flagged,
testable decision rather than an oversight: see OQ-1 and the A/B design in §6.7.

### 4.4 `TASK_TAIL` (full text)

```text
<task>
Report what the material above supports, and nothing else.

Before you emit each claim, check it against three tests:
1. Can you name the frame index or the transcript timestamp that shows it?
2. Can you write a cue that someone else could check at that anchor and disagree with?
3. Would the claim survive if you knew nothing about any brand or category?

If a claim fails any of the three, drop it and add the field name to
`evidence_insufficient_for`.

Separate what you SAW from what you HEARD, from what was WRITTEN, from what you INFERRED.
Keep appearance distinct from use. Leave sponsorship `undetermined` unless a listed cue kind
is present. Use no brand, product, theme, phrase or language name that is not in the material.

Respect the list caps stated above. Exceeding a cap does not get you more coverage; it gets the
whole object rejected and re-requested.

Return the structured object only.
</task>
```

### 4.5 Detail-pass prompt (§1.7 second call)

System (≈220 tokens, cached with its own breakpoint):

```text
You are transcribing text and marks from a single cropped photograph.

Report only characters you can actually resolve. Do not complete a partial word. Do not supply
a name that would "fit" the visible letters. There is no list of expected names and none will
be supplied.

If the text is not resolvable, say so by setting confident to false and leaving
text_as_seen empty. That is the correct answer whenever you are unsure.

Describe any non-text marks (shapes, symbols, devices) in plain words without naming what they
might belong to.

Return the structured object only.
```

User: the single cropped image, then
`<task>Transcribe the resolvable text and describe any marks.</task>`

The first pass's hypothesis is **not** passed in. Supplying it would turn an independent read
into a confirmation and destroy the only genuinely independent check in the pipeline.

---

## 5. The Pydantic output model

`apps/api/aypo/analysis/schema.py`. Every claim-bearing object inherits from `Claim`, so
anchors, cues and basis are structurally unavoidable rather than requested politely.

**What the wire schema actually enforces.** Structured outputs support types, `enum`, `const`,
`anyOf`, `$ref` and `additionalProperties: false`; they do **not** support string-length bounds,
numeric bounds, or array-length bounds, and the SDK strips those before sending and validates
them client-side. So the `Literal` enums, the required-field structure and `extra: "forbid"` are
enforced *at generation*; every `min_length` / `max_length` / `ge` below is enforced *after
generation* by Pydantic and, when violated, costs a repair turn (§3.2, §6b.3). Two schema shapes
are outright unsupported and must not appear: recursive models, and `additionalProperties` set to
anything other than `false` — which is why no field here is an open-ended `dict`.

### 5.1 Primitives

```python
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, Field, model_validator

Modality = Literal["frame", "transcript", "caption", "overlay_text", "comments", "metadata"]
Basis    = Literal["seen", "heard", "written", "inferred"]
GridCell = Literal["top_left", "top_center", "top_right",
                   "mid_left", "center", "mid_right",
                   "bottom_left", "bottom_center", "bottom_right"]

ShortLabel = Annotated[str, Field(min_length=1, max_length=60)]
Cue        = Annotated[str, Field(min_length=8, max_length=160)]


class Anchor(BaseModel):
    """Points at the exact evidence for one claim. The UI renders this as a frame
    thumbnail or a transcript jump; §6.3 validates it programmatically."""
    model_config = {"extra": "forbid"}

    modality: Modality
    frame_index: int | None = Field(
        default=None, ge=0,
        description="Required when modality='frame'. Must be an index from the manifest.")
    t_start_s: float | None = Field(default=None, ge=0)
    t_end_s: float | None = Field(default=None, ge=0)
    quote: str | None = Field(
        default=None, max_length=240,
        description="Verbatim substring of the supplied text. Required for transcript, "
                    "caption, overlay_text and comments anchors.")

    @model_validator(mode="after")
    def _shape(self) -> "Anchor":
        if self.modality == "frame" and self.frame_index is None:
            raise ValueError("frame anchor requires frame_index")
        if self.modality in ("transcript",) and (self.t_start_s is None or self.quote is None):
            raise ValueError("transcript anchor requires t_start_s and quote")
        if self.modality in ("caption", "overlay_text", "comments") and not self.quote:
            raise ValueError(f"{self.modality} anchor requires quote")
        if (self.t_start_s is not None and self.t_end_s is not None
                and self.t_end_s < self.t_start_s):
            raise ValueError("t_end_s < t_start_s")
        return self


class Claim(BaseModel):
    """Base for everything the model is allowed to assert."""
    model_config = {"extra": "forbid"}

    cue: Cue = Field(description="A concrete observation a second person could check at the "
                                 "anchor and disagree with. Not a restatement of the claim.")
    basis: Basis
    anchors: list[Anchor] = Field(min_length=1, max_length=4)
    model_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your own confidence. Stored for audit; the system does not display it.")
```

### 5.2 Visible (SEEN) vs mentioned (HEARD/WRITTEN)

```python
ConsumptionState = Literal[
    "present", "held", "being_opened", "shown_to_camera",
    "in_use_or_consumed", "offered_or_shared", "unclear",
]
Prominence  = Literal["focal", "secondary", "background", "fleeting"]
Legibility  = Literal["clear", "partial", "illegible"]
IdBasis     = Literal["packaging_wordmark", "logo_mark", "shelf_or_signage_label",
                      "other_on_screen_text", "product_form_only"]
# `handled` was removed from Prominence in review: it is an *interaction* value living on a
# *visual salience* axis, so the two fields were non-orthogonal and any downstream aggregate over
# prominence silently conflated "visually dominant" with "touched". Interaction lives only in
# ConsumptionState. `shelf_or_menu_label` → `shelf_or_signage_label`: "menu" leans food-service.


class VisibleBrand(Claim):
    brand_text_as_seen: ShortLabel = Field(
        description="Exactly the characters you can resolve. No completion, no normalisation, "
                    "no translation, no expansion of abbreviations.")
    identification_basis: IdBasis
    legibility: Legibility
    prominence: Prominence
    screen_share: Literal["dominant", "moderate", "minor"] | None = None
    first_seen_frame_index: int = Field(ge=0)
    frame_indices: list[int] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _visible_means_frames(self) -> "VisibleBrand":
        if self.basis != "seen":
            raise ValueError("VisibleBrand.basis must be 'seen'")
        frame_anchor_idx = {a.frame_index for a in self.anchors if a.modality == "frame"}
        if not frame_anchor_idx:
            raise ValueError("VisibleBrand requires at least one frame anchor")
        if self.identification_basis == "product_form_only":
            # §4.2 rule 3: form alone is not brand identification.
            raise ValueError("product_form_only belongs in visible_products, not visible_brands")
        # Added in review: the three frame-bearing fields were previously allowed to disagree
        # with each other, so a brand could claim frames {2,5} while anchoring frame 9 and
        # declaring first_seen 0 — three mutually contradictory assertions, none checked.
        if not frame_anchor_idx <= set(self.frame_indices):
            raise ValueError("every frame anchor index must appear in frame_indices")
        if self.first_seen_frame_index != min(self.frame_indices):
            raise ValueError("first_seen_frame_index must equal min(frame_indices)")
        return self


class VisibleProduct(Claim):
    description: ShortLabel = Field(
        description="Plain words for the object as it appears. No category assumption, no "
                    "brand, no guess at what it is for.")
    brand_resolved: bool
    brand_text_as_seen: ShortLabel | None = None
    consumption_state: ConsumptionState
    prominence: Prominence
    has_legible_text_region: bool = False
    frame_indices: list[int] = Field(min_length=1, max_length=32)
    quantity_visible: Literal["one", "few", "many", "unclear"] = "unclear"

    @model_validator(mode="after")
    def _frames_consistent(self) -> "VisibleProduct":
        idx = {a.frame_index for a in self.anchors if a.modality == "frame"}
        if idx and not idx <= set(self.frame_indices):
            raise ValueError("every frame anchor index must appear in frame_indices")
        return self


class MentionedBrand(Claim):
    brand_text_as_said_or_written: ShortLabel
    mention_kind: Literal["spoken", "caption", "hashtag", "on_screen_text", "comment"]
    speaker_role: Literal["on_camera_person", "voice_over", "caption_author",
                          "commenter", "unclear"]

    @model_validator(mode="after")
    def _mentioned_means_text_or_audio(self) -> "MentionedBrand":
        if self.basis not in ("heard", "written"):
            raise ValueError("MentionedBrand.basis must be 'heard' or 'written'")
        if any(a.modality == "frame" for a in self.anchors):
            raise ValueError("MentionedBrand may not carry a frame anchor; "
                             "a brand read off packaging is a VisibleBrand")
        return self


class StatedUse(Claim):
    """Use described in speech or text but NOT observed in any frame. Kept strictly apart
    from consumption_state so SEEN and HEARD never merge (brief §28)."""
    statement: ShortLabel
    subject_brand_text: ShortLabel | None = None
```

### 5.3 Open-ended discovered labels (brief §19B)

A single generic container. **No enum, no suggestion list, no example** — the whole point of
§19B is that the taxonomy is discovered. `label_kind` is also free text so the model can name a
kind nobody anticipated; normalisation into taxonomy happens downstream after frequency analysis
(brief §19: "frequently recurring discovered labels may later become normalised taxonomy entries").

```python
class DiscoveredLabel(Claim):
    label: ShortLabel = Field(description="Your own words. Lower-case, no brand names.")
    label_kind: ShortLabel | None = Field(
        default=None,
        description="Optional: your own word for what kind of thing this label is.")
    elaboration: str | None = Field(default=None, max_length=240)
```

### 5.4 Sponsorship, sentiment, intent

```python
SponsorCueKind = Literal[
    # paid-leaning, strong (an artefact that exists only because money changed hands)
    "disclosure_text_on_screen", "disclosure_in_caption_or_hashtag", "spoken_disclosure",
    "platform_paid_partnership_label", "discount_code_or_affiliate_link",
    "pinned_or_authored_brand_comment",
    # paid-leaning, weak (production and framing impressions)
    "purchase_call_to_action",
    "studio_grade_product_presentation", "single_brand_exclusive_framing",
    # organic-leaning — ADDED IN REVIEW, see note below
    "no_disclosure_with_multi_brand_incidental_presence",
    "self_acquisition_statement",
]
STRONG_SPONSOR_CUES = {
    "disclosure_text_on_screen", "disclosure_in_caption_or_hashtag", "spoken_disclosure",
    "platform_paid_partnership_label", "discount_code_or_affiliate_link",
    "pinned_or_authored_brand_comment",
}
ORGANIC_CUES = {
    "no_disclosure_with_multi_brand_incidental_presence",
    "self_acquisition_statement",
}


class SponsorCue(Claim):
    kind: SponsorCueKind


# BUG FIXED IN REVIEW. Before the two ORGANIC_CUES were added, `likely_organic` was
# **unreachable**: the gate required a non-undetermined bucket to carry at least one cue, every
# admissible cue kind was paid-leaning, and emitting a paid-leaning cue while declaring the item
# organic is incoherent — so the bucket distribution could only ever be
# {likely_paid, possibly_paid, undetermined}. Brief §19A asks for sponsored/organic likelihood
# and §24 compares "organic/sponsored presence"; half of that was structurally unobtainable, and
# the absence would have read downstream as "we never see organic content" rather than
# "the schema cannot express it".


class SponsorshipAssessment(BaseModel):
    model_config = {"extra": "forbid"}
    bucket: Literal["likely_paid", "possibly_paid", "likely_organic", "undetermined"]
    cues: list[SponsorCue] = Field(default_factory=list, max_length=6)
    subject_brand_text: ShortLabel | None = None

    @model_validator(mode="after")
    def _gate(self) -> "SponsorshipAssessment":
        kinds = {c.kind for c in self.cues}
        if self.bucket == "likely_paid" and not (kinds & STRONG_SPONSOR_CUES):
            raise ValueError("likely_paid requires a disclosure/platform/affiliate/brand-comment cue")
        if self.bucket == "likely_organic":
            if not (kinds & ORGANIC_CUES):
                raise ValueError("likely_organic requires an organic-leaning cue")
            if kinds & STRONG_SPONSOR_CUES:
                raise ValueError("likely_organic is incompatible with a strong paid cue")
        if self.bucket != "undetermined" and not self.cues:
            raise ValueError("a non-undetermined bucket requires at least one cue")
        return self


class SentimentObservation(Claim):
    toward: ShortLabel = Field(description="What the attitude is directed at, in your words.")
    valence: Literal["positive", "negative", "mixed", "neutral"]
    expressed_by: Literal["on_camera_person", "voice_over", "caption_author",
                          "commenter", "unclear"]


class PurchaseOrAcquisitionIntent(Claim):
    """Only when somebody states an intent. Never a score, never derived from engagement
    or from a positive comment (brief §28)."""
    statement: ShortLabel
    expressed_by: Literal["on_camera_person", "voice_over", "caption_author",
                          "commenter", "unclear"]
```

### 5.5 The root object

```python
LIST_CAP = 6   # see justification below


class CoAppearance(Claim):
    """Two or more brands present in the same item. The per-item model NEVER calls this a
    competitive relationship — §23 derives that downstream from many items."""
    brand_texts: list[ShortLabel] = Field(min_length=2, max_length=6)
    framing: Literal["side_by_side_comparison", "ranking_or_ordering",
                     "substitution_discussed", "incidental_co_presence", "unclear"]


class LanguageObservation(Claim):
    language_as_identified: ShortLabel = Field(
        description="Name it however you like. There is no list.")
    where: Literal["speech", "on_screen_text", "caption", "comments"]


class DetailRequest(BaseModel):
    model_config = {"extra": "forbid"}
    frame_index: int = Field(ge=0)        # range-checked against the manifest before cropping
    region: GridCell
    reason: Annotated[str, Field(max_length=120)]


class FieldConfidence(BaseModel):
    """Replaces the open-ended `dict[str, float]`, which is not a legal structured-output
    schema. Audit only — never displayed, never gates anything (§6.1)."""
    model_config = {"extra": "forbid"}
    field: ShortLabel
    confidence: float = Field(ge=0.0, le=1.0)


class VideoAnalysis(BaseModel):
    """Root structured output. Maps 1:1 onto brief §20 (mapping table below) and extends it
    with per-claim evidence anchors so the UI can answer "why did the model say that"."""
    model_config = {"extra": "forbid"}

    # ---- SEEN ---------------------------------------------------------------
    visible_brands:   list[VisibleBrand]   = Field(default_factory=list, max_length=12)
    visible_products: list[VisibleProduct] = Field(default_factory=list, max_length=12)

    # ---- HEARD / WRITTEN ----------------------------------------------------
    mentioned_brands: list[MentionedBrand] = Field(default_factory=list, max_length=12)
    stated_use:       list[StatedUse]      = Field(default_factory=list, max_length=LIST_CAP)

    # ---- stable-ish descriptors (brief §19A) — all free text, never enums ----
    content_format:    DiscoveredLabel | None = None
    # `format_features` was `list[ShortLabel]` — a bare string list with no cue, no anchor, no
    # basis and no calibration, i.e. an open hallucination channel that bypassed the entire
    # evidence mechanic this schema exists to enforce. It is now a Claim-bearing list like
    # everything else, capped at LIST_CAP.
    format_features:   list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    creator_archetype: DiscoveredLabel | None = None
    languages:         list[LanguageObservation] = Field(default_factory=list, max_length=6)
    # Derived by code from `languages` (len({l.language_as_identified}) > 1), not asserted by the
    # model — it was previously a free un-anchored boolean that could contradict `languages`.

    sentiment:        list[SentimentObservation]        = Field(default_factory=list, max_length=LIST_CAP)
    purchase_intent:  list[PurchaseOrAcquisitionIntent] = Field(default_factory=list, max_length=LIST_CAP)
    sponsorship:      SponsorshipAssessment

    # ---- open-ended discovered fields (brief §19B) --------------------------
    occasions:                list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    behaviors:                list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    social_context:           list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    cultural_signals:         list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    slang:                    list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    humor_signals:            list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    activities:               list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    product_pairings:         list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    setting_observations:     list[DiscoveredLabel] = Field(default_factory=list, max_length=LIST_CAP)
    unexpected_observations:  list[DiscoveredLabel] = Field(default_factory=list, max_length=4)

    # ---- co-appearance (NOT competition) ------------------------------------
    brand_co_appearance: list[CoAppearance] = Field(default_factory=list, max_length=4)

    # ---- abstention, summary, self-reported confidence ----------------------
    evidence_insufficient_for: list[ShortLabel] = Field(default_factory=list, max_length=32)
    summary: Annotated[str, Field(max_length=600)] = Field(
        description="At most 80 words. Must contain no claim that is not also in a field "
                    "above. No brand name that is not in visible_brands or mentioned_brands.")
    # Was `dict[str, float]`. Structured outputs reject `additionalProperties` set to anything
    # other than `false`, so an open-ended map is not a legal wire schema: the field would have
    # been stripped to an empty object or the request rejected. Expressed as a closed list.
    field_confidence: list[FieldConfidence] = Field(default_factory=list, max_length=32)

    # ---- follow-up request --------------------------------------------------
    detail_request: DetailRequest | None = None
```

**Why `LIST_CAP = 6`, and how it is actually enforced.** Open-ended list length is the dominant
hallucination channel in extraction tasks: an uncapped list invites the model to keep going past
the evidence, and the marginal item is almost always the least-supported one. A cap forces
ranking, which is the behaviour we want.

**The cap is enforced post-hoc, and the model must be told the number.** Array-length bounds are
not supported by structured outputs and are stripped from the wire schema (§5 preamble), so
`max_length` here is a Pydantic check that fires *after* generation and costs a repair turn. The
original design relied on generation-time enforcement and the prompt said only "each open-ended
list is capped" without ever stating a number — a cap the model is not told cannot be respected,
and every overflow would have been paid for twice. §4.2 rule 10 now enumerates the caps and the
task tail restates the consequence. 6 is chosen because (a) short-form items of 15–60 s rarely contain more than
~4 independently anchorable observations of any one kind, so 6 is not binding on honest output;
(b) it bounds worst-case output at ≈3,100 tokens, which sets `max_tokens` and the §7 cost model;
(c) the prompt instructs the model to note overflow in `summary`, so the rare genuinely rich item
is still flagged for human follow-up rather than silently truncated. `visible_brands` and
`visible_products` are capped at 12 instead, because a shelf or a table legitimately contains
many objects and under-reporting those directly damages the co-appearance signal §23 depends on.
`unexpected_observations` is capped at 4 because it is the field most prone to padding.

### 5.6 Mapping to brief §20

| Brief §20 key | Here | Note |
|---|---|---|
| `visible_brands` | `visible_brands` | now requires a frame anchor |
| `visible_products` | `visible_products` | |
| `product_prominence` | `VisibleBrand.prominence` / `VisibleProduct.prominence` + `screen_share` | per-object rather than a loose dict, so it is anchorable. The flat §20 dict is reconstructed by the view `content_product_prominence` (§10.4), which was referenced but never defined in the original draft |
| `content_format` | `content_format` | free-text `DiscoveredLabel`, not an enum |
| `creator_archetype` | `creator_archetype` | free text |
| `languages` | `languages` | free text + `where` + anchors |
| `sentiment` | `sentiment[]` | a list of *directed* observations, not one global scalar |
| `purchase_intent` | `purchase_intent[]` | only explicit statements; never a score |
| `sponsored_likelihood` | `sponsorship` | bucket + required cues; `undetermined` is first-class |
| `occasions`,`behaviors`,`social_context`,`cultural_signals`,`slang`,`humor_signals`,`activities`,`product_pairings`,`unexpected_observations` | same names | all `DiscoveredLabel` |
| `competitors` | **`brand_co_appearance`** | deliberately renamed. The per-item model has no category and no baseline, so it cannot identify a competitor; it records co-appearance and framing, and §23 derives relationships downstream |
| `summary` | `summary` | ≤80 words, validated against the structured fields |
| `confidence` | `field_confidence` (model-stated, audit only; a closed list, not a map) + `calibrated_confidence` per claim (§6) | the displayed number is never the model's own |
| — (new) | `stated_use` | separates described use from observed use |
| — (new) | `evidence_insufficient_for` | makes abstention observable (brief §28) |
| — (new) | `Anchor` on every claim | makes brief §27 drill-down possible at claim granularity |

### 5.7 SDK note

`client.messages.parse(output_format=VideoAnalysis)` returns `response.parsed_output` — verified
against the current SDK. `output_config.format` is the canonical API-level parameter and
`output_format=` is the `.parse()` convenience form, so whether the SDK accepts
`output_config={"effort": "medium"}` *alongside* `output_format=` (rather than merging them) is a
genuine one-line check (OQ-7). The interactive path streams (§3.2), and `.parse()` plus streaming
is a second combination to verify; if either is rejected, fall back to
`client.messages.create(output_config={"format": {"type": "json_schema", "schema":
VideoAnalysis.model_json_schema(), ...}, "effort": "medium"})` and validate with
`VideoAnalysis.model_validate_json(text)`. The wire schema must be generated once and stored in
`analysis_configs.output_json_schema`, because a Pydantic-version change that alters the emitted
JSON Schema is a silent prompt change and must bump the fingerprint (§8).

---

## 6. CALIBRATION

### 6.1 Why self-reported LLM confidence is weakly calibrated

Four independent reasons, all of which apply here:

1. **No independent error signal.** `model_confidence` is produced by the same forward pass that
   produced the claim. It is a *continuation* of the claim, not a measurement of it. Nothing in
   the generation process compares the claim to the evidence a second time.
2. **Range compression and round-number clustering.** Preference-trained models emit a narrow,
   high band (empirically most mass in 0.7–0.95) with heavy spikes at 0.8, 0.9 and 0.95. A score
   that almost never goes below 0.7 cannot separate a solid claim from a shaky one, which is
   precisely the separation this product needs.
3. **Sensitivity to the wrong variables.** Stated confidence moves with prompt wording, position
   in the output, and list length far more than with the amount of evidence. Two items with
   identical evidence but different caption lengths get different numbers.
4. **No access to the underlying distribution.** The Messages API does not expose token
   logprobs, so there is no way to recover a calibrated probability from the model's own
   distribution post hoc. Whatever we do must be built on observable properties of the output.

Consequence: `model_confidence` and `field_confidence` are **stored but never displayed and never
used for gating**. They are kept as *features* for the calibrator and as research data on how
badly they are calibrated.

### 6.2 The cheap improvement: evidence-gated confidence

The design already forces the model to attach, to every claim, two things it cannot produce
without having actually looked: an **anchor** (a frame index or timestamp) and a **cue** (an
observable a third party can check). Those are machine-verifiable. The improvement is to make the
displayed confidence a function of *verified* evidence rather than of the model's self-report.

Three cheap, pure-Python checks run on every claim (no extra model calls, ~0.3 ms/claim):

**Check A — anchor validity.**
`frame_index ∈ [0, n_frames)`; `0 ≤ t_start_s ≤ t_end_s ≤ duration_s`; the timestamp must fall
inside some retained transcript segment; for `VisibleBrand`/`VisibleProduct`, the §5.2
frame-consistency validators must also hold. Fail ⇒ `cue_check='anchor_invalid'`, claim
**rejected**. A fabricated anchor is a direct indicator that the claim was generated rather than
observed.

**Honest statement of what Check A can and cannot do.** For a *text*-anchored claim it is a real
verification: the quote either is in the supplied text or is not. For a *frame*-anchored claim it
is only a bounds check — any integer in `[0, n_frames)` passes, and nothing confirms that the
nominated frame contains what the claim says it contains. The original §6.2 claimed this
mechanic "converts 'the model asserted X with 0.9 confidence' into 'the model asserted X and
produced a checkable pointer that checks out'". For the ~60% of claims whose only anchor is a
frame index, that overstates it: the pointer is well-formed, not checked. Three consequences,
now explicit rather than implied:
- A claim whose anchors are all frames carries `f_frame_anchor_unverified = 1` in the §6.3
  feature vector, so the calibrator can learn how much less reliable that class is instead of
  the design asserting it is just as good.
- The cross-field consistency checks above are the only *free* verification available for visual
  claims, which is why they were added.
- Ablation A1 (cue required vs removed) must be reported **split by anchor modality**, because a
  mechanic that works on quotes and not on frames will look like a mechanic that works.

**Check B — cue quotability (text-anchored claims). Measure corrected in review.**
The original rule was character-trigram **Jaccard ≥ 0.60** between the quote and the ±2 s
transcript window (or the whole caption/comments block). That rule is numerically impossible to
pass. Jaccard divides by the size of the *union*, and the window is one to two orders of
magnitude longer than the quote: an exactly verbatim 18-character quote inside a 180-character
window scores **0.106**. Against a ~700-token comments block it is lower still. The gate would
have rejected essentially every transcript-, caption-, overlay- and comment-anchored claim in the
corpus as `unquotable` — silently, as an evidence-quality result rather than a bug.

The correct measure for "is this short string present in that long block" is **containment**, not
Jaccard:

```
norm(x)      = NFKC, casefold, collapse all whitespace runs to one space, strip
tri(x)       = set of character trigrams of norm(x)
containment  = |tri(quote) ∩ tri(window)| / |tri(quote)|        # 1.00 for a verbatim quote
```

Thresholds, restated for containment and for an instruction that demands verbatim quoting:
`≥ 0.85` pass; `0.70 – 0.85` → `low_conf` (kept, but cannot be the sole support for a published
claim); `< 0.70` → `cue_check='unquotable'`, claim **rejected**. The tolerance below 1.00 absorbs
case, whitespace, elided filler and segment-boundary straddling; 0.70 is far above what unrelated
text in the same language reaches at trigram level. ±2 s still absorbs the segment-boundary case.
Quotes shorter than 12 normalised characters are checked by exact substring instead, because
trigram containment is unstable on very short strings. The normalisation steps are named here
because they move the score materially and the original left them to the implementer.

**Check C — cue non-vacuity. Measure corrected in review.**
The original rule rejected a cue when `difflib.SequenceMatcher(cue, value).ratio() ≥ 0.80`. That
ratio is `2·M/(len(a)+len(b))`, so it is bounded above by `2·min(la,lb)/(la+lb)` — for a 25-char
label against a 140-char cue the **maximum attainable value is 0.25**. The gate could therefore
only ever fire when cue and value happened to be of similar length, making it a length test
rather than a vacuity test. Worse, the design's own worked example is false: the restatement it
says "scores above it", `SequenceMatcher("the product is being consumed", "in_use_or_consumed")`,
scores **0.426**. Check C never fired on anything it was written to catch, and for
`SponsorCue` (whose `value` is a 25-character enum member) it could not fire at all.

Replaced with a length-invariant, set-based test that says what "vacuous" actually means — *the
cue contributes no observation beyond the label itself*:

```
HEDGES = structural frames only: {the, a, an, is, are, this, that, there, it, in, of, and,
         video, image, frame, shows, show, showing, appears, appear, seen, see, visible,
         can, you, we}        # no content word, no brand, no category term — §0.3 clean
content(x) = tokens of norm(x), minus HEDGES, minus tokens of norm(value)
vacuous    = len(content(cue)) < 3
```

A legitimate cue ("a hand lifts the object toward the mouth" against label
`in_use_or_consumed`) retains `hand / lifts / object / toward / mouth` → 5 content tokens, passes
at any length. A restatement ("the product is being consumed") retains `product / being` after
subtracting the label's own tokens → 2, fails. This is invariant to cue length and to label
length, which the ratio test was not. The same rule applies to enum-valued claims, where the
label tokens are the snake_case parts. **The hedge list is structural, not semantic**, it is
applied **only** to the model's own English-language `cue` field and never to transcript, caption
or comment text, and it contains no content word, brand or category term — so it does not violate
§0.3.

**A claim with no surviving cue is treated as unsupported, full stop.** This is the central
mechanic for text-anchored claims: it converts "the model asserted X with 0.9 confidence" into
"the model asserted X and produced a pointer whose quote is actually in the source". For
frame-only claims it is weaker, and the design now says so rather than borrowing the stronger
claim's credibility.

### 6.3 Support features and the calibrated score

For each surviving claim, compute a feature vector (all cheap, all observable):

| Feature | Definition |
|---|---|
| `f_modalities` | count of distinct `Anchor.modality` values on the claim, 1–4 |
| `f_anchors` | count of distinct anchors, capped at 4. **Note:** anchors on one claim come from one forward pass, so this is *more assertion*, not independent corroboration — it is included as a feature so the fit can price it, never as a rule-table bonus without evidence (cf. §6.5) |
| `f_frame_spread` | for frame-anchored claims, over **sampled frames only** (the platform thumbnail index is excluded), `(max_t − min_t) / (t_hi − t_lo)` in **seconds**, else 0 |
| `f_quote_sim` | the Check-B **containment**, 0 for frame-only claims |
| `f_frame_anchor_unverified` | 1 when every anchor on the claim is a `frame` anchor (Check A is then only a bounds check — §6.2), else 0 |
| `f_duration_tier` | the item's §1.4 tier index / 5, so the fit can absorb tier-dependence instead of confounding it into `f_frame_spread` |
| `f_thumbnail_only_anchored` | 1 when the claim's only frame anchor is the platform cover image (§1.3 step 7) |
| `f_cue_len` | `min(len(cue), 160) / 160` |
| `f_cue_distinct` | `1 − SequenceMatcher(cue, value).ratio()` |
| `f_corroborated` | 1 if the same normalised value also appears in a *different* field with a *different* basis (e.g. a brand both `seen` and `heard`), else 0 |
| `f_model_conf` | the model's own number — included **only** so the fit can measure how little it contributes |
| `f_asr_quality` | 0/0.5/1 for `degenerate`/`low`/`ok`, 0 for frame-only claims |
| `f_legibility` | for brand claims, 1.0 `clear` / 0.5 `partial` / 0 `illegible`; else 0.5 |

**`f_frame_spread` was index-based and is now time-based.** `(max_idx − min_idx) / (n_frames − 1)`
is a share-of-index, and index spacing is neither uniform in time (buckets are scene-max, not
uniform) nor comparable across tiers (index span 0→7 is ~45 s on a short-form item and ~9 minutes
on a long one). Pooled across tiers, the fit would have learned a coefficient confounded with
duration. It also awarded the maximum possible spread to a claim anchored on frame 0 plus the
platform thumbnail — i.e. maximum "evidence" from the least trustworthy frame in the item.

**Field → `value` extraction must be explicit**, because Checks B and C both compare against
`value` and the schema puts it in a different attribute on every class. The mapping is part of
`validator_version` (§8.1), not left to the implementer:
`VisibleBrand → brand_text_as_seen`; `VisibleProduct → description`;
`MentionedBrand → brand_text_as_said_or_written`; `StatedUse → statement`;
`DiscoveredLabel → label`; `SponsorCue → kind`; `SentimentObservation → f"{toward} {valence}"`;
`PurchaseOrAcquisitionIntent → statement`; `LanguageObservation → language_as_identified`;
`CoAppearance → " / ".join(brand_texts)`.

`calibrated_confidence = σ(w · f + b)` from a **per-field-group** L2-regularised logistic
regression fitted on human labels (§6.4). Field groups: `{brand_identity}` (visible/mentioned
brands), `{product_use}` (consumption_state, stated_use), `{sponsorship}` (the assessment and its
cues), `{discovered_label}` (all §19B fields, plus `format_features`), `{descriptor}` (format,
archetype, language, sentiment, intent, co-appearance). Grouping rather than per-field keeps the
per-model sample size viable; separating these five is necessary because their base rates and
failure modes are genuinely different (brand reads fail on legibility, discovered labels fail on
vacuity).

**Sample size per group is the binding constraint, and the original plan contradicted itself.**
§6.5 states — correctly — that "a 10-feature logistic on <500 claim labels will produce confident
nonsense". §6.6 then plans ~900 labels split across five groups, i.e. **~180 per group**, and
fits a 13-feature logistic to each. The gate must therefore be **per group, not global**:

| Labels in the group | What is fitted |
|---|---|
| < 200 | nothing — the group uses the §6.5 rule table and shows the "uncalibrated" badge |
| 200 – 500 | a **pooled** logistic over all groups with a per-group intercept only, and the feature set reduced to the five with the clearest mechanism (`f_modalities`, `f_quote_sim`, `f_corroborated`, `f_cue_distinct`, `f_frame_anchor_unverified`) |
| > 500 | the full per-group 13-feature fit described above |

At the planned set size most groups land in the middle row on the first pass. That is the honest
expectation and it should be stated in the UI, not discovered later.

**Logistic rather than isotonic:** isotonic regression needs a single monotone input score and
overfits badly below ~1,000 points per group. Logistic with ~10 features, L2 and 5-fold CV is
stable at the sample sizes realistically available (§6.4) and gives interpretable coefficients —
and the coefficient on `f_model_conf` is itself the deliverable evidence for §6.1.

**Both numbers are stored**: `content_analysis_claims.model_confidence` (never displayed) and
`.calibrated_confidence` (what the UI shows, what downstream aggregation filters on).

### 6.4 Publication thresholds and what happens at the boundary

| `calibrated_confidence` | `claim_status` | Effect |
|---|---|---|
| ≥ τ | `published` | Visible in the UI; counted in clustering, trends, findings |
| `unverified_floor` – τ | `unverified` | Stored and visible in Content Explorer behind an explicit "weak evidence" toggle; **excluded from every numerator**; see the denominator rule below |
| < `unverified_floor` | `rejected` | Stored for QA only; never surfaced; counted in the per-run quality report |

τ is **not** a guess to be kept: it is set by the §6.6 harness as the lowest threshold at which
held-out claim-level precision ≥ **0.90**, and the resulting recall is reported alongside it.
0.90 is chosen because brief §28 and §3 make an unsupported claim far more costly than a missed
one — the product's entire value proposition is that a marketer can click through to evidence, so
a claim that does not survive that click is worse than absent.

**The pre-measurement defaults are regime-dependent, and the original single default of 0.60 was
corpus-destroying.** Before any calibrator exists, scores come from the §6.5 rule table, whose
base is 0.45 and whose bonuses require ≥2 modalities, ≥2 anchors, or corroboration. The **modal**
claim — one frame anchor, one modality, not corroborated, cue passing — scores exactly **0.45**.
At τ = 0.60 that claim is `unverified`, so on day one the *majority* of claims would have been
excluded from every cluster and every finding, and the survivors would have been precisely the
multi-modal ones: brands both seen and said, i.e. items with spoken brand mentions and
disclosures. The product's first corpus would have been silently biased toward branded and
sponsored content — in exactly the direction brief §1 warns about — and nothing in the design
would have reported it. Therefore:

| Regime | τ | `unverified_floor` | Displayed |
|---|---|---|---|
| **Uncalibrated** (rule table, §6.5) | **0.45** — a clean single-anchor claim whose cue survives all three checks is publishable | 0.30 | band + "uncalibrated" badge, never a point estimate |
| **Calibrated** (per group) | the lowest τ with held-out precision ≥ 0.90, per group | 0.40 | `calibrated_confidence` |

In the uncalibrated regime the real gate is the three pure-Python checks plus the hard gates
below, and the design should say that plainly rather than dress it as a calibrated threshold.
The per-run quality report **must** publish the published / unverified / rejected split with its
denominator, per field group: if that split is ever extreme, the gate is doing something nobody
intended and it needs to be visible on the first run, not the tenth.

**Denominators (this corrects an ambiguity that mattered).** The original text said `unverified`
claims are "excluded from every aggregate, denominator, cluster and finding", which conflicts
with CSR-7 and hides a real statistical decision. The rule is: **claim status affects numerators;
item status affects denominators.** An item whose only claim for a field is `unverified` stays in
the denominator and counts as *absence* of that label — which biases presence rates **downward**,
the conservative direction, and is the right default under brief §28. Dropping the item instead
would bias presence rates upward. Every aggregate publishes, alongside its sample size, the count
of items contributing a weak-but-excluded claim, so a reader can see how much of an "absence" is
really "uncertainty".

Additional hard gates, applied before calibration and independent of it:
- `asr_quality in ('low','degenerate')` ⇒ a transcript-only, single-anchor claim cannot be
  `published` (it may be `unverified`).
- `media_tier = 'thumbnail_only'` ⇒ `consumption_state > 'present'`, non-empty `activities`,
  and any transcript anchor are rejected outright.
- `summary` introducing a name that appears nowhere else triggers one repair turn (§6b.3); a
  second failure drops the summary to empty and records `summary_dropped=true` rather than
  publishing an unanchored sentence. **This had to be made computable:** the original rule —
  "contains a brand string absent from `visible_brands ∪ mentioned_brands`" — presupposes a brand
  detector, and the one thing this subsystem may not have is a brand list (§0.3, CSR-6). The
  implementable form is a *provenance* test rather than a brand test: flag any token in `summary`
  that is (a) alphabetic, length ≥ 3, (b) Titlecase or ALLCAPS **not** at a sentence start, and
  (c) absent, casefolded, from the union of {caption, hashtags, overlay text, comments,
  transcript text, every emitted `value` in the object}. That catches an invented name without
  knowing what a brand is. It will occasionally fire on a legitimately capitalised ordinary word;
  the cost of a false fire is one repair turn, the cost of a miss is an unanchored name in the
  one field a marketer reads first, so the asymmetry is the right way round. The false-fire rate
  is recorded and reviewed after the pilot.

### 6.5 Fallback when there is no calibrator yet

Before any labels exist, `calibrated_confidence` is produced by a transparent **rule table**
(`calibration_models.method='rule_table'`), not by a model:

```
base = 0.45                          # a clean single-anchor claim; publishable at the
                                     # uncalibrated tau of 0.45 (§6.4)
+0.15 if f_modalities >= 2           # distinct modalities: genuine cross-channel support
+0.10 if f_corroborated
+0.10 if f_legibility == 1.0         (brand claims only)
-0.10 if f_frame_anchor_unverified   # bounds-checked only, not verified (§6.2)
-0.10 if f_thumbnail_only_anchored   # cover image is not in-video evidence (§1.3 step 7)
-0.20 if cue_check == 'low_conf'
-0.15 if f_asr_quality <= 0.5 and claim is transcript-only
clamp to [0.05, 0.85]
```

**The `+0.15 if f_anchors >= 2` bonus was removed.** Multiple anchors on one claim are produced
by the same forward pass, so they are more assertion rather than independent corroboration, and
rewarding them in a hand-written rule table is a direct incentive for the model to emit more
anchors per claim — a Goodhart loop where the thing being measured is produced by the thing being
scored. `f_anchors` remains a *feature* so the fitted calibrator can price it from labels; it is
not a prior. `f_modalities` keeps a bonus because distinct modalities really are distinct
evidence channels.

The 0.85 clamp is deliberate: with no validation data the system must not be able to display high
confidence, and the UI shows the badge "uncalibrated — no validation set yet" for every claim
scored this way. **Do not fit a model for a field group with fewer than 200 adjudicated labels in
that group** (see the per-group table in §6.3) — a 13-feature logistic on ~180 labels will produce
confident nonsense, which is strictly worse than an honest rule table. The original gate was
stated globally ("fewer than 80 labelled videos") while the plan fitted five separate models off
a pool that could never satisfy it per group; the gate is per group now because that is the level
the fit happens at.

### 6.6 Validating against a human-labelled set

**Set construction.** 150 videos sampled from the run's actual corpus (never hand-picked, never
from a pilot).

*Stratification, corrected.* The original `platform × duration tier × has_speech × media_tier`
is a 2+ × 6 × 2 × 3 = **72-cell** full crossing for 150 videos — about two items per cell, which
supports no per-cell statement and leaves many cells empty. Collapse to a crossing that 150 can
actually fill, and handle the rest by quota:

| Stratum | Levels | Allocation |
|---|---|---|
| platform | the platforms actually present | proportional to corpus |
| length | short (< 60 s) / long (≥ 60 s) | proportional |
| `has_speech` | true / false | proportional, min 20 per level |
| `midpoint_fraction` (static vs cut-heavy, §1.3 step 5) | low / high at the corpus median | proportional, min 30 per level |

`media_tier` is a **quota**, not a crossing: ≥ 20 non-`full_video` items, because degraded tiers
have their own failure modes and nothing else guarantees they appear. `midpoint_fraction` is in
the design because frame selection is better on cut-heavy, professionally edited items than on
static single-take ones, so brand-read recall is correlated with production value — which is
correlated with sponsorship, which the product reports on. If that bias is not measured it will
be published as a finding.

*Label volume.* The "~6 claims/video" figure was stated as observed; nothing has been observed
yet, and the schema admits 25 claim-bearing fields, so the realistic range is wider and probably
higher. Plan on **900–2,500 claim labels** and treat the number as an output of the pilot, not an
input. The "12–20 annotator-hours" figure also counted a single pass: the protocol is
double-labelling plus third-pass adjudication of disagreements, so the real budget is
**≈2.3× the single-pass figure** — roughly 30–50 hours at the low end of the label range. Sizing
the annotation budget off the single-pass number is how calibration sets end up half-labelled.
**At the boundary:** below the per-group thresholds in §6.3, use §6.5 and say so in the UI; above
~400 videos the marginal gain in calibration quality is small relative to annotation cost, so 150
is the recommended operating point and 300 the point of diminishing returns.

**Labelling protocol.** Two annotators independently label every claim with
`supported / unsupported / wrong / cannot_tell`, seeing only the claim, its cue and its anchor
(the exact frame image, or the transcript window) — **not** the rest of the model output, so
they cannot be anchored by the model's narrative. Report, per field group, **raw agreement, the marginal prevalence of `supported`, and Cohen's κ
together** — κ alone is misleading here, because the `supported` rate is expected to be high and
κ collapses under skewed marginals even when agreement is excellent (the base-rate paradox). A
group with κ < 0.6 **and** raw agreement < 0.85 has an ambiguous definition and the *schema*, not
the model, needs fixing; κ < 0.6 with high agreement and skewed marginals is a prevalence
artefact and is reported as such (add Gwet's AC1 if the ambiguity persists). Disagreements
are adjudicated by a third pass and the adjudicated label is the training target
(`supported` → 1; `unsupported`/`wrong` → 0; `cannot_tell` → excluded from the fit but counted).

**Splitting: grouped by video, three ways, and this is not optional.**
Claims are nested inside videos. Claims from one video share a media file, a frame set, a
transcript, one forward pass and one set of correlated errors — they are emphatically **not**
900 independent observations, and the original plan ("a held-out 30% split, with 5-fold CV on the
remainder") said nothing about grouping. A random claim-level split puts sibling claims from the
same video in both train and test, which leaks and inflates every held-out number. Because **τ is
chosen as the lowest threshold achieving held-out precision ≥ 0.90**, an inflated precision
estimate sets τ *too low* — the system would then publish claims at a real precision below its
own stated bar, while reporting that it met it. That is the single most consequential statistical
defect in the original document. Required instead:

1. **`GroupKFold` / grouped splits keyed on `content_item_id`** everywhere — the held-out split
   and every CV fold.
2. **A three-way split, because τ is a selected parameter.** Selecting τ on the same data used to
   report precision at τ is selection-on-test and biases the reported precision upward. Fit on
   train, **select τ on a validation split**, report precision/recall/ECE at that frozen τ on a
   **test split touched once**. Equivalently: nested CV with τ chosen in the inner loop.
3. **Uncertainty is cluster-bootstrapped by video** (resample videos, not claims). Every number
   below is reported with a 95% interval; a point estimate from ~180 labels across ~150 videos
   without one invites exactly the over-reading brief §28 forbids.

**Metrics reported per field group**, under that protocol:
- **Expected Calibration Error** with a reliability diagram — the headline number, because the
  claim being made is "this confidence means something". Use **equal-mass (quantile) bins, 5 of
  them**, not 10 equal-width bins: with ~180 points and a score distribution concentrated in a
  narrow band, equal-width bins leave most bins near-empty and ECE becomes a noisy, biased
  statistic whose value is dominated by binning choice. Report the bootstrap interval; an ECE
  without one at this sample size is not interpretable.
- **Brier score** and **AUC**, both with cluster-bootstrap intervals.
- **Precision / recall at the frozen τ**, on the untouched test split.
- **Unsupported-claim rate**: fraction of *emitted* claims an adjudicator marks unsupported.
  This is the product-level number and is published in the run's quality report (brief §28).
- **The coefficient on `f_model_conf`** relative to the others, as the direct measurement of
  §6.1's claim for this task. Report it with its interval — "small" is a claim that needs one.

**Multiplicity.** A1–A7, three effort levels, five field groups and six metrics is on the order
of a hundred comparisons against one labelled set. Nothing in the original plan controlled for
it, and every selection rule in the document ("lowest effort within 2.0 points", "lowest τ at
precision 0.90") is a pick-the-max over noise. Required: each ablation declares **one
pre-registered primary metric** before it runs; the primary metrics across A1–A7 carry a
**Holm correction** at family level; everything else is labelled exploratory and may generate a
hypothesis but may not settle a config. No config default changes on an uncorrected secondary
metric.

**Ablations run on the same set** (each is a prompt/config variant, each gets its own
`analysis_config` so results are comparable by construction):
- A1: cue required vs cue field removed → measures what the cue mechanic actually buys.
  **Reported split by anchor modality** (text-anchored vs frame-only), because Check B verifies
  the former and only bounds-checks the latter (§6.2); a pooled number would let the quote
  verifier's success masquerade as evidence that frame anchors are verified too.
- A2: effort `low` / `medium` / `high` → sets the §3.2 default.
- A3: frame count 5 / 8 / 12 at fixed resolution → sets the §1.4 budget (OQ-4).
- A4: long edge 768 / 960 / 1344 → measures legibility against token cost.
- A5: frames-first vs caption-first ordering → measures the §3.3 anti-anchoring claim directly,
  using `visible_brands` precision as the metric.
- A6: detail pass on vs off → sets the §1.7 trigger rate (OQ-3).

**Non-determinism floor (important, and the original rule for using it was wrong).**
`temperature` is not available on Opus 5, so runs are not reproducible. Before any ablation is
interpreted, run the **baseline config 3× over the whole 150-item set** and compute per-field
self-agreement.

The original rule — "any ablation difference smaller than [the self-agreement floor] is not a
result" — conflates two different quantities and is far too conservative. Self-agreement is a
**per-item** variability statistic. An ablation compares **aggregate** metrics over 150 items, and
the standard error of an aggregate shrinks roughly as 1/√n: a per-item disagreement rate of 15%
does not imply a 15-point minimum detectable difference in macro-F1 over the set. Applying the
per-item floor to an aggregate comparison would cause the team to discard real effects and freeze
every config at its unmeasured prior — the opposite of what the measurement is for.

The correct procedure, which uses the repeats properly:
- Run **both arms with the same number of repeats** (3× each) on the same items.
- Compare **paired by item**, and compute the interval on the *difference* by a
  **paired cluster bootstrap over items**, with the repeat-to-repeat variance entering as a
  within-config variance component (a simple variance-components or mixed-effects form over
  item × config × repeat is sufficient; the arithmetic matters more than the estimator).
- A difference is a result when its interval excludes zero after the Holm correction above —
  **not** when it exceeds the per-item self-agreement rate.
- Report the per-item self-agreement anyway: it is the right statistic for the *per-item* claim
  in §8.3, and it bounds how much of the residual disagreement between two prompt versions is
  just the model.

### 6.7 The discovery-query A/B (OQ-1)

Run as ablation A7 on the same 150-item set: identical config except
`include_discovery_query=true`. Metric: `visible_brands` precision and recall against the
adjudicated labels, plus the **query-term leak rate** — the fraction of items whose
`visible_brands` contains a term from the item's own discovery query but which annotators mark
unsupported. If the leak rate exceeds the no-query arm by more than the §6.6 noise floor, the
field stays off permanently and the result is recorded in the design history. Default stays
**off** until this runs.

---

## 6b. Refusal and failure handling

Every path below writes a row to `analysis_attempts`, so the per-run quality report can state
exactly how many items failed and why — brief §28 requires that insufficient data be identified,
not hidden.

### 6b.1 `stop_reason == "refusal"`

Returned as HTTP 200, so it **must** be checked before reading `response.content`;
`response.stop_details` is populated only in this case (`type`, `category`, `explanation`) and is
`null` for every other stop reason, so guard before reading it.

1. Record `status='refused'`, `refusal_category=stop_details.category`, and the explanation.
2. **Do not retry the identical payload** — the classifier is a function of the content, so a
   straight retry burns money for the same outcome.
3. **Degrade:** re-issue with `media_tier='audio_only'` semantics — frames dropped,
   transcript and platform text retained, manifest states `frame_count: 0`. Most refusals on this
   corpus will be triggered by imagery, and a text-only pass usually completes. The result is
   stored with `media_tier='audio_only'` and `degraded_from='full_video'` so the reduced evidence
   base is visible in the UI and in downstream denominators.
4. If the text-only pass also refuses: `status='refused_terminal'`. The item is **excluded from
   every denominator** and the exclusion count is surfaced in the run's quality report. It is
   never silently counted as "no brands found" — that would bias every aggregate toward absence.
5. **The ladder is ordered, and the original text gave two incompatible orders** ("batch
   -dispatched items reach step 3 directly" *and* "items that refuse in a batch are re-queued to
   the interactive path … before step 4"). The single authoritative ladder:

   | # | Dispatched interactively | Dispatched in a batch |
   |---|---|---|
   | 1 | `fallbacks="default"` with `betas=["server-side-fallback-2026-07-01"]` is already on the request, so the API reroutes by refusal category inside the same call. A `stop_reason=="refusal"` on the returned message means the whole chain refused. | `fallbacks` is **rejected on the Batches API**, so this step does not exist. Re-queue the item **to the interactive path**, unchanged, where step 1 applies. |
   | 2 | frames-dropped text-only pass (step 3 above) | same |
   | 3 | `status='refused_terminal'` (step 4 above) | same |

   One re-queue only; a re-queued item that refuses again proceeds to step 2, never back to the
   batch. The asymmetry must be reflected in both code paths, and the re-queue count is recorded
   so the batch discount's real yield is measurable.

### 6b.2 Truncation (`stop_reason == "max_tokens"`)

With `output_config.format` active, a truncated response is **invalid JSON** and must not be
repair-parsed — a partially emitted object is a partially emitted *claim list*, and salvaging it
would silently drop whatever the model was still writing.

1. Record `status='truncated'`, store `output_tokens` for diagnosis.
2. Retry once with `max_tokens=64_000` **and** `effort` one notch lower, **streaming** on the
   interactive path. *Revised in review:* with the ceiling now 32,000 (§3.2), a truncation is
   either genuine runaway thinking or an object near the schema's real worst case (~24k), and the
   original 24,000 retry was *below* that worst case — it could not succeed on the second cause
   it claimed to address. 64,000 clears both; it is a ceiling, not a charge.
3. If it truncates again, retry with `schema_variant='minimal'`: drop
   `unexpected_observations`, `product_pairings`, `setting_observations`, `humor_signals`, drop
   comments from the user prompt, and set `LIST_CAP=3`. Record `schema_variant` on the row so
   downstream knows those fields are structurally absent rather than empty-because-unsupported.
4. Third failure ⇒ `status='schema_error'`.

### 6b.3 Schema-validation failure

Structured outputs constrain generation, so raw JSON Schema violations are rare. What does fail
is this design's **semantic** validators: the §5.2 basis/anchor cross-checks, the §5.4
sponsorship gate, anchor ranges, the summary-brand check.

1. Catch `pydantic.ValidationError`; serialise `e.errors()` into a compact list.
2. **One** repair turn: append the assistant's message and a user message containing only the
   validator errors and the instruction *"Return a corrected object. Remove any claim you cannot
   fix by correcting its anchor or cue — removing a claim is the correct repair."* Note the
   repair turn re-sends the full image payload, so it costs roughly a whole call; the cap of one
   is a cost decision as much as a quality one, and the §7 model budgets **12%** of items for it
   (raised from 8% in review: list caps and string bounds are **not** enforced at generation time
   (§3.2), so every cap overflow is a semantic validation failure and lands here. 12% is still a
   pre-measurement prior — the realised repair rate is the first number to read off the pilot,
   and if it exceeds ~20% the caps, not the budget, are the thing to change).
3. On a second failure: `status='schema_error'`, persist the raw text to
   `content_analysis.raw_response` for offline debugging, and insert **nothing** into
   `content_analysis_claims`. Partial inserts are forbidden — a half-written analysis is
   indistinguishable downstream from a complete one that found little.

### 6b.4 Transport and provider errors

429 / 5xx / connection errors are **not** analysis failures. Retry in the Postgres queue with
exponential backoff (base 2 s, factor 2, jitter ±25%, max 5 attempts, cap 120 s), honouring
`retry-after` when present. Catch a most-specific-first chain (`NotFoundError` → `RateLimitError`
→ `APIStatusError` → `APIConnectionError`); a single broad catch would retry a 400 forever.
After 5 attempts the item returns to the queue as `deferred` and is retried in the next wave
rather than being marked failed.

### 6b.5 Media and ASR failures

| Failure | Handling |
|---|---|
| Media URL expired / 403 | one re-resolve through the collector, then degrade tier |
| FFmpeg non-zero exit on frame extraction | retry once with the seek variant (§1.5e); then degrade to `thumbnail_only` |
| FFmpeg produced < 2 frames | degrade to `thumbnail_only` |
| whisper-1 error or timeout | one retry; then `asr_quality='unavailable'`, continue with frames only |
| Audio > 25 MB | chunk per §2.6; a single failed chunk is retried, and if it still fails that time range is rendered as `(transcript unavailable)` rather than silently omitted |

---

## 7. COST at 1,500 videos

### 7.1 Unit prices used

| Item | Price |
|---|---|
| Opus 5 input | \$5.00 / MTok |
| Opus 5 output (includes thinking tokens) | \$25.00 / MTok |
| Opus 5 cache read | \$0.50 / MTok (0.1×) |
| Opus 5 cache write, 1 h TTL | \$10.00 / MTok (2×) |
| Message Batches | **50%** of all of the above (≤ 256 MB and ≤ 100,000 requests per batch — §3.5) |
| whisper-1 | \$0.006 / minute of audio |
| Image tokens | `ceil(W/28) × ceil(H/28)`; 700 at 960-px long edge, 16:9 or 9:16 |

### 7.2 Corpus assumption

Brief §6 targets ~1,100–1,700 TikTok + 300–500 YouTube. Model the run as **1,500 videos**:

- **1,100 short-form**, median ~35 s, 8 frames, ~45 s of speech.
- **400 longer**, median ~240 s, 14 frames, ~600 words of speech.

### 7.3 Per-item token itemisation

| Line | Short-form | Longer |
|---|---:|---:|
| Frames (images) | 8 × 700 = **5,600** | 14 × 700 = **9,800** |
| Frame label text blocks | 112 | 196 |
| Media manifest | 70 | 90 |
| Transcript block | 300 | 2,250 |
| Platform text (caption 140 + 10 comments 560) | 700 | 700 |
| Item context | 110 | 110 |
| Task tail | 240 | 240 |
| Platform thumbnail, when present (+700) | item-dependent | item-dependent |
| **Uncached input** (excl. thumbnail) | **7,132** | **13,386** |
| Cached system read (2,400 tok @ 0.1×) | 240 eff. | 240 eff. |
| Output: structured object | 950 | 1,050 |
| Output: adaptive thinking @ `medium` | ~1,150 | ~1,400 |
| **Output total** | **2,100** | **2,450** |

| Cost line | Short-form | Longer |
|---|---:|---:|
| Input | \$0.03566 | \$0.06693 |
| Cache read | \$0.00120 | \$0.00120 |
| Output | \$0.05250 | \$0.06125 |
| **Per item** | **\$0.0894** | **\$0.1293** |

### 7.4 Run total — default configuration

| Line | Qty | Cost |
|---|---|---:|
| Opus 5, short-form | 1,100 × \$0.0894 | \$98.34 |
| Opus 5, longer | 400 × \$0.1293 | \$51.72 |
| Cache writes | ~24 | \$0.58 |
| Detail passes (§1.7) @ 15% | 225 × \$0.0153 | \$3.44 |
| Repairs + retries, budget 12% of calls | — | \$18.00 |
| whisper-1 | (1,100 × 0.75) + (400 × 4) = 2,425 min | \$14.55 |
| S3 storage, ~1.2 GB frames + audio | 1 month | \$0.03 |
| **Total, fully interactive** | | **≈ \$186.7** |

**The batched total, recomputed.** The original line — "≈\$97.9, 50% on all Anthropic lines" —
applied the batch discount to lines this very design routes to the **interactive** path: §3.5
sends the 25-item preview head, *all* detail passes, *all* repair turns and *all* single-item
re-runs interactively, and §3.2 notes `fallbacks` is unavailable in batch so refusal re-queues go
interactive too. Only the bulk fusion calls are discounted:

| Line | Path | Cost |
|---|---|---:|
| Fusion, 1,075 short-form + 400 longer | **batched** (50%) | \$73.92 |
| Fusion, 25-item interactive preview head | interactive | \$2.23 |
| Detail passes @ 15% | interactive | \$3.44 |
| Repairs + retries @ 12% | interactive | \$18.00 |
| Cache writes | mixed | \$0.58 |
| whisper-1 | — | \$14.55 |
| S3 | — | \$0.03 |
| **Total, Message Batches (realistic)** | | **≈ \$112.8** |

That is ~15% above the figure the original quoted and recommended configurations on. It does not
change the recommendation — batching is still the largest single lever — but a cost number that
is quoted to anyone must be the one the dispatch policy actually produces.

**Wall clock is not estimated here any more.** The original "~35–55 minutes at 8 concurrent
workers" rested on the scene-scoring pass being I/O-bound, which it is not: `fps=6` decimates
after decode, so every item pays a full decode (§1.5c). With the `-skip_frame nokey` branch for
items over 180 s the figure is plausibly similar, but it is **unmeasured** and is recorded as
such rather than quoted. Measure it on the pilot wave; FFmpeg and ASR still dominate wall clock
rather than cost.

### 7.5 Levers, ranked by how much they move the number

**All deltas below are quoted against the same base — the realistic batched configuration
(≈\$112.8).** The original table mixed bases: lever 1's −\$83 was the interactive→batch saving
while levers 2–6 were quoted against the *interactive* total, so the rows were not comparable and
the ranking they produced was not meaningful. Lever 1 is also **not additive** with the others:
once batching is on, every subsequent saving is halved on the fusion lines.

| Lever | Change | Δ per run | Notes |
|---|---|---:|---|
| **1. Message Batches** | fully interactive → current policy | **−\$74** (\$186.7 → \$112.8) | Largest single lever and **free** in quality terms. Costs: latency (≤24 h, typically ≤1 h), loss of `fallbacks` (§6b.1), and multi-batch chunking (§3.5). Keep the 25-item interactive preview head so the research loop is not serialised. Already applied in the base below. |
| **2. `effort`** | `medium` → `low` | **−\$15** | Thinking tokens are the biggest output line. Pure quality trade; gate on ablation A2. (Was quoted −\$28 against the interactive base.) |
| **3. Frame budget + resolution** | 8 frames @ 960 → 5 @ 768 (448 tok) | **−\$13** | Image tokens fall 5,600 → 2,240. Directly risks packaging legibility; gate on A3/A4. |
| **4. Repair rate** | 12% → 6% via caps the model is actually told (§4.2 rule 10) | **−\$9** | New row. The largest *free* lever left: half this line is overflow of caps the original prompt never stated. |
| **5. Prompt caching** | on vs off | −\$9 | Already on. Without it every item pays 2,400 input tokens at full price. |
| 6. Drop comments from the prompt | −700 tok input | −\$2.5 | Cheap, but comments are the only audience-voice channel; losing them costs §19B `social_context` and `slang` evidence — and see CSR-12 on why comment-derived claims need their own denominator anyway. |
| 7. `LIST_CAP` 6 → 4 | −~150 output tok | −\$2.5 | Also reduces hallucination surface, so arguably free. |
| 8. Detail pass off | −225 calls | −\$3.4 | Undiscounted (interactive); costs brand-read accuracy on partially legible packaging. |
| 9. Word-level ASR timestamps off | — | \$0 | Costs nothing; only storage. |

**Observation worth stating plainly: output tokens, not images, are the dominant cost.**
At the default config, output is \$0.0525 of the \$0.0894 short-form unit cost (59%), of which
roughly half is adaptive thinking. Frame count is the lever people reach for first and it is only
third-largest. Optimise batching, then the repair rate, then `effort`, before cutting frames.
Note also the current Opus 5 guidance that on vision-heavy work a crop tool buys more per dollar
than raising thinking (OQ-13) — if that holds here, the effort lever and the detail pass are both
dominated by a mechanism this design does not yet use.

### 7.6 Recommended configurations

**DEFAULT (`mma/2026.10.02+1`) — ≈ \$113/run on the recommended dispatch policy, ≈ \$187 fully interactive**

```
model=claude-opus-5   effort=medium   max_tokens=32000   thinking=adaptive
frames: hybrid_bucket_scene, long_edge=960 (832 if near-square), jpeg_q=3
frame budget: 3/6/8/12/16/20 by duration tier
asr: whisper-1, verbose_json, segment(+word ≤120s), no language, no prompt
prompt text: caption + hashtags + 10 comments; discovery query OFF; market OFF
detail pass: ON at 15% trigger
dispatch: Message Batches (sealed at 200 MB / 500 requests), 25-item interactive preview head
cache: system block, ephemeral, ttl=5m interactive / 1h batch
LIST_CAP=6 (stated numerically in the prompt; enforced post-hoc, not at generation)
```

**CHEAP (`mma/2026.10.02+1-cheap`) — ≈ \$78/run on the recommended dispatch policy**
(the original "≈\$60 batched" inherited the over-discounted base; recomputed against §7.4)

```
effort=low   max_tokens=24000
frames: long_edge=768 (448 tok/frame), budget 3/4/5/7/9/12 by duration tier
prompt text: caption + hashtags only (no comments)
detail pass: OFF
asr: segment granularity only
LIST_CAP=4
```

Use CHEAP for a first exploratory wave where the goal is breadth and hypothesis generation, and
DEFAULT for the hypothesis-testing waves whose output reaches findings. **Both configs must be
scored on the same 150-item labelled set before either is trusted**, and the per-config metrics
stored in `calibration_models` so the UI can state which quality bar a given finding rests on.

**Cost verification is mandatory before these numbers are quoted to anyone.** Run
`client.messages.count_tokens` on 20 representative assembled payloads (both profiles) and
compare to the table in §7.3. The image-patch formula is the current documented behaviour, not a
contract; a tokeniser change would move every line in this section.

---

## 8. Idempotency and prompt versioning

### 8.1 The two identities that matter

**`prompt_version`** — a human-readable, monotonic string, e.g. `mma/2026.10.02+1`
(`family/date+revision`). Bumped by a human whenever any intent changes. It is what appears in
the UI next to a finding ("analysed with `mma/2026.10.02+1`").

**`config_fingerprint`** — `sha256` of the canonical (sorted-key, no-whitespace) JSON of
**everything that can change a result**:

```python
FINGERPRINT_INPUTS = {
    "prompt_version", "system_prompt", "user_template", "task_tail",
    "output_json_schema",            # the emitted wire schema, not the Python class
    "model", "effort", "max_tokens", "thinking",
    "schema_variant", "list_cap",
    "frame_policy",                  # mode, long_edge, square_long_edge, jpeg_qscale,
                                     # tiers, lead_s, scene_flat_abs/rel, trim, min_gap,
                                     # relevance cfg, keyframe-only crossover,
                                     # ffmpeg_build_id  (ADDED: JPEG bytes are build-dependent,
                                     #   so a toolchain upgrade must produce a new, visible
                                     #   artefact generation rather than silently different
                                     #   frames under the same hash)
                                     # relevance_lexicon_hash  (ADDED: §1.6 relevance windows
                                     #   are selected using the run's query lexicon, so two runs
                                     #   with different lexicons select different frames for the
                                     #   same long item. Without this the §1.8 artefact cache
                                     #   serves run A's frames to run B and the evidence does not
                                     #   match the policy that is recorded. Null for items with
                                     #   no relevance windows, so short-form caching is
                                     #   unaffected.)
    "asr_config",                    # model, response_format, granularities, language(null),
                                     # prompt(null), filter thresholds, chunking
    "prompt_text_inclusions",        # {caption, hashtags, comments_n, comment_selection,
                                     #  overlay, discovery_query: false, market: false}
    "detail_pass",                   # enabled, trigger rule, crop long_edge, jpeg_q, its prompt
    "validator_version",             # §6.2/§6.4 check thresholds
}
```

The fingerprint is the safety net: `prompt_version` can be forgotten, a hash cannot. If a
developer edits the system prompt without bumping the version, the fingerprint changes, a new
`analysis_configs` row is created, and the two prompt generations do not get silently mixed in
one aggregate. **This is the whole point** — brief §27 requires that a finding be traceable, and
a finding computed over a mixture of two prompt generations is not traceable.

**`media_fingerprint`** — `sha256` over the **source media bytes** (falling back to the
concatenated frame bytes plus transcript text only when the source is no longer retained).
Distinguishes "same config, same media" (a true no-op re-run) from "same config, different
media" (the download produced a different cut, a different resolution, or the platform replaced
the file). Without it, a re-run after a changed upstream file would overwrite a result whose
evidence no longer exists.

**Changed in review, for two reasons.** (1) Hashing the *output* frame bytes makes the
fingerprint a function of the FFmpeg build, so a routine toolchain upgrade would have created a
new fingerprint for all 1,500 items and silently triggered a full re-analysis (≈\$113) that
nobody requested — while a source-bytes hash is immune to encoder drift and detects exactly the
thing the field is for. (2) It was internally inconsistent: §1.8 already keys the audio artefact
off `sha256(source_bytes)`.

**Resolution order, which the original left unstated and which the "zero downloads" claim depends
on.** On a re-run the pipeline resolves, in this order:
1. Rows in `content_frames` / `content_transcripts` for this `(content_item_id,
   frame_policy_hash | asr_config_hash)`. A hit is **trusted**: artefacts are reused from S3, no
   download, no FFmpeg, no ASR, and `media_fingerprint` is read from the stored value.
2. On a miss, re-resolve and download, recompute `media_fingerprint` from the source bytes, and
   regenerate artefacts.

The honest consequence: **an upstream file that changed is detected only on an artefact-cache
miss, or when the caller sets `refresh_media=true`.** "Zero downloads on a prompt-only re-run"
and "detects a changed upstream file" cannot both be true at once, and the original §1.8/§8.1
asserted both. The default is the cheap branch; the forced-refresh flag exists for when evidence
integrity matters more than cost.

### 8.2 Re-runnability

- `content_analysis` has a unique index on
  `(content_item_id, analysis_config_id, coalesce(media_fingerprint, ''))`. Re-running is an
  `ON CONFLICT DO NOTHING` upsert and costs **zero** API calls.
- **`research_run_id` is deliberately *not* in that key, and that created a counting bug the
  original did not notice.** The same item reused by a second research run hits
  `DO NOTHING`, so the stored row keeps run #1's `research_run_id` and run #2 has no analysis
  row attributable to it — every per-run denominator, quality report and §32 run view would
  under-count run #2's corpus by exactly the items it reused. Since brief §28 makes sample sizes
  load-bearing, the fix is a junction rather than a wider key (widening it would force a paid
  re-analysis per run): `content_analysis_runs (analysis_id, research_run_id)`, written on every
  run that uses the analysis, and **all per-run denominators read through it** (§10.4).
- Results are **append-only versioned rows**, never updates. Two prompt generations coexist for
  the same item, which is what makes a version comparison possible at all.
- `analysis_configs.is_active` (single-row partial unique index) selects which generation the
  product reads; the `content_analysis_current` view resolves it. Flipping the active config is
  an instant, reversible switch between prompt generations with no reprocessing.
- Media artefacts are cached by `frame_policy_hash` / `asr_config_hash` (§1.8), so a
  prompt-only change re-runs the model but performs no download, no FFmpeg and no ASR, subject to
  the resolution order and the caveat in §8.1. Measured against §7: a prompt-only re-analysis of
  1,500 items costs the Anthropic lines only (≈\$98 on the realistic dispatch policy) and zero
  whisper spend.

### 8.3 The honest caveat about comparability

`temperature` is removed on Opus 5, so **identical input does not produce identical output**.
Idempotency here means *"re-running will not duplicate, double-charge, or silently mix
generations"*; it does **not** mean "re-running yields the same object". Consequences that must
be respected by anything comparing versions:

1. A per-item diff between two prompt versions is meaningful only if it exceeds the measured
   self-agreement floor (§6.6). That floor must be measured before any such comparison is
   published.
2. Version comparison is therefore done on a **fixed eval set with N=3 repeats per config**, and
   reported as mean ± SD of per-field agreement — never as a single-run before/after.
3. The UI must not present a per-item difference between two prompt versions as a change in the
   world. Only aggregate differences beyond the noise floor may be presented, and they carry the
   sample size (brief §28).

---

## 9. Python interfaces

All signatures type-annotated; all I/O async. Modules under `apps/api/aypo/analysis/`.

### 9.1 `media.py`

```python
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Sequence

@dataclass(frozen=True, slots=True)
class MediaProbe:
    duration_s: float | None
    width: int | None
    height: int | None
    rotation_deg: int
    avg_frame_rate: float | None
    has_audio: bool
    audio_sample_rate: int | None
    video_codec: str | None
    container_bytes: int

@dataclass(frozen=True, slots=True)
class FramePolicy:
    mode: Literal["hybrid_bucket_scene", "uniform"] = "hybrid_bucket_scene"
    long_edge: int = 960
    square_long_edge: int = 832
    square_ar_band: tuple[float, float] = (0.8, 1.25)
    jpeg_qscale: int = 3
    duration_tiers: tuple[tuple[float, int], ...] = (
        (5.0, 3), (20.0, 6), (60.0, 8), (180.0, 12), (600.0, 16), (float("inf"), 20))
    lead_s: float = 0.30
    scene_flat_abs: float = 0.08      # absolute floor
    scene_flat_rel: float = 3.0       # × median scene score — §1.3 step 5
    trim_head_frac: float = 0.03      # clamped to [0.20, 1.00] s
    trim_tail_frac: float = 0.04      # clamped to [0.25, 2.00] s
    scoring_fps: float = 6.0
    scoring_width: int = 160
    keyframe_only_scoring_above_s: float = 180.0   # §1.5c decode cost
    single_pass_max_duration_s: float = 120.0      # CFR sources only — §1.5d
    ffmpeg_build_id: str = ""         # set at runtime; part of the policy hash
    include_platform_thumbnail: bool = True
    relevance_windows: int = 4
    relevance_frames_per_window: int = 3
    max_frames: int = 32
    def hash(self) -> str: ...

@dataclass(frozen=True, slots=True)
class SceneScore:
    t_s: float
    score: float          # normalised 0..1

@dataclass(frozen=True, slots=True)
class FrameChoice:
    frame_index: int
    t_s: float | None
    scene_score: float | None
    selection_reason: Literal["bucket_scene_max", "bucket_midpoint", "thumbnail",
                              "relevance_window", "zoom_crop"]

@dataclass(frozen=True, slots=True)
class FrameArtifact:
    frame_index: int
    t_s: float | None
    path: Path
    s3_key: str | None
    width: int
    height: int
    image_tokens: int
    bytes: int
    sha256: str
    selection_reason: str

@dataclass(frozen=True, slots=True)
class AudioArtifact:
    path: Path
    duration_s: float
    bytes: int
    speech_ratio: float
    sha256: str

class MediaProcessor(Protocol):
    async def probe(self, src: Path) -> MediaProbe: ...
    async def extract_audio(self, src: Path, out: Path) -> AudioArtifact | None: ...
    async def speech_ratio(self, audio: Path, duration_s: float,
                           *, noise_db: int = -35, min_silence_s: float = 0.4) -> float: ...
    async def scene_scores(self, src: Path, policy: FramePolicy) -> list[SceneScore]: ...
    def select_frames(self, probe: MediaProbe, scores: Sequence[SceneScore],
                      policy: FramePolicy, *,
                      thumbnail_available: bool,
                      relevance_times_s: Sequence[float] = ()) -> list[FrameChoice]: ...
    async def extract_frames(self, src: Path, choices: Sequence[FrameChoice],
                             probe: MediaProbe, policy: FramePolicy,
                             out_dir: Path) -> list[FrameArtifact]: ...
    async def crop_region(self, src: Path, t_s: float, region: str, probe: MediaProbe,
                          *, long_edge: int = 896, jpeg_qscale: int = 2,
                          out: Path) -> FrameArtifact: ...

def image_tokens(width: int, height: int) -> int:
    """ceil(w/28) * ceil(h/28) — the planning formula for §7. Verify against
    client.messages.count_tokens before quoting costs."""
```

### 9.2 `transcribe.py`

```python
@dataclass(frozen=True, slots=True)
class AsrConfig:
    model: str = "whisper-1"
    response_format: str = "verbose_json"
    word_timestamps_max_duration_s: float = 120.0
    language: None = None                 # typed None: never settable (§2.2)
    prompt: None = None                   # typed None: never settable (§2.3)
    temperature: float = 0.0
    no_speech_prob_drop: float = 0.6
    avg_logprob_drop: float = -0.8
    compression_ratio_drop: float = 2.4
    repeat_run_drop: int = 4
    speech_ratio_skip: float = 0.08
    speech_seconds_skip: float = 4.0   # BOTH must hold to skip — §2.4
    speech_ratio_low: float = 0.15
    speech_seconds_low: float = 8.0
    chunk_target_s: float = 900.0
    chunk_overlap_s: float = 2.0
    def hash(self) -> str: ...

@dataclass(frozen=True, slots=True)
class Segment:
    id: int
    start: float
    end: float
    text: str
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None
    low_conf: bool = False

AsrQuality = Literal["ok", "low", "degenerate", "skipped_no_speech", "unavailable"]

@dataclass(frozen=True, slots=True)
class TranscriptResult:
    has_speech: bool
    quality: AsrQuality
    language_detected: str | None
    code_switch_suspected: bool | None
    duration_s: float | None
    speech_ratio: float | None
    text: str
    segments: list[Segment]
    words: list[dict] | None
    dropped_segment_count: int
    cost_usd: float
    raw: dict | None

class Transcriber(Protocol):
    async def transcribe(self, audio: AudioArtifact, cfg: AsrConfig) -> TranscriptResult: ...

def filter_segments(segs: Sequence[Segment],
                    cfg: AsrConfig) -> tuple[list[Segment], dict[str, int]]: ...
def render_transcript(segs: Sequence[Segment], *, duration_s: float | None,
                      quality: AsrQuality, min_segment_s: float = 1.0,
                      max_chars: int = 12_000) -> str: ...
def build_transcript_index(segs: Sequence[Segment]) -> "TranscriptIndex":
    """Interval index used by the §6.2 Check-B quote verifier."""
```

### 9.3 `fusion.py`

```python
@dataclass(frozen=True, slots=True)
class PromptInclusions:
    caption: bool = True
    hashtags: bool = True
    overlay_text: bool = True
    comments_n: int = 10
    comment_char_cap: int = 200
    comment_selection: Literal["platform_order_first_n"] = "platform_order_first_n"
    # Made explicit in review. Which 10 comments was previously unspecified, and the platform
    # default order is engagement-ranked — so every comment-derived label would have been drawn
    # from the most-liked comments and then reported as audience voice. The selection is
    # deterministic (first N in the order the collector received them), it is recorded in the
    # fingerprint, and CSR-12 forbids treating comment-anchored claims as representative.
    discovery_query: bool = False     # §4.3 / OQ-1 — default OFF
    market: bool = False              # §0.3 — default OFF

@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    id: str
    prompt_version: str
    config_fingerprint: str
    model: str = "claude-opus-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = 32_000
    thinking: Mapping[str, str] = MappingProxyType({"type": "adaptive"})
    # was `thinking: dict = None` — annotated as dict, defaulted to None, and mutable-by-type
    # inside a frozen dataclass; it is hashed into the config fingerprint so it must be both
    # non-None and immutable.
    schema_variant: Literal["full", "minimal"] = "full"
    list_cap: int = 6
    frame_policy: FramePolicy = FramePolicy()
    asr_config: AsrConfig = AsrConfig()
    inclusions: PromptInclusions = PromptInclusions()
    detail_pass_enabled: bool = True
    cache_ttl_interactive: Literal["5m", "1h"] = "5m"   # §3.4
    cache_ttl_batch: Literal["5m", "1h"] = "1h"

MediaTier = Literal["full_video", "audio_only", "thumbnail_only", "metadata_only"]
AnalysisStatus = Literal["ok", "refused", "refused_terminal", "truncated",
                         "schema_error", "media_error", "skipped", "deferred"]
# `deferred` added: §6b.4 puts exhausted-retry items back on the queue in that state and
# `analysis_attempts.outcome` already had it, but the status enum and the DDL check did not.

@dataclass(frozen=True, slots=True)
class UsageRecord:
    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    image_tokens: int
    cost_usd: float
    latency_ms: int
    anthropic_message_id: str | None
    stop_reason: str | None
    refusal_category: str | None

@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    status: AnalysisStatus
    media_tier: MediaTier
    analysis: "VideoAnalysis | None"
    usage: UsageRecord | None
    attempts: list[dict]
    raw_response: str | None
    detail_request: "DetailRequest | None"

class MultimodalAnalyzer:
    def __init__(self, llm: "anthropic.AsyncAnthropic", cfg: AnalysisConfig) -> None: ...

    def build_blocks(self, item: "NormalizedContentItem",
                     frames: Sequence[FrameArtifact],
                     transcript: TranscriptResult | None,
                     media_tier: MediaTier) -> list[dict]:
        """Returns the user content blocks in the exact §3.3 order."""

    async def analyze(self, item: "NormalizedContentItem",
                      frames: Sequence[FrameArtifact],
                      transcript: TranscriptResult | None,
                      media_tier: MediaTier,
                      *, allow_repair: bool = True,
                      allow_degrade: bool = True) -> AnalysisOutcome: ...

    async def submit_batch(self, jobs: Sequence["AnalysisJob"]) -> str:
        """custom_id = f'{content_item_id}:{analysis_config_id}'. Returns batch_id."""

    async def collect_batch(self, batch_id: str) -> "AsyncIterator[tuple[str, AnalysisOutcome]]":
        """Yields (custom_id, outcome). Results arrive unordered — key by custom_id."""

    async def detail_pass(self, item: "NormalizedContentItem", src: Path,
                          req: "DetailRequest",
                          frames: Sequence[FrameArtifact]) -> "DetailResult": ...
```

### 9.4 `calibration.py`

```python
CueCheck = Literal["pass", "low_conf", "vacuous", "unquotable", "anchor_invalid", "not_checked"]

@dataclass(frozen=True, slots=True)
class ClaimRecord:
    field: str
    ordinal: int
    value: str
    basis: str
    cue: str
    anchors: list[dict]
    model_confidence: float | None
    extra: dict

def check_cue(claim: ClaimRecord, *, n_frames: int, duration_s: float | None,
              transcript_index: "TranscriptIndex | None",
              text_blocks: dict[str, str],
              quote_containment_min: float = 0.85,    # §6.2 Check B (was Jaccard 0.60)
              quote_containment_low: float = 0.70,
              exact_match_below_chars: int = 12,
              min_cue_content_tokens: int = 3) -> CueCheck: ...  # §6.2 Check C (was ratio 0.80)

def claim_value(claim: ClaimRecord) -> str:
    """The field → value mapping Checks B and C compare against (§6.3). Part of
    validator_version: changing it changes results and must change the fingerprint."""

def support_features(claim: ClaimRecord, *, n_frames: int,
                     sampled_frame_times_s: Sequence[float],
                     thumbnail_frame_index: int | None,
                     duration_tier: int,
                     asr_quality: AsrQuality,
                     cue_check: CueCheck,
                     corroborated: bool) -> dict[str, float]: ...

FieldGroup = Literal["brand_identity", "product_use", "sponsorship",
                     "discovered_label", "descriptor"]
def field_group(field: str) -> FieldGroup: ...

class Calibrator(Protocol):
    method: Literal["logistic_l2", "rule_table"]
    def score(self, group: FieldGroup, feats: Mapping[str, float]) -> float: ...
    def threshold(self, group: FieldGroup) -> float: ...

@dataclass(frozen=True, slots=True)
class CalibrationFit:
    group: FieldGroup
    method: str
    feature_names: list[str]
    coefficients: dict[str, float]
    intercept: float
    n_train: int
    n_test: int
    ece: float
    brier: float
    auc: float
    threshold: float
    precision_at_threshold: float
    recall_at_threshold: float

def fit_calibrator(labels: Sequence[dict], claims: Sequence[ClaimRecord],
                   *, method: str = "logistic_l2", folds: int = 5,
                   target_precision: float = 0.90,
                   min_labels_per_group_full: int = 500,   # §6.3 per-group gate
                   min_labels_per_group_pooled: int = 200,
                   group_key: str = "content_item_id",     # GroupKFold — §6.6, mandatory
                   tau_selection_split: str = "validation" # never the test split — §6.6
                   ) -> list[CalibrationFit]: ...

def classify_claim(score: float, *, tau: float,
                   unverified_floor: float
                   ) -> Literal["published", "unverified", "rejected"]: ...
# No defaults: the pair is regime-dependent (uncalibrated 0.45 / 0.30, calibrated
# per-group-fitted / 0.40 — §6.4). A default here is how the corpus-gutting 0.60 happened.
```

### 9.5 Other modules

```
aypo/analysis/schema.py        # §5 Pydantic models (the versioned output contract)
aypo/analysis/prompts/mma_v1.py  # SYSTEM_PROMPT, USER_TEMPLATE, TASK_TAIL, DETAIL_SYSTEM,
                                 # PROMPT_VERSION  — frozen strings, byte-stable
aypo/analysis/validators.py    # §6.4 hard gates (tier caps, asr gates, summary-brand check)
aypo/analysis/idempotency.py   # config_fingerprint(), media_fingerprint(), canonical_json()
aypo/analysis/pipeline.py      # orchestration; the only module jobs/ imports
aypo/analysis/costing.py       # token→USD, per-call UsageRecord accounting
aypo/analysis/repo.py          # all SQL for the tables in §10
```

`pipeline.analyze_item(content_item_id, analysis_config_id) -> AnalysisOutcome` is the single
entry point the Postgres-backed queue calls. It is idempotent (§8.2), retry-safe, and never
raises for an analysis-level failure — only for infrastructure faults the queue should retry.

---

## 10. Owned DDL

Supabase Postgres. `content_items`, `research_runs`, `brands` and `content_embeddings` are owned
elsewhere and referenced only.

### 10.1 `analysis_configs`

```sql
create table analysis_configs (
  id                  uuid primary key default gen_random_uuid(),
  prompt_version      text        not null,
  config_fingerprint  char(64)    not null unique,
  model               text        not null,
  effort              text        not null check (effort in ('low','medium','high','xhigh','max')),
  max_tokens          integer     not null check (max_tokens between 1024 and 128000),
  tau                 jsonb       not null default '{}'::jsonb,   -- per field group (§6.4)
  unverified_floor    real        not null default 0.30,
  thinking            jsonb       not null default '{"type":"adaptive"}'::jsonb,
  schema_variant      text        not null default 'full'
                                  check (schema_variant in ('full','minimal')),
  list_cap            smallint    not null default 6 check (list_cap between 1 and 20),
  frame_policy        jsonb       not null,
  frame_policy_hash   char(64)    not null,
  asr_config          jsonb       not null,
  asr_config_hash     char(64)    not null,
  inclusions          jsonb       not null,
  detail_pass         jsonb       not null,
  validator_version   text        not null,
  system_prompt       text        not null,
  user_template       text        not null,
  task_tail           text        not null,
  output_json_schema  jsonb       not null,
  cache_ttl_interactive text      not null default '5m' check (cache_ttl_interactive in ('5m','1h')),
  cache_ttl_batch     text        not null default '1h' check (cache_ttl_batch in ('5m','1h')),
  is_active           boolean     not null default false,
  notes               text,
  created_at          timestamptz not null default now()
);

-- exactly one active config at a time
create unique index analysis_configs_single_active
  on analysis_configs ((is_active)) where is_active;
create index analysis_configs_prompt_version on analysis_configs (prompt_version);
```

### 10.2 `content_frames`

```sql
create table content_frames (
  id                 bigserial primary key,
  content_item_id    uuid      not null references content_items(id) on delete cascade,
  frame_policy_hash  char(64)  not null,
  frame_index        smallint  not null check (frame_index >= 0),
  t_s                numeric(10,3),
  width              smallint  not null,
  height             smallint  not null,
  image_tokens       smallint  not null,
  scene_score        real,
  selection_reason   text      not null check (selection_reason in
                       ('bucket_scene_max','bucket_midpoint','thumbnail',
                        'relevance_window','zoom_crop')),
  s3_key             text      not null,
  bytes              integer   not null,
  sha256             char(64)  not null,
  created_at         timestamptz not null default now(),
  unique (content_item_id, frame_policy_hash, frame_index)
);
create index content_frames_item on content_frames (content_item_id);
```

### 10.3 `content_transcripts`

```sql
create table content_transcripts (
  content_item_id       uuid        not null references content_items(id) on delete cascade,
  asr_config_hash       char(64)    not null,
  asr_model             text        not null,
  has_speech            boolean     not null,
  asr_quality           text        not null check (asr_quality in
                          ('ok','low','degenerate','skipped_no_speech','unavailable')),
  language_detected     text,                      -- free text; NEVER a constrained enum (§2.2)
  code_switch_suspected boolean,
  asr_review_queue      boolean     not null default false,   -- §2.2 translate-not-transcribe risk
  duration_s            numeric(10,3),
  speech_ratio          real,
  text                  text        not null default '',
  segments              jsonb       not null default '[]'::jsonb,
  words                 jsonb,
  dropped_segment_count smallint    not null default 0,
  chunk_count           smallint    not null default 1,
  cost_usd              numeric(10,6),
  created_at            timestamptz not null default now(),
  primary key (content_item_id, asr_config_hash)
);
create index content_transcripts_review on content_transcripts (asr_review_queue)
  where asr_review_queue;
```

### 10.4 `content_analysis`

```sql
create table content_analysis (
  id                     uuid        primary key default gen_random_uuid(),
  content_item_id        uuid        not null references content_items(id) on delete cascade,
  analysis_config_id     uuid        not null references analysis_configs(id),
  research_run_id        uuid        references research_runs(id),
  media_tier             text        not null check (media_tier in
                           ('full_video','audio_only','thumbnail_only','metadata_only')),
  degraded_from          text,
  status                 text        not null check (status in
                           ('ok','refused','refused_terminal','truncated',
                            'schema_error','media_error','skipped','deferred')),
  skip_reason            text,
  frame_policy_hash      char(64),
  asr_config_hash        char(64),
  media_fingerprint      char(64),
  frame_count            smallint,
  platform_thumbnail_present boolean not null default false,  -- §1.3 step 7: thumbnail
                           -- availability differs by platform and confounds any cross-platform
                           -- visibility comparison if it is not recorded
  midpoint_fraction      real,     -- §1.3 step 5: static-vs-cut-heavy proxy; §6.6 stratifies
  repaired_fields        text[]    not null default '{}',
  wall_clock_ms          integer,
  payload                jsonb,                   -- validated VideoAnalysis
  summary                text,
  summary_dropped        boolean     not null default false,
  evidence_insufficient_for text[]   not null default '{}',
  model_field_confidence jsonb       not null default '{}'::jsonb,
  stop_reason            text,
  refusal_category       text,
  refusal_explanation    text,
  input_tokens           integer,
  cache_read_tokens      integer,
  cache_creation_tokens  integer,
  output_tokens          integer,
  image_tokens           integer,
  cost_usd               numeric(10,6),
  latency_ms             integer,
  attempt_count          smallint    not null default 1,
  repaired               boolean     not null default false,
  anthropic_message_id   text,
  batch_id               text,
  raw_response           text,                    -- only on schema_error / truncated
  created_at             timestamptz not null default now()
);

create unique index content_analysis_idem
  on content_analysis (content_item_id, analysis_config_id, coalesce(media_fingerprint, ''));
create index content_analysis_item   on content_analysis (content_item_id);
create index content_analysis_run    on content_analysis (research_run_id, status);
create index content_analysis_status on content_analysis (analysis_config_id, status);
create index content_analysis_payload_gin on content_analysis using gin (payload jsonb_path_ops);

create view content_analysis_current as
select ca.*
from content_analysis ca
join analysis_configs ac on ac.id = ca.analysis_config_id
where ac.is_active;

-- Added in review (§8.2). An analysis row is created once per (item, config, media) and is
-- reused by later runs, so `content_analysis.research_run_id` records only the FIRST run that
-- produced it. Per-run denominators must read through this junction or they under-count every
-- run that reused an item — and brief §28 makes those denominators load-bearing.
create table content_analysis_runs (
  analysis_id     uuid not null references content_analysis(id) on delete cascade,
  research_run_id uuid not null references research_runs(id)    on delete cascade,
  first_used_at   timestamptz not null default now(),
  primary key (analysis_id, research_run_id)
);
create index content_analysis_runs_run on content_analysis_runs (research_run_id);

-- Referenced by §5.6 but never defined in the original draft: the flat brief-§20
-- `product_prominence` dict, reconstructed for API compatibility.
create view content_product_prominence as
select c.analysis_id,
       c.content_item_id,
       c.value_norm                                     as subject,
       jsonb_build_object('prominence',  c.extra->>'prominence',
                          'screen_share', c.extra->>'screen_share')  as prominence
from content_analysis_claims c
where c.field in ('visible_brands','visible_products')
  and c.claim_status = 'published';
```

### 10.5 `content_analysis_claims`

The normalised claim table is what makes brief §27 drill-down (finding → evidence → frame) and
§21 label-frequency analysis possible without unpacking JSONB on every query.

```sql
create table content_analysis_claims (
  id                    bigserial primary key,
  analysis_id           uuid     not null references content_analysis(id) on delete cascade,
  content_item_id       uuid     not null,
  analysis_config_id    uuid     not null,
  field                 text     not null,          -- 'visible_brands', 'behaviors', ...
  field_group           text     not null check (field_group in
                          ('brand_identity','product_use','sponsorship',
                           'discovered_label','descriptor')),
  ordinal               smallint not null,
  value                 text     not null,          -- verbatim as returned
  value_norm            text generated always as (lower(btrim(value))) stored,
  basis                 text     not null check (basis in ('seen','heard','written','inferred')),
  cue                   text     not null,
  anchors               jsonb    not null,
  modality_count        smallint not null,
  anchor_count          smallint not null,
  first_frame_index     smallint,
  t_start_s             numeric(10,3),
  cue_check             text     not null check (cue_check in
                          ('pass','low_conf','vacuous','unquotable',
                           'anchor_invalid','not_checked')),
  -- Added in review. Three evidence qualifiers downstream aggregation is required to respect
  -- (CSR-7, CSR-12) and that were previously invisible once a claim reached this table.
  frame_anchor_only       boolean not null default false,  -- Check A is a bounds check only
  thumbnail_only_anchored boolean not null default false,  -- cover image, not in-video evidence
  audience_authored       boolean not null default false,  -- anchored only in `comments`
  support_features      jsonb    not null,
  model_confidence      real,                       -- stored, NEVER displayed (§6.1)
  calibrated_confidence real,                       -- what the UI shows
  calibration_model_id  uuid,                       -- FK added after §10.7 (see note)
  claim_status          text     not null check (claim_status in
                          ('published','unverified','rejected')),
  extra                 jsonb    not null default '{}'::jsonb,
  created_at            timestamptz not null default now(),
  unique (analysis_id, field, ordinal)
);

create index claims_published_value
  on content_analysis_claims (field, value_norm)
  where claim_status = 'published';
create index claims_item          on content_analysis_claims (content_item_id);
create index claims_group_status  on content_analysis_claims (field_group, claim_status);
create index claims_conf          on content_analysis_claims (calibrated_confidence desc)
  where claim_status = 'published';
```

**Migration order note:** `calibration_model_id` references `calibration_models` (§10.7), which
is created later, so the constraint is added in a second statement after that table exists:

```sql
alter table content_analysis_claims
  add constraint claims_calibration_model_fk
  foreign key (calibration_model_id) references calibration_models(id);
```

### 10.6 `analysis_attempts` and `analysis_calls`

```sql
create table analysis_attempts (
  id              bigserial primary key,
  content_item_id uuid     not null,
  analysis_config_id uuid  not null,
  attempt_no      smallint not null,
  stage           text     not null check (stage in
                    ('retrieve','probe','audio','asr','frames','fusion',
                     'detail_pass','repair','validate')),
  outcome         text     not null check (outcome in
                    ('ok','degraded','refused','truncated','schema_error',
                     'transport_error','media_error','skipped','deferred')),
  detail          jsonb    not null default '{}'::jsonb,
  duration_ms     integer,
  created_at      timestamptz not null default now()
);
create index analysis_attempts_item on analysis_attempts (content_item_id, attempt_no);
create index analysis_attempts_outcome on analysis_attempts (outcome, created_at desc);

create table analysis_calls (
  id                 bigserial primary key,
  content_item_id    uuid,
  analysis_config_id uuid,
  provider           text not null check (provider in ('anthropic','openai','gemini')),
                     -- 'jina' removed: it appears nowhere else in this design
  purpose            text not null check (purpose in
                       ('fusion','detail_pass','repair','asr','asr_qa')),
  model              text not null,
  batch_id           text,
  input_tokens       integer,
  cache_read_tokens  integer,
  cache_creation_tokens integer,
  output_tokens      integer,
  image_tokens       integer,
  audio_seconds      numeric(10,2),
  cost_usd           numeric(10,6) not null,
  latency_ms         integer,
  ok                 boolean not null,
  error_class        text,
  created_at         timestamptz not null default now()
);
create index analysis_calls_cost on analysis_calls (created_at desc);
create index analysis_calls_run  on analysis_calls (analysis_config_id, purpose);
```

`analysis_calls` exists so the per-run cost figures in §7 are **measured**, not estimated, and so
the research-run view (brief §32) can show what a run actually spent.

### 10.7 Calibration tables

```sql
create table calibration_labels (
  id              bigserial primary key,
  claim_id        bigint   not null references content_analysis_claims(id) on delete cascade,
  content_item_id uuid     not null,
  field_group     text     not null,
  annotator       text     not null,
  verdict         text     not null check (verdict in
                    ('supported','unsupported','wrong','cannot_tell')),
  is_adjudicated  boolean  not null default false,
  notes           text,
  labelled_at     timestamptz not null default now(),
  unique (claim_id, annotator)
);
create index calibration_labels_group on calibration_labels (field_group, verdict);

create table calibration_models (
  id                     uuid primary key default gen_random_uuid(),
  field_group            text not null,
  method                 text not null check (method in ('logistic_l2','isotonic','rule_table')),
  analysis_config_id     uuid references analysis_configs(id),
  feature_names          text[] not null,
  coefficients           jsonb  not null,
  intercept              real,
  n_train                integer not null,
  n_test                 integer not null,
  n_labelled_videos      integer not null,
  cohens_kappa           real,
  ece                    real,
  brier                  real,
  auc                    real,
  threshold              real not null,
  precision_at_threshold real,
  recall_at_threshold    real,
  unsupported_claim_rate real,
  self_agreement_floor   real,          -- §6.6 per-item non-determinism floor (NOT a minimum
                                        -- detectable aggregate difference — see §6.6)
  n_labels_in_group      integer not null default 0,
  split_grouped_by       text not null default 'content_item_id',
  tau_selected_on        text not null default 'validation',
  ece_ci_low             real,
  ece_ci_high            real,
  raw_agreement          real,
  supported_prevalence   real,
  is_active              boolean not null default false,
  fitted_at              timestamptz not null default now()
);
create unique index calibration_models_active
  on calibration_models (field_group) where is_active;
```

---

## 11. Cross-subsystem requirements

Stated as contracts this design needs **from** other subsystems.

1. **CSR-1 — `NormalizedContentItem` contract.** `collectors/` must supply, per item:
   `id`, `platform`, `source_url`, `media_url | null`, `thumbnail_url | null`, `duration_s | null`,
   `caption | null`, `hashtags: list[str]`, `overlay_text | null`, `creator_handle | null`,
   `creator_follower_count | null`, `published_at | null`, engagement counts (`views`, `likes`,
   `comments_count`, `shares`, `saves`, each nullable), `comments: list[{text, author?, likes?}]`,
   `discovery_query`, `collection_provider`, `research_run_id`, `sampling_strategy`.
   Every field nullable except `id`, `platform`, `source_url`. Downstream analysis must never
   crash on a missing field (brief §15/§16).
2. **CSR-2 — media URL freshness.** Apify/CDN media URLs expire. `collectors/` must expose
   `re_resolve_media(content_item_id) -> str | None` so §6b.5 can refresh a 403 once without
   re-running discovery.
3. **CSR-3 — the job queue.** `jobs/` provides a Postgres-backed queue (no Redis, no Celery) with
   at-least-once delivery, per-item visibility timeout ≥ 300 s, a `deferred` state for §6b.4, and
   a concurrency knob. `pipeline.analyze_item` is idempotent so at-least-once is sufficient.
4. **CSR-4 — embeddings read from claims, not from JSONB.** `intelligence/` embeds the
   `summary` plus the `published` claim values from `content_analysis_claims`; it must filter
   `claim_status='published'` and must scope to one `analysis_config_id`. Mixing prompt
   generations inside one cluster makes the cluster uninterpretable (§8.3).
5. **CSR-5 — competitor discovery owns the word "competitor".** This subsystem emits only
   `brand_co_appearance` with `framing`. §23 must derive `competitor_relationships` from
   co-appearance plus cluster co-membership plus marketplace adjacency, and must record which of
   those produced each relationship.
6. **CSR-6 — brand resolution is downstream.** `brand_text_as_seen` is deliberately unnormalised
   (verbatim, possibly partial, possibly mis-cased). A separate resolver must map surface strings
   to `brands` rows with its own confidence, and must keep the surface string so a UI evidence
   view can show what was actually on the packaging. This design must **not** do fuzzy brand
   matching, because fuzzy matching against a known brand list is precisely the mechanism that
   would make the system "see" expected brands.
7. **CSR-7 — denominators must respect tiers, statuses and evidence qualifiers.** Any aggregate
   must publish the denominator it used, and must:
   - exclude `status in ('refused_terminal','schema_error','skipped','deferred')` **items** from
     the denominator, and surface the excluded count (never count a refusal as "no brands
     found", which biases every aggregate toward absence);
   - exclude `claim_status != 'published'` claims from the **numerator**, while leaving their
     items in the **denominator** as absence — and publish the weak-but-excluded count beside
     the sample size, so a reader can tell absence from uncertainty (§6.4);
   - exclude `media_tier='thumbnail_only'` items from motion/behaviour/activity aggregates;
   - exclude `thumbnail_only_anchored` claims from every **in-video** visibility, behaviour and
     consumption aggregate (the platform cover image is not a frame of the video — §1.3 step 7),
     while allowing them in item-level brand-presence counts under their own denominator;
   - **report per-run denominators through `content_analysis_runs`**, not through
     `content_analysis.research_run_id`, which names only the first run that produced the row
     (§8.2);
   - when comparing across platforms, condition on `platform_thumbnail_present`, since thumbnail
     availability differs systematically by platform and otherwise confounds the comparison.

   Brief §28 requires sample sizes; this is where they come from, and most of the bullets above
   exist because the first draft produced a *number* in each of these cases without producing a
   correct one.
8. **CSR-8 — the research engine must not pass hypotheses into analysis.** The research engine may
   use analysis output to generate queries, but must not supply a hypothesis, a brand of interest,
   a theme, or a market to `pipeline.analyze_item`. The only query-derived input permitted is the
   §1.6 relevance lexicon, which affects *frame selection only* and is never sent to the model.
9. **CSR-9 — UI evidence rendering.** Content Explorer (§31.5) must render, for each published
   claim: the cue, the basis badge (`seen`/`heard`/`written`/`inferred`), the anchored frame image
   from `content_frames.s3_key`, or the transcript segment with the quote highlighted. It must
   show `calibrated_confidence`, never `model_confidence`, and must show the "uncalibrated" badge
   while §6.5's rule table is in force.
10. **CSR-10 — commerce reuses the vision primitives, not this pipeline.** The commerce subsystem
    may import `media.image_tokens`, `media.crop_region` and the detail-pass prompt, but must own
    its own prompt and schema. Routing 2,500 commerce product images through a one-image analysis
    call would cost roughly \$0.020/record ≈ **\$50/run** (≈\$25 batched) on the same unit prices;
    that budget belongs to the commerce design, not here.
11. **CSR-11 — secrets and config.** `config.py` must expose `ANTHROPIC_API_KEY`,
    `OPENAI_API_KEY`, `GEMINI_API_KEY`, `S3_*`, and the analysis knobs as environment-driven
    settings. No credential in code; `APIFY_TOKEN` / `YOUTUBE_API_KEY` absence must not prevent
    analysis of items already collected. Concretely: with neither collection credential present,
    this subsystem still runs end-to-end on any item that already has a row in `content_items`
    and either a cached artefact set (§1.8) or a still-live `media_url`; items with neither
    degrade to `metadata_only` and are skipped (§1.1) rather than failing the run. No path in
    this design calls Apify or the YouTube API directly.
12. **CSR-12 — creator-authored and audience-authored evidence must not be pooled.** A brand or
    label anchored only in `comments` is something the *audience* said, not something the item
    shows or its creator said; `MentionedBrand.mention_kind='comment'` and
    `speaker_role='commenter'` already carry it, and `content_analysis_claims.audience_authored`
    now records it. Downstream must keep the two in separate denominators. Two reasons, both
    statistical: pooling turns "N items mention Brand A" into a number where some Ns are
    creators and some are commenters; and comment selection is platform-ranked
    (`comment_selection='platform_order_first_n'`, §9.3), so comment-derived labels are drawn
    from the most-engaged comments and are **not** a random sample of audience voice. Any
    audience-prevalence statement built on them must say so.
13. **CSR-13 — frame-only claims carry weaker verification than text-anchored ones.** §6.2's
    Check B genuinely verifies a quote; for a frame anchor, Check A is a bounds check. Any
    surface that presents "verified evidence" must not imply the two are equivalent, and §27
    findings resting entirely on `frame_anchor_only` claims should say so in the evidence panel.

---

## 12. Open questions

Genuine uncertainties. Each is a decision deferred to measurement, not a gap to be papered over.

- **OQ-1 — Should the discovery query (and/or market) be in the prompt?** Default is **off** on
  seeding grounds (§4.3). It is plausible that including it improves recall of genuinely present
  brands. Resolved by ablation A7 (§6.7) against the query-term leak rate. Until then, off.
- **OQ-2 — Does whisper-1 translate rather than transcribe code-switched speech often enough to
  matter?** No API flag suppresses this. Resolved by routing a 50-clip stratified sample through
  Gemini 2.x and measuring disagreement; if the rate is material, Gemini becomes the primary ASR
  for items flagged `code_switch_suspected` (which changes `asr_config` and therefore the
  fingerprint, and roughly doubles ASR cost — still under \$30/run).
- **OQ-3 — Is coarse 9-cell region localisation accurate enough to drive the detail crop?**
  If not, the fallback is 5 fixed crops (centre + four quadrants) at 1,024 tokens each, which
  raises the detail-pass cost ~5× (to ≈\$17/run at a 15% trigger rate) and is still affordable,
  but makes the trigger rate the thing to tune.
- **OQ-4 — The frame-count/accuracy curve is unmeasured.** 8 frames for the 20–60 s tier is a
  reasoned prior, not a measurement. Ablation A3 may well show 6 is sufficient (−\$12/run) or that
  12 is needed for `consumption_state` accuracy (+\$17/run).
- **OQ-5 — The non-determinism floor is unknown.** Until the 3× baseline repeat in §6.6 is run,
  no cross-version or cross-config comparison is interpretable. This is the single most important
  measurement to take first, because every other ablation result depends on it. **Note the
  corrected use:** the floor is a *per-item* statistic and enters aggregate comparisons as a
  within-config variance component in a paired bootstrap, not as a minimum detectable difference
  (§6.6).
- **OQ-6 — Do Message Batches actually achieve prompt-cache hits at our dispatch pattern?** Cache
  behaviour across a batch's internal scheduling is not something we control. If hit rates are
  near zero in batch, the system-prompt cost rises from ≈\$0.0012 to ≈\$0.012 per item
  (+≈\$16/run before the batch discount) — which does not change the batch recommendation, but it
  does mean the §7 cache line must be measured rather than assumed.
- **OQ-7 — SDK combination of `messages.parse(output_format=…)` with
  `output_config={"effort": …}`.** Needs a one-line check against the installed SDK; §5.7 gives
  the fallback.
- **OQ-8 — Should on-screen text get a dedicated cheap OCR pass?** Platform-supplied overlay text
  is unreliable and frames carry burnt-in text. Currently handled inside the fusion call. A
  dedicated pass would cost ~\$0.004/item (+\$6/run) and might materially improve §19B `slang`
  recall. Unmeasured.
- **OQ-9 — Should `thumbnail_only` items enter the corpus at all?** They cannot support any
  temporal claim, and including them biases every behaviour aggregate toward absence even with
  CSR-7's exclusions. The alternative is to analyse them but keep them in a wholly separate
  denominator used only for brand-presence counts. Needs a product decision once the real tier
  mix is known.
- **OQ-10 — Retention of derived media, and the personal data inside it.** Frames and audio
  extracted from platform media are stored indefinitely in §1.8 because every evidence view
  depends on them. The brief specifies neither a retention policy nor a platform-ToS position on
  storing derived media. Broadened in review, because retention is the smaller half: the stored
  artefacts are **images of identifiable people, their voices, their handles and their
  comments**, held indefinitely, under whatever data-protection regime governs the market named
  in the run configuration. The design's own guardrails cover the *inference* side well (no
  demographic attribution, §4.2 rule 6) and say nothing about the *storage* side. Before the
  first production run, someone outside engineering must set: a retention period and a deletion
  job; whether comment author identifiers are stored at all (the prompt payload already omits
  them — §4.3 passes comment text only, which should be made a stated requirement rather than an
  accident of the template); and a takedown/erasure path, since the evidence trail in §27 is
  built on durable links to specific people's content. **This is a release blocker, not a
  deferred question.**
- **OQ-11 — `LIST_CAP = 6` may be binding on long-form items.** A 10-minute video plausibly
  contains more than 6 anchorable behaviours. Options: scale `list_cap` with duration tier, or
  accept the loss and rely on `summary` overflow notes. Deferred until the longer-item mix is
  real. Note this is now also a *cost* question: because caps are not enforced at generation
  (§3.2), a binding cap costs a repair turn rather than a silent truncation, and the realised
  repair rate is the signal that the cap is binding.
- **OQ-12 — `scdet` metadata key, and the two scene-flat constants.** The metadata key
  (`lavfi.scd.score`) must be confirmed against the installed FFmpeg 8 build; the design parses
  defensively, falls back to `lavfi.scene_score`, and degrades to pure uniform sampling if no
  scores parse. Widened in review: `SCENE_FLAT_ABS = 0.08` and `SCENE_FLAT_REL = 3.0` (§1.3
  step 5) are unmeasured priors, and the original absolute-only threshold was justified against
  *full-rate* scores while the pass runs at `fps=6`. Calibrate both on a hand-labelled sample of
  20 static and 20 cut-heavy clips: the target is that `midpoint_fraction` is near 1.0 on the
  static set and near 0 on the cut-heavy set. Until then, treat `midpoint_fraction` as a
  diagnostic, not a feature to tune against.
- **OQ-13 — A declared crop tool, versus the one-shot detail pass and the effort lever.** Current
  Opus 5 vision guidance is that an iterative crop/zoom tool is a more cost-effective lever on
  vision-heavy work than raising thinking effort, because it spends test-time compute in image
  tokens rather than in thinking tokens — which are this design's largest cost line. §1.7's
  detail pass is an open-loop version of the same idea, gated on a 9-cell region the model
  nominates blind (OQ-3). The reasons for not adopting it now are in §3.4 and are about control
  flow and cost variance, not about caching. Measure as an ablation alongside A6: a single frozen
  crop tool versus detail-pass-on versus detail-pass-off, scored on `visible_brands` precision
  and recall and on per-item cost. If the tool wins it plausibly dominates **both** the detail
  pass and the effort lever, and the §7.5 ranking changes. A related, independent question:
  whether to send frames via the Files API by `file_id` instead of inline base64 (same token
  cost, far smaller requests, no image re-upload on a repair turn — §3.5).
- **OQ-14 — `screen_share` is an un-anchorable estimate.** `dominant/moderate/minor` is a
  quantity a human could measure from the frame and the model is guessing at, with no cue that
  distinguishes it from the `prominence` judgement sitting next to it. Either drop it, or compute
  it (a cheap saliency/box heuristic over the nominated frame) and stop asking the model.
  Unresolved; it is currently stored and should not be published on its own until it is.

---

## 13. Review notes

Adversarial review, 2026-10-02. Every claim in the original was checked against the brief and,
where it touched the API, against the current Anthropic API reference. The architecture survives:
one fusion call, frames-first ordering, anchors-and-cues on every claim, no brand/market/query in
the prompt, and `competitors` renamed to `brand_co_appearance` are all correct and are the
reasons this subsystem is a credible defence of brief §1/§34. What did not survive was a layer of
**numbers and API shapes that had never been executed**. Four mechanisms the document described
as its core safeguards were inoperative as written.

### 13.1 Fatal defects found and fixed

| # | Defect | Where | Fix |
|---|---|---|---|
| 1 | **Check B could not pass.** Quote verification used character-trigram **Jaccard ≥ 0.60** between a short quote and a long window. Jaccard divides by the union, so a *verbatim* 18-char quote in a 180-char window scores **0.106** (measured). Every transcript-, caption-, overlay- and comment-anchored claim in the corpus would have been rejected as `unquotable` — and reported as an evidence-quality finding, not as a bug. | §6.2 | Replaced with trigram **containment** (|∩|/|quote|, 1.00 for verbatim): pass ≥ 0.85, `low_conf` 0.70–0.85, reject < 0.70; exact substring below 12 chars; NFKC + casefold + whitespace normalisation specified. |
| 2 | **Check C could not fire.** Vacuity used `SequenceMatcher(cue, value).ratio() ≥ 0.80`, which is bounded by `2·min/(la+lb)` — max **0.25** for a 25-char label against a 140-char cue. The document's own worked example scores **0.426**, not "above 0.8". It was a length test, and for `SponsorCue` it was dead. | §6.2 | Replaced with a length-invariant token-set test: cue content tokens, minus structural hedges, minus the label's own tokens, must number ≥ 3. |
| 3 | **`likely_organic` was unreachable.** The gate required any non-`undetermined` bucket to carry a cue; every admissible cue kind was paid-leaning. Half of brief §19A's sponsored/organic axis was structurally unobtainable, and the absence would have read as "we see no organic content". | §4.2 r5, §5.4 | Two observable organic-leaning cue kinds added; gate requires one of them **and** no strong paid cue. |
| 4 | **The pre-calibration threshold gutted the corpus.** τ = 0.60 against a rule table whose base is 0.45 means the **modal** claim (one frame anchor, one modality, uncorroborated) is `unverified` and excluded from every cluster and finding. Survivors would be multi-modal claims — brands both seen and said — biasing the first corpus toward branded and sponsored content, in the exact direction brief §1 warns about, invisibly. | §6.4, §6.5 | Regime-dependent thresholds: uncalibrated τ = 0.45 / floor 0.30 with band display; calibrated τ fitted per group. Run quality report must publish the published/unverified/rejected split with its denominator. |
| 5 | **Calibration leaked and τ was selected on its own test set.** ~900 claim labels from 150 videos were treated as 900 independent observations; the split was not grouped by video, so siblings landed on both sides. Because τ is *chosen* as the lowest threshold hitting held-out precision ≥ 0.90, an inflated estimate sets τ too low — the system would publish below its stated precision bar while reporting it had met it. | §6.6 | `GroupKFold` on `content_item_id` everywhere; three-way split with τ frozen on validation and reported once on test; cluster-bootstrap intervals by video on every metric. |
| 6 | **Batch payload exceeded the 256 MB per-batch limit by ~5×** (1,500 items × 1–2 MB of base64 ≈ 1.2–1.5 GB) with no chunking rule; and `custom_id` as specified is **73 characters** against a 64-character limit, so the first submission fails outright. | §3.5 | Seal batches at 200 MB / 500 requests; `custom_id = f"{item.hex}:{fingerprint[:16]}"` (49 chars). Files-API alternative recorded as OQ-13. |
| 7 | **`field_confidence: dict[str, float]` is not a legal structured-output schema** — `additionalProperties` other than `false` is unsupported. The field would have been stripped empty or the request rejected. | §5.5 | Closed `list[FieldConfidence]`. |
| 8 | **The seek-variant FFmpeg command produced one frame, not N.** `-f mjpeg -y f_%03d.jpg` on a one-invocation-per-timestamp loop: the mjpeg muxer does not expand the pattern, so all N invocations wrote the same path. Every item over 120 s would have been analysed on its last frame alone. | §1.5e | Explicit per-timestamp output path; `-f image2`. |
| 9 | **`max_tokens = 12,000` was below the schema's own worst case.** "Worst-case valid output ≈3,100 tokens" is wrong by 4–8×: the caps admit ~134 claim objects, i.e. 10k–24k output tokens before thinking. Truncation was the normal path for a rich item, and the 24,000 retry was also below the bound. | §3.2, §6b.2 | Ceiling 32,000 (expected output and therefore the cost model unchanged); truncation retry 64,000; interactive path streams. |

### 13.2 Serious defects found and fixed

**Statistical.**
- The **non-determinism floor was to be used as a minimum detectable difference** on aggregate
  ablation metrics. It is a per-item statistic; aggregate standard errors shrink as 1/√n, so the
  rule would have discarded real effects and frozen every config at its unmeasured prior.
  Replaced with a paired cluster bootstrap over items with repeats as a variance component (§6.6).
- The **A2 tolerance ("within 2.0 macro-F1 points, because that is below annotator
  disagreement")** is a non sequitur: label noise attenuates absolute scores but largely cancels
  in a paired comparison against the same labels. Replaced with a corrected paired interval (§3.2).
- **No multiplicity control** across A1–A7 × 3 effort levels × 5 groups × 6 metrics, with
  pick-the-max selection rules throughout. Added: one pre-registered primary metric per ablation,
  Holm at family level, everything else labelled exploratory (§6.6).
- **Per-group fits contradicted the document's own `<500 labels` rule** (~180 per group). Gate is
  now per group, with a pooled reduced-feature fallback between 200 and 500 (§6.3).
- **ECE on 10 equal-width bins at ~180 points** is dominated by binning choice. Now 5 equal-mass
  bins with a bootstrap interval. **Cohen's κ alone** collapses under the skewed `supported`
  marginal expected here; now reported with raw agreement and prevalence (§6.6).
- **Stratification was a 72-cell crossing for 150 videos** (~2 per cell). Collapsed, with
  `media_tier` as a quota; added a `midpoint_fraction` stratum because frame selection — and
  therefore brand-read recall — is better on cut-heavy than on static clips, which correlates
  with production value, which correlates with sponsorship, which the product reports on.
- **"~6 claims/video, 12–20 annotator-hours"** was presented as observed and counted one pass.
  Restated as a pilot output, with the double-labelling-plus-adjudication multiplier (≈2.3×).
- **`f_frame_spread` was index-based**, so the same value meant 4 seconds on one tier and 9
  minutes on another, and a claim anchored on frame 0 plus the platform thumbnail scored maximum
  spread. Now time-based over sampled frames only, with a tier feature to absorb the rest (§6.3).

**Evidence integrity.**
- **The platform thumbnail was treated as a frame of the video.** On some platforms it is an
  arbitrary upload. It could replace a sampled frame (breaking the deterministic frame count the
  policy hash depends on), the prompt never said what `source=platform_thumbnail` meant, and its
  availability differs by platform — confounding every cross-platform visibility comparison with
  nothing recording the confound. Now additive only, explained in the prompt, flagged per claim
  (`thumbnail_only_anchored`), recorded per item, and excluded from in-video aggregates (§1.3, CSR-7).
- **Frame anchors are only bounds-checked**, yet §6.2 claimed the mechanic produces "a checkable
  pointer that checks out". Now stated honestly, with the free cross-field checks added
  (`frame_indices ⊇ anchor indices`, `first_seen == min`), an `f_frame_anchor_unverified`
  feature, A1 reported split by modality, and CSR-13.
- **Comment selection was unspecified** and the platform default is engagement-ranked, so
  audience-voice evidence would have been drawn from the most-liked comments and pooled with
  creator speech. Now deterministic, fingerprinted, flagged `audience_authored`, separated by CSR-12.
- **`research_run_id` was outside the idempotency key**, so an item reused by a second run had no
  analysis row attributable to it and every per-run denominator under-counted. Added
  `content_analysis_runs`.
- **The summary brand-check presupposed a brand detector** the subsystem may not have. Replaced
  with a computable provenance test over tokens absent from all supplied and emitted text.

**Media and feasibility.**
- **`fps=6` does not avoid the decode** (it filters after it), so the scene pass is not I/O-bound
  and the wall-clock estimate built on that is withdrawn; `-skip_frame nokey` added above 180 s.
- **`SCENE_FLAT = 0.08` was justified at full frame rate** but applied to `fps=6` scores, where
  within-shot motion inflates them — so a handheld single-take clip takes the "has a cut" branch
  everywhere and `argmax` selects the *most motion-blurred* frame in each bucket. Now a
  median-relative threshold with an absolute floor.
- **`round(t × avg_frame_rate)` is wrong for VFR** (common on mobile platform video), and
  `select` + `image2` numbers outputs in selection order, not by `frame_index` — a silent
  mis-mapping would attach every claim to the wrong image with no error anywhere. Both specified,
  with a `best_effort_timestamp` assertion and actual `t_s` stored and shown.
- **`silencedetect` never emits `silence_duration` for a silence running to EOF**, so a clip
  ending in 20 s of silence scored as speech.
- **`speech_ratio < 0.08` is duration-independent but was justified on a 45 s clip** — on a 600 s
  item it discards the audio channel of anything with under 48 s of speech. Now conjoined with an
  absolute `speech_seconds < 4.0`.
- **`media_fingerprint` over frame bytes is FFmpeg-build-dependent**, so a toolchain upgrade would
  have silently triggered a full paid re-analysis; and it contradicted §1.8, which already keys
  audio off source bytes. Moved to source bytes; `ffmpeg_build_id` added to the policy hash; the
  artefact-resolution order written down, with the honest admission that "zero downloads" and
  "detects a changed upstream file" cannot both hold by default.
- **The §1.6 relevance lexicon was not in `frame_policy_hash`**, so two runs with different
  lexicons would collide in the artefact cache and one run's evidence would be served to another.
- **The crop window was unclamped** (negative `x` on `top_left`).

**API and cost.**
- **Structured outputs do not enforce the cardinality caps.** `maxItems`/`maxLength` are stripped
  from the wire schema and validated client-side — so the caps are post-hoc, each overflow costs a
  repair turn, and, critically, **the prompt never stated the cap numbers**. A cap the model is
  not told cannot be respected. Numbers added to the prompt; repair budget 8% → 12%.
- **The batched total applied the 50% discount to interactive-only lines.** §3.5 routes the
  preview head, all detail passes, all repairs and all re-runs interactively. Recomputed:
  **≈\$113, not ≈\$98**; CHEAP ≈\$78, not ≈\$60. The **levers table mixed bases** (lever 1 batched,
  levers 2–6 interactive) so its ranking was not meaningful; all now on the batched base, with
  the non-additivity of lever 1 stated.
- **The 1-hour cache TTL rationale was wrong** on two counts: a cache read refreshes the timer, so
  continuous dispatch keeps a 5m entry warm indefinitely; and the cache is keyed on the prefix,
  not per worker. Now 5m interactive / 1h batch, the latter for the real reason (batch scheduling
  gaps, OQ-6).
- **"Tool definitions are the only change that forces a full cache rebuild"** overstates it: only
  *changing* them does, and a frozen tool list costs nothing per item. This matters because
  current Opus 5 guidance says a crop tool beats raising thinking on vision-heavy work — i.e. the
  mechanism the design declines may dominate both the detail pass and its largest cost lever.
  Recorded as OQ-13 rather than adopted, for control-flow reasons stated in §3.4.
- **The refusal ladder contradicted itself** on what happens to a batch refusal. One ordered table now.
- Verified correct and left alone: Opus 5 at \$5/\$25 with 0.1× reads and 2× 1h writes; the
  512-token cacheable minimum; images and message content leaving tools+system caches intact;
  `effort` and `thinking` needing to be pinned per config; the ~28×28-patch image formula and the
  2576 px / 4784-token ceiling; `stop_reason == "refusal"` arriving as HTTP 200 with
  `stop_details` null otherwise; `fallbacks` being rejected on Batches; the disabled-thinking
  failure modes; `messages.parse` → `parsed_output`; and that structured outputs **do** work with
  Batches and with extended thinking.

**Schema.**
- `Prominence` contained `handled`, an interaction value on a salience axis, so any aggregate over
  prominence conflated "visually dominant" with "touched". `format_features` was a bare string
  list with no cue, anchor or basis — an open hallucination channel bypassing the entire mechanic.
  `code_switching_observed` was a free boolean that could contradict `languages`; now derived.
  `shelf_or_menu_label` leaned food-service. `pinned_or_authored_brand_comment` required brand
  knowledge the design forbids itself; given an observable definition. The field → `value` mapping
  that Checks B and C depend on was never specified; now enumerated and versioned. The
  `content_product_prominence` view referenced by §5.6 did not exist. `AnalysisStatus` lacked
  `deferred`. `thinking: dict = None` was mis-typed and mutable inside a frozen dataclass.
  `'jina'` was a stray provider.

### 13.3 Seeding review (brief §1, §34)

No prohibited content was found: no brand name, no Philippine or snack reference, no occasion or
behaviour vocabulary, no slang example, no competitor list, no market string in any prompt,
schema default, threshold or fixture. The discovery query and locale remain off by default and
flagged (OQ-1), and the §1.6 relevance lexicon still touches only *where to look*. Three
borderline items were kept and are now **declared** in §0.3 rather than left implicit, so a
reviewer can object to them on the record: the `ConsumptionState` ladder (a physical-interaction
ordinal that nonetheless presumes a packaged, manipulable artefact — brief §28 requires the
distinction and free text cannot supply an ordering); the `SponsorCueKind` enum (each member an
observable artefact); and §1.3's framing of the frame-selection prior in terms of "packaging
text", softened to object presentation and routed to ablation. One apparent violation is
defensible and now carries its justification: the single `language == "en"` check in §2.2 is a
property of **whisper's** translation behaviour, not a hypothesis about the corpus, and sets a QA
flag that filters nothing.

### 13.4 Residual risk

1. **Every number in §7 remains an estimate.** The `count_tokens` verification §7.6 mandates has
   not been run, and the repair rate, thumbnail rate, detail-pass trigger rate and realised
   claims-per-item are all priors. Treat ≈\$113 as ±30% until the pilot wave.
2. **OQ-5 still gates everything.** No ablation result is interpretable before the 3× baseline
   repeat, and the corrected paired-bootstrap procedure has not been exercised.
3. **Frame-anchored claims remain weakly verified.** The added cross-field checks catch
   self-inconsistency, not fabrication. Nothing short of a second look at the pixels can confirm
   that frame 4 contains what the claim says, which is part of why OQ-13 matters.
4. **The uncalibrated regime will be the live regime for weeks.** Until each field group clears
   200 adjudicated labels, confidence is a hand-written rule table with a 0.85 clamp. The badge
   and the band are honest, but any finding produced in this window rests on uncalibrated
   evidence weights and should be re-scored once the calibrators exist.
5. **Thumbnail-only items (OQ-9) and the §1.6 relevance windows both make items non-comparable**
   in ways CSR-7 mitigates rather than removes: a long item analysed under two different query
   lexicons has two different evidence bases, and no aggregate currently says so.
6. **OQ-10 is a release blocker, not an open question.** The pipeline stores images of
   identifiable people, their voices and their comments indefinitely, with no retention period,
   no deletion job and no erasure path, under an unspecified data-protection regime.
7. **The `midpoint_fraction` bias is measured but not corrected.** If brand-read recall really is
   higher on cut-heavy production, every cross-brand visibility comparison inherits a production
   -value confound, and the only honest response downstream is to stratify on it — which §22/§24
   do not yet know they need to do.
