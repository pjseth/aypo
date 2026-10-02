# AYPO

**Social, cultural, competitive and commerce intelligence for Southeast Asia.**

An AI market researcher, not a listening dashboard. It collects public
social/video content and marketplace data, analyses the video itself with
multimodal AI, and produces findings that each link back to the content that
supports them.

First research subject: **Piattos** (Universal Robina Corporation), Philippines.

> **Piattos is the research subject, not a predetermined answer.** The system
> encodes no assumptions about its consumers, occasions, competitors, cultural
> associations or content formats. Those are discovered from collected data, and
> the point of the product is to find something nobody told it. See
> [`docs/BRIEF.md`](docs/BRIEF.md) §1 and §34 — these are binding on all code,
> prompts, taxonomy, fixtures and UI defaults.

---

## What makes it different

Conventional social listening reads captions, hashtags, mentions and text
sentiment. This reads **what is actually happening inside the video** — what is
visible, how a product is being used, who is using it and in what situation,
what is being said and in which language, what the humour and cultural
references are — and then connects those signals to marketplace behaviour.

Three commitments shape every surface:

| | |
|---|---|
| **Signal** | What appears to be happening. |
| **Context** | Why it might matter, who drives it, where it appears, whether it is moving, how the subject brand compares. |
| **Evidence** | The actual videos, posts, listings and reviews — clickable, with sample size. |

Every claim is labelled by how it was derived — **observed** (in the data),
**computed** (by code), **interpreted** (by a model), or **hypothesis** (not yet
established) — and carries a confidence band that includes an explicit
*insufficient data* state. If the evidence is weak, the product says so.

---

## Architecture

```
apps/web      Next.js 16 · React 19 · TypeScript · Tailwind 4      the eight product surfaces
apps/api      Python 3.13 · FastAPI · asyncpg                      research engine + API
supabase/     SQL migrations                                       Postgres + pgvector schema
docs/         BRIEF · ARCHITECTURE · ENVIRONMENT · design/         normative spec and design record
```

Inside `apps/api/aypo/`:

| Package | Responsibility |
|---|---|
| `config.py` | all credentials and tunables; computes which sources are usable |
| `llm/` | the only module that touches the Anthropic SDK; cost ledger |
| `providers/` | transport to Apify, web search, embeddings, transcription, storage |
| `collectors/` | per-platform discovery + retrieval, normalising to internal objects |
| `analysis/` | FFmpeg, frame sampling, transcription, multimodal analysis, embeddings |
| `intelligence/` | clustering, trends, competitors, commerce linking, findings |
| `research/` | the agentic loop: query generation, hypotheses, stopping conditions |
| `jobs/` | Postgres-backed queue and worker |
| `api/` | FastAPI routers |

**Collection is provider-independent by design.** Downstream analysis only ever
sees normalised internal objects, never an Apify response shape, so a managed
collector can be swapped for a licensed data feed or a direct API without
touching analysis.

---

## Getting started

### Prerequisites

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- Node 22+ and `pnpm`
- PostgreSQL 17 with the `pgvector` extension
- FFmpeg

```bash
brew install postgresql@17 pgvector ffmpeg
brew services start postgresql@17
createdb aypo
psql -d aypo -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

### Configure

```bash
cp .env.example .env
```

Fill in `.env`. Nothing is hard-coded; every provider reads from here, and a
provider without a credential reports itself unavailable rather than crashing
the run.

| Variable | Needed for | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | all analysis and reasoning | the engine cannot run |
| `OPENAI_API_KEY` | embeddings + transcription | no clustering, no semantic search, no audio signal |
| `APIFY_TOKEN` | TikTok, Instagram, Facebook, Shopee, Lazada | those five sources are skipped |
| `YOUTUBE_API_KEY` | YouTube | YouTube is skipped |
| `SERPER_API_KEY` | public-web discovery | no web discovery fallback |
| `DATABASE_URL` | everything | nothing persists |

`GET /health` reports exactly which capabilities are live and names the
environment variables needed to enable the rest.

### Install and run

```bash
pnpm install                      # web dependencies
cd apps/api && uv sync --group dev # api dependencies

pnpm db:migrate                   # apply SQL migrations
pnpm api                          # FastAPI on :8000
pnpm worker                       # background worker (separate terminal)
pnpm dev                          # Next.js on :3000
```

### Start a research run

```bash
cd apps/api

# See what a run would cost and cover, without spending anything
uv run python -m aypo.research --brand "Piattos" --terms "snacks,chips" --dry-run --yes

# Run it
uv run python -m aypo.research --brand "Piattos" --terms "snacks,chips" \
    --collection-usd 5 --iterations 2
```

The plan and its estimated spend are printed before anything is collected, and
a run is confirmed interactively unless `--yes` is passed. The same thing is
available over HTTP as `POST /api/runs/preview` and `POST /api/runs`.

**Two separate meters.** `--collection-usd` caps managed-collection spend
(Apify, a hard monthly cap on that account) and is checked against *live*
provider usage rather than a local tally, because pay-per-event billing lags.
`--llm-usd` caps model spend, which is billed separately. A run stops on
whichever ceiling binds first and always records which one.

### Checks

```bash
pnpm api:test        # pytest
pnpm api:lint        # ruff
pnpm typecheck       # tsc
pnpm build           # next build
```

---

## The eight surfaces

| Route | Answers |
|---|---|
| `/` **Cultural Pulse** | What is happening right now, and what changed? |
| `/landscape` **Consumer & Cultural** | What behaviours, occasions and cultural signals appear? |
| `/competition` **Competitive** | Which brands were discovered, and how do they differ? |
| `/trends` **Trends** | What is gaining momentum, and where is the gap? |
| `/content` **Content Explorer** | What is actually in each video? |
| `/commerce` **Commerce Explorer** | What does the marketplace look like for these themes? |
| `/ask` **Ask the Data** | Ask a question, get a cited answer. |
| `/runs` **Research Runs** | How did the system reach its conclusions? |

What appears inside a surface is never hard-coded. The cards are whatever the
data supports.

---

## How a research run works

```
objective
   ↓
broad queries           brand-level, category-level — deliberately mixed so the
   ↓                    corpus is not mostly brand-owned content
collect                 TikTok · YouTube · Instagram · Facebook · web · Shopee · Lazada
   ↓
multimodal analysis     frames + audio + text + context fused per item
   ↓
patterns                open-ended labels, canonicalised across the corpus
   ↓
hypotheses              each with what would confirm or refute it
   ↓
follow-up queries       competitor-level, theme-level, hypothesis-testing
   ↓
collect more            ... loop until a stopping condition is reached
   ↓
cluster · trends · competitors · commerce links
   ↓
findings                evidence-linked, confidence-scored, adversarially checked
```

A run stops when its budget is exhausted, new searches stop returning anything
novel, cluster structure stabilises, the competitor set stabilises, or no
meaningful new hypotheses are produced. **The reason it stopped is recorded**,
and the whole chain is replayable in the Research Run view.

---

## Guardrails

These are enforced in code and surfaced in the UI, not left to good intentions:

- Sample sizes are always shown; a sample proportion never reads as a
  market-wide fact.
- Growth and decline are only claimed with sufficient temporal coverage —
  because a corpus collected at one moment has publication dates shaped by
  platform ranking, which will manufacture false growth if counted naively.
- Visibility is not endorsement; engagement is not preference; positive
  comments are not sales.
- Incidental product appearance is distinguished from active consumption.
- Sponsored and organic content are distinguished where observable.
- Numbers are computed by code, never written by a model; a finding citing
  evidence that does not exist or does not support it is rejected before it is
  ever shown.
- Conflicting evidence is surfaced on the finding, not suppressed.
- Competitors are not given arbitrary overall scores.

---

## Documentation

| | |
|---|---|
| [`docs/BRIEF.md`](docs/BRIEF.md) | the normative product brief — binding |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | consolidated system architecture and cross-module contracts |
| [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) | verified facts measured on this machine — trust over recollection |
| [`docs/design/`](docs/design) | the seven subsystem designs, each adversarially reviewed |
