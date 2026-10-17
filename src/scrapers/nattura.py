"""Scraper for Náttúruverndarstofnun via Payload CMS API.

URL: https://www.nattura.is
Nature Conservation Agency (est. January 2025, successor to parts of UST).
Covers protected areas, national parks, species protection.

The website uses Payload CMS with a public REST API at
nattura-is.payload.is/api/news. Since about October 2026 the API returns the
localized fields (blocks, title, path) only when asked for `locale=is`; the
title and path live in the `url` relation and body text is Lexical JSON.
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class NatturaScraper(BaseScraper):
    """Fetches news from Náttúruverndarstofnun via Payload CMS API.

    Every run looks back over the whole MAX_AGE_DAYS window and skips known
    items by seen_ids, so a failed run (or a format change like the one in
    October 2026) never leaves a gap behind last_check.
    """

    SEEN_IDS_CAP = 200

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

        cutoff = self._max_age_cutoff().isoformat()

        for doc in all_docs:
            doc_id = doc.get("id", "")
            item_id = f"{self.source_id}_{doc_id}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            date_str = doc.get("publishedDate", "")
            if date_str and date_str < cutoff:
                continue

            url_data = doc.get("url") if isinstance(doc.get("url"), dict) else {}
            title = (doc.get("meta") or {}).get("title") or url_data.get("label") or ""
            if not title:
                # Try extracting from blocks
                for block in doc.get("blocks") or []:
                    if block.get("blockType") == "hero":
                        title = block.get("title", "")
                        break
            if not title:
                continue

            slug = url_data.get("url") or url_data.get("path") or ""
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
                    "tags": [t.get("title", "") for t in doc.get("tags") or [] if isinstance(t, dict)],
                },
            ))

        # Update state
        # Newest last, so trimming drops the oldest IDs
        new_seen = list(dict.fromkeys(state.get("seen_ids", []) + [item.item_id for item in items]))
        new_seen = new_seen[-self.SEEN_IDS_CAP:]

        state_update = {"seen_ids": new_seen}
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
            "locale": "is",  # without it, localized fields (blocks, title, path) come back empty
        }
        try:
            resp = self.session.get(api_url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("docs", [])
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to fetch from Payload CMS: {e}")
            self._record_failure(f"Payload CMS: {e}")
            return None

    def _extract_content(self, doc: dict) -> str:
        """Extract text content from a Payload CMS news document."""
        parts = []
        for block in doc.get("blocks") or []:
            block_type = block.get("blockType", "")
            if block_type == "hero":
                desc = block.get("description", "")
                if desc:
                    parts.append(desc)
            elif block_type == "richText":
                rich = block.get("richText", {})
                self._extract_rich_text(rich, parts)
            elif isinstance(block.get("text"), dict):
                # TextBlock and similar: Lexical rich text
                text = self._lexical_text(block["text"]).strip()
                if text:
                    parts.append(text)

        content = "\n".join(parts)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content

    @staticmethod
    def _lexical_text(node) -> str:
        """Plain text of a Lexical rich-text tree, one line per paragraph."""
        if isinstance(node, list):
            return "".join(NatturaScraper._lexical_text(child) for child in node)
        if not isinstance(node, dict):
            return ""
        if "root" in node:
            return NatturaScraper._lexical_text(node["root"])
        text = node.get("text") or ""
        inner = "".join(NatturaScraper._lexical_text(child) for child in node.get("children") or [])
        end = "\n" if node.get("type") in ("paragraph", "heading", "listitem", "quote") else ""
        return text + inner + end

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
