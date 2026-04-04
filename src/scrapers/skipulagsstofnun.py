"""Scraper for Skipulagsstofnun (National Planning Agency).

URL: https://www.skipulagsstofnun.is
Tracks environmental impact assessments (EIA) and planning cases.
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class SkipulagsstofnunScraper(BaseScraper):
    """Scrapes Skipulagsstofnun for EIA and planning cases."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []

        for section in self.config.get("sections", []):
            section_name = section["name"]
            url = self.config["url"] + section["path"]

            html = self.fetch_page(url)
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            section_items = self._parse_case_list(soup, url, section_name, seen_ids)
            items.extend(section_items)

        # Update state with all seen IDs
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 500:
            new_seen = set(list(new_seen)[-500:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _parse_case_list(self, soup: BeautifulSoup, base_url: str,
                         section_name: str, seen_ids: set[str]) -> list[ScrapedItem]:
        """Parse a list of cases from a section page."""
        items = []

        # Try various selectors for case lists
        case_elements = (
            soup.select("table.case-list tr")
            or soup.select(".case-list .case-item")
            or soup.select("article")
            or soup.select(".news-item, .list-item")
        )

        for element in case_elements:
            link = element.find("a", href=True)
            if not link:
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = f"{self.config['url']}{href}"

            item_id = f"skip_{href.rstrip('/').split('/')[-1]}"

            if item_id in seen_ids:
                continue

            title = link.get_text(strip=True)
            if not title:
                continue

            # Extract date if available
            date_str = ""
            date_el = element.find("time") or element.find("td", class_=lambda c: c and "date" in c.lower() if c else False)
            if date_el:
                date_str = date_el.get("datetime", "") or date_el.get_text(strip=True)

            # Fetch full content
            content = self._fetch_case_content(href)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=href,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "skipulagsstofnun",
                    "section": section_name,
                },
            ))

        return items

    def _fetch_case_content(self, url: str) -> str:
        """Fetch and extract main content from a case page."""
        html = self.fetch_page(url)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")

        content_el = (
            soup.select_one("article")
            or soup.select_one(".content-area")
            or soup.select_one("main .content")
            or soup.select_one("main")
        )

        if content_el:
            for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            return content_el.get_text(separator="\n", strip=True)

        return ""
