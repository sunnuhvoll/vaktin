"""Generic RSS feed scraper.

Used for sources that publish RSS feeds: Vegagerðin, Náttúrufræðistofnun,
Matvælastofnun (MAST), and any future RSS-based sources.

Parses standard RSS 2.0 / Atom feeds and returns new items since last run.
"""

import logging
import re
from datetime import datetime
from xml.etree import ElementTree as ET

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class RssScraper(BaseScraper):
    """Fetches items from an RSS or Atom feed.

    Items older than MAX_AGE_DAYS are skipped to avoid processing
    large backlogs on first run or after long gaps.
    """

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []

        rss_url = self.config.get("rss_url", "")
        if not rss_url:
            logger.error(f"[{self.source_id}] No 'rss_url' in config — skipping")
            return []

        xml_text = self._fetch_feed(rss_url)
        if not xml_text:
            return []

        entries = self._parse_feed(xml_text)
        skipped_old = 0

        for entry in entries:
            guid = entry.get("guid", entry.get("link", ""))
            if not guid:
                continue

            # Skip items older than MAX_AGE_DAYS
            date_str = entry.get("date", "")
            if self._is_too_old(date_str):
                skipped_old += 1
                continue

            item_id = f"{self.source_id}_{self._slugify(guid)}"
            if item_id in seen_ids:
                continue

            title = entry.get("title", "").strip()
            if not title:
                continue

            link = entry.get("link", "")
            description = entry.get("description", "")

            # Optionally fetch full article content
            content = description
            if link and len(description) < 500:
                full_content = self._fetch_article(link)
                if full_content:
                    content = full_content

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=link,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "rss",
                    "categories": entry.get("categories", []),
                },
            ))

        if skipped_old:
            logger.info(f"[{self.source_id}] Skipped {skipped_old} items older than {self.MAX_AGE_DAYS} days")

        # Update state — cap at 400
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 400:
            new_seen = set(list(new_seen)[-400:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _fetch_feed(self, url: str) -> str | None:
        """Fetch RSS/Atom XML from URL."""
        try:
            resp = self.session.get(url, timeout=20, headers={
                "Accept": "application/rss+xml, application/xml, text/xml",
                "Accept-Encoding": "gzip, deflate",
            })
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to fetch RSS feed {url}: {e}")
            return None

    def _parse_feed(self, xml_text: str) -> list[dict]:
        """Parse RSS 2.0 or Atom feed XML into a list of entry dicts."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f"[{self.source_id}] Failed to parse RSS XML: {e}")
            return []

        entries = []

        # RSS 2.0 format
        for item in root.iter("item"):
            entries.append({
                "title": self._text(item, "title"),
                "link": self._text(item, "link"),
                "guid": self._text(item, "guid") or self._text(item, "link"),
                "date": self._text(item, "pubDate"),
                "description": self._clean_html(self._text(item, "description")),
                "categories": [c.text for c in item.findall("category") if c.text],
            })

        # Atom format (if no RSS items found)
        if not entries:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                entries.append({
                    "title": self._text_ns(entry, "title", ns),
                    "link": link,
                    "guid": self._text_ns(entry, "id", ns) or link,
                    "date": self._text_ns(entry, "updated", ns)
                           or self._text_ns(entry, "published", ns),
                    "description": self._clean_html(
                        self._text_ns(entry, "summary", ns)
                        or self._text_ns(entry, "content", ns)
                    ),
                    "categories": [],
                })

        return entries

    def _text(self, parent: ET.Element, tag: str) -> str:
        """Get text content of a child element."""
        el = parent.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    def _text_ns(self, parent: ET.Element, tag: str, ns: dict) -> str:
        """Get text content of a namespaced child element."""
        el = parent.find(f"atom:{tag}", ns)
        return el.text.strip() if el is not None and el.text else ""

    def _clean_html(self, text: str) -> str:
        """Strip HTML tags from description text."""
        if not text:
            return ""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _slugify(self, text: str) -> str:
        """Create a short slug from a URL or GUID for use as item_id."""
        # Use last meaningful path segment or hash
        slug = text.rstrip("/").split("/")[-1]
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
        return slug[:80]

    def _fetch_article(self, url: str) -> str:
        """Fetch full article content from a URL."""
        html = self.fetch_page(url)
        if not html:
            return ""

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        content_el = (
            soup.select_one("article .content")
            or soup.select_one("article")
            or soup.select_one(".news-content")
            or soup.select_one(".field--name-body")
            or soup.select_one("main .content")
            or soup.select_one("main")
        )

        if not content_el:
            return ""

        for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        text = content_el.get_text(separator="\n", strip=True)
        if len(text) > 12000:
            text = text[:12000] + "\n\n[Texti styttur]"
        return text
