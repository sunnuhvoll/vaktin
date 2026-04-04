"""Report generation — creates markdown reports and updates the index."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent / "reports"
WEEKLY_DIR = REPORTS_DIR / "weekly"
ARCHIVE_DIR = REPORTS_DIR / "archive"

SEVERITY_EMOJI = {
    "critical": "🔴",
    "important": "🟡",
    "monitor": "🔵",
}

SEVERITY_ORDER = {"critical": 0, "important": 1, "monitor": 2}


def generate_index(all_results: list[dict]) -> None:
    """Update reports/index.md with current active issues."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing index items if any
    existing = _load_existing_index()

    # Merge new results into existing
    for result in all_results:
        item_id = result.get("item_id", "")
        existing[item_id] = result

    # Sort by severity then date
    sorted_items = sorted(
        existing.values(),
        key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "monitor"), 9), x.get("date", "")),
    )

    # Build index markdown
    now = datetime.now()
    lines = [
        "# Vaktin — Virk mál",
        "",
        f"*Síðast uppfært: {now.strftime('%d.%m.%Y kl. %H:%M')}*",
        "",
        f"Fjöldi virkra mála: **{len(sorted_items)}**",
        "",
    ]

    # Group by severity
    for severity, label in [("critical", "Aðkallandi mál"), ("important", "Mikilvæg mál"), ("monitor", "Til eftirlits")]:
        group = [i for i in sorted_items if i.get("severity") == severity]
        if not group:
            continue

        emoji = SEVERITY_EMOJI[severity]
        lines.append(f"## {emoji} {label} ({len(group)})")
        lines.append("")

        for item in group:
            title = item.get("title", "Ótitlað")
            url = item.get("url", "")
            summary = item.get("summary_is", "")
            category = item.get("category", "")
            action = item.get("action_needed", "")
            deadline = item.get("deadline")
            location = item.get("location")
            source = item.get("source_id", "")
            date = item.get("date", "")

            lines.append(f"### [{title}]({url})")
            lines.append("")
            if category:
                lines.append(f"**Flokkur:** {category}  ")
            if source:
                lines.append(f"**Heimild:** {source}  ")
            if date:
                lines.append(f"**Dagsetning:** {date}  ")
            if location:
                lines.append(f"**Staðsetning:** {location}  ")
            if deadline:
                lines.append(f"**Frestur:** ⏰ {deadline}  ")
            lines.append("")
            if summary:
                lines.append(f"{summary}")
                lines.append("")
            if action:
                lines.append(f"**Aðgerð:** {action}")
                lines.append("")
            lines.append("---")
            lines.append("")

    if not sorted_items:
        lines.append("*Engin virk mál fundust í þessari keyrslu.*")
        lines.append("")

    lines.append("")
    lines.append("---")
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/INECTA/vaktin)*")

    index_path = REPORTS_DIR / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Updated index with {len(sorted_items)} items")

    # Save structured data for future runs
    _save_index_data(existing)


def generate_weekly_report(new_results: list[dict]) -> Path | None:
    """Generate a weekly summary report for new findings."""
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    week_num = now.isocalendar()[1]
    year = now.isocalendar()[0]
    filename = f"{year}-V{week_num:02d}.md"
    report_path = WEEKLY_DIR / filename

    # If file exists, append to it
    existing_content = ""
    if report_path.exists():
        existing_content = report_path.read_text(encoding="utf-8")

    if not new_results and not existing_content:
        return None

    lines = []

    if not existing_content:
        # New weekly report header
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=6)
        lines.append(f"# Vikuskýrsla — Vika {week_num}, {year}")
        lines.append("")
        lines.append(f"*{week_start.strftime('%d.%m.%Y')} – {week_end.strftime('%d.%m.%Y')}*")
        lines.append("")
        lines.append(f"## Ný mál ({len(new_results)})")
        lines.append("")
    else:
        lines.append(existing_content.rstrip())
        lines.append("")
        lines.append(f"## Uppfærsla {now.strftime('%d.%m.%Y')} — {len(new_results)} ný mál")
        lines.append("")

    if not new_results:
        lines.append("*Engin ný mál fundust.*")
    else:
        # Summary table
        lines.append("| Alvarleiki | Flokkur | Mál | Heimild |")
        lines.append("|:---:|---|---|---|")

        sorted_results = sorted(
            new_results,
            key=lambda x: SEVERITY_ORDER.get(x.get("severity", "monitor"), 9),
        )

        for item in sorted_results:
            severity = item.get("severity", "monitor")
            emoji = SEVERITY_EMOJI.get(severity, "⚪")
            category = item.get("category", "")
            title = item.get("title", "")[:60]
            url = item.get("url", "")
            source = item.get("source_id", "")
            lines.append(f"| {emoji} | {category} | [{title}]({url}) | {source} |")

        lines.append("")
        lines.append("### Samantekt")
        lines.append("")

        for item in sorted_results:
            title = item.get("title", "")
            summary = item.get("summary_is", "")
            action = item.get("action_needed", "")
            if summary:
                lines.append(f"**{title}**")
                lines.append(f"{summary}")
                if action:
                    lines.append(f"→ *{action}*")
                lines.append("")

    lines.append("")
    lines.append("---")
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/INECTA/vaktin)*")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Weekly report written to {report_path}")
    return report_path


def _load_existing_index() -> dict:
    """Load existing index data from JSON cache."""
    cache_path = REPORTS_DIR / ".index_data.json"
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_index_data(data: dict) -> None:
    """Save index data to JSON cache for future runs."""
    cache_path = REPORTS_DIR / ".index_data.json"
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
