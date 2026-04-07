"""Vaktin — Main orchestrator.

Runs all scrapers, analyzes new content, and generates reports.
Writes a health report (reports/.health.json) after every run so failures
are visible in the committed output, not just in CI logs.

Pending analysis: items that were scraped but not analyzed (due to
--skip-analysis or analysis failure) are saved to state/pending.json
and automatically picked up on the next run.
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from scrapers.althingi import AlthingiScraper
from scrapers.base import ScrapedItem, close_browser
from scrapers.island_news import IslandNewsScraper
from scrapers.rss import RssScraper
from scrapers.samradsgatt import SamradsgattScraper
from scrapers.skipulagsgatt import SkipulagsgattScraper
from scrapers.skipulagsstofnun import SkipulagsstofnunScraper
from scrapers.sveitarfelog import SveitarfelagScraper
from scrapers.uos import UosScraper
from scrapers.ust import UstScraper
from scrapers.wp_graphql import WpGraphqlScraper
from analyze import analyze_batch
from notify import send_notification
from reporter import generate_index

PENDING_FILE = Path(__file__).parent.parent / "state" / "pending.json"
MAX_CONSECUTIVE_FAILURES = 3  # Stop analysis after this many failures in a row
PENDING_MAX_AGE_DAYS = 7  # Drop pending items older than this

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Map source IDs to scraper classes based on source type
SCRAPER_MAP = {
    "samradsgatt": SamradsgattScraper,
    "skipulagsgatt": SkipulagsgattScraper,
    "skipulagsstofnun": SkipulagsstofnunScraper,
    "ust": UstScraper,
    "orkustofnun": UosScraper,
    "althingi": AlthingiScraper,
    "vegagerdin": RssScraper,
    "natturufraedistofnun": RssScraper,
    "mast": RssScraper,
    "hafrannsoknastofnun": RssScraper,
    "ferdamalastofa": UstScraper,
    "tjorneshreppur": WpGraphqlScraper,
}

# Municipality sources use the generic scraper
MUNICIPALITY_SOURCES = {
    # Stig 2 — stærstu sveitarfélögin
    "reykjavik", "kopavogur", "hafnarfjordur", "akureyri",
    "gardabaer", "mosfellsbaer",
    # Stig 3 — önnur mikilvæg sveitarfélög
    "skagafjordur", "vesturbyggd", "sudurnesjabaer", "rangarthing_eystra",
    "husavik", "ísafjarðarbær", "grindavik", "hornafjordur",
    "fjardabyggd", "mulathing", "snaefellsbaer", "blaskogabyggd",
    "hvalfjardarsveit", "olfus",
    # Höfuðborgarsvæðið
    "seltjarnarnes", "kjosarhreppur",
    # Suðurnes
    "reykjanesbaer", "vogar",
    # Vesturland
    "akranes", "borgarbyggd", "dalabyggd", "eyja_og_miklaholtshreppur",
    "grundarfjordur", "skorradalshreppur", "stykkisholmur",
    # Vestfirðir
    "bolungarvik", "kaldrananeshreppur", "reykholar", "strandabyggd",
    "sudavik", "arneshreppur",
    # Norðurland vestra
    "hunabyggd", "hunathing_vestra", "skagastrond",
    # Norðurland eystra
    "dalvikurbyggd", "eyjafjardarsveit", "fjallabyggd", "grytubakkahreppur",
    "horgarsveit", "langanesbyggd", "svalbardsstrond",
    "thingeyjarsveit",
    # Austurland
    "fljotsdalshreppur", "vopnafjardarhreppur",
    # Suðurland
    "floahreppur", "grimsnes_og_grafningshreppur", "hrunamannahreppur",
    "hveragerdi", "myrdalshreppur", "rangarthing_ytra", "skaftarhreppur",
    "skeida_og_gnupverjahreppur", "arborg", "vestmannaeyjar", "asahreppur",
}

CONFIG_PATH = Path(__file__).parent.parent / "config" / "sources.yml"
HEALTH_PATH = Path(__file__).parent.parent / "reports" / ".health.json"

# Maximum content length stored per pending item (avoid bloating state)
MAX_PENDING_CONTENT = 12000


def _load_pending() -> list[ScrapedItem]:
    """Load items pending analysis from previous runs.

    Items older than PENDING_MAX_AGE_DAYS (based on _pending_since) are
    dropped to prevent unbounded growth.
    """
    if not PENDING_FILE.exists():
        return []
    try:
        with open(PENDING_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Could not load pending items: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=PENDING_MAX_AGE_DAYS)
    items = []
    expired = 0
    for d in data:
        pending_since = d.pop("_pending_since", None)
        if pending_since:
            try:
                ts = datetime.fromisoformat(pending_since)
                if ts < cutoff:
                    expired += 1
                    continue
            except (ValueError, TypeError):
                pass  # keep items with unparseable timestamps
        else:
            # Legacy items without _pending_since — drop them (they're stale)
            expired += 1
            continue
        try:
            item = ScrapedItem(**d)
            # Preserve _pending_since in metadata for future saves
            if pending_since:
                item.metadata["_pending_since"] = pending_since
            items.append(item)
        except TypeError:
            expired += 1

    if expired:
        logger.info(f"Dropped {expired} expired pending items (older than {PENDING_MAX_AGE_DAYS} days)")
    if items:
        logger.info(f"Loaded {len(items)} pending items from previous run")
    return items


def _save_pending(items: list[ScrapedItem]) -> None:
    """Save items pending analysis for the next run.

    Each item gets a _pending_since timestamp (stored in metadata)
    so old items can be expired on the next load.
    """
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    data = []
    for item in items:
        # Stamp when item first entered pending (preserve existing)
        if "_pending_since" not in item.metadata:
            item.metadata["_pending_since"] = now
        d = item.to_dict()
        # Promote _pending_since to top level for load_pending
        d["_pending_since"] = item.metadata["_pending_since"]
        # Truncate content to keep pending.json manageable
        if d.get("content") and len(d["content"]) > MAX_PENDING_CONTENT:
            d["content"] = d["content"][:MAX_PENDING_CONTENT]
        data.append(d)
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if items:
        logger.info(f"Saved {len(items)} items to pending analysis")
    elif PENDING_FILE.exists():
        logger.info("Cleared pending analysis queue")


def load_sources() -> dict:
    """Load source configuration from YAML."""
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        logger.error(f"FATAL: Cannot load {CONFIG_PATH}: {e}")
        sys.exit(1)


# Map config type field to scraper class — for sources not in SCRAPER_MAP
TYPE_MAP = {
    "island_news": IslandNewsScraper,
    "rss": RssScraper,
}


def create_scraper(source_id: str, config: dict):
    """Create the appropriate scraper for a source."""
    if source_id in SCRAPER_MAP:
        return SCRAPER_MAP[source_id](source_id, config)
    if source_id in MUNICIPALITY_SOURCES:
        return SveitarfelagScraper(source_id, config)
    source_type = config.get("type", "")
    if source_type in TYPE_MAP:
        return TYPE_MAP[source_type](source_id, config)
    if source_type == "html_scrape":
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

    # ── Load pending items from previous runs ──────────────
    pending_items = _load_pending()

    # ── Scrape ──────────────────────────────────────────────
    new_items: list[ScrapedItem] = []

    active_sources = [
        (sid, cfg) for sid, cfg in sources.items()
        if not source_filter or sid in source_filter
    ]
    total_sources = len(active_sources)

    try:
        for idx, (source_id, config) in enumerate(active_sources, 1):
            logger.info(f"[{idx}/{total_sources}] Scraping {source_id}...")

            scraper = create_scraper(source_id, config)
            if not scraper:
                health["sources"][source_id] = {"status": "no_scraper", "items": 0}
                continue

            items = scraper.run()
            new_items.extend(items)

            # Record health per source — distinguish "nothing new" from "broken"
            fetched = scraper._total_fetched
            has_prior = scraper._has_prior_state
            if items:
                status = "ok"
            elif fetched > 0:
                status = "ok"  # site works, just nothing new
            elif has_prior and fetched == 0:
                status = "ok"  # API returned 0 since last check — normal
            else:
                status = "empty"
            health["sources"][source_id] = {
                "status": status,
                "items": len(items),
                "fetched": fetched,
            }
            if status == "empty":
                logger.warning(
                    f"[{source_id}] Returned 0 items — scraper may be broken "
                    f"or site structure changed"
                )
    finally:
        close_browser()

    logger.info(f"Total new items found: {len(new_items)}")
    if new_items:
        from collections import Counter
        by_source = Counter(item.source_id for item in new_items)
        for src, count in by_source.most_common():
            logger.info(f"  {src}: {count} new")

    # Combine pending + new items for analysis
    all_items = pending_items + new_items

    # ── Analyze ─────────────────────────────────────────────
    if not all_items:
        logger.info("No new items to analyze.")
        generate_index([])
        _save_pending([])
        _write_health(health)
        return

    if skip_analysis:
        logger.info("Skipping analysis (--skip-analysis flag) — saving items for next run")
        logger.info(f"{len(all_items)} items saved to pending ({len(pending_items)} carried over, {len(new_items)} new)")
        for source_id in sorted(set(i.source_id for i in all_items)):
            count = sum(1 for i in all_items if i.source_id == source_id)
            logger.info(f"  {source_id}: {count} items")
        _save_pending(all_items)
        health["analysis"] = {"skipped": True, "pending": len(all_items)}
        generate_index([])
        _write_health(health)
        return
    else:
        if pending_items:
            logger.info(
                f"Analyzing {len(all_items)} items with Claude "
                f"({len(pending_items)} pending + {len(new_items)} new)..."
            )
        else:
            logger.info(f"Analyzing {len(all_items)} items with Claude...")

        # Save all items to pending BEFORE analysis starts — if the process
        # is killed, these items will be retried on the next run
        _save_pending(all_items)
        logger.info(f"Pre-saved {len(all_items)} items to pending as safety net")

        results, analysis_stats, failed_items = analyze_batch(
            all_items, checkpoint_fn=_checkpoint, checkpoint_interval=300,
        )
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

        # Save failed items back to pending for retry next run
        if failed_items:
            _save_pending(failed_items)
            logger.info(f"{len(failed_items)} failed items saved for retry next run")
        else:
            _save_pending([])

    # ── Report ──────────────────────────────────────────────
    generate_index(results)

    # Summary
    if results:
        critical = sum(1 for r in results if r.get("severity") == "critical")
        important = sum(1 for r in results if r.get("severity") == "important")
        monitor = sum(1 for r in results if r.get("severity") == "monitor")
        logger.info(f"Summary: {critical} critical, {important} important, {monitor} monitor")
    else:
        logger.info("No relevant items found in this run.")

    # ── Notify ─────────────────────────────────────────────
    if results:
        send_notification(results)

    _write_health(health)


CHECKPOINT_PATHS = ["state/", "reports/", "sunn/", "index.md", "sources.md"]


def _checkpoint(results: list[dict], remaining: list, completed: int, total: int) -> None:
    """Save intermediate progress during long analysis runs.

    Generates reports from results so far, saves remaining items to pending,
    and commits everything to git so progress survives timeout/cancel.
    """
    logger.info(f"── Checkpoint at {completed}/{total} — saving progress ──")

    # Save remaining unanalyzed items to pending
    _save_pending(remaining)

    # Generate reports from results so far
    generate_index(results)

    # Git commit
    import subprocess
    try:
        subprocess.run(["git", "add"] + CHECKPOINT_PATHS, cwd=str(Path(__file__).parent.parent), check=True)
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=str(Path(__file__).parent.parent),
        )
        if result.returncode != 0:
            repo_root = str(Path(__file__).parent.parent)
            subprocess.run(
                ["git", "commit", "-m", f"vaktin: checkpoint {completed}/{total}"],
                cwd=repo_root, check=True,
            )
            subprocess.run(
                ["git", "push"],
                cwd=repo_root, check=True,
            )
            logger.info(f"Checkpoint committed and pushed ({completed}/{total})")
        else:
            logger.info("Checkpoint — no changes to commit")
    except subprocess.CalledProcessError as e:
        logger.warning(f"Checkpoint commit failed: {e}")


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
