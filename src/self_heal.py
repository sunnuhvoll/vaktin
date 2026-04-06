"""Self-healing diagnostics for Vaktin.

Analyzes health report and run logs to identify broken scrapers,
changed websites, merged municipalities, and other issues.
Generates a diagnostic prompt for Claude to investigate and fix.

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

# Sources where "empty" is expected and should not trigger healing
KNOWN_BROKEN = {
    "borgarbyggd",        # Fundagátt login required — no public fundargerðir page
}

# How many consecutive empty runs before triggering self-heal
EMPTY_THRESHOLD = 2


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
        return {"runs": [], "empty_streaks": {}}
    with open(HEAL_LOG_PATH) as f:
        return json.load(f)


def save_heal_log(log: dict) -> None:
    """Save healing history."""
    HEAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEAL_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def identify_issues(health: dict, heal_log: dict,
                    source_filter: list[str] | None = None) -> list[dict]:
    """Identify sources that need investigation."""
    issues = []
    sources = health.get("sources", {})
    streaks = heal_log.get("empty_streaks", {})

    for source_id, info in sources.items():
        if source_filter and source_id not in source_filter:
            continue
        if source_id in KNOWN_BROKEN:
            continue

        status = info.get("status", "")
        items = info.get("items", 0)

        if status == "empty":
            # Track consecutive empty runs
            streak = streaks.get(source_id, 0) + 1
            streaks[source_id] = streak

            if streak >= EMPTY_THRESHOLD:
                issues.append({
                    "source_id": source_id,
                    "type": "empty",
                    "streak": streak,
                    "message": f"Empty for {streak} consecutive runs",
                })
        elif status == "no_scraper":
            issues.append({
                "source_id": source_id,
                "type": "no_scraper",
                "streak": 0,
                "message": "No scraper mapped for this source",
            })
        else:
            # Reset streak on success
            streaks[source_id] = 0

    heal_log["empty_streaks"] = streaks
    return issues


def build_heal_prompt(issues: list[dict]) -> str:
    """Build a prompt for Claude to diagnose and fix issues."""
    with open(SOURCES_PATH) as f:
        sources = yaml.safe_load(f)

    prompt_parts = [
        "You are a maintenance agent for Vaktin, an Icelandic nature conservation monitoring system.",
        "Several scrapers are returning empty results. Your job is to diagnose WHY and fix them.",
        "",
        "IMPORTANT RULES:",
        "1. Read the CLAUDE.md file first for full project context",
        "2. For each broken source, fetch the live website and compare with the configured URL/selectors",
        "3. If a URL has changed, update config/sources.yml",
        "4. If CSS selectors need updating, update src/scrapers/sveitarfelog.py or the relevant scraper",
        "5. If a municipality has merged with another, update sources.yml and src/main.py",
        "6. If a website is permanently down (domain expired), remove the source",
        "7. Check https://www.samband.is/sveitarfelog/ for the current list of Icelandic municipalities",
        "8. Do NOT modify state/state.json or reports/",
        "9. After making changes, briefly explain what you fixed",
        "",
        "BROKEN SOURCES TO INVESTIGATE:",
        "",
    ]

    for issue in issues:
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
        prompt_parts.append(f"- Empty streak: {issue['streak']} runs")
        prompt_parts.append("")

    prompt_parts.append("Investigate each source and fix what you can. Be concise.")

    return "\n".join(prompt_parts)


def run_claude_heal(prompt: str) -> str:
    """Run Claude to diagnose and fix issues."""
    logger.info("Running Claude self-heal analysis...")

    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--max-turns", "20",
                "--output-format", "text",
                "--allowedTools",
                "Read", "Edit", "Write", "Glob", "Grep",
                "Bash(grep:*)", "Bash(curl:*)", "Bash(python3:*)",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=Path(__file__).parent.parent,
        )

        if result.returncode != 0:
            logger.error("Claude self-heal failed: %s", result.stderr[:500])
            return f"ERROR: {result.stderr[:500]}"

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

    health = load_health()
    if not health:
        logger.error("Cannot proceed without health report")
        sys.exit(1)

    heal_log = load_heal_log()

    # Find issues
    issues = identify_issues(health, heal_log, args.sources)

    if args.force and args.sources:
        # Force-investigate specific sources regardless of streak
        forced = []
        sources_cfg = health.get("sources", {})
        for sid in args.sources:
            if sid not in [i["source_id"] for i in issues]:
                info = sources_cfg.get(sid, {})
                forced.append({
                    "source_id": sid,
                    "type": info.get("status", "unknown"),
                    "streak": 0,
                    "message": f"Forced investigation (status: {info.get('status', 'unknown')})",
                })
        issues.extend(forced)

    if not issues:
        logger.info("No issues to heal — all sources healthy")
        save_heal_log(heal_log)
        return

    logger.info("Found %d sources needing attention:", len(issues))
    for issue in issues:
        logger.info("  %s: %s", issue["source_id"], issue["message"])

    if args.dry_run:
        logger.info("Dry run — not running Claude fix")
        prompt = build_heal_prompt(issues)
        print("\n--- PROMPT THAT WOULD BE SENT ---")
        print(prompt)
        save_heal_log(heal_log)
        return

    # Run Claude to fix
    prompt = build_heal_prompt(issues)
    result = run_claude_heal(prompt)

    # Log the result
    heal_log["runs"].append({
        "timestamp": datetime.now().isoformat(),
        "issues": [i["source_id"] for i in issues],
        "result_summary": result[:500],
    })
    # Keep only last 20 runs
    heal_log["runs"] = heal_log["runs"][-20:]

    save_heal_log(heal_log)
    logger.info("Self-heal complete")
    print(result)


if __name__ == "__main__":
    main()
