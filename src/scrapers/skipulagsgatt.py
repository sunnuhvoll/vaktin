"""Scraper for Skipulagsgátt (Icelandic Planning Portal).

URL: https://www.skipulagsgatt.is
Uses a public GraphQL API to fetch planning cases (skipulagsmál).
Covers all municipalities: zoning plans, master plans, EIA, construction
permits, and more.

Separate from the island.is-based skipulagsstofnun.py which only covers
the HMS EIA database. This portal is much broader.
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://www.skipulagsgatt.is/graphql"

LIST_QUERY = """
query($input: IssueSpecificationInput, $first: Int, $after: String) {
  issueConnection(input: $input, first: $first, after: $after) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      cursor
      node {
        id
        issueNumber
        title
        description
        publishedDate
        process { title type shortTitle }
        currentPhase { name state reviewStartDate reviewEndDate }
        communities { name }
        delegation { entityName }
        tags
      }
    }
  }
}
"""


class SkipulagsgattScraper(BaseScraper):
    """Fetches planning cases from Skipulagsgátt via GraphQL API.

    Uses timestamp-based delta: the API supports fromDate filtering,
    so we only fetch cases published after last_check. A small seen_ids
    set is kept as a safety net against duplicates.
    """

    SEEN_IDS_CAP = 200

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items = []

        date_from = last_check or self._max_age_cutoff().isoformat()
        cases, fetch_ok = self._fetch_cases(date_from=date_from)
        self._total_fetched = len(cases)

        for case in cases:
            case_id = str(case.get("id", ""))
            item_id = f"skipgatt_{case_id}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            title = case.get("title", "")
            if not title:
                continue

            issue_number = case.get("issueNumber", "")
            published = case.get("publishedDate", "")

            if self._is_too_old(published):
                continue

            # Build URL: https://www.skipulagsgatt.is/issues/YYYY/ORDER/
            year_order = case.get("issueNumber", "").split("/")
            if len(year_order) == 2:
                url = f"https://www.skipulagsgatt.is/issues/{year_order[1]}/{year_order[0].lstrip('0')}/"
            else:
                url = f"https://www.skipulagsgatt.is/"

            content = self._build_content(case)

            # Extract deadline from current phase
            phase = case.get("currentPhase") or {}
            deadline = phase.get("reviewEndDate")

            communities = [c.get("name", "") for c in (case.get("communities") or [])]
            process = case.get("process") or {}

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=f"[{issue_number}] {title}" if issue_number else title,
                url=url,
                date=published or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "skipulagsgatt",
                    "issue_number": issue_number,
                    "process_type": process.get("type", ""),
                    "process_title": process.get("title", ""),
                    "communities": communities,
                    "deadline": deadline,
                    "municipality": ", ".join(communities) if communities else None,
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
        """Execute a GraphQL query against the Skipulagsgátt API."""
        try:
            resp = self.session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                timeout=20,
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

    def _fetch_cases(self, date_from: str, page_size: int = 50) -> tuple[list[dict], bool]:
        """Fetch planning cases published after date_from.

        Returns (cases, fetch_ok). fetch_ok is False on API failure.
        """
        date_str = date_from[:19]  # Trim to second precision
        if not date_str.endswith("Z") and "+" not in date_str:
            date_str += "Z"
        logger.info(f"[{self.source_id}] Fetching cases since {date_str}")

        variables = {
            "input": {
                "fromDate": date_str,
                "sortBy": "PUBLISHED",
            },
            "first": page_size,
        }

        data = self._graphql(LIST_QUERY, variables)
        if not data:
            return [], False

        connection = data.get("issueConnection", {})
        edges = connection.get("edges", [])
        cases = [edge.get("node", {}) for edge in edges]
        total = connection.get("totalCount", len(cases))
        page_info = connection.get("pageInfo", {})

        # Paginate if needed
        while page_info.get("hasNextPage") and page_info.get("endCursor"):
            variables["after"] = page_info["endCursor"]
            page_data = self._graphql(LIST_QUERY, variables)
            if not page_data:
                break
            conn = page_data.get("issueConnection", {})
            more_edges = conn.get("edges", [])
            cases.extend(edge.get("node", {}) for edge in more_edges)
            page_info = conn.get("pageInfo", {})

        logger.info(f"[{self.source_id}] Skipulagsgátt has {total} total cases, fetched {len(cases)}")
        return cases, True

    def _build_content(self, case: dict) -> str:
        """Build content string from case data for analysis."""
        parts = []

        process = case.get("process") or {}
        if process.get("title"):
            parts.append(f"Tegund: {process['title']}")

        communities = [c.get("name", "") for c in (case.get("communities") or [])]
        if communities:
            parts.append(f"Sveitarfélag: {', '.join(communities)}")

        delegation = case.get("delegation") or {}
        if delegation.get("entityName"):
            parts.append(f"Framkvæmdaraðili: {delegation['entityName']}")

        phase = case.get("currentPhase") or {}
        if phase.get("name"):
            parts.append(f"Fasi: {phase['name']}")
        if phase.get("reviewEndDate"):
            parts.append(f"Umsagnarfrestur til: {phase['reviewEndDate'][:10]}")

        tags = case.get("tags") or []
        if tags:
            parts.append(f"Merki: {', '.join(tags)}")

        description = case.get("description", "")
        if description:
            parts.append(f"\n{description}")

        content = "\n".join(parts)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content
