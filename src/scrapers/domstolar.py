"""Scraper for Icelandic courts (Hæstiréttur, Landsréttur, Héraðsdómstólar).

All three court levels use a shared CoreData/ASP.NET CMS at domstolar.is.
Pages are server-rendered with JavaScript "show more" buttons.
No RSS or API available — HTML scraping with Playwright is required.

Config keys:
  url: Listing page URL (e.g. https://www.haestirettur.is/domar/)
"""

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class DomstolarScraper(BaseScraper):
    """Scrapes court rulings from Icelandic courts.

    Uses Playwright to load listing pages, extracts ruling entries,
    then fetches detail pages for full text.
    """

    SEEN_IDS_CAP = 500
    MAX_AGE_DAYS = 14  # Only 2 weeks on first run — courts have high volume

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items: list[ScrapedItem] = []

        listing_url = self.config.get("url", "")
        if not listing_url:
            logger.error(f"[{self.source_id}] No URL configured")
            return []

        # Fetch listing page with Playwright (JS required for full rendering)
        html = self.fetch_page_js(listing_url, wait_ms=3000)
        if not html:
            logger.error(f"[{self.source_id}] Failed to fetch listing page")
            return []

        # Extract ruling entries from listing HTML
        entries = self._parse_listing(html, listing_url)
        self._total_fetched = len(entries)

        for entry in entries:
            guid = entry["guid"]
            item_id = f"{self.source_id}_{guid}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            # Check date filter
            if entry.get("date_str") and self._is_too_old(entry["date_str"]):
                continue

            # Fetch detail page for full ruling text
            content = self._fetch_detail(entry["url"])

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=entry["title"],
                url=entry["url"],
                date=entry.get("date_str", datetime.now().isoformat()),
                content=content or entry["title"],
                metadata={
                    "source_type": "domstolar",
                    "case_number": entry.get("case_number", ""),
                },
            ))

        # Update state
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > self.SEEN_IDS_CAP:
            new_seen = set(list(new_seen)[-self.SEEN_IDS_CAP:])
        self.save_state({"seen_ids": list(new_seen)})

        return items

    def _parse_listing(self, html: str, base_url: str) -> list[dict]:
        """Extract ruling entries from a court listing page."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        entries = []

        # Court listing pages use various structures — try multiple selectors
        # Common pattern: links containing "domur" or "urskurdur" with GUID params
        links = soup.find_all("a", href=True)
        seen_guids = set()

        for link in links:
            href = link["href"]
            # Match ruling detail links (contain id= or Id= GUID parameter)
            guid = self._extract_guid(href)
            if not guid or guid in seen_guids:
                continue
            seen_guids.add(guid)

            # Only accept links that look like ruling pages
            if not re.search(r'(?:domur|urskurdur|verdictid)', href, re.IGNORECASE):
                continue

            full_url = urljoin(base_url, href)

            # Extract title and metadata from surrounding elements
            title, date_str, case_number = self._extract_entry_info(link)

            if not title:
                continue

            entries.append({
                "guid": guid,
                "url": full_url,
                "title": title,
                "date_str": date_str,
                "case_number": case_number,
            })

        logger.info(f"[{self.source_id}] Found {len(entries)} rulings on listing page")
        return entries

    def _extract_guid(self, href: str) -> str | None:
        """Extract GUID from a ruling URL."""
        # Try query parameter patterns: ?id=GUID, ?Id=GUID, ?verdictid=GUID
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        for key in ("id", "Id", "verdictid"):
            values = params.get(key, [])
            if values:
                val = values[0]
                # Validate it looks like a GUID
                if re.match(r'^[0-9a-f-]{32,36}$', val, re.IGNORECASE):
                    return val
        return None

    def _extract_entry_info(self, link_tag) -> tuple[str, str, str]:
        """Extract title, date, and case number from a ruling link and its context."""
        title = ""
        date_str = ""
        case_number = ""

        # Walk up to find the containing element (often a div or li)
        container = link_tag.parent
        for _ in range(5):
            if container is None:
                break
            text = container.get_text(separator=" ", strip=True)
            if len(text) > 30:
                break
            container = container.parent

        if container:
            text = container.get_text(separator=" ", strip=True)

            # Extract date patterns like "27 mar. 2026", "01 apr 2026"
            date_match = re.search(
                r'(\d{1,2})\s*\.?\s*(jan|feb|mar|apr|ma[ií]|j[úu]n|j[úu]l|[áa]g[úu]|sep|okt|n[óo]v|des)\.?\s*(\d{4})',
                text, re.IGNORECASE
            )
            if date_match:
                day, month_str, year = date_match.groups()
                month_map = {
                    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
                    'maí': '05', 'mai': '05', 'jún': '06', 'jun': '06',
                    'júl': '07', 'jul': '07', 'ágú': '08', 'águ': '08', 'agu': '08',
                    'sep': '09', 'okt': '10', 'nóv': '11', 'nov': '11', 'des': '12',
                }
                month = month_map.get(month_str.lower().rstrip('.'), '01')
                date_str = f"{year}-{month}-{int(day):02d}"

            # Extract case number (e.g., "8 / 2026", "S-125/2026", "236/2026")
            case_match = re.search(r'([A-Z]?-?\d{1,4}\s*/\s*\d{4})', text)
            if case_match:
                case_number = case_match.group(1).strip()

        # Title: use link text, or case number + parties
        link_text = link_tag.get_text(strip=True)
        if link_text and len(link_text) > 5:
            title = link_text
        elif case_number:
            title = f"Dómur nr. {case_number}"

        # If title is too short, try to get more context
        if len(title) < 10 and container:
            full_text = container.get_text(separator=" — ", strip=True)
            if len(full_text) > len(title):
                title = full_text[:200]

        return title, date_str, case_number

    def _fetch_detail(self, url: str) -> str:
        """Fetch full ruling text from a detail page."""
        try:
            html = self.fetch_page(url)
            if not html:
                return ""

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")

            # Remove script/style tags
            for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()

            # Try common content selectors for court ruling pages
            content_el = None
            for selector in [
                "#main", "[id='main']",
                ".verdict-text", ".ruling-text",
                "article", ".content-area",
                "#readid", "[readid]",
                "main",
            ]:
                content_el = soup.select_one(selector)
                if content_el:
                    break

            if not content_el:
                content_el = soup.find("body")

            text = content_el.get_text(separator="\n", strip=True) if content_el else ""

            # Truncate to reasonable size
            if len(text) > 15000:
                text = text[:15000] + "\n\n[Texti styttur]"
            return text

        except Exception as e:
            logger.warning(f"[{self.source_id}] Failed to fetch detail page {url}: {e}")
            return ""
