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
│   │   ├── samradsgatt.py        # Samráðsgátt ríkisins (government consultation portal)
│   │   ├── skipulagsstofnun.py   # Skipulagsstofnun (National Planning Agency / EIA)
│   │   ├── ust.py                # Umhverfisstofnun (Environment Agency)
│   │   └── sveitarfelog.py       # Generic municipality scraper (fundargerðir)
│   ├── analyze.py                # Claude -p integration — contains the analysis prompt
│   ├── reporter.py               # Markdown report generation (index + weekly)
│   └── main.py                   # Orchestrator — runs scrapers → analysis → reports
├── state/
│   └── state.json                # Persistent state — tracks seen_ids per source (committed to git)
├── reports/
│   ├── index.md                  # Active issues — current overview sorted by severity
│   ├── .index_data.json          # Structured cache of index items
│   ├── .health.json              # Health report from last run (ok/errors per source)
│   ├── weekly/                   # Weekly summary reports (YYYY-VWW.md)
│   └── archive/                  # Resolved/old items (manual move)
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

### Authentication — OAuth tokens (not API keys)
Authentication uses **Claude Code OAuth tokens** from a Claude Max subscription, NOT Anthropic API keys.

- GitHub secrets: `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_CODE_OAUTH_TOKEN_2` (failover)
- Tokens start with `sk-ant-oat01-...`
- Tokens expire every ~30 days — renew by running `claude login` and extracting the new token
- The workflow tests the primary token first, falls back to secondary if expired

**To get/renew a token (macOS):**
```bash
claude login
security find-generic-password -s "Claude Code-credentials" -w \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])"
```
Then update the secret at the repo's GitHub Settings → Secrets → Actions.

### State tracking — scrape only what's new
Each scraper maintains a list of `seen_ids` in `state/state.json`. This file is committed to git so state persists across GitHub Actions runs. When adding a new scraper:
1. Inherit from `BaseScraper` in `src/scrapers/base.py`
2. Call `self.load_state()` at start, `self.save_state()` at end
3. Keep seen_ids capped (300-500) to prevent unbounded growth
4. Truncate content to 10-15k chars before analysis

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

### Scraper resilience
Scrapers use CSS selectors with multiple fallbacks since Icelandic government websites vary in structure. When a scraper finds no elements:
- It logs a WARNING naming the source and section
- It records `"status": "empty"` in the health report
- It returns an empty list (does not crash the pipeline)
- Other scrapers continue running
- Fix by inspecting the live site HTML and updating selectors in the scraper file

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
6. **Deploy Pages** — Builds Jekyll site from repo and deploys to GitHub Pages.

### Recurring maintenance
- **Token renewal (~every 30 days):** Tokens expire. The workflow fails over to the secondary token automatically. When BOTH expire, the workflow fails. To renew: run `claude login`, extract token, update the GitHub secret. Stagger renewals so both don't expire at the same time.
- **Scraper breakage:** Government websites change. Check `.health.json` — sources with `"status": "empty"` likely have broken selectors. Inspect the live HTML and update the CSS selectors in the scraper.

### GitHub Pages
Reports are automatically published to GitHub Pages after each run.

The site uses a custom layout (`_layouts/default.html`) with Jekyll config (`_config.yml`). All report markdown files include Jekyll front matter (`layout: default`) which is added automatically by `src/reporter.py`.

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
