"""Vaktin — Main orchestrator.

Runs all scrapers, analyzes new content, and generates reports.
Writes a health report (reports/.health.json) after every run so failures
are visible in the committed output, not just in CI logs.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml

from scrapers.base import ScrapedItem, close_browser
from scrapers.samradsgatt import SamradsgattScraper
from scrapers.skipulagsstofnun import SkipulagsstofnunScraper
from scrapers.sveitarfelog import SveitarfelagScraper
from scrapers.uos import UosScraper
from scrapers.ust import UstScraper
from analyze import analyze_batch
from reporter import generate_index, generate_weekly_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Map source IDs to scraper classes based on source type
SCRAPER_MAP = {
    "samradsgatt": SamradsgattScraper,
    "skipulagsstofnun": SkipulagsstofnunScraper,
    "ust": UstScraper,
    "orkustofnun": UosScraper,
}

# Municipality sources use the generic scraper
MUNICIPALITY_SOURCES = {
    "reykjavik", "kopavogur", "hafnarfjordur", "akureyri",
    "gardabaer", "mosfellsbaer", "skagafjordur", "vesturbyggd",
    "sudurnesjabaer", "rangarthing_eystra", "husavik", "ísafjarðarbær",
}

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sources.yml"
HEALTH_PATH = Path(__file__).parent.parent / "reports" / ".health.json"


def load_sources() -> dict:
    """Load source configuration from YAML."""
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        logger.error(f"FATAL: Cannot load {CONFIG_PATH}: {e}")
        sys.exit(1)


def create_scraper(source_id: str, config: dict):
    """Create the appropriate scraper for a source."""
    if source_id in SCRAPER_MAP:
        return SCRAPER_MAP[source_id](source_id, config)
    if source_id in MUNICIPALITY_SOURCES or config.get("type") == "html_scrape":
        return SveitarfelagScraper(source_id, config)
    logger.warning(f"No scraper for source: {source_id}")
    return None


def run(source_filter: list[str] | None = None, skip_analysis: bool = False) -> None:
    """Run the full pipeline: scrape → analyze → report."""
    sources = load_sources()
    health = {
        "run_start": datetime.now().isoformat(),
        "sources": {},
        "analysis": {},
        "errors": [],
    }

    # ── Scrape ──────────────────────────────────────────────
    all_items: list[ScrapedItem] = []

    try:
        for source_id, config in sources.items():
            if source_filter and source_id not in source_filter:
                continue

            scraper = create_scraper(source_id, config)
            if not scraper:
                health["sources"][source_id] = {"status": "no_scraper", "items": 0}
                continue

            items = scraper.run()
            all_items.extend(items)

            # Record health per source
            health["sources"][source_id] = {
                "status": "ok" if items else "empty",
                "items": len(items),
            }
            if not items:
                logger.warning(
                    f"[{source_id}] Returned 0 items — scraper may be broken "
                    f"or site structure changed"
                )
    finally:
        close_browser()

    logger.info(f"Total new items found: {len(all_items)}")

    # ── Analyze ─────────────────────────────────────────────
    if not all_items:
        logger.info("No new items to analyze.")
        generate_index([])
        _write_health(health)
        return

    if skip_analysis:
        logger.info("Skipping analysis (--skip-analysis flag) — no reports will be generated")
        logger.info(f"Scraped {len(all_items)} items from {len(set(i.source_id for i in all_items))} sources")
        for source_id in sorted(set(i.source_id for i in all_items)):
            count = sum(1 for i in all_items if i.source_id == source_id)
            logger.info(f"  {source_id}: {count} items")
        health["analysis"] = {"skipped": True}
        _write_health(health)
        return
    else:
        logger.info(f"Analyzing {len(all_items)} items with Claude...")
        results, analysis_stats = analyze_batch(all_items)
        health["analysis"] = analysis_stats
        logger.info(
            f"Analysis: {analysis_stats['relevant']} relevant, "
            f"{analysis_stats['not_relevant']} irrelevant, "
            f"{analysis_stats['failed']} failed, "
            f"{analysis_stats['skipped_no_content']} skipped (no content)"
        )
        if analysis_stats["failed"] > 0:
            health["errors"].append(
                f"{analysis_stats['failed']}/{analysis_stats['total']} analyses failed"
            )

    # ── Report ──────────────────────────────────────────────
    generate_index(results)
    weekly_path = generate_weekly_report(results)
    if weekly_path:
        logger.info(f"Weekly report: {weekly_path}")

    # Summary
    if results:
        critical = sum(1 for r in results if r.get("severity") == "critical")
        important = sum(1 for r in results if r.get("severity") == "important")
        monitor = sum(1 for r in results if r.get("severity") == "monitor")
        logger.info(f"Summary: {critical} critical, {important} important, {monitor} monitor")
    else:
        logger.info("No relevant items found in this run.")

    _write_health(health)


def _write_health(health: dict) -> None:
    """Write health report to reports/.health.json (committed to git)."""
    health["run_end"] = datetime.now().isoformat()

    # Count problems
    empty_sources = [s for s, v in health["sources"].items() if v["status"] == "empty"]
    if empty_sources:
        health["errors"].append(f"Sources returned 0 items: {', '.join(empty_sources)}")

    health["ok"] = len(health["errors"]) == 0

    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_PATH, "w") as f:
        json.dump(health, f, indent=2, ensure_ascii=False)

    if not health["ok"]:
        logger.warning(f"HEALTH ISSUES: {'; '.join(health['errors'])}")
    else:
        logger.info("Health: OK — no issues detected")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Vaktin — Icelandic Nature Conservation Monitor")
    parser.add_argument("--sources", nargs="*", help="Only run specific sources (e.g. samradsgatt reykjavik)")
    parser.add_argument("--skip-analysis", action="store_true", help="Skip Claude analysis step")
    args = parser.parse_args()

    run(source_filter=args.sources, skip_analysis=args.skip_analysis)


if __name__ == "__main__":
    main()
