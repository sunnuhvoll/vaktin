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


def _case_num(item_id: str) -> int:
    """Numeric case ID from 'samradsgatt_<id>' (IDs increase over time)."""
    tail = item_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


class SamradsgattScraper(BaseScraper):
    """Fetches consultation cases from Samráðsgátt via GraphQL API.

    Rolling lookback: every run re-queries the whole MAX_AGE_DAYS window
    (dateFrom filters on the publishOnWeb date) and seen_ids skips cases
    already processed. The window covers the active report period, so a
    failed run, or a fetch that silently returns nothing, is made good by
    the next run. ~1 case/day, so this is 2-3 list requests per run.
    """

    # Must hold every case ID in the lookback window (~60 per 62 days)
    SEEN_IDS_CAP = 500
    # Safety cap on list pages per run
    MAX_PAGES = 20

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items = []

        # Query from the MAX_AGE_DAYS cutoff, not from last_check: a fetch that
        # wrongly returns nothing still advances last_check, and a delta from
        # last_check would never go back for what it missed.
        date_from = self._max_age_cutoff().isoformat()
        cases = self._fetch_cases(date_from=date_from)
        fetch_ok = cases is not None
        cases = cases or []
        self._total_fetched = len(cases)

        for case in cases:
            case_id = str(case.get("id", ""))
            item_id = f"samradsgatt_{case_id}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            name = case.get("name", "")
            if not name:
                continue
            seen_ids.add(item_id)  # also drops a case repeated across pages

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

        # Update state — only advance last_check on successful fetch
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > self.SEEN_IDS_CAP:
            # Keep the newest IDs — the lookback re-fetches recent cases every run
            new_seen = set(sorted(new_seen, key=_case_num)[-self.SEEN_IDS_CAP:])

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

    def _fetch_cases(self, date_from: str, page_size: int = 30) -> list[dict] | None:
        """Fetch cases published on or after date_from (the API compares dates,
        not times). Returns None if the first page fails."""
        # pageNumber is 0-based. Starting at 1 skipped the newest page, so any
        # query with total <= page_size returned total > 0 but cases: [].
        query_input: dict = {"pageSize": page_size, "dateFrom": date_from}
        logger.info(f"[{self.source_id}] Fetching cases since {date_from[:19]}")

        cases: list[dict] = []
        total = 0
        for page in range(self.MAX_PAGES):
            query_input["pageNumber"] = page
            data = self._graphql(LIST_QUERY, {"input": query_input})
            if not data:
                if page == 0:
                    return None
                break  # keep what we have — the lookback retries the rest next run
            result = data.get("consultationPortalGetCases") or {}
            total = result.get("total", 0)
            batch = result.get("cases") or []
            cases.extend(batch)
            if len(batch) < page_size:
                break

        if len(cases) < total:
            logger.warning(
                f"[{self.source_id}] API reported {total} cases but only "
                f"{len(cases)} were returned"
            )
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
