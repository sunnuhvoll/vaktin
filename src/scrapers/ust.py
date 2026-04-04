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

        for section in self.config.get("sections", []):
            section_name = section["name"]
            url = self.config.get("url", "") + section["path"]

            html = self.fetch_page(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            section_items = self._parse_list(soup, section_name, seen_ids)
            items.extend(section_items)

        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 500:
            new_seen = set(list(new_seen)[-500:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _parse_list(self, soup: BeautifulSoup, section_name: str,
                    seen_ids: set[str]) -> list[ScrapedItem]:
        """Parse a list page from UST."""
        items = []

        elements = (
            soup.select("article")
            or soup.select(".news-item")
            or soup.select(".list-item")
            or soup.select(".card")
        )

        for element in elements:
            link = element.find("a", href=True)
            if not link:
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = f"{self.config.get('url', '')}{href}"

            path_end = href.rstrip('/').split('/')[-1]
            if not path_end:
                continue
            item_id = f"ust_{path_end}"
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
        html = self.fetch_page(url)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        content_el = (
            soup.select_one("article")
            or soup.select_one(".content-area")
            or soup.select_one("main")
        )

        if content_el:
            for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = content_el.get_text(separator="\n", strip=True)
            if len(text) > 15000:
                text = text[:15000] + "\n\n[Texti styttur]"
            return text

        return ""
