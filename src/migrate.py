"""Bring stored items up to the current analysis version.

Items analysed before analysis v2 lack the case-grouping fields
(`headline_is`, `project_is`, `case_refs`) and were rated with looser
criteria. Each run re-triages the active ones from their stored summaries, in
batches, so the site follows the current rules without re-scraping. Archived
items are left as they are.

Texts written before text v2 tell the NGOs what to do ("Náttúruverndarsamtök
ættu að …"). `rewrite_stored_texts` rewrites them in batches, active items
first, then the archive, so they describe the case, its documents and its
deadlines instead. The old texts are in the git history of .index_data.json.

Re-triage may hide `monitor` items that no longer meet the criteria (they stay
in reports/.index_data.json with `relevant: false`), but never hides a
critical or important item: those were judged from the full document, which
this pass doesn't see. Every change is logged to reports/.retriage_log.json.
"""

import json
import logging
import re
from datetime import date, datetime

from analyze import ANALYSIS_VERSION, TEXT_VERSION, retriage_items, rewrite_texts
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
REWRITE_PER_RUN = 300   # text rewrites: 15 claude calls, 3 at a time

# Wording that may tell the NGOs what to do. An item with an action text is
# always rewritten; one without only when its dek or summary has such words
# (false hits cost a little; the rewrite returns those texts unchanged).
ADVICE_WORDS = re.compile(
    r"\b(ættu|ætti|þurfa|þarf að|mikilvægt|nauðsynlegt|brýnt|rétt er|vert er|ástæða til|"
    r"fylgjast með|kynna sér|hvet\w*)\b",
    re.IGNORECASE,
)


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


def _strip_tags(text) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def _hrefs(*texts) -> set[str]:
    return {href for text in texts for href in re.findall(r"href=['\"]([^'\"]+)", str(text or ""))}


def _needs_rewrite(item: dict) -> bool:
    if item.get("action_needed"):
        return True
    return any(ADVICE_WORDS.search(_strip_tags(item.get(key))) for key in ("dek_is", "summary_is"))


def _rewritten_fields(item: dict, row: dict) -> dict | None:
    """The rewritten texts, or None when the rewrite lost links or most of the summary."""
    new = {}
    for key in ("dek_is", "summary_is"):
        value = row.get(key)
        # An empty text stays empty; an empty answer keeps the old text
        new[key] = value.strip() if item.get(key) and isinstance(value, str) and value.strip() else item.get(key)
    action = row.get("action_needed")
    action = action.strip() if isinstance(action, str) else ""
    new["action_needed"] = action if action not in ("", "null", "—", "-") else None

    old = [item.get(key) for key in new]
    if _hrefs(*old) - _hrefs(*new.values()):
        return None
    if len(_strip_tags(new["summary_is"])) < 0.5 * len(_strip_tags(item.get("summary_is"))):
        return None
    return new


def rewrite_stored_texts() -> int:
    """Rewrite visible items' texts written before TEXT_VERSION. Returns how many were rewritten."""
    data = _load_existing_index()
    if not data:
        return 0

    old = [
        it for it in data.values()
        if it.get("text_v", 1) < TEXT_VERSION and it.get("relevant", True) is not False
    ]
    if not old:
        return 0

    todo = []
    for item in old:
        if _needs_rewrite(item):
            todo.append(item)
        else:
            item["text_v"] = TEXT_VERSION  # nothing to rewrite

    # Active items first, newest first, so the site's front lists are fixed first
    active_start = _active_period_start(date.today())

    def order(item):
        parsed = _parse_item_datetime(item.get("date"))
        return (_is_active(item, active_start), parsed.timestamp() if parsed else 0)

    todo.sort(key=order, reverse=True)
    batch = todo[:REWRITE_PER_RUN]
    if batch:
        logger.info(f"Rewriting the texts of {len(batch)} of {len(todo)} stored items (no advice to the NGOs)...")
    updates = rewrite_texts(batch) if batch else {}

    kept = 0
    for item_id, row in updates.items():
        item = data.get(item_id)
        if item is None:
            continue
        new = _rewritten_fields(item, row)
        if new is None:
            # Marked done anyway, so a text the rewrite can't handle isn't sent again every run
            logger.warning(f"Text rewrite of {item_id} lost links or most of the summary; kept the old text")
            kept += 1
        else:
            item.update(new)
        item["text_v"] = TEXT_VERSION

    _save_index_data(data)
    logger.info(
        f"Text rewrite: {len(updates) - kept} of {len(batch)} items rewritten, {kept} kept as they were, "
        f"{len(todo) - len(batch)} left for later runs"
    )
    return len(updates) - kept
