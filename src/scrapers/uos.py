"""Generic scraper for Prismic CMS API sites.

Used for:
- Umhverfis- og orkustofnun (UOS) — uos.is (repo: uos-web, doc type: news)
- Vatnajökulsþjóðgarður — vatnajokulsthjodgardur.is (repo: vatnajokulsthjodgardur, doc type: article)

Config keys:
  prismic_repo: Repository name (e.g. "uos-web") → API at {repo}.cdn.prismic.io/api/v2
  prismic_doc_type: Document type to query (default: "news")
  prismic_url_prefix: Base URL for article links (default: "{url}/frettir")
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class UosScraper(BaseScraper):
    """Fetches news from a Prismic CMS API.

    Uses timestamp-based delta: the Prismic API supports date.after
    predicates, so we only fetch documents published after last_check.
    A small seen_ids set is kept as a safety net.
    """

    SEEN_IDS_CAP = 30

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items = []

        # Migrate legacy seen_ids: rename "uos_*" → "{source_id}_*"
        if self.source_id != "uos" and any(sid.startswith("uos_") for sid in seen_ids):
            seen_ids = {
                sid.replace("uos_", f"{self.source_id}_", 1) if sid.startswith("uos_") else sid
                for sid in seen_ids
            }
            logger.info(f"[{self.source_id}] Migrated {len(seen_ids)} legacy uos_ seen_ids")

        # Read config — prismic_repo, doc type, URL prefix
        prismic_repo = self.config.get("prismic_repo", "uos-web")
        self._api_url = f"https://{prismic_repo}.cdn.prismic.io/api/v2"
        self._doc_type = self.config.get("prismic_doc_type", "news")
        base_url = self.config.get("url", "").rstrip("/")
        self._url_prefix = self.config.get("prismic_url_prefix", f"{base_url}/frettir")

        # Get current master ref (required for all Prismic queries)
        master_ref = self._get_master_ref()
        if not master_ref:
            return []

        # Fetch news published since last_check (or last MAX_AGE_DAYS on first run)
        date_after = last_check or self._max_age_cutoff().isoformat()
        news_result = self._fetch_news(master_ref, date_after=date_after)
        fetch_ok = news_result is not None
        news_items = news_result or []
        self._total_fetched = len(news_items)
        for doc in news_items:
            uid = doc.get("uid", "")
            item_id = f"{self.source_id}_{uid}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            title = self._extract_title(doc)
            if not title:
                continue

            date_str = doc.get("first_publication_date", "")
            content = self._extract_content(doc)
            url = f"{self._url_prefix}/{uid}"

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "prismic",
                    "tags": doc.get("tags", []),
                },
            ))

        # Update state — only advance last_check on successful fetch
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

    def _get_master_ref(self) -> str | None:
        """Fetch the current master ref from the Prismic API."""
        try:
            resp = self.session.get(self._api_url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for ref in data.get("refs", []):
                if ref.get("isMasterRef"):
                    return ref["ref"]
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to get Prismic master ref: {e}")
        return None

    def _fetch_news(self, ref: str, date_after: str) -> list[dict] | None:
        """Fetch documents from Prismic published after date_after. Returns None on failure."""
        predicates = f'[[at(document.type,"{self._doc_type}")]'
        date_str = date_after[:10]  # YYYY-MM-DD
        predicates += f'[date.after(document.first_publication_date,"{date_str}")]'
        logger.info(f"[{self.source_id}] Fetching {self._doc_type} since {date_str}")
        predicates += ']'

        params = {
            "ref": ref,
            "q": predicates,
            "lang": "is",
            "pageSize": 20,
            "orderings": "[document.first_publication_date desc]",
        }
        try:
            resp = self.session.get(
                f"{self._api_url}/documents/search",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to fetch from Prismic: {e}")
            return None

    def _extract_title(self, doc: dict) -> str:
        """Extract title text from a Prismic document."""
        title_field = doc.get("data", {}).get("title", [])
        if isinstance(title_field, list):
            return " ".join(block.get("text", "") for block in title_field).strip()
        if isinstance(title_field, str):
            return title_field
        return ""

    def _extract_content(self, doc: dict) -> str:
        """Extract text content from a Prismic news document."""
        parts = []
        data = doc.get("data", {})

        # Primary content is in the 'paragraph' rich text field
        for block in data.get("paragraph", []):
            text = block.get("text", "")
            if text:
                parts.append(text)

        # Also extract from slices (cards, text blocks, etc.)
        for slice_item in data.get("slices", []):
            primary = slice_item.get("primary", {})
            for value in primary.values():
                if isinstance(value, list):
                    for block in value:
                        text = block.get("text", "")
                        if text and text not in parts:
                            parts.append(text)

        content = "\n".join(parts)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content
