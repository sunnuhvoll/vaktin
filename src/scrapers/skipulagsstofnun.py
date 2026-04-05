"""Scraper for Skipulagsstofnun / HMS on island.is.

Skipulagsstofnun was merged into HMS (Húsnæðis-, mannvirkja- og
skipulagsstofnun) and its content moved to island.is.  The environmental
assessment database (gagnagrunnur umhverfismats) and planning pages now
live under island.is/s/hms/.

island.is uses Next.js with server-side rendering. The initial HTML
contains navigational content, but dynamic lists (e.g. the EIA database)
require JavaScript. We use fetch_page_auto which falls back to Playwright
when available.
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class SkipulagsstofnunScraper(BaseScraper):
    """Scrapes HMS / Skipulagsstofnun pages on island.is."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []

        for section in self.config.get("sections", []):
            section_name = section.get("name", "unknown")
            base_url = self.config.get("url", "https://island.is")
            url = base_url + section.get("path", "")

            # island.is pages are JS-rendered; try Playwright if available
            html = self.fetch_page_auto(url, wait_selector="a[href]")
            if not html:
                logger.warning(f"[{self.source_id}] Could not fetch {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            section_items = self._parse_case_list(soup, url, section_name, seen_ids)
            items.extend(section_items)

        # Update state with all seen IDs
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 500:
            logger.info(f"[{self.source_id}] Truncating seen_ids from {len(new_seen)} to 500")
            new_seen = set(list(new_seen)[-500:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _parse_case_list(self, soup: BeautifulSoup, base_url: str,
                         section_name: str, seen_ids: set[str]) -> list[ScrapedItem]:
        """Parse a list of cases from an island.is section page."""
        items = []

        # island.is uses various component structures. Try common patterns:
        # - GenericList items (database results)
        # - AccordionCard / FaqList items
        # - Linked cards
        # - Standard article/list-item elements
        case_elements = (
            soup.select("[data-testid='generic-list-item']")
            or soup.select(".GenericList a, .generic-list a")
            or soup.select("[class*='AccordionCard']")
            or soup.select("ul[class*='list'] li a")
            or soup.select("article")
            or soup.select(".news-item, .list-item, .card")
        )

        if not case_elements:
            logger.warning(
                f"[{self.source_id}] No elements found for section '{section_name}' — "
                f"CSS selectors may need updating"
            )
            return []

        for element in case_elements:
            link = element if element.name == "a" else element.find("a", href=True)
            if not link or not link.get("href"):
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = f"https://island.is{href}"

            # Skip navigation / anchor links
            if href.startswith("#") or "/s/hms" not in href:
                # Allow links to sub-pages under HMS, or external case links
                if not href.startswith("http"):
                    continue

            item_id = f"skip_{href.rstrip('/').split('/')[-1]}"

            if item_id in seen_ids:
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            # Extract date if available
            date_str = ""
            date_el = element.find("time") or element.find(
                "span", class_=lambda c: c and "date" in c.lower() if c else False
            )
            if date_el:
                date_str = date_el.get("datetime", "") or date_el.get_text(strip=True)

            # Fetch full content from the linked page
            content = self._fetch_case_content(href)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=href,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "skipulagsstofnun_hms",
                    "section": section_name,
                },
            ))

        return items

    def _fetch_case_content(self, url: str) -> str:
        """Fetch and extract main content from a case page."""
        html = self.fetch_page_auto(url, wait_selector="main")
        if not html:
            logger.warning(f"[{self.source_id}] Could not fetch case page: {url}")
            return ""

        soup = BeautifulSoup(html, "html.parser")

        content_el = (
            soup.select_one("main [class*='Content']")
            or soup.select_one("article")
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
