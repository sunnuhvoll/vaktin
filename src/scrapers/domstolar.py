"""Scraper for Icelandic courts via island.is GraphQL API.

All three court levels (Hæstiréttur, Landsréttur, Héraðsdómstólar) migrated
to island.is in 2025-2026. The webVerdicts GraphQL query replaces the old
HTML/Playwright scraping approach.

Config keys:
  court_ids: List of court filter IDs for the webVerdicts query
"""

import logging
import re
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://island.is/api/graphql"

VERDICTS_QUERY = """
query WebVerdicts($input: WebVerdictsInput!) {
  webVerdicts(input: $input) {
    total
    items {
      id
      title
      court
      caseNumber
      verdictDate
      keywords
      presentings
    }
  }
}
"""

COURT_IDS = {
    "haestirettur": ["Hæstiréttur"],
    "landsrettur": ["landsrettur"],
    "heradsdomar": [
        "hd-reykjavik", "hd-vesturland", "hd-vestfirdir",
        "hd-nordurland-vestra", "hd-nordurland-eystra",
        "hd-austurland", "hd-sudurland", "hd-reykjanes",
    ],
}


class DomstolarScraper(BaseScraper):
    """Fetches court rulings from island.is webVerdicts GraphQL API."""

    SEEN_IDS_CAP = 500
    MAX_AGE_DAYS = 62

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        last_check = state.get("last_check")
        items: list[ScrapedItem] = []

        court_ids = self.config.get("court_ids") or COURT_IDS.get(self.source_id, [])
        if not court_ids:
            logger.error(f"[{self.source_id}] No court_ids configured")
            return []

        date_from = last_check or self._max_age_cutoff().isoformat()[:10]
        date_to = datetime.now().strftime("%Y-%m-%d")

        verdicts = self._fetch_verdicts(court_ids, date_from, date_to)
        fetch_ok = verdicts is not None
        verdicts = verdicts or []
        self._total_fetched = len(verdicts)

        for v in verdicts:
            vid = v.get("id", "")
            item_id = f"{self.source_id}_{vid}"

            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            verdict_date = v.get("verdictDate", "")
            if verdict_date and self._is_too_old(verdict_date[:10]):
                continue

            title = self._build_title(v)
            content = self._build_content(v)
            url = f"https://island.is/domar/{vid}"

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=verdict_date or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "domstolar",
                    "case_number": v.get("caseNumber", ""),
                    "court": v.get("court", ""),
                    "keywords": v.get("keywords", []),
                },
            ))

        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > self.SEEN_IDS_CAP:
            new_seen = set(list(new_seen)[-self.SEEN_IDS_CAP:])

        state_update = {"seen_ids": list(new_seen)}
        if fetch_ok:
            state_update["last_check"] = date_to
        elif last_check:
            state_update["last_check"] = last_check
        self.save_state(state_update)

        return items

    def _fetch_verdicts(self, court_ids: list[str], date_from: str, date_to: str) -> list[dict] | None:
        """Fetch verdicts from all pages. Returns None on failure."""
        all_items = []
        page = 1

        while True:
            query_input = {
                "court": court_ids,
                "dateFrom": date_from,
                "dateTo": date_to,
                "page": page,
            }

            logger.info(f"[{self.source_id}] Fetching verdicts page {page} ({date_from} to {date_to})")
            data = self._graphql(query_input)
            if data is None:
                return None if page == 1 else all_items

            result = data.get("webVerdicts", {})
            page_items = result.get("items", [])
            total = result.get("total", 0)

            all_items.extend(page_items)

            if len(all_items) >= total or not page_items:
                break
            page += 1

        logger.info(f"[{self.source_id}] Fetched {len(all_items)} verdicts total")
        return all_items

    def _graphql(self, query_input: dict) -> dict | None:
        try:
            resp = self.session.post(
                GRAPHQL_URL,
                json={"query": VERDICTS_QUERY, "variables": {"input": query_input}},
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

    def _build_title(self, verdict: dict) -> str:
        case_num = verdict.get("caseNumber", "")
        court = verdict.get("court", "")
        raw_title = verdict.get("title", "").replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
        parties = re.sub(r'\s*\([^)]*\)', '', raw_title).strip()
        if len(parties) > 120:
            parties = parties[:117] + "..."
        parts = []
        if case_num:
            parts.append(f"[{case_num}]")
        if court:
            parts.append(court)
        if parties:
            parts.append(parties)
        return " — ".join(parts) if parts else raw_title

    def _build_content(self, verdict: dict) -> str:
        parts = []

        if verdict.get("court"):
            parts.append(f"Dómstóll: {verdict['court']}")
        if verdict.get("caseNumber"):
            parts.append(f"Málsnúmer: {verdict['caseNumber']}")
        if verdict.get("verdictDate"):
            parts.append(f"Dagsetning: {verdict['verdictDate'][:10]}")
        if verdict.get("keywords"):
            parts.append(f"Efnisorð: {', '.join(verdict['keywords'])}")

        presentings = verdict.get("presentings", "")
        if presentings:
            clean = re.sub(r'<[^>]+>', '', presentings)
            parts.append(f"\n{clean}")

        content = "\n".join(parts)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content
