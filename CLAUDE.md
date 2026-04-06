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
│   │   ├── uos.py                # UOS (Umhverfis- og orkustofnun) — Prismic CMS API
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
│   ├── .health.json              # Health report from last run (ok/errors per source)
│   ├── weekly/                   # Weekly summary reports (YYYY-VWW.md)
│   └── archive/                  # Resolved/old items (manual move)
├── COVERAGE.md                   # Full coverage table — all 62 municipalities + 8 national agencies
├── sources.md                    # Jekyll page — published coverage overview on GitHub Pages
├── index.md                      # Jekyll page — landing page for GitHub Pages site
├── .github/
│   └── workflows/
│       └── vaktin.yml            # Runs weekdays at 08:00 UTC, commits results back
└── requirements.txt              # Python: requests, beautifulsoup4, pyyaml, lxml, playwright
```

## Key Architecture Decisions

### Analysis via `claude -p` (not Anthropic API)
The project uses Claude Code CLI in pipe mode (`claude -p`) for all AI analysis. This requires:
- `npm install -g @anthropic-ai/claude-code` in the workflow
- Content is piped to Claude with `--output-format json`

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
4. Truncate content to 10-15k chars before analysis

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

### Dismissed items
Items can be manually removed from the active index by adding their `item_id` to `reports/.dismissed.json` (a JSON array of strings) and committing the change. Dismissed items are filtered out during `generate_index()` and will not reappear even if the scraper finds them again. This is intended for git committers (superusers), not end users — there is no dismiss button on the public site.

### No weekly reports
The system runs multiple times per week, making weekly summary reports redundant. The active issues index (`reports/index.md`) is the primary view. Weekly report generation has been removed.

### Delta strategy — long-term performance
The system must not slow down after months of operation. Three mechanisms work together:

**1. MAX_AGE_DAYS (safety net for all scrapers)** — `BaseScraper.MAX_AGE_DAYS = 30`. The `_is_too_old(date_str)` method skips items older than 30 days regardless of state. Supports ISO 8601, RFC 2822, Icelandic numeric formats (`d.m.yyyy`), and Icelandic month names (`2. júlí '25`, `4. feb '26`) via `_parse_icelandic_date()`.

**2. Timestamp-based deltas (preferred)** — Use `last_check` to query only items newer than the last run. On first run, falls back to `_max_age_cutoff()` (30 days ago) instead of a hardcoded date. Use when the source API supports date filtering:
- `samradsgatt.py` — GraphQL API supports date predicates
- `uos.py` — Prismic CMS API supports `gt(first_publication_date, ...)` predicates
- `rss.py` — RSS/Atom feeds have `pubDate`/`published` for client-side filtering

**3. Seen-IDs with cap (fallback)** — Maintain a capped list of processed item IDs in `state.json`. Use this when the source has no date-based filtering (HTML scraping, APIs without date params):
- `ust.py`, `sveitarfelog.py` — HTML scraped, no API
- `althingi.py` — XML API returns no date fields (has its own first-run seeding)
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
- `sources.<id>.status`: `"ok"` (found items), `"empty"` (0 items — likely broken scraper), `"no_scraper"` (no matching scraper class)
- `analysis.failed`: number of items where Claude analysis failed
- `errors`: human-readable list of problems

### Scraper types

**API-based scrapers** (most reliable):
- `samradsgatt.py` — Uses island.is public GraphQL API (`https://island.is/api/graphql`). Query names: `consultationPortalGetCases`, `consultationPortalCaseById`. Input types: `ConsultationPortalCasesInput`, `ConsultationPortalCaseInput`.
- `skipulagsstofnun.py` — Uses island.is `getGenericListItems` GraphQL query with GenericList ID `6PA6bW36D1LIHI3iueZX6t` (the HMS EIA database). 1,575+ cases in the database.
- `island_news.py` — Generic scraper for island.is organization news. Uses `getNews` GraphQL query with `organization` filter. Config key: `island_org`. Used for Fiskistofa (`fiskistofa`) and Land og skógur (`land-og-skogur`).
- `uos.py` — Uses Prismic CMS API at `https://uos-web.cdn.prismic.io/api/v2`. Queries news documents. Must fetch master ref first, then search by document type "news".

**XML/RSS scrapers** (reliable):
- `althingi.py` — Uses Alþingi XML REST API at `https://www.althingi.is/altext/xml/`. Fetches bills filtered by 6 nature-relevant subject categories (efnisflokkar): 31 (umhverfisstjórn), 30 (orkumál), 29 (mengun), 24 (samgöngur), 3 (landbúnaður), 4 (sjávarútvegur). Current session: 157.
- `rss.py` — Generic RSS/Atom feed scraper. Parses standard RSS 2.0 and Atom feeds. Used for Vegagerðin, Náttúrufræðistofnun, MAST, and Hafrannsóknastofnun (`hafogvatn.is/feed`). Optionally fetches full article content when RSS description is short.

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
- **Borgarbyggð** (`borgarbyggd.is`) — Fundargerðir behind Fundagátt.is login. Cannot scrape. Only known-broken source.
- **Hornafjörður** (`hornafjordur.is`) — Uses ASP.NET SearchMeetings.aspx with ViewState. Limited extraction.
- **Vestmannaeyjabær** — Fundargerðir on separate subdomain (`ibuagatt.vestmannaeyjar.is`). Works but content extraction is limited.
- **Árborg** (`arborg.is`) — SSL certificate expired. Fixed with `ssl_verify: false` in config.
- **"No content element" warnings** — Some municipality sub-pages don't have a parseable `<article>` or `<main>` element. Items are still listed (titles + URLs) but without full content. This is cosmetic — Claude analysis still works on the title + metadata.
- **Alþingi session number** — Hardcoded to session 157 (2025-2026). Must be updated when a new legislative session starts (typically every September). Config key: `session` in sources.yml.

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

The GitHub Actions workflow (`.github/workflows/vaktin.yml`) runs automatically on weekdays at 08:00 UTC. It can also be triggered manually via `workflow_dispatch` with optional source filtering.

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
- **Scraper breakage:** Government websites change. Check `.health.json` — sources with `"status": "empty"` likely have broken selectors. Inspect the live HTML and update the CSS selectors in the scraper.

### GitHub Pages
Reports are automatically published to GitHub Pages after each run.

The site uses a custom layout (`_layouts/default.html`) with Jekyll config (`_config.yml`). All report markdown files include Jekyll front matter (`layout: default`) which is added automatically by `src/reporter.py`.

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
