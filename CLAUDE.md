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
│   │   ├── base.py               # Base scraper class with state tracking and HTTP session
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
│   ├── weekly/                   # Weekly summary reports (YYYY-VWW.md)
│   └── archive/                  # Resolved/old items (manual move)
├── .github/
│   └── workflows/
│       └── vaktin.yml            # Runs weekdays at 08:00 UTC, commits results back
└── requirements.txt              # Python: requests, beautifulsoup4, pyyaml, lxml
```

## Key Architecture Decisions

### Analysis via `claude -p` (not Anthropic API)
The project uses Claude Code CLI in pipe mode (`claude -p`) for all AI analysis. This requires:
- `ANTHROPIC_API_KEY` stored as a GitHub Actions secret
- `npm install -g @anthropic-ai/claude-code` in the workflow
- Content is piped to Claude with `--output-format json`

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

### Scraper resilience
Scrapers use best-effort CSS selectors with multiple fallbacks since Icelandic government websites vary in structure and may change. When a scraper breaks:
- It logs the error and returns an empty list (doesn't crash the pipeline)
- Other scrapers continue running
- Fix by inspecting the live site HTML and updating selectors

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

## Running in CI

The GitHub Actions workflow (`.github/workflows/vaktin.yml`) runs automatically on weekdays at 08:00 UTC. It can also be triggered manually via `workflow_dispatch` with optional source filtering.

Required GitHub secret: `ANTHROPIC_API_KEY`

## Language Conventions

- **Code, comments, variable names, config keys:** English
- **Report content, analysis output, summaries:** Icelandic
- **Source names/descriptions in sources.yml:** Icelandic with English notes
- **CLAUDE.md and documentation:** English (so AI tools can parse it reliably)
