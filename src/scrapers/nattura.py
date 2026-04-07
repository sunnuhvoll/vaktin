"""Scraper for Náttúruverndarstofnun via Payload CMS API.

URL: https://www.nattura.is
Nature Conservation Agency (est. January 2025, successor to parts of UST).
Covers protected areas, national parks, species protection.

The website uses Payload CMS with a public REST API at
nattura-is.payload.is/api/news.
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class NatturaScraper(BaseScraper):
    """Fetches news from Náttúruverndarstofnun via Payload CMS API.

    Uses timestamp-based delta with seen_ids as safety net.
    """

    SEEN_IDS_CAP = 50

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items = []

        api_url = self.config.get("api_url", "https://nattura-is.payload.is/api/news")
        docs = self._fetch_news(api_url)
        fetch_ok = docs is not None
        all_docs = docs or []
        self._total_fetched = len(all_docs)

        cutoff = last_check or self._max_age_cutoff().isoformat()

        for doc in all_docs:
            doc_id = doc.get("id", "")
            item_id = f"{self.source_id}_{doc_id}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            date_str = doc.get("publishedDate", "")
            if date_str and date_str < cutoff:
                continue

            title = doc.get("meta", {}).get("title", "") or ""
            if not title:
                # Try extracting from blocks
                for block in doc.get("blocks", []):
                    if block.get("blockType") == "hero":
                        title = block.get("title", "")
                        break
            if not title:
                continue

            url_data = doc.get("url", {})
            slug = url_data.get("path", "") if isinstance(url_data, dict) else ""
            url = f"https://www.nattura.is{slug}" if slug else "https://www.nattura.is"

            content = self._extract_content(doc)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "payload_cms",
                    "tags": [t.get("title", "") for t in doc.get("tags", []) if isinstance(t, dict)],
                },
            ))

        # Update state
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > self.SEEN_IDS_CAP:
            new_seen = set(list(new_seen)[-self.SEEN_IDS_CAP:])

        state_update = {"seen_ids": list(new_seen)}
        if fetch_ok:
            state_update["last_check"] = datetime.now().isoformat()
        elif last_check:
            state_update["last_check"] = last_check
        self.save_state(state_update)

        return items

    def _fetch_news(self, api_url: str) -> list[dict] | None:
        """Fetch news documents from Payload CMS API. Returns None on failure."""
        params = {
            "sort": "-publishedDate",
            "limit": 20,
        }
        try:
            resp = self.session.get(api_url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("docs", [])
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to fetch from Payload CMS: {e}")
            return None

    def _extract_content(self, doc: dict) -> str:
        """Extract text content from a Payload CMS news document."""
        parts = []
        for block in doc.get("blocks", []):
            block_type = block.get("blockType", "")
            if block_type == "hero":
                desc = block.get("description", "")
                if desc:
                    parts.append(desc)
            elif block_type == "richText":
                rich = block.get("richText", {})
                self._extract_rich_text(rich, parts)

        content = "\n".join(parts)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content

    @staticmethod
    def _extract_rich_text(node: dict | list, parts: list[str]) -> None:
        """Recursively extract text from Payload rich text (Slate-like JSON)."""
        if isinstance(node, list):
            for child in node:
                NatturaScraper._extract_rich_text(child, parts)
            return
        if not isinstance(node, dict):
            return
        text = node.get("text", "")
        if text:
            parts.append(text)
        for child in node.get("children", []):
            NatturaScraper._extract_rich_text(child, parts)
