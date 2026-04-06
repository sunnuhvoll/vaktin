"""Scraper for island.is organization news via GraphQL API.

Generic scraper for any government organization hosted on island.is.
Uses the public GraphQL API with organization-based filtering.

Works for: Fiskistofa, Land og skógur, and any other island.is org.
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://island.is/api/graphql"

NEWS_QUERY = """
query($input: GetNewsInput!) {
  getNews(input: $input) {
    total
    items {
      id
      title
      slug
      date
      intro
      genericTags { title }
    }
  }
}
"""


class IslandNewsScraper(BaseScraper):
    """Fetches news from an island.is organization via GraphQL API.

    Uses timestamp-based delta: the API supports date filtering.
    Config must include 'island_org' with the organization slug.
    """

    SEEN_IDS_CAP = 50

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items = []

        org_slug = self.config.get("island_org", "")
        if not org_slug:
            logger.error(f"[{self.source_id}] No 'island_org' in config — skipping")
            return []

        org_base_url = f"https://island.is/s/{org_slug}"

        # Fetch news since last_check (or last MAX_AGE_DAYS on first run)
        date_from = last_check or self._max_age_cutoff().isoformat()
        news_result = self._fetch_news(org_slug, date_from)
        fetch_ok = news_result is not None
        news_items = news_result or []
        self._total_fetched = len(news_items)

        for item_data in news_items:
            news_id = item_data.get("id", "")
            item_id = f"{self.source_id}_{news_id}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            title = item_data.get("title", "").strip()
            if not title:
                continue

            slug = item_data.get("slug", "")
            url = f"{org_base_url}/frett/{slug}"
            date_str = item_data.get("date", "") or datetime.now().isoformat()
            intro = item_data.get("intro", "")
            tags = [t.get("title", "") for t in item_data.get("genericTags", [])]

            content_parts = []
            if tags:
                content_parts.append(f"Flokkar: {', '.join(tags)}")
            if intro:
                content_parts.append(intro)
            content = "\n".join(content_parts)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=date_str,
                content=content,
                metadata={
                    "source_type": "island_news",
                    "organization": org_slug,
                    "tags": tags,
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

    def _graphql(self, query: str, variables: dict) -> dict | None:
        """Execute a GraphQL query against the island.is API."""
        try:
            resp = self.session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                logger.error(f"[{self.source_id}] GraphQL errors: {data['errors']}")
                return None
            return data.get("data")
        except Exception as e:
            logger.error(f"[{self.source_id}] GraphQL request failed: {e}")
            return None

    def _fetch_news(self, org_slug: str, date_from: str) -> list[dict] | None:
        """Fetch news for an organization published after date_from. Returns None on failure."""
        date_str = date_from[:10]
        logger.info(f"[{self.source_id}] Fetching news since {date_str}")

        data = self._graphql(NEWS_QUERY, {
            "input": {
                "lang": "is",
                "size": 30,
                "order": "desc",
                "organization": org_slug,
            },
        })
        if not data:
            return None

        result = data.get("getNews", {})
        all_items = result.get("items", [])

        # Client-side date filtering (API doesn't support dateFrom)
        filtered = []
        for item in all_items:
            item_date = item.get("date", "")
            if item_date and item_date >= date_str:
                filtered.append(item)

        logger.info(
            f"[{self.source_id}] {result.get('total', 0)} total news, "
            f"{len(all_items)} fetched, {len(filtered)} after date filter"
        )
        return filtered
