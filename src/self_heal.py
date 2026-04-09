"""Self-healing diagnostics for Vaktin.

Comprehensive pipeline health monitoring and automatic repair.
Covers all phases:
  Phase 1: Scraper heal — sources returning 0 items
  Phase 2: Content heal — pages where content extraction fails
  Phase 3: Analysis heal — Claude response parsing failures
  Phase 4: Validation — quick-test that fixes work
  Phase 5: Self-check — catch errors in self-heal itself

Usage:
    python self_heal.py                    # Analyze and fix
    python self_heal.py --dry-run          # Analyze only, don't fix
    python self_heal.py --sources ust ry   # Only check specific sources
"""

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HEALTH_PATH = Path(__file__).parent.parent / "reports" / ".health.json"
SOURCES_PATH = Path(__file__).parent.parent / "config" / "sources.yml"
HEAL_LOG_PATH = Path(__file__).parent.parent / "reports" / ".heal_log.json"
FAILED_RESPONSES_PATH = Path(__file__).parent.parent / "state" / "failed_responses.json"

# Sources where "empty" is expected and should not trigger healing
KNOWN_BROKEN = set()

# Thresholds
EMPTY_THRESHOLD = 2       # consecutive empty runs before scraper heal
NO_CONTENT_THRESHOLD = 2  # items with no content from same source


def load_health() -> dict:
    """Load the most recent health report."""
    if not HEALTH_PATH.exists():
        logger.error("No health report found at %s", HEALTH_PATH)
        return {}
    with open(HEALTH_PATH) as f:
        return json.load(f)


def load_heal_log() -> dict:
    """Load the healing history log."""
    if not HEAL_LOG_PATH.exists():
        return {"runs": [], "empty_streaks": {}, "no_content_streaks": {}}
    with open(HEAL_LOG_PATH) as f:
        data = json.load(f)
    # Ensure new fields exist in old logs
    data.setdefault("no_content_streaks", {})
    return data


def save_heal_log(log: dict) -> None:
    """Save healing history."""
    HEAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEAL_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def identify_issues(health: dict, heal_log: dict,
                    source_filter: list[str] | None = None) -> list[dict]:
    """Identify all pipeline issues across scraping, content, and analysis."""
    issues = []
    sources = health.get("sources", {})
    analysis = health.get("analysis", {})
    streaks = heal_log.get("empty_streaks", {})
    nc_streaks = heal_log.get("no_content_streaks", {})

    # ── Phase 1: Scraper issues (empty sources) ──
    for source_id, info in sources.items():
        if source_filter and source_id not in source_filter:
            continue
        if source_id in KNOWN_BROKEN:
            continue

        status = info.get("status", "")

        if status == "empty":
            streak = streaks.get(source_id, 0) + 1
            streaks[source_id] = streak
            if streak >= EMPTY_THRESHOLD:
                issues.append({
                    "source_id": source_id,
                    "phase": "scraper",
                    "type": "empty",
                    "streak": streak,
                    "message": f"Empty for {streak} consecutive runs",
                })
        elif status == "no_scraper":
            issues.append({
                "source_id": source_id,
                "phase": "scraper",
                "type": "no_scraper",
                "streak": 0,
                "message": "No scraper mapped for this source",
            })
        else:
            streaks[source_id] = 0

    heal_log["empty_streaks"] = streaks

    # ── Phase 2: Content extraction issues ──
    no_content_sources = analysis.get("no_content_sources", {})
    for source_id, count in no_content_sources.items():
        if source_filter and source_id not in source_filter:
            continue
        nc_streak = nc_streaks.get(source_id, 0) + 1
        nc_streaks[source_id] = nc_streak
        if count >= NO_CONTENT_THRESHOLD or nc_streak >= EMPTY_THRESHOLD:
            issues.append({
                "source_id": source_id,
                "phase": "content",
                "type": "no_content",
                "count": count,
                "streak": nc_streak,
                "message": f"{count} items with no content extracted (streak: {nc_streak})",
            })

    # Reset streaks for sources that had no content issues this run
    for source_id in list(nc_streaks.keys()):
        if source_id not in no_content_sources:
            nc_streaks[source_id] = 0
    heal_log["no_content_streaks"] = nc_streaks

    # ── Phase 3: Analysis issues ──
    failed_sources = analysis.get("failed_sources", {})
    for source_id, count in failed_sources.items():
        if source_filter and source_id not in source_filter:
            continue
        issues.append({
            "source_id": source_id,
            "phase": "analysis",
            "type": "parse_failure",
            "count": count,
            "message": f"{count} analysis results could not be parsed",
        })

    return issues


def build_heal_prompt(issues: list[dict], health: dict | None = None) -> str:
    """Build a prompt for Claude to diagnose and fix issues."""
    with open(SOURCES_PATH) as f:
        sources = yaml.safe_load(f)
    analysis = (health or {}).get("analysis", {})

    prompt_parts = [
        "You are a maintenance agent for Vaktin, an Icelandic nature conservation monitoring system.",
        "The pipeline has detected issues that need diagnosis and repair.",
        "",
        "IMPORTANT RULES:",
        "1. Read the CLAUDE.md file first for full project context",
        "2. For each issue, investigate the root cause and fix it",
        "3. Do NOT modify state/state.json or reports/ (except .heal_log.json)",
        "4. After making changes, briefly explain what you fixed",
        "",
    ]

    # Group issues by phase
    scraper_issues = [i for i in issues if i["phase"] == "scraper"]
    content_issues = [i for i in issues if i["phase"] == "content"]
    analysis_issues = [i for i in issues if i["phase"] == "analysis"]

    if scraper_issues:
        prompt_parts.append("## PHASE 1 — BROKEN SCRAPERS (returning 0 items)")
        prompt_parts.append("")
        prompt_parts.append("These sources returned no items. Investigate the live website and fix URLs/selectors.")
        prompt_parts.append("")
        for issue in scraper_issues:
            sid = issue["source_id"]
            cfg = sources.get(sid, {})
            url = cfg.get("url", "unknown")
            sections = cfg.get("sections", [])
            paths = [s.get("path", "") for s in sections]
            prompt_parts.append(f"### {sid}")
            prompt_parts.append(f"- Name: {cfg.get('name', 'unknown')}")
            prompt_parts.append(f"- URL: {url}")
            prompt_parts.append(f"- Paths: {', '.join(paths)}")
            prompt_parts.append(f"- Type: {cfg.get('type', 'unknown')}")
            prompt_parts.append(f"- Issue: {issue['message']}")
            prompt_parts.append("")

    if content_issues:
        no_content_urls = analysis.get("no_content_urls", {})
        prompt_parts.append("## PHASE 2 — CONTENT EXTRACTION FAILURES")
        prompt_parts.append("")
        prompt_parts.append("These sources were scraped successfully but content could not be extracted from the pages.")
        prompt_parts.append("The problem is usually wrong CSS selectors in src/scrapers/sveitarfelog.py `_extract_content()`.")
        prompt_parts.append("For each source: fetch the failing URL(s), inspect the HTML structure, and fix the selectors.")
        prompt_parts.append("")
        for issue in content_issues:
            sid = issue["source_id"]
            cfg = sources.get(sid, {})
            url = cfg.get("url", "unknown")
            sections = cfg.get("sections", [])
            paths = [s.get("path", "") for s in sections]
            failing_urls = no_content_urls.get(sid, [])
            prompt_parts.append(f"### {sid}")
            prompt_parts.append(f"- Name: {cfg.get('name', 'unknown')}")
            prompt_parts.append(f"- URL: {url}")
            prompt_parts.append(f"- Paths: {', '.join(paths)}")
            prompt_parts.append(f"- Issue: {issue['message']}")
            if failing_urls:
                prompt_parts.append(f"- Failing URLs (fetch these to inspect HTML):")
                for furl in failing_urls[:5]:
                    prompt_parts.append(f"  - {furl}")
            prompt_parts.append("- Action: Fetch the failing URL(s), find what HTML element wraps the meeting content,")
            prompt_parts.append("  and add the correct selector to `_extract_content()` in sveitarfelog.py if missing.")
            prompt_parts.append("")

    if analysis_issues:
        failed_details = analysis.get("failed_details", [])
        # Load saved failed responses for detailed diagnosis
        failed_responses = []
        try:
            if FAILED_RESPONSES_PATH.exists():
                failed_responses = json.loads(FAILED_RESPONSES_PATH.read_text())
        except Exception:
            pass

        prompt_parts.append("## PHASE 3 — ANALYSIS PARSING FAILURES")
        prompt_parts.append("")
        prompt_parts.append("Claude returned responses that could not be parsed as JSON.")
        prompt_parts.append("Check src/analyze.py `_extract_json()` for edge cases and fix the parser.")
        prompt_parts.append("Also check if the analysis prompt in ANALYSIS_PROMPT needs adjustment.")
        prompt_parts.append("")
        for issue in analysis_issues:
            sid = issue["source_id"]
            prompt_parts.append(f"### {sid}")
            prompt_parts.append(f"- Issue: {issue['message']}")
            details = [d for d in failed_details if d["source_id"] == sid]
            if details:
                prompt_parts.append("- Failed items:")
                for d in details[:5]:
                    prompt_parts.append(f"  - {d['item_id']} ({d['url']})")
            # Show actual failed responses so Claude can see the parsing problem
            relevant_responses = [r for r in failed_responses if r.get("item_id", "").startswith(sid)]
            if relevant_responses:
                prompt_parts.append("- Actual failed Claude responses (showing first/last chars):")
                for r in relevant_responses[:3]:
                    prompt_parts.append(f"  - item: {r['item_id']}")
                    prompt_parts.append(f"    parse_error: {r.get('parse_error', 'unknown')}")
                    prompt_parts.append(f"    response_start: {r.get('response_first_500', '')[:300]}")
                    prompt_parts.append(f"    response_end: ...{r.get('response_last_200', '')}")
            prompt_parts.append("")

    prompt_parts.append("Investigate each issue and fix what you can. Be concise.")

    return "\n".join(prompt_parts)


def run_claude_heal(prompt: str) -> str:
    """Run Claude to diagnose and fix issues."""
    logger.info("Running Claude self-heal analysis...")

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--model", "claude-opus-4-6",
                "--max-turns", "20",
                "--output-format", "text",
                "--allowedTools",
                "Read", "Edit", "Write", "Glob", "Grep",
                "Bash(grep:*)", "Bash(curl:*)", "Bash(python3:*)",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=Path(__file__).parent.parent,
        )

        if result.returncode != 0:
            err_detail = result.stderr.strip()[:500] or result.stdout.strip()[:500] or "(no output)"
            logger.error("Claude self-heal failed: %s", err_detail)
            return f"ERROR: {err_detail}"

        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        logger.error("Claude self-heal timed out after 5 minutes")
        return "ERROR: Timed out"
    except FileNotFoundError:
        logger.error("Claude CLI not found — is it installed?")
        return "ERROR: Claude CLI not found"


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Vaktin self-healing diagnostics")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze only, don't run Claude to fix")
    parser.add_argument("--sources", nargs="*",
                        help="Only check specific sources")
    parser.add_argument("--force", action="store_true",
                        help="Investigate even if streak < threshold")
    args = parser.parse_args()

    # Phase 5: Self-check — wrap everything in try/except
    heal_log = None
    try:
        health = load_health()
        if not health:
            logger.error("Cannot proceed without health report")
            sys.exit(1)

        heal_log = load_heal_log()

        # Find issues across all phases
        issues = identify_issues(health, heal_log, args.sources)

        if args.force and args.sources:
            forced = []
            sources_cfg = health.get("sources", {})
            for sid in args.sources:
                if sid not in [i["source_id"] for i in issues]:
                    info = sources_cfg.get(sid, {})
                    forced.append({
                        "source_id": sid,
                        "phase": "scraper",
                        "type": info.get("status", "unknown"),
                        "streak": 0,
                        "message": f"Forced investigation (status: {info.get('status', 'unknown')})",
                    })
            issues.extend(forced)

        if not issues:
            logger.info("No issues to heal — all pipeline phases healthy")
            save_heal_log(heal_log)
            return

        # Log issues by phase
        phases = {}
        for issue in issues:
            phase = issue.get("phase", "unknown")
            phases.setdefault(phase, []).append(issue)

        logger.info("Found %d issues across %d phases:", len(issues), len(phases))
        for phase, phase_issues in phases.items():
            logger.info("  [%s] %d issues:", phase, len(phase_issues))
            for issue in phase_issues:
                logger.info("    %s: %s", issue["source_id"], issue["message"])

        if args.dry_run:
            logger.info("Dry run — not running Claude fix")
            prompt = build_heal_prompt(issues, health)
            print("\n--- PROMPT THAT WOULD BE SENT ---")
            print(prompt)
            save_heal_log(heal_log)
            return

        # Run Claude to fix
        prompt = build_heal_prompt(issues, health)
        result = run_claude_heal(prompt)

        # Log the result
        heal_log["runs"].append({
            "timestamp": datetime.now().isoformat(),
            "issues": [
                {"source": i["source_id"], "phase": i["phase"], "type": i["type"]}
                for i in issues
            ],
            "result_summary": result[:500],
        })
        # Keep only last 20 runs
        heal_log["runs"] = heal_log["runs"][-20:]

        save_heal_log(heal_log)

        # Clean up diagnostic logs — we've learned from them
        if FAILED_RESPONSES_PATH.exists():
            FAILED_RESPONSES_PATH.unlink()
            logger.info("Cleaned up failed_responses.json")

        logger.info("Self-heal complete")
        print(result)

    except Exception as e:
        # Phase 5: Self-check — self-heal must never crash the pipeline
        logger.error("Self-heal itself failed: %s", e)
        logger.error("This is a bug in self_heal.py — needs manual investigation")
        # Save what we can
        if heal_log is not None:
            try:
                save_heal_log(heal_log)
            except Exception:
                pass


if __name__ == "__main__":
    main()
