# Decision log — Snack Launch Radar

Newest first.

| Date | Decision | Why |
| --- | --- | --- |
| 2026-10-02 | Pull includes FR-1… and FR-2… registration numbers | The FDA's eServices system (2026) issues shorter numbers; without them, new registrations from Mar 2026 onward looked like they had collapsed from ~2,000 to ~35 a month |
| 2026-10-02 | Highlighted competitors come from the client watchlist in `config.json`, not raw counts | Name-based company typing let importers (e.g. Nextrade, Legende) rank as "main competitors" |
| 2026-10-02 | Trend charts start in 2022 | Products registered before the e-registration system appear under their latest renewal date, so earlier years are undercounted |
| 2026-10-02 | "New product" = earliest registration on file is Initial; one product registered at several plants counts once | Plants file one registration each, and renewals would otherwise look like launches |
| 2026-10-02 | Snack tagging uses transparent keyword rules (v1), no model calls | Every tag traces to a word in the product name; an LLM pass can refine edge cases later |
| 2026-10-02 | Scope v1 = salty snacks; biscuits, confectionery, pastries excluded | First customer (URC) brief is chips and snacks; scope can widen via `classify.py` |
| 2026-10-02 | Every pull stores raw responses (gzipped) and every record version in SQLite | Requested by the user; enables week-over-week diffs and audits |
| 2026-10-02 | Data from the public portal search API, partitioned by registration-number prefix, ~4 s between requests | No bulk export; robots.txt allows crawling; approved by the user. FOI request drafted for a fuller history incl. expired records |
| 2026-10-02 | Dashboard is one self-contained HTML file | No server needed; easy to share with the client; works offline |
