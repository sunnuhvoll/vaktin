"""Vaktin — Main orchestrator.

Runs all scrapers, analyzes new content, and generates reports.
"""

import logging
import sys
from pathlib import Path

import yaml

from scrapers.base import ScrapedItem
from scrapers.samradsgatt import SamradsgattScraper
from scrapers.skipulagsstofnun import SkipulagsstofnunScraper
from scrapers.sveitarfelog import SveitarfelagScraper
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
}

# Municipality sources use the generic scraper
MUNICIPALITY_SOURCES = {
    "reykjavik", "kopavogur", "hafnarfjordur", "akureyri",
    "gardabaer", "mosfellsbaer", "skagafjordur", "vesturbyggd",
    "sudurnesjabaer", "rangarthing_eystra", "husavik", "ísafjarðarbær",
}

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sources.yml"


def load_sources() -> dict:
    """Load source configuration from YAML."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


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

    # Scrape all sources
    all_items: list[ScrapedItem] = []

    for source_id, config in sources.items():
        if source_filter and source_id not in source_filter:
            continue

        scraper = create_scraper(source_id, config)
        if scraper:
            items = scraper.run()
            all_items.extend(items)

    logger.info(f"Total new items found: {len(all_items)}")

    if not all_items:
        logger.info("No new items to analyze.")
        # Still update index in case we need to refresh it
        generate_index([])
        return

    # Analyze with Claude
    if skip_analysis:
        logger.info("Skipping analysis (--skip-analysis flag)")
        results = [item.to_dict() for item in all_items]
    else:
        logger.info(f"Analyzing {len(all_items)} items with Claude...")
        results = analyze_batch(all_items)
        logger.info(f"Found {len(results)} relevant items")

    # Generate reports
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


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Vaktin — Icelandic Nature Conservation Monitor")
    parser.add_argument("--sources", nargs="*", help="Only run specific sources (e.g. samradsgatt reykjavik)")
    parser.add_argument("--skip-analysis", action="store_true", help="Skip Claude analysis step")
    args = parser.parse_args()

    run(source_filter=args.sources, skip_analysis=args.skip_analysis)


if __name__ == "__main__":
    main()
