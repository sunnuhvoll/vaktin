"""Bring stored items up to the current analysis version.

Items analysed before analysis v2 lack the case-grouping fields
(`headline_is`, `project_is`, `case_refs`) and were rated with looser
criteria. Each run re-triages the active ones from their stored summaries, in
batches, so the site follows the current rules without re-scraping. Archived
items are left as they are.

Re-triage may hide `monitor` items that no longer meet the criteria (they stay
in reports/.index_data.json with `relevant: false`), but never hides a
critical or important item: those were judged from the full document, which
this pass doesn't see. Every change is logged to reports/.retriage_log.json.
"""

import json
import logging
from datetime import date, datetime

from analyze import ANALYSIS_VERSION, retriage_items
from cases import known_projects
from reporter import (
    REPORTS_DIR,
    _active_period_start,
    _load_existing_index,
    _parse_item_datetime,
    _save_index_data,
)

logger = logging.getLogger(__name__)

LOG_PATH = REPORTS_DIR / ".retriage_log.json"
MAX_PER_RUN = 300       # bounds the extra Claude usage of a single run
MAX_LOG_ENTRIES = 3000


def _is_active(item: dict, active_start: date) -> bool:
    parsed = _parse_item_datetime(item.get("date"))
    # Unparseable dates stay in the active view, so they are re-triaged too
    return parsed is None or parsed.date() >= active_start


def migrate_active_items() -> int:
    """Re-triage active items stored before ANALYSIS_VERSION. Returns how many were updated."""
    data = _load_existing_index()
    if not data:
        return 0

    active_start = _active_period_start(date.today())
    active = [it for it in data.values() if _is_active(it, active_start)]
    stale = [
        it for it in active
        if it.get("analysis_v", 1) < ANALYSIS_VERSION and it.get("relevant", True) is not False
    ]
    if not stale:
        return 0

    # Newest first, so the most visible items are fixed first if the cap applies
    stale.sort(key=lambda it: (_parse_item_datetime(it.get("date")) or datetime.min).timestamp()
               if _parse_item_datetime(it.get("date")) else 0, reverse=True)
    stale = stale[:MAX_PER_RUN]
    logger.info(f"Re-triaging {len(stale)} stored items with the current analysis rules...")

    current = [it for it in active if it.get("analysis_v", 1) >= ANALYSIS_VERSION]
    updates = retriage_items(stale, known_projects(current))

    today = date.today().isoformat()
    changes = []
    for item_id, row in updates.items():
        item = data.get(item_id)
        if item is None:
            continue
        old_severity = item.get("severity")
        if row["relevant"]:
            item["severity"] = row["severity"]
        elif old_severity == "monitor":
            item["relevant"] = False
        # else: a critical/important item rated irrelevant keeps its old rating

        item["headline_is"] = row.get("headline_is")
        item["project_is"] = row.get("project_is")
        item["case_refs"] = row.get("case_refs") or []
        item["analysis_v"] = ANALYSIS_VERSION
        item["retriaged"] = today

        if item.get("severity") != old_severity or item.get("relevant") is False:
            item["severity_before_retriage"] = old_severity
            changes.append({
                "date": today,
                "item_id": item_id,
                "title": item.get("title", ""),
                "source_id": item.get("source_id", ""),
                "from": old_severity,
                "to": item.get("severity") if item.get("relevant", True) is not False else "hidden",
            })

    _save_index_data(data)

    if changes:
        try:
            log = json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else []
        except (json.JSONDecodeError, OSError):
            log = []
        log = (log + changes)[-MAX_LOG_ENTRIES:]
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

    hidden = sum(1 for c in changes if c["to"] == "hidden")
    logger.info(
        f"Re-triage: {len(updates)} of {len(stale)} items updated, "
        f"{len(changes) - hidden} re-rated, {hidden} hidden as no longer relevant"
    )
    return len(updates)
