"""Base scraper with state tracking to only process new items."""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent.parent / "state" / "state.json"


class ScrapedItem:
    """A single item fetched from a source."""

    def __init__(self, source_id: str, item_id: str, title: str, url: str,
                 date: str, content: str, metadata: dict | None = None):
        self.source_id = source_id
        self.item_id = item_id
        self.title = title
        self.url = url
        self.date = date  # ISO format string
        self.content = content
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "item_id": self.item_id,
            "title": self.title,
            "url": self.url,
            "date": self.date,
            "content": self.content,
            "metadata": self.metadata,
        }


class BaseScraper(ABC):
    """Base class for all scrapers. Handles state tracking and HTTP sessions."""

    def __init__(self, source_id: str, config: dict):
        self.source_id = source_id
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Vaktin/1.0 (Icelandic Nature Conservation Monitor; +https://github.com/INECTA/vaktin)"
        })

    def load_state(self) -> dict:
        """Load state for this source from the shared state file."""
        if not STATE_FILE.exists():
            return {}
        try:
            with open(STATE_FILE) as f:
                all_state = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[{self.source_id}] Could not read state file, starting fresh: {e}")
            return {}
        return all_state.get(self.source_id, {})

    def save_state(self, state: dict) -> None:
        """Save state for this source to the shared state file."""
        all_state = {}
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    all_state = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[{self.source_id}] Could not read state file, overwriting: {e}")
                all_state = {}
        all_state[self.source_id] = state
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(all_state, f, indent=2, ensure_ascii=False)

    def fetch_page(self, url: str) -> str | None:
        """Fetch a web page with error handling."""
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as e:
            logger.error(f"[{self.source_id}] Failed to fetch {url}: {e}")
            return None

    @abstractmethod
    def scrape(self) -> list[ScrapedItem]:
        """Scrape the source and return only NEW items since last run.

        Implementations must:
        1. Load state via self.load_state()
        2. Fetch content from the source
        3. Filter out already-processed items
        4. Save updated state via self.save_state()
        5. Return list of new ScrapedItem objects
        """
        ...

    def run(self) -> list[ScrapedItem]:
        """Run the scraper with logging."""
        logger.info(f"[{self.source_id}] Starting scrape of {self.config.get('name', self.source_id)}")
        try:
            items = self.scrape()
            logger.info(f"[{self.source_id}] Found {len(items)} new items")
            return items
        except Exception as e:
            logger.error(f"[{self.source_id}] Scraper failed: {e}", exc_info=True)
            return []
