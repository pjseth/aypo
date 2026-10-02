# AYPO — Social, Cultural, Competitive & Commerce Intelligence for Southeast Asia

> This file is the **normative product brief**. Every agent working on this repo MUST
> read §1 and §34 before writing code, prompts, taxonomy, fixtures, or UI defaults.

First customer: **Universal Robina Corporation (URC)**
First brand under research: **Piattos**
First market: **Philippines**

The product analyses public social/video content and marketplace data with multimodal AI
to explain what is actually happening around a brand and its category. It goes beyond
traditional social listening, which leans on captions, OCR, hashtags, mentions and text
sentiment. It should behave like **an AI market researcher**, not a listening dashboard.

Core questions it must answer from data:
what is actually happening inside videos; what products and brands are visible; how
products are being consumed; consumer situations and behaviours; creator and audience
contexts; cultural references; humour; language and slang; content formats; emerging
trends; competitor activity; product and marketplace behaviour; and where social/cultural
signals intersect with commerce.

---

## 1. CRITICAL RESEARCH PRINCIPLE — DISCOVERY, NOT ASSUMPTION

Do **not** hard-code assumptions about: Piattos; Piattos consumers; Piattos marketing
strategy; Piattos audiences; Piattos consumption occasions; Piattos cultural
associations; Piattos content formats; Piattos competitors; Philippine snack culture;
emerging trends.

The system discovers these from data.

Do **not** begin with predefined findings such as gaming, school, parties, barkada,
office snacking, etc. merely because they sound plausible. Do **not** define the main
competitors in advance unless the user explicitly supplies optional seeds.

The platform must be capable of discovering something the URC marketing team did not
already tell it.

The research loop works approximately like this:

1. Begin with broad research around Piattos and the Philippine snack category.
2. Collect an initial sample of social/video and marketplace content.
3. Analyse that content.
4. Identify recurring behaviours, contexts, products, competitors, creator types,
   language, phrases, cultural signals, and unexpected patterns.
5. Form research hypotheses from those discoveries.
6. Generate follow-up searches automatically.
7. Collect more relevant content.
8. Compare Piattos against newly discovered competitors and category behaviour.
9. Test whether initial patterns generalise beyond the seed sample.
10. Surface evidence-backed findings with confidence and sample size.

**Do not seed the answer the product is supposed to discover.**

---

## 2. PRODUCT GOAL

The MVP must help URC answer:

1. How and in what contexts are consumers actually interacting with Piattos?
2. What social, cultural, creative and consumer behaviours are emerging around Piattos
   and the broader snack category?
3. How is Piattos positioned relative to competitors?
4. Which competitors are becoming associated with emerging themes?
5. What is changing in the category?
6. Where does Piattos appear over- or under-represented?
7. Which content formats, cultural signals, consumer behaviours or product themes appear
   to be gaining momentum?
8. Are social trends also visible in marketplace behaviour?
9. What should the marketing team investigate further?
10. What actual content supports each conclusion?

It must be useful enough to inform: campaign briefs; consumer research; creative
strategy; competitive reviews; positioning discussions; trend monitoring; category
reviews; weekly and quarterly marketing reviews.

---

## 3. CORE PRODUCT PHILOSOPHY — SIGNAL / CONTEXT / EVIDENCE

Every important finding has three layers.

**SIGNAL** — what appears to be happening?

**CONTEXT** — why might it matter? who or what is driving it? where is it appearing? is
it increasing, declining or stable? how does Piattos compare with competitors?

**EVIDENCE** — the actual social posts, videos, marketplace listings, reviews, data
points or clusters supporting the finding.

The system must always distinguish between: directly observed data; calculated patterns;
model interpretation; hypotheses requiring more investigation.

**Do not present unsupported AI recommendations as facts.**

---

## 4. MVP SCOPE

Intelligence categories: social video intelligence; cultural intelligence; consumer
behaviour; competitive intelligence; creative intelligence; trend detection; marketplace
intelligence; social-to-commerce relationships.

Sources: TikTok; YouTube / YouTube Shorts; Instagram public content where accessible;
Facebook public content where accessible; Shopee Philippines; Lazada Philippines; general
public web/search discovery.

The MVP must operate **without manual or semi-manual ingestion**. The research process is
automated end-to-end.

---

## 5. AUTOMATED DATA COLLECTION

Modular automated collection architecture. Prefer, in order:

1. official APIs where suitable
2. Apify actors / managed public-data collectors
3. web search and indexed public-page discovery

Do **not** spend MVP engineering effort on: CAPTCHA bypasses; rotating proxy systems;
fingerprint evasion; anti-bot circumvention; session/account farms; custom scraping
infrastructure where an existing managed collector is sufficient.

Apify is the primary managed collection layer for sources without suitable official APIs.
The collection system must be **provider-independent** so Apify can later be replaced by
licensed data providers, direct APIs or first-party integrations.

---

## 6. TARGET DATASET (approximate research targets, not strict quotas)

- TikTok: ~800–1,200 relevant videos
- YouTube / Shorts: ~300–500 relevant videos
- Instagram / Facebook / public web: ~200–400 relevant posts/videos where available
- Shopee PH: ~750–1,500 product/listing/review records
- Lazada PH: ~500–1,000 product/listing/review records

Do **not** blindly collect until a quota is reached. Prioritise diversity, relevance,
recency and coverage.

---

## 7. DISCOVERY VS RETRIEVAL

**Discovery** = finding potentially relevant URLs/content.
**Retrieval** = obtaining media, metadata, captions, marketplace data, reviews, or other
content necessary for analysis.

Store provenance for every item: platform; source URL; discovery query; collection
provider; date discovered; publication date where available; research run ID; research
hypothesis ID if relevant; collection method.

If a platform becomes unavailable, degrade gracefully and continue through other
available sources.

---

## 8. INITIAL QUERY STRATEGY

Do not only search for Piattos. The sample must contain several kinds of queries:

- **A. Brand-level** — broad Piattos-related searches.
- **B. Category-level** — broad Philippine snacks, chips, snacking and adjacent category
  searches. These need not mention Piattos.
- **C. Competitor-level** — generated only *after* relevant competitors begin to emerge.
- **D. Theme-level** — generated from themes discovered in the data. May relate to
  behaviours, consumer situations, creator formats, slang, cultural references, products,
  occasions, activities, visual patterns, flavour themes, audience contexts. **Do not
  predefine those themes.**
- **E. Hypothesis-testing** — queries generated specifically to test whether an observed
  pattern appears elsewhere.

The research corpus must be deliberately mixed so it does **not** consist mostly of
Piattos-branded content. Track which sampling strategy generated every content item.

---

## 9. AGENTIC RESEARCH SYSTEM

A typical research run:

research objective → generate broad initial queries → collect initial social + marketplace
data → run multimodal/content analysis → detect recurring patterns → generate hypotheses →
generate follow-up queries → collect additional evidence → discover competitors → compare
Piattos and competitors → investigate commerce relationships → produce evidence-backed
findings → repeat until stopping condition / research budget is reached.

The agent maintains a research log containing: query; why the query was generated; which
finding or hypothesis triggered it; source searched; number of results; relevant results;
follow-up hypotheses; final supporting evidence.

**The research process must be auditable.**

---

## 10. COLLECTION ARCHITECTURE

Provider-independent collector interface:

```python
class Collector:
    async def search(self, query, filters): ...
    async def fetch(self, item): ...
    async def normalize(self, raw_item): ...
```

Implement: TikTokCollector, YouTubeCollector, InstagramCollector, FacebookCollector,
WebSearchCollector, ShopeeCollector, LazadaCollector.

Downstream analysis operates **only on normalised internal objects**. Do not couple
analytics directly to Apify response structures.

---

## 11. APIFY INTEGRATION

```python
class ApifyProvider:
    async def run_actor(self, actor_id, input_data): ...
    async def get_dataset(self, run_id): ...
```

Env: `APIFY_TOKEN`. Do not hard-code credentials. Actor IDs must be configurable by
environment or configuration file, because individual third-party actors may change:
`APIFY_TIKTOK_ACTOR`, `APIFY_INSTAGRAM_ACTOR`, `APIFY_FACEBOOK_ACTOR`,
`APIFY_SHOPEE_ACTOR`, `APIFY_LAZADA_ACTOR`.

---

## 12. TIKTOK COLLECTION

TikTokCollector via a suitable Apify actor. Support automatic search/discovery by
keyword, hashtag, creator, relevant sound/music if discovered, and discovered themes.

Collect where available: video ID; URL; video/media URL; caption; hashtags; creator;
creator metadata; publication date; views; likes; comments count; shares; saves if
available; sound/music metadata; comments where appropriate; thumbnail.

Search begins broadly. The research agent generates new TikTok searches from discovered
patterns. **Do not hard-code what those patterns are.**

---

## 13. YOUTUBE COLLECTION

Use the official YouTube Data API where practical. Support keyword search; date
filtering; region relevance for the Philippines where supported; channel information;
video metadata; engagement; thumbnails; URLs.

Prefer short-form video where appropriate, but **do not make the architecture depend on
an unreliable "Shorts" classification.** Env: `YOUTUBE_API_KEY`.

---

## 14. INSTAGRAM / FACEBOOK COLLECTION

Useful but **non-critical** automated sources. Use Apify or public-web discovery where
technically reliable. Collect where available: URL; creator/page; caption; date;
engagement; hashtags; media; thumbnail; comments; public page metadata.

If one of these sources fails, the research run continues without blocking.

---

## 15. SHOPEE PHILIPPINES COLLECTION

ShopeeCollector via a suitable Apify actor. The research agent automatically generates
marketplace searches from broad snack/category searches; discovered competitors;
discovered product themes; discovered flavours; and discovered cultural/consumer signals
where commercially relevant.

Collect where available: product title; brand; seller; price; original price; discount;
rating; review count; sold count / sales proxy; category; product image; product URL;
variant information; seller information; review text where available; review dates; the
search query that found the listing.

Do not assume every field exists. Normalise missing data safely.

---

## 16. LAZADA PHILIPPINES COLLECTION

LazadaCollector with a similar schema: product title; brand; seller; price; original
price; discount; rating; review count; units sold where available; category; image;
product URL; variants; review text; review dates; search query.

Missing fields must not break analysis.

---

## 17. SOCIAL-TO-COMMERCE INTELLIGENCE

Marketplace data must **not** be siloed from social analysis. This is an important
differentiating capability.

When the research agent discovers a meaningful theme in social data, it decides whether
corresponding commerce research could validate or contextualise it.

```
Social content → emergent theme X → search broader social content for X →
search Shopee for X → search Lazada for X → identify associated products/brands →
compare Piattos presence → generate evidence-backed finding
```

**Do not hard-code X.** The system should be capable of identifying relationships such
as: social theme → product listings; social theme → competitor products; social theme →
new flavours; social theme → search/category behaviour; competitor social momentum →
marketplace visibility; consumer language → product positioning.

**Do not claim causality unless evidence genuinely supports it.**

---

## 18. MULTIMODAL VIDEO ANALYSIS

For every video, analyse four categories.

**VISUAL** — visible brands; visible products; packaging; product variants; people;
setting/environment; activities; product usage; consumption behaviour; social context;
visual aesthetic; content structure; product prominence; competitor co-appearance;
notable objects; notable visual patterns. **Do not reduce video analysis to OCR.**

**AUDIO** — transcription; spoken brand/product references; language; code-switching;
tone; slang; humour cues; relevant music/sound context.

**TEXT** — caption; hashtags; text overlay; OCR where useful; comments where available.

**CONTEXT** — creator; publication date; platform; engagement; discovery query; location
information where explicitly available; collection source; research run.

Fuse these into one structured representation.

---

## 19. DYNAMIC CONTENT REPRESENTATION — HYBRID SCHEMA

**A. Stable fields** (likely to exist across content): brands visible; products visible;
category; language; content format; sentiment/tone; purchase intent; product prominence;
creator type; sponsored/organic likelihood; engagement metadata.

**B. Open-ended discovered fields** — the model may propose labels such as: consumer
occasion; behaviour; social situation; cultural reference; humour style; slang; emerging
theme; unexpected observation; product pairing; audience signal; narrative; creator
archetype; commercial signal.

**Do not force every item into a rigid predefined taxonomy.** Frequently recurring
discovered labels may later become normalised taxonomy entries.

---

## 20. STRUCTURED ANALYSIS OUTPUT (conceptual)

```json
{
  "visible_brands": [], "visible_products": [], "product_prominence": {},
  "content_format": null, "creator_archetype": null, "languages": [],
  "sentiment": null, "purchase_intent": null, "sponsored_likelihood": null,
  "occasions": [], "behaviors": [], "social_context": [], "cultural_signals": [],
  "slang": [], "humor_signals": [], "competitors": [], "activities": [],
  "product_pairings": [], "unexpected_observations": [], "summary": "", "confidence": {}
}
```

**Avoid generating fields when evidence is weak.**

---

## 21. CONTENT CLUSTERING

After analysing sufficient content: (1) create embeddings; (2) cluster related content;
(3) **inspect the cluster**; (4) only *then* assign a human-readable label.

Discover clusters involving consumer behaviours; occasions; visual formats; creator
styles; memes; language; cultural signals; brand associations; competitive contexts;
product usage; marketplace themes.

**Do not name clusters before analysing their members.**

Per cluster store: ID; generated name; description; representative examples; size;
Piattos presence; competitor presence; engagement characteristics; first observed date;
latest observed date; growth/decline if statistically meaningful; confidence; embedding
centroid.

Allow content to belong to multiple thematic groups if appropriate.

---

## 22. TREND DETECTION

A trend is not simply a popular hashtag. Possible trends include emerging behaviour;
emerging consumption context; new visual format; recurring cultural reference; new
creator behaviour; meme; flavour/product theme; competitor positioning shift; changing
audience association; marketplace movement; social-commerce signal.

Classify themes where enough data exists as: established; growing; emerging; weak
signal; stable; declining.

**Only make growth or decline claims when temporal coverage is sufficient.**

---

## 23. COMPETITOR DISCOVERY

Dynamic. Detect competitor relationships through direct comparison videos; product
co-appearance; shared content clusters; category searches; marketplace adjacency;
recurring brand mentions; consumer substitution language; ranking/review content; similar
product positioning; shared creator ecosystems; shared cultural associations.

```
competitor_relationships
  brand_id, competitor_brand_id, relationship_type, evidence_count, confidence,
  first_detected, last_detected, supporting_content_ids
```

An optional competitor seed list may exist, but it must **not** prevent discovery of
additional competitors.

---

## 24. COMPETITIVE ANALYSIS

Compare Piattos and discovered competitors across consumer contexts; consumption
behaviours; creative formats; product visibility; creator types; organic/sponsored
presence; cultural themes; social contexts; engagement; visual share within the analysed
sample; marketplace presence; price positioning; product variants; content clusters;
trend participation; emerging themes; category associations.

**Do not create simplistic overall competitor scores.** Prefer statements such as:
"Within the analysed sample, Brand A appears more frequently in Theme X than Piattos." or
"Piattos has relatively low presence within Cluster Y compared with several competitors."

**Always scope conclusions to the dataset.**

---

## 25. RESEARCH HYPOTHESIS ENGINE

A hypothesis includes: statement; reason generated; supporting initial evidence;
confidence; what evidence would confirm/refute it; follow-up queries; relevant platforms;
status.

```json
{
  "statement": "Theme X may be more strongly associated with Brand A than Piattos.",
  "reason": "Initial cluster analysis showed repeated Brand A presence.",
  "follow_up_plan": [
    "Search Theme X category content", "Search Piattos + Theme X",
    "Search Brand A + Theme X", "Search relevant marketplace terms"
  ]
}
```

**Do not hard-code the hypothesis content.**

---

## 26. RESEARCH STOPPING CONDITIONS

Avoid endless autonomous searching. A run stops when one or more conditions are reached:
research budget exhausted; configured content count reached; new searches produce highly
repetitive results; cluster structure stabilises; competitor set stabilises; no meaningful
new hypotheses are generated; marginal new evidence becomes low; user-specified research
depth reached.

**Store the reason a run stopped.**

---

## 27. EVIDENCE SYSTEM

Every finding must link directly to supporting evidence. Tables: `findings`,
`finding_evidence`.

A finding includes: claim; type; confidence; sample size; generated date; supporting
cluster IDs; supporting content IDs; supporting marketplace records; competing evidence
where relevant; interpretation; research run ID.

The UI must allow a marketer to click: finding → supporting evidence → actual
videos/posts/listings.

**The product must never be a black box.**

---

## 28. DATA QUALITY / GUARDRAILS

The system must: show sample sizes; distinguish sample findings from market-wide facts;
avoid invented demographics; avoid treating visibility as endorsement; distinguish
incidental appearances from active product usage; distinguish sponsored and organic
content where possible; avoid equating engagement with consumer preference; avoid
treating positive comments as sales; show uncertainty; preserve provenance; allow
conflicting evidence; identify insufficient data; avoid hallucinating absent evidence.

**If the evidence is weak, the system must say so.**

---

## 29. DATABASE

Supabase Postgres. pgvector for embeddings. Suggested tables:

```
brands, products, creators, content_items, content_metadata, content_analysis,
content_embeddings, commerce_items, commerce_reviews, clusters, cluster_members,
research_runs, research_queries, research_results, hypotheses, findings,
finding_evidence, competitor_relationships, collection_jobs, collection_sources
```

Use JSONB where the taxonomy is intentionally flexible.

---

## 30. TECHNICAL STACK

Frontend: Next.js, React, TypeScript, Tailwind, shadcn/ui or equivalent.
Backend: Python, FastAPI. Database: Supabase Postgres. Vector: pgvector.
Auth: Supabase Auth. Storage: Supabase Storage or S3-compatible.
Video processing: FFmpeg. Collection: Apify, YouTube API, search engine/public-web.
AI: current high-quality multimodal model; speech transcription model; text embeddings;
multimodal or text embeddings where appropriate. **Do not train custom models.**
Analytics: Python, pandas, scikit-learn, HDBSCAN or another suitable clustering algorithm.
Background jobs: Celery + Redis, or another lightweight managed queue if simpler.
Deployment: Vercel frontend; Railway/Render/Fly/similar backend; managed Supabase.

**Do not introduce Kubernetes, Kafka, Snowflake or heavy infrastructure for the MVP.**

---

## 31. PRODUCT UI — SEVEN MAIN SECTIONS

**1. Overview / Cultural Pulse** — most important currently detected findings: major
patterns; emerging themes; changing themes; competitive movements; unusual signals;
marketplace signals; strongest evidence-backed insights; sample size; time period. **Do
not hard-code what cards appear.** Each finding links to evidence.

**2. Consumer & Cultural Landscape** — discovered behaviours; occasions; social contexts;
cultural signals; creator archetypes; language/slang; content archetypes; product
associations. Filter by date, brand, product, competitor, platform, content format,
creator type, cluster, language, organic/sponsored, discovered theme.

**3. Competitive Landscape** — discovered competitors; why each was detected; Piattos vs
competitor cluster presence; creative differences; consumer-context differences; cultural
differences; emerging competitive momentum; marketplace differences; representative
content. **Do not rank competitors with arbitrary scores.**

**4. Trends & Opportunities** — themes that are relevant, evidence-supported,
emerging/growing and competitively important. Especially highlight: emerging theme +
Piattos presence + competitor presence + marketplace signal + supporting evidence.
Strategic implications must be labelled as hypotheses or investigation areas rather than
definitive prescriptions.

**5. Content Explorer** — browse individual social content: thumbnail; source; creator;
date; engagement; transcript; caption; detected brands; product prominence; content
analysis; cultural signals; clusters; reason relevant; original URL. Support semantic
search.

**6. Commerce Explorer** — browse Shopee/Lazada data: product; brand; marketplace; price;
discount; rating; review count; seller; category; variants; reviews where available;
associated social themes; discovered competitor relationship.

**7. Ask the Data** — AI analyst over the collected dataset. Answers must query the
actual dataset; cite evidence; provide sample size; mention uncertainty; distinguish
observation from interpretation; link to source content. **Do not hard-code the answer.**

---

## 32. RESEARCH RUN VIEW

Show: research objective → initial queries → sources searched → patterns discovered →
hypotheses generated → follow-up queries → competitor discoveries → commerce
investigation → findings → supporting evidence.

This demonstrates how the AI arrived at its conclusions.

---

## 33. DESIGN PRINCIPLES

Feel like an enterprise intelligence platform built for a professional marketing team:
clean; modern; analytical; evidence-first; minimal decorative UI; strong hierarchy;
video-heavy; visually clear; credible.

Avoid: meaningless word clouds; vanity mention counts; arbitrary AI scores; excessive pie
charts; generic dashboard filler.

Prefer: evidence cards; video grids; comparative tables; timelines; cluster views; trend
trajectories; research narratives; competitor comparisons; source drill-downs.

---

## 34. IMPORTANT: DO NOT SEED PIATTOS FINDINGS

Do not encode presumed Piattos strategies or consumer associations into prompts; example
data used in production; taxonomy; recommendation logic; trend logic; or dashboard
defaults.

The system discovers its own Piattos findings from actual collected data.

Examples used in code/tests must be **generic placeholders** such as `Brand A`, `Theme X`,
`Cluster Y`.

**Piattos is the research subject, not a predetermined answer.**

---

## 35. MVP SUCCESS CRITERIA

A URC marketing user can open it and answer: What is happening around Piattos? What
changed recently? Which patterns are emerging? Why might they matter? What is Piattos
associated with? Which competitors matter? How does Piattos differ from them? What are
competitors doing differently? Which behaviours or cultural signals are growing? Are
social signals appearing in commerce? Which areas deserve further investigation? What
actual content supports each conclusion?

**The user must be able to trace every important claim back to evidence.**

---

## 36. BUILD ORDER

1. **Infrastructure** — Next.js frontend; FastAPI backend; Supabase; authentication;
   environment configuration; base database schema.
2. **Collection** — Apify integration; collector abstraction; TikTok; YouTube; Shopee;
   Lazada; Instagram/Facebook optional; collection jobs; provenance tracking.
3. **Multimodal analysis** — video/audio retrieval; FFmpeg processing; frame sampling;
   transcription; multimodal analysis; structured and open-ended labels; embeddings.
4. **Research engine** — initial query generation; iterative query generation; hypothesis
   generation; competitor discovery; research stopping conditions; research history.
5. **Intelligence** — clustering; trend detection; competitive analysis; commerce
   analysis; social-commerce linking; finding generation; evidence linking.
6. **Product UI** — Overview; Consumer/Cultural Landscape; Competition; Trends; Content
   Explorer; Commerce Explorer; Ask the Data; Research Run view.
7. **Quality** — confidence indicators; sample-size displays; evidence drill-down;
   failure handling; source availability handling; deduplication; rate limiting; research
   auditability.

---

## 37. IMPLEMENTATION PRIORITIES

When making tradeoffs, prioritise in this order:

1. usefulness to URC's marketing team
2. actual evidence quality
3. ability to discover unexpected patterns
4. competitive intelligence
5. social-to-commerce intelligence
6. speed to usable MVP
7. modular architecture
8. UI polish
9. infrastructure sophistication

**Do not over-engineer.** The system does not need perfect coverage of the internet. It
needs to autonomously collect enough relevant content to produce useful, surprising,
evidence-backed intelligence.

---

## 38. FIRST WORKING MILESTONE

1. User starts a research run for Piattos / Philippines.
2. Research agent generates broad queries.
3. System automatically collects data from TikTok, YouTube, Shopee and Lazada.
4. System analyses social videos multimodally.
5. System discovers themes and potential competitors.
6. System creates follow-up searches.
7. System gathers additional evidence.
8. System clusters the content.
9. System produces several evidence-backed findings.
10. User can view those findings in the dashboard.
11. User can click each finding to inspect supporting videos/listings.
12. User can ask questions about the dataset through Ask the Data.

Build this end-to-end flow before adding additional sophistication.

The final product should feel like **"An AI market researcher continuously investigating
Piattos, Philippine snack culture, competitors and marketplace behaviour"** — not "a
dashboard displaying scraped social posts."
