"""Scraper for Skipulagsstofnun / HMS on island.is via GraphQL API.

Skipulagsstofnun was merged into HMS (Húsnæðis-, mannvirkja- og
skipulagsstofnun). The environmental assessment database (gagnagrunnur
umhverfismats) is now on island.is and uses a GenericList component
backed by a public GraphQL API.

This scraper fetches recent EIA cases (umhverfismat framkvæmda) directly
from the API — no HTML parsing or browser needed.
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://island.is/api/graphql"

# The GenericList ID for "Gagnagrunnur umhverfismats" on island.is/s/hms
EIA_DATABASE_LIST_ID = "6PA6bW36D1LIHI3iueZX6t"

LIST_QUERY = """
query($input: GetGenericListItemsInput!) {
  getGenericListItems(input: $input) {
    total
    items {
      id
      title
      slug
      date
      cardIntro {
        ... on Html { id document }
      }
      filterTags {
        title
        genericTagGroup { title }
      }
    }
  }
}
"""

DETAIL_QUERY = """
query($input: GetGenericListItemsInput!) {
  getGenericListItems(input: $input) {
    items {
      id
      title
      slug
      date
      content {
        ... on Html { id document }
      }
      filterTags {
        title
      }
    }
  }
}
"""


class SkipulagsstofnunScraper(BaseScraper):
    """Fetches EIA cases from HMS via island.is GraphQL API."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []

        # Fetch recent cases (newest first, 30 at a time)
        cases_result = self._fetch_cases(page=1, size=30)
        fetch_ok = cases_result is not None
        cases = cases_result or []
        self._total_fetched = len(cases)

        for case in cases:
            case_id = case.get("id", "")
            item_id = f"skip_{case_id}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            # Skip items older than MAX_AGE_DAYS
            if self._is_too_old(case.get("date", "")):
                continue

            title = case.get("title", "")
            if not title:
                continue

            slug = case.get("slug", "")
            url = f"https://island.is/s/hms/gagnagrunnur-umhverfismats/{slug}"

            tags = [t.get("title", "").strip() for t in case.get("filterTags", [])]
            intro = self._extract_rich_text(case.get("cardIntro", []))

            # Build content from intro + tags
            content_parts = []
            if tags:
                content_parts.append(f"Flokkur: {', '.join(tags)}")
            if intro:
                content_parts.append(intro)
            content = "\n".join(content_parts)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=case.get("date", "") or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "skipulagsstofnun_hms",
                    "section": "Gagnagrunnur umhverfismats",
                    "tags": tags,
                },
            ))

        if self._skipped_old:
            logger.info(f"[{self.source_id}] Skipped {self._skipped_old} items older than {self.MAX_AGE_DAYS} days")

        # Update state — only advance last_check on successful fetch
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 500:
            new_seen = set(list(new_seen)[-500:])

        last_check = state.get("last_check")
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

    def _fetch_cases(self, page: int = 1, size: int = 30) -> list[dict] | None:
        """Fetch recent EIA cases from the HMS database. Returns None on failure."""
        data = self._graphql(LIST_QUERY, {
            "input": {
                "lang": "is",
                "page": page,
                "size": size,
                "genericListId": EIA_DATABASE_LIST_ID,
            },
        })
        if not data:
            return None
        result = data.get("getGenericListItems", {})
        total = result.get("total", 0)
        items = result.get("items", [])
        logger.info(f"[{self.source_id}] EIA database has {total} total cases, fetched {len(items)}")
        return items

    def _extract_rich_text(self, html_blocks: list) -> str:
        """Extract plain text from Contentful-style rich text HTML blocks."""
        parts = []
        for block in html_blocks:
            doc = block.get("document", {})
            self._walk_document(doc, parts)
        return "\n".join(parts)

    def _walk_document(self, node: dict, parts: list) -> None:
        """Recursively walk a Contentful document tree and extract text."""
        if not isinstance(node, dict):
            return
        if node.get("nodeType") == "text" and node.get("value"):
            parts.append(node["value"])
        for child in node.get("content", []):
            self._walk_document(child, parts)
