"""Scraper for Samráðsgátt ríkisins (Government Consultation Portal).

URL: https://island.is/samradsgatt
Uses the island.is public GraphQL API to fetch consultation cases.
This replaced the old samradsgatt.island.is HTML scraping approach
after the portal migrated to island.is (redirect 301).
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://island.is/api/graphql"

LIST_QUERY = """
query GetCases($input: ConsultationPortalCasesInput!) {
  consultationPortalGetCases(input: $input) {
    total
    cases {
      id
      caseNumber
      name
      statusName
      institutionName
      policyAreaName
      publishOnWeb
      adviceCount
    }
  }
}
"""

DETAIL_QUERY = """
query GetCase($input: ConsultationPortalCaseInput!) {
  consultationPortalCaseById(input: $input) {
    id
    caseNumber
    name
    statusName
    institutionName
    policyAreaName
    processBegins
    processEnds
    shortDescription
    detailedDescription
  }
}
"""


class SamradsgattScraper(BaseScraper):
    """Fetches consultation cases from Samráðsgátt via GraphQL API.

    Uses timestamp-based delta: the GraphQL API supports dateFrom filtering,
    so we only fetch cases published after last_check. A small seen_ids set
    is kept as a safety net against duplicates near the boundary.
    """

    # Small safety-net cap — timestamp does the heavy lifting
    SEEN_IDS_CAP = 50

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items = []

        # Fetch cases published since last_check (or last MAX_AGE_DAYS on first run)
        date_from = last_check or self._max_age_cutoff().isoformat()
        cases = self._fetch_cases(date_from=date_from)

        for case in cases:
            case_id = str(case.get("id", ""))
            item_id = f"samradsgatt_{case_id}"

            if item_id in seen_ids:
                continue

            name = case.get("name", "")
            if not name:
                continue

            # Fetch full case details
            detail = self._fetch_case_detail(int(case_id))
            content = self._build_content(detail or case)

            case_number = case.get("caseNumber", "")
            url = f"https://island.is/samradsgatt/mal/{case_id}"

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=f"[{case_number}] {name}" if case_number else name,
                url=url,
                date=case.get("publishOnWeb", "") or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "samradsgatt",
                    "case_number": case_number,
                    "status": case.get("statusName", ""),
                    "institution": case.get("institutionName", ""),
                    "policy_area": case.get("policyAreaName", ""),
                },
            ))

        # Update state — small seen_ids as safety net, timestamp does the filtering
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > self.SEEN_IDS_CAP:
            new_seen = set(list(new_seen)[-self.SEEN_IDS_CAP:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

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

    def _fetch_cases(self, date_from: str, page_size: int = 30) -> list[dict]:
        """Fetch consultation cases published after date_from."""
        query_input: dict = {"pageSize": page_size, "pageNumber": 1}
        query_input["dateFrom"] = date_from
        logger.info(f"[{self.source_id}] Fetching cases since {date_from[:19]}")

        data = self._graphql(LIST_QUERY, {"input": query_input})
        if not data:
            return []

        result = data.get("consultationPortalGetCases", {})
        total = result.get("total", 0)
        cases = result.get("cases", [])

        # If there are more cases than page_size, fetch remaining pages
        if total > page_size:
            pages_needed = (total + page_size - 1) // page_size
            for page in range(2, pages_needed + 1):
                query_input["pageNumber"] = page
                page_data = self._graphql(LIST_QUERY, {"input": query_input})
                if page_data:
                    more = page_data.get("consultationPortalGetCases", {}).get("cases", [])
                    cases.extend(more)

        return cases

    def _fetch_case_detail(self, case_id: int) -> dict | None:
        """Fetch full details for a single case."""
        data = self._graphql(DETAIL_QUERY, {
            "input": {"caseId": case_id},
        })
        if not data:
            return None
        return data.get("consultationPortalCaseById")

    def _build_content(self, case: dict) -> str:
        """Build content string from case data."""
        parts = []

        if case.get("institutionName"):
            parts.append(f"Stofnun: {case['institutionName']}")
        if case.get("policyAreaName"):
            parts.append(f"Málefnasvið: {case['policyAreaName']}")
        if case.get("statusName"):
            parts.append(f"Staða: {case['statusName']}")
        if case.get("processBegins") and case.get("processEnds"):
            parts.append(f"Tímabil: {case['processBegins'][:10]} til {case['processEnds'][:10]}")

        if case.get("shortDescription"):
            parts.append(f"\n{case['shortDescription']}")
        if case.get("detailedDescription"):
            parts.append(f"\n{case['detailedDescription']}")

        content = "\n".join(parts)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content
