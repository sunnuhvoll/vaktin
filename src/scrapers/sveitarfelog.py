"""Scraper for Icelandic municipality websites (sveitarfélög).

Handles meeting minutes (fundargerðir) from municipal councils and committees.
Each municipality has a different website but many follow similar patterns.
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class SveitarfelagScraper(BaseScraper):
    """Generic scraper for Icelandic municipality websites."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []

        for section in self.config.get("sections", []):
            section_name = section["name"]
            url = self.config["url"] + section["path"]

            html = self.fetch_page(url)
            if not html:
                logger.warning(f"[{self.source_id}] Could not fetch {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            section_items = self._parse_meeting_list(soup, url, section_name, seen_ids)
            items.extend(section_items)

        # Update state
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 300:
            new_seen = set(list(new_seen)[-300:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _parse_meeting_list(self, soup: BeautifulSoup, base_url: str,
                            section_name: str, seen_ids: set[str]) -> list[ScrapedItem]:
        """Parse meeting minutes list from a municipality page."""
        items = []

        # Municipalities use various CMS systems. Try common patterns.
        meeting_elements = (
            soup.select(".fundargerdir-list .item")
            or soup.select("table.meetings tr")
            or soup.select(".meeting-list a")
            or soup.select("article.meeting")
            or soup.select(".list-group .list-group-item")
            or soup.select("ul.document-list li")
            or self._find_meeting_links(soup)
        )

        for element in meeting_elements:
            link = element if element.name == "a" else element.find("a", href=True)
            if not link or not link.get("href"):
                continue

            href = link["href"]
            if not href.startswith("http"):
                base = self.config["url"]
                href = f"{base}{href}" if href.startswith("/") else f"{base}/{href}"

            # Create a unique ID from source + URL
            item_id = f"{self.source_id}_{href.rstrip('/').split('/')[-1]}"

            if item_id in seen_ids:
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 3:
                continue

            # Skip obvious non-meeting links
            if self._is_navigation_link(title, href):
                continue

            date_str = self._extract_date(element)
            content = self._fetch_meeting_content(href)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=href,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "sveitarfelag",
                    "section": section_name,
                    "municipality": self.config.get("name", self.source_id),
                },
            ))

        return items

    def _find_meeting_links(self, soup: BeautifulSoup) -> list:
        """Fallback: find links that look like meeting minutes."""
        results = []
        keywords = ["fundargerð", "fundargerðir", "fundur", "bókun"]
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True).lower()
            if any(kw in text for kw in keywords):
                results.append(link)
        return results

    def _is_navigation_link(self, title: str, href: str) -> bool:
        """Filter out navigation and irrelevant links."""
        skip_patterns = [
            "forsíða", "heim", "hafa samband", "leit", "english",
            "login", "innskráning", ".pdf", ".docx",
        ]
        title_lower = title.lower()
        href_lower = href.lower()
        return any(p in title_lower or p in href_lower for p in skip_patterns)

    def _extract_date(self, element) -> str:
        """Try to extract a date from a meeting element."""
        # Check for time element
        time_el = element.find("time")
        if time_el:
            return time_el.get("datetime", "") or time_el.get_text(strip=True)

        # Check for date class
        date_el = element.find(class_=lambda c: c and "date" in c.lower() if c else False)
        if date_el:
            return date_el.get_text(strip=True)

        # Try to find date pattern in text (DD.MM.YYYY or YYYY-MM-DD)
        import re
        text = element.get_text()
        date_match = re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', text)
        if date_match:
            return date_match.group()
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', text)
        if date_match:
            return date_match.group()

        return ""

    def _fetch_meeting_content(self, url: str) -> str:
        """Fetch and extract content from a meeting minutes page."""
        html = self.fetch_page(url)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")

        # Try various content selectors
        content_el = (
            soup.select_one(".fundargerdir-content")
            or soup.select_one("article .content")
            or soup.select_one(".meeting-content")
            or soup.select_one("article")
            or soup.select_one("main .content")
            or soup.select_one("main")
        )

        if content_el:
            for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = content_el.get_text(separator="\n", strip=True)
            # Limit content length to avoid huge payloads
            if len(text) > 15000:
                text = text[:15000] + "\n\n[Texti styttur — of langur fyrir greiningu]"
            return text

        return ""
