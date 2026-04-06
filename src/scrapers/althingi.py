"""Scraper for Alþingi (Icelandic Parliament) via XML API.

URL: https://www.althingi.is
Uses the public XML REST API at /altext/xml/ to fetch bills (þingmál)
filtered by nature-relevant subject categories (efnisflokkar).

Nature-relevant categories:
  31 — Umhverfisstjórn og náttúruvernd (EIA, protected areas, national parks)
  30 — Orkumál og auðlindir (energy, power plants, mines)
  29 — Mengun (pollution, waste, environmental protection)
  24 — Samgöngur (roads, harbors, transport)
   3 — Landbúnaður (agriculture, aquaculture, forestry)
   4 — Sjávarútvegur (fisheries, whaling)
"""

import logging
from datetime import datetime
from xml.etree import ElementTree as ET

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

API_BASE = "https://www.althingi.is/altext/xml"

# Subject categories relevant to nature conservation
NATURE_CATEGORIES = [31, 30, 29, 24, 3, 4]

# Current legislative session (2025-2026)
CURRENT_SESSION = 157


class AlthingiScraper(BaseScraper):
    """Fetches nature-relevant bills from Alþingi via XML API.

    The Alþingi XML API does not return dates, so on first run we
    only seed the seen_ids and return nothing (to avoid a massive
    backlog of 200+ undated bills). Subsequent runs catch new bills.
    """

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        session = self.config.get("session", CURRENT_SESSION)
        first_run = not state.get("last_check")
        items = []

        for category_id in NATURE_CATEGORIES:
            category_items = self._fetch_category(session, category_id, seen_ids)
            items.extend(category_items)

        if first_run and items:
            # First run: seed seen_ids without returning items for analysis
            logger.info(
                f"[{self.source_id}] First run — seeding {len(items)} bill IDs "
                f"(no dates available, skipping analysis of backlog)"
            )
            new_seen = {item.item_id for item in items}
            self.save_state({
                "seen_ids": list(new_seen),
                "last_check": datetime.now().isoformat(),
                "session": session,
            })
            return []

        # Update state — only advance last_check if items were fetched successfully
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 500:
            new_seen = set(list(new_seen)[-500:])

        state_update = {
            "seen_ids": list(new_seen),
            "session": session,
        }
        if self._total_fetched > 0 or items:
            state_update["last_check"] = datetime.now().isoformat()
        else:
            state_update["last_check"] = state.get("last_check", datetime.now().isoformat())
        self.save_state(state_update)

        return items

    def _fetch_category(self, session: int, category_id: int,
                        seen_ids: set[str]) -> list[ScrapedItem]:
        """Fetch bills for a specific subject category."""
        url = f"{API_BASE}/efnisflokkar/efnisflokkur/?efnisflokkur={category_id}&lthing={session}"

        try:
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to fetch category {category_id}: {e}")
            return []

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.error(f"[{self.source_id}] Failed to parse XML for category {category_id}: {e}")
            return []

        items = []
        # XML structure: <efnisflokkar>/<yfirflokkur>/<efnisflokkur>/<málalisti>/<mál>
        all_mals = list(root.iter("mál"))
        self._total_fetched += len(all_mals)
        for mal in all_mals:
            mal_nr = mal.get("málsnúmer", "")
            if not mal_nr:
                continue

            item_id = f"althingi_{session}_{mal_nr}"
            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            name = mal.findtext("málsheiti", "").strip()
            if not name:
                continue

            # Type is nested: <málstegund><heiti>...</heiti></málstegund>
            mal_type_el = mal.find("málstegund")
            mal_type = ""
            if mal_type_el is not None:
                mal_type = mal_type_el.findtext("heiti", "").strip()

            efnisgreining = mal.findtext("efnisgreining", "").strip()

            bill_url = f"https://www.althingi.is/thingstorf/thingmalalistar-eftir-thingum/ferill/?ltg={session}&mnr={mal_nr}"

            content_parts = []
            if mal_type:
                content_parts.append(f"Tegund: {mal_type}")
            if efnisgreining:
                content_parts.append(f"Efnisgreining: {efnisgreining}")

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=f"[{mal_nr}] {name}",
                url=bill_url,
                date=datetime.now().isoformat(),
                content="\n".join(content_parts),
                metadata={
                    "source_type": "althingi",
                    "mal_nr": mal_nr,
                    "session": session,
                    "category_id": category_id,
                    "mal_type": mal_type,
                },
            ))

        return items

