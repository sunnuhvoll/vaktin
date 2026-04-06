"""Base scraper with state tracking to only process new items."""

import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import time

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES_429 = 2
RETRY_DELAY_429 = 5  # seconds

STATE_FILE = Path(__file__).parent.parent.parent / "state" / "state.json"

# Playwright is used as a fallback for JS-rendered pages.
# Lazy-loaded to avoid import cost when not needed.
_playwright_available = None
_browser = None


def _check_playwright():
    """Check if playwright is installed and usable."""
    global _playwright_available
    if _playwright_available is None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
            logger.info("Playwright not installed — JS rendering fallback unavailable")
    return _playwright_available


def _get_browser():
    """Get or create a shared browser instance (reused across all scrapers)."""
    global _browser
    if _browser is None or not _browser.is_connected():
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _browser = pw.chromium.launch(headless=True)
        logger.info("Launched headless Chromium for JS rendering")
    return _browser


def close_browser():
    """Close the shared browser. Call at end of pipeline."""
    global _browser
    if _browser is not None and _browser.is_connected():
        _browser.close()
        _browser = None
        logger.info("Closed headless Chromium")


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

    # Minimum text length from requests before considering JS fallback
    MIN_CONTENT_LENGTH = 200

    # Maximum age for items (days). Items older than this are skipped even
    # when state is empty (first run, state loss). Prevents huge backlogs.
    MAX_AGE_DAYS = 30

    def __init__(self, source_id: str, config: dict):
        self.source_id = source_id
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Vaktin/1.0 (Icelandic Nature Conservation Monitor; +https://github.com/INECTA/vaktin)"
        })
        # Allow sources with expired SSL certificates (e.g. arborg.is)
        if config.get("ssl_verify") is False:
            self.session.verify = False
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def load_state(self) -> dict:
        """Load state for this source from the shared state file."""
        if not STATE_FILE.exists():
            return {}
        with open(STATE_FILE) as f:
            all_state = json.load(f)
        return all_state.get(self.source_id, {})

    def save_state(self, state: dict) -> None:
        """Save state for this source to the shared state file."""
        all_state = {}
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                all_state = json.load(f)
        all_state[self.source_id] = state
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(all_state, f, indent=2, ensure_ascii=False)

    def fetch_page(self, url: str) -> str | None:
        """Fetch a web page with requests (fast, no JS).

        Retries on 429 (Too Many Requests) with a delay.
        Sets self._last_status to the HTTP status code (or None on
        network error) so callers can distinguish 404 from other failures.
        """
        for attempt in range(1 + MAX_RETRIES_429):
            self._last_status = None
            try:
                resp = self.session.get(url, timeout=30)
                self._last_status = resp.status_code
                if resp.status_code == 429 and attempt < MAX_RETRIES_429:
                    delay = RETRY_DELAY_429 * (attempt + 1)
                    logger.info(f"[{self.source_id}] 429 rate limited, waiting {delay}s before retry")
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text
            except requests.RequestException as e:
                if self._last_status == 429 and attempt < MAX_RETRIES_429:
                    continue  # already sleeping above
                # 404 on content pages is common and expected — log at debug
                if self._last_status == 404:
                    logger.debug(f"[{self.source_id}] 404 for {url}")
                else:
                    logger.error(f"[{self.source_id}] Failed to fetch {url}: {e}")
                return None
        return None

    def fetch_page_js(self, url: str, wait_selector: str | None = None,
                      wait_ms: int = 3000) -> str | None:
        """Fetch a page using headless Chromium (for JS-rendered content).

        Args:
            url: Page URL.
            wait_selector: Optional CSS selector to wait for before extracting HTML.
            wait_ms: Milliseconds to wait for network idle (default 3000).
        """
        if not _check_playwright():
            logger.warning(f"[{self.source_id}] Playwright unavailable, cannot JS-render {url}")
            return None

        try:
            browser = _get_browser()
            page = browser.new_page(
                user_agent="Vaktin/1.0 (Icelandic Nature Conservation Monitor)"
            )
            page.goto(url, timeout=30000, wait_until="networkidle")

            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=10000)
            else:
                page.wait_for_timeout(wait_ms)

            html = page.content()
            page.close()
            return html
        except Exception as e:
            logger.error(f"[{self.source_id}] Playwright failed for {url}: {e}")
            return None

    def fetch_page_auto(self, url: str, wait_selector: str | None = None,
                        min_length: int | None = None) -> str | None:
        """Try requests first; fall back to Playwright if content looks empty.

        This is the recommended method for pages that might be JS-rendered.
        It keeps things fast for static pages while handling SPAs gracefully.

        Args:
            url: Page URL.
            wait_selector: CSS selector to wait for when using Playwright.
            min_length: Minimum HTML length to accept from requests.
                        Below this, Playwright is tried. Defaults to MIN_CONTENT_LENGTH.
        """
        threshold = min_length if min_length is not None else self.MIN_CONTENT_LENGTH

        # Step 1: Try fast requests fetch
        html = self.fetch_page(url)

        if html and len(html.strip()) >= threshold:
            return html

        # Step 2: If the server returned a definitive HTTP error (4xx/5xx),
        # don't waste time trying Playwright — it won't fix a 404.
        status = getattr(self, "_last_status", None)
        if status and status >= 400:
            return None

        # Step 3: Content missing or too short — try JS rendering
        if html is not None:
            logger.info(
                f"[{self.source_id}] HTML from {url} too short ({len(html.strip())} chars), "
                "trying Playwright"
            )
        else:
            logger.info(f"[{self.source_id}] requests failed for {url}, trying Playwright")

        js_html = self.fetch_page_js(url, wait_selector=wait_selector)
        if js_html:
            return js_html

        # Return whatever we got (even if short), or None
        return html

    def _max_age_cutoff(self) -> datetime:
        """Return the UTC cutoff datetime: items older than this are skipped."""
        return datetime.now(timezone.utc) - timedelta(days=self.MAX_AGE_DAYS)

    def _is_too_old(self, date_str: str) -> bool:
        """Check if a date string is older than MAX_AGE_DAYS.

        Returns False (don't skip) if the date can't be parsed — better
        to include an unparseable item than to silently drop it.
        """
        if not date_str:
            return False

        cutoff = self._max_age_cutoff()

        # Try RFC 2822 (RSS pubDate: "Mon, 01 Jan 2026 12:00:00 GMT")
        try:
            dt = parsedate_to_datetime(date_str)
            return dt < cutoff
        except Exception:
            pass

        # Try ISO 8601
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt < cutoff
        except Exception:
            pass

        # Try Icelandic date formats: "1.4.2026", "01.04.2026"
        m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', date_str)
        if m:
            try:
                dt = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                              tzinfo=timezone.utc)
                return dt < cutoff
            except ValueError:
                pass

        # Try "2026-04-01" embedded in text
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                              tzinfo=timezone.utc)
                return dt < cutoff
            except ValueError:
                pass

        return False  # Can't parse → don't skip

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
