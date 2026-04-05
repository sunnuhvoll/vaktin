"""Scraper for Umhverfisstofnun (Environment Agency of Iceland).

URL: https://ust.is
Tracks environmental permits, news, and enforcement actions.
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class UstScraper(BaseScraper):
    """Scrapes the Environment Agency website."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []
        base_url = self.config.get("url", "https://ust.is")

        for section in self.config.get("sections", []):
            section_name = section.get("name", "unknown")
            url = base_url + section.get("path", "")

            html = self.fetch_page_auto(url)
            if not html:
                logger.warning(f"[{self.source_id}] Could not fetch {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            section_items = self._parse_list(soup, base_url, section_name, seen_ids)
            items.extend(section_items)

        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 500:
            logger.info(f"[{self.source_id}] Truncating seen_ids from {len(new_seen)} to 500")
            new_seen = set(list(new_seen)[-500:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _parse_list(self, soup: BeautifulSoup, base_url: str,
                    section_name: str, seen_ids: set[str]) -> list[ScrapedItem]:
        """Parse a list page from UST."""
        items = []

        elements = (
            soup.select("article")
            or soup.select(".news-item")
            or soup.select(".list-item")
            or soup.select(".card")
        )

        if not elements:
            logger.warning(
                f"[{self.source_id}] No elements found for section '{section_name}' — "
                f"CSS selectors may need updating"
            )
            return []

        for element in elements:
            link = element.find("a", href=True)
            if not link:
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = f"{base_url}{href}"

            item_id = f"ust_{href.rstrip('/').split('/')[-1]}"
            if item_id in seen_ids:
                continue

            title = link.get_text(strip=True)
            if not title:
                continue

            date_str = ""
            date_el = element.find("time")
            if date_el:
                date_str = date_el.get("datetime", "") or date_el.get_text(strip=True)

            content = self._fetch_content(href)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=href,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "ust",
                    "section": section_name,
                },
            ))

        return items

    def _fetch_content(self, url: str) -> str:
        """Fetch full content from a UST page."""
        html = self.fetch_page_auto(url)
        if not html:
            logger.warning(f"[{self.source_id}] Could not fetch page: {url}")
            return ""

        soup = BeautifulSoup(html, "html.parser")
        content_el = (
            soup.select_one("article")
            or soup.select_one(".content-area")
            or soup.select_one("main")
        )

        if not content_el:
            logger.warning(f"[{self.source_id}] No content element found on {url}")
            return ""

        for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = content_el.get_text(separator="\n", strip=True)
        if len(text) > 15000:
            text = text[:15000] + "\n\n[Texti styttur]"
        return text
