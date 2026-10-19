# Vaktin - Icelandic Nature Conservation Monitor

## Purpose

Vaktin ("The Watch") is an automated monitoring system that tracks government and municipal activity in Iceland to ensure that nature conservation organizations (Landvernd, SUNN, and others) don't miss cases that require their attention. It runs as a scheduled GitHub Actions workflow.

The target audience is Icelandic nature conservation NGOs. All reports and analysis output must be in Icelandic. The user communicates in Icelandic.

## How It Works

1. **Scrapers** (`src/scrapers/`) fetch new content from government portals and municipality websites
2. **State tracking** (`state/state.json`) ensures only new/unprocessed items are analyzed — each scraper stores `seen_ids` so the same item is never processed twice
3. **Claude CLI** (`claude -p`) analyzes new items for nature conservation relevance using the prompt in `src/analyze.py`
4. **Reports** (`reports/`) are generated as markdown files committed back to the repo by the GitHub Actions workflow

## Project Structure

```
vaktin/
├── CLAUDE.md                     # This file — project context for all contributors and AI tools
├── config/
│   └── sources.yml               # All monitored websites and sources (priority 1-3)
├── src/
│   ├── scrapers/
│   │   ├── base.py               # Base scraper class with state tracking, HTTP session, and Playwright fallback
│   │   ├── samradsgatt.py        # Samráðsgátt ríkisins — island.is GraphQL API
│   │   ├── skipulagsstofnun.py   # HMS/Skipulagsstofnun — island.is GraphQL API (EIA database)
│   │   ├── uos.py                # Generic Prismic CMS API scraper — used for UOS and Vatnajökulsþjóðgarður
│   │   ├── nattura.py            # Náttúruverndarstofnun — Payload CMS API
│   │   ├── domstolar.py          # Icelandic courts (Hæstiréttur, Landsréttur, Héraðsdómstólar) — island.is GraphQL API
│   │   ├── ust.py                # Umhverfisstofnun (Environment Agency) — HTML scraping
│   │   ├── althingi.py           # Alþingi (Parliament) — XML REST API, nature-relevant bill categories
│   │   ├── rss.py                # Generic RSS scraper — used for Vegagerðin, NI, MAST
│   │   ├── wp_graphql.py         # WordPress GraphQL scraper — used for Tjörneshreppur
│   │   └── sveitarfelog.py       # Generic municipality scraper (fundargerðir) — HTML scraping
│   ├── analyze.py                # Claude -p integration — contains the analysis prompt
│   ├── reporter.py               # Markdown report generation (index + weekly)
│   ├── self_heal.py              # Self-healing — Claude diagnoses and fixes broken scrapers
│   └── main.py                   # Orchestrator — runs scrapers → analysis → reports
├── state/
│   ├── state.json                # Persistent state — tracks seen_ids per source (committed to git)
│   └── pending.json              # Items awaiting analysis — auto-retried next run
├── reports/
│   ├── index.md                  # Active issues — current overview sorted by severity
│   ├── .index_data.json          # Structured cache of index items
│   ├── leit.json                 # Search index: every visible document, active and archived (vaktin.js)
│   ├── .health.json              # Health report from last run (ok/errors per source)
│   ├── weekly/                   # Weekly summary reports (YYYY-VWW.md)
│   └── archive/                  # Resolved/old items (manual move)
├── COVERAGE.md                   # Full coverage table — all 62 municipalities + 8 national agencies
├── sources.md                    # Jekyll page — published coverage overview on GitHub Pages
├── index.md                      # Jekyll page — landing page for GitHub Pages site
├── _layouts/
│   └── default.html              # The one site layout: utility strip, masthead, sticky tabs, footer
├── assets/
│   ├── vaktin.css                # Site stylesheet — design tokens at the top, hand-written, no build step
│   ├── vaktin.js                 # Theme toggle, filters, histogram, deadline countdowns
│   ├── fonts/                    # Self-hosted IBM Plex Sans (woff2, latin subset) + OFL licence
│   └── logo-120.png              # Masthead logo (logo-512.png is kept for anything that hotlinks it)
├── .github/
│   └── workflows/
│       └── vaktin.yml            # Runs weekdays at :23 past 00, 12, 14, 16, 18 UTC, commits results back
└── requirements.txt              # Python: requests, beautifulsoup4, pyyaml, lxml, playwright
```

## Key Architecture Decisions

### Analysis via `claude -p` (not Anthropic API)
The project uses Claude Code CLI in pipe mode (`claude -p`) for all AI analysis. This requires:
- `npm install -g @anthropic-ai/claude-code` in the workflow
- Content is piped to Claude with `--output-format json` and `--json-schema`; the answer is read from `structured_output` (the old text parser `_extract_json` is only a fallback).
- **Lean, tool-less calls** (`_run_claude` in `src/analyze.py`): our own short `--system-prompt`, `--tools ""`, `--safe-mode`, `--strict-mcp-config`, `--no-session-persistence`, `--max-turns 3`, and `cwd` set to a temp dir so the repo's CLAUDE.md is not loaded. That cut the overhead from ~35k to ~1–2k tokens per call (usage limits were the main cause of failed analyses), and scraped text has nothing it could make the model do. The CLI runs in its own process group so a timeout kills it reliably.
- **Analysis v2 fields** (October 2026, `ANALYSIS_VERSION = 2`, stored as `analysis_v`): besides relevance, severity, categories (fixed list), `dek_is`, `summary_is`, `action_needed`, `deadline` (`YYYY-MM-DD` or null) and `location`, each item gets `headline_is` (a descriptive title — many source titles are just meeting names), `project_is` (canonical name of the project/plan the document is about) and `case_refs` (official case numbers as `system:number`). The prompt passes today's date and the project names already in use, so related documents reuse one name. Relevance criteria are strict: "monitor" is for concrete matters, not for anything that might touch nature.
- **Texts describe; they don't advise** (text v2, October 2026, `TEXT_VERSION = 2`, stored as `text_v`). The NGOs decide for themselves what to do, so `dek_is`, `summary_is` and `action_needed` say what the case is, which documents it rests on and what the deadlines are — never what the organisations should do, check or assess ("Náttúruverndarsamtök ættu að …", "Fylgjast með …", "Senda umsögn …"). How pressing a case is shows in its severity. `action_needed` is shown on cards as **"Frestir og gögn"**: deadlines for comments or appeals and where/how to submit them, the next step in the procedure if the document states it, and links to the underlying documents; null if the document gives none. The rules are `NEUTRAL_TEXT_IS` in `src/analyze.py`, used both by the analysis prompt and by the rewrite of older texts (see "Re-triage of stored items"). Until October 2026 the field was shown as "Næstu skref" and written as advice.
- **Documents with no extracted text** are analysed on title, source and URL (marked `no_content`), never skipped.
- **Analysis reads up to 30k characters** (`analyze.CONTENT_LIMIT` = `scrapers.base.MAX_CONTENT_CHARS`). Until October 2026 it read 10k, although scrapers keep 15k: in a sample of 48 relevant municipal minutes 37% were over 10k (so the last agenda items were never read) and 15% over 15k (max 27k). Municipal minutes (`sveitarfelog.py`, `borgarbyggd.py`), ÚUA rulings and court judgments now keep 30k; other scrapers still keep 15k.
- **Rulings and judgments are read decision first.** They state the decision (úrskurðarorð / dómsorð) last, after pages of facts and the parties' arguments: in October 2026 the Seyðisfjörður aquaculture card described an ÚUA ruling that annulled HMS's decision as still pending, and 11 other ÚUA cards said the same of decided cases. `BaseScraper._decision_first` rebuilds the text as `=== ÚRSKURÐARORÐ ===` / `=== DÓMSORÐ ===` → `=== FORSENDUR ===` (the reasoning, from the last "Niðurstaða"/"Forsendur og niðurstaða" heading) → `=== SÉRÁLIT / SÉRATKVÆÐI ===` (dissents, kept apart so they are not read as the decision) → `=== MÁLIÐ ===` (facts and arguments). With `appeal=True` (courts) the judgment of the earlier instance, which Landsréttur and Hæstiréttur publish below their own, is cut off at its heading ("Dómur Héraðsdóms …", "Úrskurður Héraðsdómur …") and follows as `=== NIÐURSTAÐA FYRRA DÓMSTIGS ===` + `=== FYRRI DÓMUR ===` — otherwise its dómsorð, the last in the file, would be read as the decision, and it is often the opposite. Items read this way carry `metadata["ruling"]`, which adds `RULING_NOTES_IS` to the prompt: base the analysis on the decision, state the outcome in the headline and dek, say what an "óraskaður"/"staðfestur" lower judgment decided, never describe a decided case as pending (except interim rulings). A text without a decision heading is analysed in its original order with a WARNING. ÚUA: `ruling: true` on `urskurdarnefnd` in `sources.yml` (`rss.py`); severity in `forgangur.md` ("Úrskurðir ÚUA": at least important, critical for important matters). Courts: see `domstolar.py` below.
- **Model:** `CLAUDE_MODEL` in `src/analyze.py` (also used by `self_heal.py`) is the CLI alias `opus`, i.e. the newest Opus model, so model upgrades need no code change. The workflow's token check also passes `--model opus`, and the run log prints the concrete model ID the CLI reports (`modelUsage`). To pin a specific version, change both to a full model ID (e.g. `claude-opus-5-5`).

### Priority guidelines (`config/forgangur.md`)
The file `config/forgangur.md` contains human-editable guidelines that control how Claude rates the severity of issues. These guidelines are injected into the analysis prompt at runtime. To change what counts as critical/important/monitor, edit this file — no code changes needed. The file is in Icelandic since the analysis runs in Icelandic.

### Authentication — long-lived OAuth tokens (not API keys)
Authentication uses **Claude Code OAuth tokens** from a Claude Max subscription, NOT Anthropic API keys.

- GitHub secrets: `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_CODE_OAUTH_TOKEN_2` (failover)
- Tokens are created with `claude setup-token` which generates **long-lived CI tokens**
- The workflow tests the primary token first, falls back to secondary if expired

**To create/renew a token:**
```bash
claude setup-token
```
This opens a browser OAuth flow and outputs a long-lived token for CI use. Copy the token and update the secret at the repo's GitHub Settings → Secrets → Actions.

**Do NOT use `claude login`** — that generates short-lived tokens (~30 days) meant for interactive use. Always use `claude setup-token` for CI secrets.

### State tracking — scrape only what's new
Each scraper maintains a list of `seen_ids` in `state/state.json`. This file is committed to git so state persists across GitHub Actions runs. When adding a new scraper:
1. Inherit from `BaseScraper` in `src/scrapers/base.py`
2. Call `self.load_state()` at start, `self.save_state()` at end
3. Keep seen_ids capped (300-500) to prevent unbounded growth
4. Truncate content to 15k chars before analysis — up to `MAX_CONTENT_CHARS` (30k) for long documents such as minutes, rulings and judgments

### Pending analysis — no items lost, 7-day expiry
Items that are scraped but not analyzed are saved to `state/pending.json` and automatically retried on the next run. This prevents data loss when:
- `--skip-analysis` is used (e.g. for testing scrapers)
- Analysis fails mid-run (e.g. expired token)

The pipeline detects systematic failures via **fail-fast**: if 3 consecutive analyses fail, it assumes a token/system issue, saves all remaining items to pending, and stops. Successfully analyzed items are still reported.

Pending items are combined with newly scraped items at the start of each run. The `pending.json` file is committed to git alongside `state.json`.

**Pending expiry:** Each pending item carries a `_pending_since` timestamp. Items older than 7 days (`PENDING_MAX_AGE_DAYS`) are automatically dropped when loaded. This prevents unbounded growth when analysis repeatedly fails (e.g. expired tokens causing all items to be saved to pending indefinitely).

### Active vs archived reports — month-based lifecycle
The active index (`reports/index.md`) shows items from **the first day of the previous month onward**. This means the active view always contains:
- all items from last month
- all items from the current month so far

Examples:
- On **April 1**, the active index shows items from **March 1 onward** (March + April 1 items)
- On **May 1**, the active index shows items from **April 1 onward**

Older items are not deleted. Instead they are moved into month-based archive pages under `reports/archive/`:
- `reports/archive/index.md` — archive landing page with links to archived months
- `reports/archive/YYYY-MM.md` — one page per archived month

The structured cache `reports/.index_data.json` keeps full item history so archive pages can be regenerated on every run.

**Open deadlines stay active.** An item whose `deadline` is an ISO date that hasn't passed stays in the active index however old its own date is (e.g. a consultation published on 31 Aug that is open until December).

### One card per case (`src/cases.py`)
One project reaches Vaktin through many sources (Skipulagsgátt, municipal minutes, Lögbirtingablaðið, appeals). Items sharing a normalised `project_is` or any `case_refs` entry are grouped (transitively) into one case. The site shows one card per case: the most severe, newest document is the primary content, and the other documents are listed newest first under "Fleiri skjöl um málið" (each `<li>` keeps its item id, so `/reports/#<item_id>` deep links still land; `vaktin.js` opens the list). Counts on the site are cases. Generic names ("Aðalskipulag", "Fiskeldi" …) never group; numbers that name a whole publication issue are not accepted as case refs.

E-mails (`notify.py`) send full cards only for new cases or new deadlines; further documents in cases already sent are listed briefly at the bottom, and no e-mail goes out for follow-ups alone (`split_new_cases`).

### Re-triage of stored items (`src/migrate.py`)
Items analysed before the current `ANALYSIS_VERSION` lack the grouping fields and were rated with looser rules. Every run re-triages up to 300 such *active* items from their stored summaries, 40 per `claude` call (`retriage_items`). It may hide `monitor` items that no longer meet the criteria (`relevant: false`: kept in `.index_data.json`, not shown), and may re-rate severity, but never hides a critical or important item. Each change is logged to `reports/.retriage_log.json` (item, title, old → new severity or `hidden`). To bring an item back, set `relevant` to true in `.index_data.json`. When the rules in `forgangur.md` change substantially, bump `ANALYSIS_VERSION` to re-triage the active items again.

**Rewrite of older texts.** Items stored before `TEXT_VERSION` (no `text_v`) were written as advice to the NGOs. `rewrite_stored_texts` rewrites the dek, summary and action text of up to 300 visible items per run, 20 per `claude` call and 3 calls at a time (`rewrite_texts`): active items first, newest first, then the archive (about 1,600 items in October 2026, so the archive takes a few runs). Only the advice is removed or restated as fact; facts, dates and links stay. Items with no action text and no advice words (`ADVICE_WORDS` in `migrate.py`) are only marked. A rewrite that loses a link or more than half of the summary is refused with a WARNING and the old text kept. Counted in `.health.json` as `texts_rewritten`. There is no separate log: the old texts are in the git history of `.index_data.json`. When `NEUTRAL_TEXT_IS` changes substantially, bump `TEXT_VERSION` to rewrite the stored texts again.

### Dismissed items
Items can be manually removed from the active index by adding their `item_id` to `reports/.dismissed.json` (a JSON array of strings) and committing the change. Dismissed items are filtered out during `generate_index()` and will not reappear even if the scraper finds them again. This is intended for git committers (superusers), not end users — there is no dismiss button on the public site.

### No weekly reports
The system runs multiple times per week, making weekly summary reports redundant. The active issues index (`reports/index.md`) is the primary view. Weekly report generation has been removed.

### Delta strategy — long-term performance
The system must not slow down after months of operation. Three mechanisms work together:

**1. MAX_AGE_DAYS (safety net for all scrapers)** — `BaseScraper.MAX_AGE_DAYS = 62`. The `_is_too_old(date_str)` method skips items older than 62 days regardless of state. The value must be ≥ the maximum active report window (on the last day of a month, the active window can span up to ~61 days back to the first of the previous month). Supports ISO 8601, RFC 2822, Icelandic numeric formats (`d.m.yyyy`), and Icelandic month names (`2. júlí '25`, `4. feb '26`) via `_parse_icelandic_date()`.

**2. Timestamp-based deltas (preferred)** — Use `last_check` to query only items newer than the last run. On first run, falls back to `_max_age_cutoff()` (30 days ago) instead of a hardcoded date. **Important:** `last_check` must only advance when the fetch succeeds — if the API call fails, preserve the old `last_check` to avoid skipping items. Use when the source API supports date filtering:
- `samradsgatt.py` — GraphQL `dateFrom` filters on the `publishOnWeb` date (whole days). Exception: instead of starting at `last_check` it re-queries the whole MAX_AGE_DAYS window on every run (rolling lookback, 2–3 list requests) and skips known cases via `seen_ids` (cap 500, newest IDs kept). A fetch that wrongly returns nothing still advances `last_check`; the rolling window lets the next run recover what was missed
- `skipulagsgatt.py` — GraphQL API supports `fromDate` filtering
- `uos.py` — Prismic CMS API supports `gt(first_publication_date, ...)` predicates
- `rss.py` — RSS/Atom feeds have `pubDate`/`published` for client-side filtering

**3. Seen-IDs with cap (fallback)** — Maintain a capped list of processed item IDs in `state.json`. Use this when the source has no date-based filtering (HTML scraping, APIs without date params):
- `ust.py`, `sveitarfelog.py` — HTML scraped, no API
- `althingi.py` — XML API returns no date fields. It seeds `seen_ids` without analysis only when its state has no `last_check`. A new session does not trigger seeding, so its bills are analysed as new. Cap 500, oldest session evicted first
- `skipulagsstofnun.py` — GraphQL with date field

All `seen_ids` lists are capped at 300–500 to prevent unbounded growth. When writing a new scraper, prefer timestamp deltas if the source supports it. Fall back to seen_ids only when necessary.

### Handling items without dates

Many municipality websites publish content without machine-readable dates. The system handles this differently depending on whether it's a first run or not:

**First run (no prior state):** Skip all dateless/unparseable items. Without `seen_ids` there is no way to distinguish new content from years-old backlog, so including them would flood analysis with hundreds of irrelevant items.

**Subsequent runs (seen_ids exist):** Include dateless items that pass the `seen_ids` filter. If an item is not in `seen_ids`, it appeared on the website since the last run — so it's new regardless of whether it has a date. After analysis, the item enters `seen_ids` and won't be processed again.

**Undated item tracking:** All items skipped due to missing dates are recorded in `state/undated.json` with source, title, URL, and timestamp. This file is committed to git so nothing is silently lost. Entries are deduplicated by URL and pruned after 30 days.

**Improving date extraction:** The preferred fix for dateless items is improving `_extract_date()` in `sveitarfelog.py` and `_parse_icelandic_date()` in `base.py` to handle more date formats. When encountering a new date format in the wild, add support for it rather than accepting dateless items.

**Performance-critical files to keep lean:**
- `state/state.json` — seen_ids capped per source (300–500)
- `state/pending.json` — cleared after each successful analysis run; 7-day expiry
- `state/undated.json` — deduplicated by URL, pruned after 30 days
- `state/activity.json` — one timestamp per source (when it last yielded new items), for the "stale" health check
- `reports/.index_data.json` — full item history used to build active + archived month views

### Self-healing — automatic scraper repair
After each run, the workflow checks `.health.json`. If sources returned 0 items (and are not in the `KNOWN_BROKEN` list), the self-heal step runs `src/self_heal.py`:

1. **Track empty streaks** — counts consecutive empty runs per source in `reports/.heal_log.json`
2. **Trigger after threshold** — after 2+ consecutive empty runs, the source is flagged for investigation
3. **Claude diagnosis** — sends a prompt to `claude -p` with the broken source details, asking it to:
   - Fetch the live website and compare with configured URLs/selectors
   - Check if municipalities have merged (via samband.is)
   - Update `config/sources.yml` and scraper code as needed
4. **Commit fixes** — any changes are committed back to the repo automatically

Key files:
- `src/self_heal.py` — the self-healing script
- `reports/.heal_log.json` — tracks empty streaks and heal history (committed to git)

The `KNOWN_BROKEN` set in `self_heal.py` lists sources where empty results are expected (403, login-required, SSL expired, etc.) — these are skipped to avoid wasting Claude tokens.

Run manually: `cd src && python self_heal.py --dry-run` (analyze without fixing), or `python self_heal.py --force --sources ust reykjavik` (force-check specific sources).

### Reports committed to repo
The GitHub Actions workflow commits `state/` and `reports/` back to the repo after each run. This means:
- Report history is preserved in git
- State persists between runs
- Anyone who clones the repo can see current and historical findings

### Playwright fallback for JS-rendered pages
Some government/municipality sites render content with JavaScript. The scraper base class provides three fetch methods:
- `fetch_page(url)` — fast `requests` fetch, no JS. Use for static HTML.
- `fetch_page_js(url, wait_selector=, wait_ms=)` — headless Chromium via Playwright. Use when you know JS is required.
- `fetch_page_auto(url, ...)` — **recommended default**. Tries `requests` first; if the response is too short (< `MIN_CONTENT_LENGTH` chars), automatically falls back to Playwright.

Playwright is lazy-loaded: the browser only starts if a page actually needs JS rendering. A shared Chromium instance is reused across all scrapers and cleaned up at the end of the pipeline (`close_browser()`).

In CI, `playwright install chromium --with-deps` runs in the workflow to install Chromium and its OS dependencies on `ubuntu-latest`.

### Health reporting
Every run writes `reports/.health.json` with per-source status and error summary. This file is committed to git so health is visible even if you don't check CI logs. The workflow also emits a GitHub Actions warning annotation if `health.ok == false`.

Key fields in `.health.json`:
- `ok`: `true` if no errors, `false` otherwise
- `sources.<id>.status`:
  - `"ok"` — the source answered (found items, or fetched nothing new without any failed request)
  - `"error"` — the scraper crashed, or nothing was fetched and requests failed (HTTP 4xx/5xx, network, Playwright); the reason is in `sources.<id>.error`
  - `"stale"` — no new items for `stale_days` (per source in `sources.yml`, default 60; 0 turns it off). Catches scrapers that break silently: Samráðsgátt asked for the wrong result page for six months while reporting "ok".
  - `"empty"` — 0 items on a first run (no prior state); `"no_scraper"` — no matching scraper class
- `sources.<id>.last_new`: when the source last yielded new items (kept in `state/activity.json`; a source starts counting from the first run that sees it)
- `analysis.failed`: number of items where Claude analysis failed
- `errors`: human-readable list of problems

Failed requests are recorded automatically: `BaseScraper.session` is a `_TrackingSession` that notes every failed request, so scrapers don't need their own error reporting. Failures that aren't HTTP requests (e.g. unexpected JSON) can be noted with `self._record_failure(...)`. Only `"empty"` triggers self-heal. Health is written before the pages are generated, so the home page shows the current run's source health.

### Scraper types

**API-based scrapers** (most reliable):
- `samradsgatt.py` — Uses island.is public GraphQL API (`https://island.is/api/graphql`). Query names: `consultationPortalGetCases`, `consultationPortalCaseById`. Input types: `ConsultationPortalCasesInput`, `ConsultationPortalCaseInput`. `pageNumber` is 0-based (page 0 is the first page); the scraper pages until it gets a short or empty page (max 20 pages).
- `skipulagsgatt.py` — Uses Skipulagsgátt GraphQL API (`https://www.skipulagsgatt.is/graphql`). Fetches all planning cases across all municipalities: zoning, master plans, EIA, construction permits. Uses `issueConnection` query with `fromDate` and cursor-based pagination. Separate from skipulagsstofnun.py (which covers HMS EIA only). ~1,000+ active cases, ~130 new per month.
- `skipulagsstofnun.py` — Uses island.is `getGenericListItems` GraphQL query with GenericList ID `6PA6bW36D1LIHI3iueZX6t` (the HMS EIA database). 1,575+ cases in the database.
- `domstolar.py` — Court judgments from island.is (`webVerdicts`, all three court levels; `court_ids` in sources.yml). The list gives parties, keywords and the reifun. Judgments whose parties, keywords or reifun contain one of `FULL_TEXT_TERMS` (skipulag, virkj, landsnet, þjóðlend, fiskeld …; "jörð" only as a keyword, as it is also part of names) are analysed on their full text from `webVerdictById` — Hæstiréttur as rich text (`richText.document`, flattened by `_rich_text`), Landsréttur and héraðsdómar as a base64 PDF (`pdfString`, extracted with `SveitarfelagScraper._extract_pdf`) — decision first with `appeal=True`. That is about 5% of judgments (8 of 150 from 20 Sept–17 Oct 2026) and covered every nature case stored so far; the rest (mostly criminal cases) are analysed on the list data as before. If the full text can't be had, a WARNING is logged and the list data is used.
- `island_news.py` — Generic scraper for island.is organization news. Uses `getNews` GraphQL query with `organization` filter. Config key: `island_org`. Used for Fiskistofa (`fiskistofa`) and Land og skógur (`land-og-skogur`).
- `uos.py` — Generic Prismic CMS API scraper. Reads `prismic_repo`, `prismic_doc_type`, and `prismic_url_prefix` from config. Used for UOS (`uos-web`, doc type `news`) and Vatnajökulsþjóðgarður (`vatnajokulsthjodgardur`, doc type `article`). Must fetch master ref first, then search by document type.
- `nattura.py` — Náttúruverndarstofnun (est. Jan 2025, successor to parts of UST). Uses Payload CMS REST API at `nattura-is.payload.is/api/news`. Covers protected areas, national parks, species protection. The API returns localized fields only with `locale=is` (since ~Oct 2026): the title and path are in the `url` relation (`label`, `url`) and body text is Lexical JSON (`TextBlock`). Looks back over the whole MAX_AGE_DAYS window every run and dedupes by `seen_ids`.

**XML/RSS scrapers** (reliable):
- `althingi.py` — Uses Alþingi XML REST API at `https://www.althingi.is/altext/xml/`. Fetches bills filtered by 6 nature-relevant subject categories (efnisflokkar): 31 (umhverfisstjórn), 30 (orkumál), 29 (mengun), 24 (samgöngur), 3 (landbúnaður), 4 (sjávarútvegur). The current session is read on every run from `loggjafarthing/yfirstandandi/` (`<þing númer='158'>`); `session` in sources.yml is only a fallback if that lookup fails. A bill listed under several categories is emitted once, with every category name in `metadata.categories`.
- `rss.py` — Generic RSS/Atom feed scraper. Parses standard RSS 2.0 and Atom feeds. Used for Vegagerðin, Náttúrufræðistofnun, MAST, Hafrannsóknastofnun (`hafogvatn.is/feed`) and ÚUA (`uua.is/feed`, `ruling: true` — see "ÚUA rulings are read decision first"). Optionally fetches full article content when RSS description is short.

**HTML scrapers** (fragile — sites change):
- `ust.py` — Scrapes `ust.is` permit pages. Also used for Ferðamálastofa news (`ferdamalastofa.is`).
- `sveitarfelog.py` — Generic municipality scraper. Uses cascading CSS selectors with table and keyword-based fallbacks. Supports document files (.pdf, .docx, .odt) — downloads and extracts text directly.

### Data extraction principle
**Never skip data because of format or difficulty.** The entire purpose of Vaktin is to ensure conservation groups don't miss important information. Silently skipping a document because it's hard to parse defeats this core mission.

- If a municipality publishes meeting minutes as PDF, ODT, or DOCX files instead of HTML pages, the scraper must download and extract text from those files.
- If extraction fails with one method, try others (pdftotext → pypdf → pdfminer → OCR). Never rely on a single extractor.
- If ALL extraction methods fail, log a **WARNING** with the URL and methods tried — never fail silently.
- When encountering a new document format, add extraction support rather than filtering it out.
- The `_extract_pdf()` method uses four fallbacks: `pdftotext` (poppler CLI), `pypdf`, `pdfminer.six`, and `tesseract` OCR (for scanned/image PDFs). CI installs `poppler-utils` for pdftotext/pdf2image and `tesseract-ocr` + `tesseract-ocr-isl` for Icelandic OCR.

### Scraper resilience
Scrapers use CSS selectors with multiple fallbacks since Icelandic government websites vary in structure. When a scraper finds no elements:
- It logs a WARNING naming the source and section
- It records `"status": "empty"` in the health report
- It returns an empty list (does not crash the pipeline)
- Other scrapers continue running
- Fix by inspecting the live site HTML and updating selectors in the scraper file

### Known issues (as of April 2026)
- **Akureyri** (`www.akureyri.is`) — Next.js site, requires Playwright. URL changed to `/stjornskipulag/fundargerdir` (fixed April 2026).
- **Borgarbyggð** (`borgarbyggd.is`) — Next.js site backed by Fundagátt.is. Uses custom `borgarbyggd.py` scraper that parses SSR `__next_f` JSON payload. No login required despite Fundagátt link on page.
- **Hornafjörður** (`hornafjordur.is`) — Uses ASP.NET SearchMeetings.aspx with ViewState. Limited extraction.
- **Vestmannaeyjabær** — Fundargerðir on separate subdomain (`ibuagatt.vestmannaeyjar.is`). Works but content extraction is limited.
- **Árborg** (`arborg.is`) — SSL certificate expired. Fixed with `ssl_verify: false` in config.
- **"No content element" warnings** — Some municipality sub-pages don't have a parseable `<article>` or `<main>` element. Items are still listed (titles + URLs) but without full content. This is cosmetic — Claude analysis still works on the title + metadata.
- **Alþingi session number** — Read at runtime from the XML API since October 2026. Before that, session 157 was hardcoded, and bills from session 158 (opened 2026-09-08) were missed. `session` in sources.yml is only a fallback for a failed lookup, so updating it each September is optional.
- **Samráðsgátt paging** — `consultationPortalGetCases` pages are 0-based. Until October 2026 the scraper started at page 1, so it fetched 0 cases in 403 of 409 runs after April 2026. The rolling MAX_AGE_DAYS lookback backfills missed cases that are still inside the window.

## Topics of Interest for Nature Conservation

The system flags items related to:
- **Landnotkun og skipulagsmál** — land use changes, aðalskipulag, deiliskipulag
- **Umhverfismat** — environmental impact assessment (EIA/SEA)
- **Náttúruvernd og friðlýsingar** — protected areas, species protection
- **Vatnsvernd og vistkerfi** — water protection, wetlands, ecosystems
- **Orkuframkvæmdir** — hydro, geothermal, wind energy projects
- **Vegagerð og mannvirkjagerð** — roads, construction in/near nature areas
- **Ferðaþjónusta í viðkvæmum svæðum** — tourism infrastructure in sensitive areas
- **Fiskeldi og sjávarútvegur** — aquaculture, fisheries
- **Loftslagsmál** — greenhouse gas emissions, climate policy
- **Mengunarvarnir** — pollution prevention, hazardous materials

Items are classified by severity:
- 🔴 **critical** — needs immediate attention (deadlines, major threats)
- 🟡 **important** — needs attention soon
- 🔵 **monitor** — worth tracking, no immediate action needed

## Running Locally

```bash
pip install -r requirements.txt
cd src && python main.py                              # Run all sources
cd src && python main.py --sources samradsgatt        # Run one source
cd src && python main.py --skip-analysis              # Scrape only, no Claude
cd src && python self_heal.py --dry-run               # Diagnose broken scrapers (no fix)
cd src && python self_heal.py                         # Diagnose and fix broken scrapers
cd src && python self_heal.py --force --sources ust   # Force-check specific source
```

## First-Time Setup Checklist

These are **one-time** steps required before the system runs autonomously:

1. **GitHub Secrets** — Add these in repo Settings → Secrets and variables → Actions:
   - `CLAUDE_CODE_OAUTH_TOKEN` — primary OAuth token (see "Authentication" section above)
   - `CLAUDE_CODE_OAUTH_TOKEN_2` — secondary/failover token (from a different Claude Max account, or same account refreshed at a different time)

2. **GitHub Pages** — In repo Settings → Pages → Build and deployment → Source, select **GitHub Actions**

3. **Verify first run** — Trigger the workflow manually (Actions → Vaktin → Run workflow) and check:
   - The `Select working Claude token` step passes
   - The `Run Vaktin` step completes
   - `reports/.health.json` is committed with `"ok": true`
   - The Pages site is live at `https://<org>.github.io/vaktin/`

## Running in CI

The GitHub Actions workflow (`.github/workflows/vaktin.yml`) runs automatically on weekdays at :23 past 00, 12, 14, 16 and 18 UTC (Icelandic time = UTC). Not on the hour: GitHub delays or drops scheduled runs at :00. It can also be triggered manually via `workflow_dispatch` with optional source filtering.

The runner (`ubuntu-24.04`) and the Claude CLI (`@anthropic-ai/claude-code@2.1.296`) are pinned: `analyze.py` depends on the CLI's JSON envelope and flags. Bump them deliberately and check a run. The apt (poppler/tesseract) and Playwright installs are `continue-on-error`, so a package-mirror outage degrades PDF/OCR or JS-rendered sources instead of stopping the run (in October 2026 a mirror 404 stopped every run for five days).

Analysis runs `ANALYSIS_WORKERS = 3` items at a time (`analyze_batch`), handled in input order so checkpoints and fail-fast behave as before; checkpoints save failed items to pending too.

### Workflow pipeline
1. **Install** — Python deps, Playwright Chromium, Claude CLI
2. **Token check** — Tests primary token, falls back to secondary. Uses unique probe string `VAKTIN_TOKEN_OK` to avoid false positives.
3. **Run Vaktin** — Scrape → Analyze → Report. Writes `reports/.health.json`.
4. **Commit** — Commits `state/` and `reports/` to `main`. Fails loudly if push fails.
5. **Health check** — Reads `.health.json` and emits GitHub warning annotation if issues found.
6. **Self-heal** — If health issues detected, runs `self_heal.py` which uses Claude to diagnose and fix broken scrapers (changed URLs, merged municipalities, etc.). Fixes are committed automatically.
7. **Deploy Pages** — Builds Jekyll site from repo and deploys to GitHub Pages.

### Recurring maintenance
- **Token renewal (~every 30 days):** Tokens expire. The workflow fails over to the secondary token automatically. When BOTH expire, the workflow fails. To renew: run `claude login`, extract token, update the GitHub secret. Stagger renewals so both don't expire at the same time.
- **Scraper breakage:** Government websites change. Check `.health.json` — sources with `"status"` `"error"`, `"stale"` or `"empty"` are likely broken. Inspect the live HTML and update the CSS selectors in the scraper.

### GitHub Pages
Reports are automatically published to GitHub Pages after each run.

The site uses a custom layout (`_layouts/default.html`) with Jekyll config (`_config.yml`). All report markdown files include Jekyll front matter (`layout: default`) which is added automatically by `src/reporter.py`.

**Hosting stays on GitHub Pages.** The site is fully static — no login, no server-side state, no database — so Pages (custom domain via `CNAME`) covers it. Only reconsider hosting if a feature needs a server: accounts, a public dismiss button, server-side search.

### Site design (October 2026 redesign)

The look follows the case lists on island.is (Samráðsgátt) — a human-designed public-service UI the NGO audience already uses: white pages, one sans-serif family (IBM Plex Sans, as on island.is), light-bordered cards with 8px corners, tinted status tags, a tinted header band on the home page. Vaktin's logo green (`--brand: #2d6a4f`) replaces island.is blue so the site is never mistaken for a government page. The font is self-hosted in `assets/fonts/` (400, 400 italic, 500, 600; latin subset, which covers Icelandic; SIL OFL licence alongside) — the site makes no third-party requests.

It replaced a September 2026 "editorial field bulletin" look that read as generic AI-generated design. Do not reintroduce its devices: rotated cards, offset "sticker" shadows, oversized display numerals, serif-italic accents (Georgia deks), letter-spaced uppercase eyebrows, lime highlights.

- **No CSS framework and no build step.** `assets/vaktin.css` is hand-written. All colors, fonts and measures are tokens on `:root` at the top of the file; dark mode redefines the same tokens (`:root[data-theme="dark"]` and `prefers-color-scheme`). Change the look by editing tokens, not by adding per-element colors. The old layout used the Tailwind Play CDN, which compiles CSS in the browser at runtime and is not meant for production — do not reintroduce it.
- **Severity is never an emoji on the site.** `data-severity="critical|important|monitor"` on `.severity-section` and `.issue-item` sets `--sev` / `--sev-text` / `--sev-bg`; the layout draws the dot and the tinted tag. (Emails in `notify.py` still use emoji.)
- **Theme toggle** cycles auto → dark → light, stored in `localStorage['vaktin-theme']`. An inline script in `<head>` applies it before first paint.
- **Scraped text never reaches Liquid.** Jekyll runs Liquid over every generated page, so `_write_page` in `reporter.py` turns every `{`/`}` outside `<script>` into an HTML entity (a title containing `{%` used to be able to fail the whole Pages build). Write generated pages only through `_write_page`.
- **Deep links must stay exact.** Notification emails link to `/reports/#<item_id>`. Do not add `scroll-behavior: smooth` or `content-visibility: auto` to the page or the cards: both were tried and made the jump land thousands of pixels from the card on long lists. The sticky bars are cleared with `scroll-padding-top` on `html`. On `/reports/` the filter panel and histogram arrive above the list via JS, so `#filter-target:empty` reserves their measured height per breakpoint (closed panel on phones, open from 721px) — re-measure if the panel or histogram changes size.
- **Iframe embed.** The site is embedded on logverndarsjodur.org (Google Sites, sandboxed without top navigation). When framed, `vaktin.js` gives every off-site link `target="_blank"`, otherwise the destination's `X-Frame-Options` blanks the embed. `localStorage` can throw there, so the theme toggle keeps its state in memory.
- **Filters and histogram** on `/reports/` are built by `assets/vaktin.js` into `#filter-target`, and only when the page sets `window.VAKTIN_REGIONS`. They sit full width above the list: a collapsible panel (`<details>`, open from 721px, closed on phones) and the histogram. A sidebar layout was tried in October 2026 and rejected — it squeezed the histogram and took space beside the cards; keep both full width. The histogram covers the active window only (`window.VAKTIN_ACTIVE_START`): older cases kept active by an open deadline are listed but not charted. Source names in the filter come from `window.VAKTIN_SOURCE_NAMES` (`name` in `sources.yml`); cards use the same names. Deadlines with `data-deadline="YYYY-MM-DD"` get a relative label ("eftir 5 daga", "liðinn") and `.is-soon` (≤7 days) or `.is-past`.
- **Text search** (October 2026) is a field above the filter panel on `/reports/` (built by `vaktin.js`, visible on phones while the panel is closed) and a form on the home page that sends `/reports/?q=…`; the query stays in the URL, so a search can be linked. Letters are folded (case, accents, ð→d, þ→th, æ→ae: "seydisfj" finds Seyðisfjörður); every word must occur and "quoted words" must occur together; Icelandic inflection is not handled, so the no-result hint suggests typing a stem. The cards on the page are matched on their text as soon as someone types. `reports/leit.json` (`_write_search_index` in `reporter.py`, about 1.9 MB, ~0.5 MB gzipped) is fetched on first focus or a `?q=` link and adds every visible document as plain text: headline, dek, source title, summary, place, `project_is`, `case_refs` and the filter fields, but not the action text. With it, a card also matches when a document listed only by title under it does (its `details.related` opens and the entry gets `.is-match`), and archive documents that match and pass the region/category/source/time filters are listed under the cards (`#archive-hits`, 25 at a time, newest first, each linking to `archive/YYYY-MM/#<item_id>`). The search counts as a filter: it narrows the chip counts and the histogram, and "Hreinsa síur" clears it. If `leit.json` can't be fetched, the page's own cards are still searched.
- **Cards** are cases (see "One card per case"), listed by most recent activity within each severity group (parsed dates, not the raw date string). The card title is `headline_is` when present, with the document's own title under it in `.source-title`. A card's `data-region` and `data-source` are space-separated lists (a case can span regions and sources); `data-date` is its latest activity. The soonest open deadline in the case is shown. The `.meta` footer (category, source, place, region) is the last element of a card; on cards at least 880px wide it becomes a right-hand rail. That is a container query on `.prose` / `.reports-list`, so nothing `position: fixed` may live inside those (the histogram tooltip is appended to `<body>`).

**Markup contract.** `src/reporter.py` owns the HTML; the CSS and JS depend on these names, so change them together:

| Hook | Emitted by | Used for |
|---|---|---|
| `.severity-section[data-severity]` > `h2` > `.group-count` | index, org views, archive | sticky section heading, live count |
| `.issue-item` with `id`, `data-region`, `data-source`, `data-date`, `data-category`, `data-severity` | `_append_item_html` | filtering, histogram, email deep links (`/reports/#<item_id>`) |
| `.kicker` (`.kicker-sev`, `.kicker-date`), `h3 > a`, `.source-title`, `.dek`, `.deadline[data-deadline]`, `.summary`, `.action`, `details.related` > `ol.related-list` > `li[id][data-severity]` (`.related-date`, `.related-meta`), `.meta` (`.meta-part`, `.meta-sep`, `.meta-date`, `.region-tag`) — in that order | `_append_item_html`, `_append_related` | card layout; `.meta` becomes a right-hand rail on cards ≥880px wide; `.related` is the case timeline |
| `.reports-layout` > `#filter-target` + `.reports-list`, `#total-count`, `window.VAKTIN_REGIONS`, `window.VAKTIN_REGION_LABELS`, `window.VAKTIN_SOURCE_NAMES`, `window.VAKTIN_ACTIVE_START` | `generate_index` | filter UI above the list; `.reports-list` is the cards' size container |
| `.hero`, `.status-card`, `.facts`, `.quick` | `generate_home_page` | landing page (raw HTML blocks — keep them free of blank lines so kramdown passes them through) |
| `form.hero-search.search-field` > `input.search-input[name=q]` (action `reports/`) | `generate_home_page` | home page search; `/reports/` reads `?q=` |
| `reports/leit.json`: `{"v": 1, "docs": [{id, p, d, s, t, k, src, so, r, c, x}]}` — `p` is `""` (on `/reports/`) or `archive/YYYY-MM/`; `so`/`r`/`c` use the same formats as `data-source`/`data-region`/`data-category` | `_write_search_index` | text search on `/reports/`; `.search-box`, `.search-status`, `#archive-hits` > `.hit-list` > `.hit` and `li.is-match` are built by `vaktin.js` |

Every generated page is rewritten on each pipeline run, so markup changes reach the whole site (archive included) on the next run. `vaktin.js` also tidies pages generated before the redesign (strips emoji from headings and deadlines).

**Public organization feed.** `generate_org_view()` also writes `<org>/mal.json` (SUNN: `/sunn/mal.json`). It is a static JSON file without Jekyll front matter, with `schema_version: 1`, source/update metadata, case/document counts and `issues`. Each case preserves the page's primary document, source severity, visible document date, latest activity date, explicit deadline, plain-text dek/summary/action (`action` is the "Frestir og gögn" text: deadlines, procedure and documents, no advice), category/place/source metadata and related documents. It uses the exact case groups and timestamp already used for the page; it never publishes scraper state or hidden items. SUNN consumes this feed for its native `/malavakt/` view, attributes classification to Vaktin and links to both the source and Vaktin. GitHub Pages serves the feed with its normal public CORS headers. Tests: `python3 -m unittest discover -s tests -p 'test_*.py'`.

**Preview locally** without touching committed reports — regenerate into a scratch copy, then build:

```bash
rsync -a --exclude .git --exclude _site ./ /tmp/vaktin-preview/
(cd /tmp/vaktin-preview/src && python3 -c "from reporter import generate_index; generate_index([])")
jekyll build --source /tmp/vaktin-preview --destination /tmp/vaktin-site && (cd /tmp/vaktin-site && python3 -m http.server 8000)
```

A layout/CSS change only goes live when Pages is redeployed: that happens at the end of every Vaktin run, or on demand with the **Publish GitHub Pages** workflow (`publish-pages.yml`, manual trigger).

## Git and commit policy

- **Never push code changes.** Claude Code must NEVER run `git push` for code/logic changes (scrapers, config, workflow, layout, etc.). The developer handles all code commits and pushes.
- **State and reports are OK to commit/push** when asked — `state/` and `reports/` are data, not code. These are also auto-committed by CI after each run.
- When asked to make code changes, make the edits but leave committing and pushing to the developer.

## Contributing — Keep CLAUDE.md as the Single Source of Truth

This repo must behave identically on any machine that clones it. Claude Code's local memory (`.claude/`) is gitignored and does NOT travel with the repo. Therefore:

- **All project context, conventions, and architecture decisions MUST live in this file (CLAUDE.md).**
- When you learn something important about the project, add it here — not in local memory.
- When a design decision is made or changed, update this file.
- When a new scraper, source, or pattern is added, document it here.
- Anyone (human or AI) cloning this repo should be able to understand and run it fully from CLAUDE.md + the code itself.

## Language Conventions

- **Code, comments, variable names, config keys:** English
- **Report content, analysis output, summaries:** Icelandic
- **Source names/descriptions in sources.yml:** Icelandic with English notes
- **CLAUDE.md and documentation:** English (so AI tools can parse it reliably)
