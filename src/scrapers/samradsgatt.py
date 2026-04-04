"""Scraper for Samráðsgátt ríkisins (Government Consultation Portal).

URL: https://samradsgatt.island.is
This portal lists all public consultations on legislation and policy.
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class SamradsgattScraper(BaseScraper):
    """Scrapes the government consultation portal for new cases."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check", "")

        items = []
        list_url = self.config.get("list_url", "https://samradsgatt.island.is/oll-mal")

        html = self.fetch_page(list_url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        new_ids = set()

        # Find case listings - the actual selectors need to be verified
        # against the live site structure. These are best-effort based on
        # common Icelandic government site patterns.
        case_elements = (
            soup.select("article.case-item")
            or soup.select(".consultation-list .item")
            or soup.select("[data-testid='case-card']")
            or soup.select(".card, .list-item, article")
        )

        for element in case_elements:
            link = element.find("a", href=True)
            if not link:
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = f"https://samradsgatt.island.is{href}"

            # Use the URL path as a stable ID
            item_id = href.rstrip("/").split("/")[-1]
            if not item_id:
                continue

            if item_id in seen_ids:
                continue

            title = link.get_text(strip=True)
            if not title:
                continue

            # Try to find a date
            date_el = element.find("time") or element.find(class_=lambda c: "date" in c.lower() if c else False)
            date_str = ""
            if date_el:
                date_str = date_el.get("datetime", "") or date_el.get_text(strip=True)

            # Fetch the full case page for content
            content = self._fetch_case_content(href)

            new_ids.add(item_id)
            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=href,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={"source_type": "samradsgatt"},
            ))

        # Update state
        seen_ids.update(new_ids)
        # Keep only last 500 IDs to prevent unlimited growth
        if len(seen_ids) > 500:
            seen_ids = set(list(seen_ids)[-500:])

        self.save_state({
            "seen_ids": list(seen_ids),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _fetch_case_content(self, url: str) -> str:
        """Fetch and extract the main content from a case page."""
        html = self.fetch_page(url)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")

        # Try to find the main content area
        content_el = (
            soup.select_one("article .content")
            or soup.select_one("main .content")
            or soup.select_one("[class*='description']")
            or soup.select_one("main")
        )

        if content_el:
            # Remove script/style elements
            for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = content_el.get_text(separator="\n", strip=True)
            if len(text) > 15000:
                text = text[:15000] + "\n\n[Texti styttur]"
            return text

        return ""
