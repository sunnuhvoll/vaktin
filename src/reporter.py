"""Report generation — creates markdown reports and updates the index."""

import html as html_mod
import json
import logging
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent / "reports"
WEEKLY_DIR = REPORTS_DIR / "weekly"
ARCHIVE_DIR = REPORTS_DIR / "archive"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "sources.yml"
HOME_PAGE = Path(__file__).parent.parent / "index.md"

SEVERITY_EMOJI = {
    "critical": "🔴",
    "important": "🟡",
    "monitor": "🔵",
}

SEVERITY_ORDER = {"critical": 0, "important": 1, "monitor": 2}

ICELANDIC_MONTH_NAMES = [
    "janúar",
    "febrúar",
    "mars",
    "apríl",
    "maí",
    "júní",
    "júlí",
    "ágúst",
    "september",
    "október",
    "nóvember",
    "desember",
]

REGION_LABELS = {
    "hofudborgarsvaedid": "Höfuðborgarsvæðið",
    "sudurnes": "Suðurnes",
    "vesturland": "Vesturland",
    "vestfirdir": "Vestfirðir",
    "nordurland": "Norðurland",
    "austurland": "Austurland",
    "sudurland": "Suðurland",
    "landsvitt": "Allt landið",
}

# ── Organization views ─────────────────────────────────────
# Each org has a slug, display name, relevant regions, and
# place names for matching national items to their area.

NORDURLAND_PLACES = [
    "Akureyri", "Húsavík", "Mývatn", "Eyjafjörður", "Skagafjörður",
    "Dalvík", "Siglufjörður", "Ólafsfjörður", "Sauðárkrókur", "Blönduós",
    "Grenivík", "Goðafoss", "Dettifoss", "Ásbyrgi", "Krafla",
    "Jökulsárgljúfur", "Norðurland", "Húnaflói", "Vatnsnes",
    "Fjallabyggð", "Þingeyjarsveit", "Langanesbyggð", "Skagaströnd",
    "Tjörnes", "Hörgársveit", "Eyjafjarðarsveit", "Húnaþing",
    "Skagafjörð", "Eyjafjörð",  # partial matches
]

ORG_VIEWS = {
    "sunn": {
        "name": "SUNN — Samtök um náttúruvernd á Norðurlandi",
        "short_name": "SUNN",
        "regions": ["nordurland"],
        "places": NORDURLAND_PLACES,
    },
}


def generate_index(all_results: list[dict]) -> None:
    """Update reports/index.md and archive pages."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    region_map = _load_region_map()
    source_urls = _load_source_urls()

    # Load full item history if any
    existing = _load_existing_index()

    # Load dismissed items
    dismissed = _load_dismissed()

    # Remove dismissed items from existing
    for item_id in dismissed:
        existing.pop(item_id, None)

    # Merge new results into existing, enriching with region (skip dismissed)
    for result in all_results:
        item_id = result.get("item_id", "")
        if item_id in dismissed:
            continue
        source = result.get("source_id", "")
        result["region"] = region_map.get(source, "landsvitt")
        existing[item_id] = result

    # Also backfill region on existing items that lack it
    for item in existing.values():
        if "region" not in item:
            item["region"] = region_map.get(item.get("source_id", ""), "landsvitt")

    # Tag items with org relevance before saving
    _tag_orgs(existing)

    # Save full structured history for future runs
    _save_index_data(existing)

    active_start = _active_period_start(datetime.now().date())
    active_items, archived_months = _partition_items(existing.values(), active_start)

    # Sort active items by severity then date
    sorted_items = sorted(
        active_items,
        key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "monitor"), 9), x.get("date", "")),
    )

    # Build index with Jekyll front matter
    now = datetime.now()
    lines = [
        "---",
        "layout: default",
        "title: Virk mál",
        "---",
        "",
        "<h1>Vaktin — Virk mál</h1>",
        "",
        f'<p><em>Síðast uppfært: {now.strftime("%d.%m.%Y kl. %H:%M")}</em></p>',
        "",
        f"<p>Virk mál eru birt frá og með <strong>{active_start.strftime('%d.%m.%Y')}</strong> "
        f"(fyrsti dagur síðasta mánaðar).</p>",
        "",
        f'<p>Fjöldi virkra mála: <strong><span id="total-count">{len(sorted_items)}</span></strong></p>',
        "",
        '<p><a href="archive/">Sjá eldri mánuði í skjalasafni</a></p>',
        "",
        '<div id="filter-target"></div>',
        "",
    ]

    # Group by severity — output as HTML for filtering support
    for severity, label in [("critical", "Aðkallandi mál"), ("important", "Mikilvæg mál"), ("monitor", "Til eftirlits")]:
        group = [i for i in sorted_items if i.get("severity") == severity]
        if not group:
            continue

        emoji = SEVERITY_EMOJI[severity]
        lines.append(f'<div class="severity-section" data-severity="{severity}">')
        lines.append(f'<h2>{emoji} {label} (<span class="group-count">{len(group)}</span>)</h2>')

        for item in group:
            source = item.get("source_id", "")
            region = item.get("region", region_map.get(source, "landsvitt"))
            region_label = REGION_LABELS.get(region, region)
            _append_item_html(lines, item, region, region_label, source_urls)

        lines.append("</div>")
        lines.append("")

    if not sorted_items:
        lines.append("<p><em>Engin virk mál fundust í þessari keyrslu.</em></p>")
        lines.append("")

    # Inject region data for client-side filtering
    lines.append("<script>")
    lines.append(f"window.VAKTIN_REGIONS={json.dumps(region_map, ensure_ascii=False)};")
    lines.append(f"window.VAKTIN_REGION_LABELS={json.dumps(REGION_LABELS, ensure_ascii=False)};")
    lines.append("</script>")
    lines.append("")
    lines.append("---")
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

    index_path = REPORTS_DIR / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Updated index with {len(sorted_items)} items")

    # Generate org-specific views
    for slug, org_config in ORG_VIEWS.items():
        generate_org_view(slug, org_config, active_items)

    # Generate archive pages
    generate_archive_views(archived_months, active_start, source_urls)

    # Regenerate sources page from sources.yml
    generate_sources_page()
    generate_home_page(sorted_items, active_start)


def _tag_orgs(items: dict) -> None:
    """Tag each item with which organizations it's relevant to."""
    for item in items.values():
        orgs = ["landvernd"]  # Landvernd covers all of Iceland
        region = item.get("region", "landsvitt")
        for slug, org in ORG_VIEWS.items():
            if region in org["regions"]:
                orgs.append(slug)
            elif region == "landsvitt" and _location_matches(item.get("location"), org["places"]):
                orgs.append(slug)
        item["orgs"] = orgs


def _location_matches(location: str | None, places: list[str]) -> bool:
    """Check if a location string mentions any of the given place names."""
    if not location:
        return False
    loc_lower = location.lower()
    return any(place.lower() in loc_lower for place in places)


def generate_org_view(slug: str, org_config: dict, all_items: list[dict]) -> None:
    """Generate a filtered index page for a specific organization."""
    org_dir = Path(__file__).parent.parent / slug
    org_dir.mkdir(parents=True, exist_ok=True)

    region_map = _load_region_map()
    source_urls = _load_source_urls()

    # Filter items tagged for this org
    filtered = [item for item in all_items if slug in item.get("orgs", [])]

    sorted_items = sorted(
        filtered,
        key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "monitor"), 9), x.get("date", "")),
    )

    now = datetime.now()
    org_name = org_config["name"]
    lines = [
        "---",
        "layout: default",
        f"title: {org_config['short_name']}",
        "---",
        "",
        f"<h1>{org_name}</h1>",
        "",
        f'<p><em>Síðast uppfært: {now.strftime("%d.%m.%Y kl. %H:%M")}</em></p>',
        "",
        f"<p>Virk mál eru birt frá og með <strong>{_active_period_start(now.date()).strftime('%d.%m.%Y')}</strong>.</p>",
        "",
        f'<p>Fjöldi virkra mála: <strong>{len(sorted_items)}</strong></p>',
        "",
    ]

    for severity, label in [("critical", "Aðkallandi mál"), ("important", "Mikilvæg mál"), ("monitor", "Til eftirlits")]:
        group = [i for i in sorted_items if i.get("severity") == severity]
        if not group:
            continue

        emoji = SEVERITY_EMOJI[severity]
        lines.append(f'<div class="severity-section" data-severity="{severity}">')
        lines.append(f'<h2>{emoji} {label} (<span class="group-count">{len(group)}</span>)</h2>')

        for item in group:
            source = item.get("source_id", "")
            region = item.get("region", region_map.get(source, "landsvitt"))
            region_label = REGION_LABELS.get(region, region)
            _append_item_html(lines, item, region, region_label, source_urls)

        lines.append("</div>")
        lines.append("")

    if not sorted_items:
        lines.append("<p><em>Engin virk mál fundust fyrir þetta svæði.</em></p>")
        lines.append("")

    lines.append("---")
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

    index_path = org_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Updated {slug} view with {len(sorted_items)} items")


def _active_period_start(today: date) -> date:
    """Return the first day of the previous month."""
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)


def _partition_items(items, active_start: date) -> tuple[list[dict], dict[str, list[dict]]]:
    """Split items into active and archived-by-month buckets."""
    active_items: list[dict] = []
    archived_months: dict[str, list[dict]] = {}

    for item in items:
        parsed = _parse_item_datetime(item.get("date"))
        if not parsed:
            active_items.append(item)
            continue

        item_date = parsed.date()
        if item_date >= active_start:
            active_items.append(item)
            continue

        month_key = item_date.strftime("%Y-%m")
        archived_months.setdefault(month_key, []).append(item)

    return active_items, archived_months


def _format_month_label(month_key: str) -> str:
    """Return an Icelandic month label like 'mars 2026'."""
    year_str, month_str = month_key.split("-")
    month_idx = int(month_str)
    return f"{ICELANDIC_MONTH_NAMES[month_idx - 1]} {year_str}"


def _cleanup_archive_pages(valid_months: set[str]) -> None:
    """Remove stale generated archive month pages."""
    if not ARCHIVE_DIR.exists():
        return

    for path in ARCHIVE_DIR.glob("*.md"):
        if path.name == "index.md":
            continue
        if path.stem not in valid_months:
            path.unlink(missing_ok=True)


def generate_archive_views(archived_months: dict[str, list[dict]], active_start: date,
                           source_urls: dict[str, str] | None = None) -> None:
    """Generate archive index and one page per archived month."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_archive_pages(set(archived_months))

    sorted_month_keys = sorted(archived_months.keys(), reverse=True)
    now = datetime.now()

    index_lines = [
        "---",
        "layout: default",
        "title: Skjalasafn",
        "---",
        "",
        "<h1>Vaktin — Skjalasafn</h1>",
        "",
        f'<p><em>Síðast uppfært: {now.strftime("%d.%m.%Y kl. %H:%M")}</em></p>',
        "",
        f"<p>Hér eru eldri mál sem eru eldri en virka tímabilið frá "
        f"<strong>{active_start.strftime('%d.%m.%Y')}</strong>.</p>",
        "",
        '<p><a href="../">Til baka í virk mál</a></p>',
        "",
    ]

    if sorted_month_keys:
        index_lines.append("| Mánuður | Fjöldi mála |")
        index_lines.append("|---|---:|")
        for month_key in sorted_month_keys:
            month_label = _format_month_label(month_key)
            count = len(archived_months[month_key])
            index_lines.append(f"| [{month_label}]({month_key}/) | {count} |")
    else:
        index_lines.append("<p><em>Engin eldri mál hafa verið færð í skjalasafn enn.</em></p>")

    index_lines.append("")
    index_lines.append("---")
    index_lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

    (ARCHIVE_DIR / "index.md").write_text("\n".join(index_lines), encoding="utf-8")

    region_map = _load_region_map()
    for month_key in sorted_month_keys:
        month_items = sorted(
            archived_months[month_key],
            key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "monitor"), 9), x.get("date", "")),
        )
        month_label = _format_month_label(month_key)

        lines = [
            "---",
            "layout: default",
            f"title: {month_label}",
            "---",
            "",
            f"<h1>Skjalasafn — {month_label}</h1>",
            "",
            f'<p><em>Síðast uppfært: {now.strftime("%d.%m.%Y kl. %H:%M")}</em></p>',
            "",
            f"<p>Fjöldi mála í skjalasafni: <strong>{len(month_items)}</strong></p>",
            "",
            '<p><a href="../">Til baka í skjalasafn</a></p>',
            "",
        ]

        for severity, label in [("critical", "Aðkallandi mál"), ("important", "Mikilvæg mál"), ("monitor", "Til eftirlits")]:
            group = [i for i in month_items if i.get("severity") == severity]
            if not group:
                continue

            emoji = SEVERITY_EMOJI[severity]
            lines.append(f'<div class="severity-section" data-severity="{severity}">')
            lines.append(f'<h2>{emoji} {label} (<span class="group-count">{len(group)}</span>)</h2>')

            for item in group:
                source = item.get("source_id", "")
                region = item.get("region", region_map.get(source, "landsvitt"))
                region_label = REGION_LABELS.get(region, region)
                _append_item_html(lines, item, region, region_label, source_urls)

            lines.append("</div>")
            lines.append("")

        if not month_items:
            lines.append("<p><em>Engin mál fundust fyrir þennan mánuð.</em></p>")
            lines.append("")

        lines.append("---")
        lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

        (ARCHIVE_DIR / f"{month_key}.md").write_text("\n".join(lines), encoding="utf-8")


def _sanitize_with_links(text: str) -> str:
    """Escape HTML while preserving a small safe subset of inline tags."""

    class SafeInlineHTMLParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag = tag.lower()
            if tag == "a":
                href = ""
                for key, value in attrs:
                    if key.lower() == "href" and value:
                        href = value.strip()
                        break
                if href.startswith(("http://", "https://")):
                    safe_href = html_mod.escape(href, quote=True)
                    self.parts.append(f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">')
                    return
            elif tag in {"strong", "em"}:
                self.parts.append(f"<{tag}>")

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if tag in {"a", "strong", "em"}:
                self.parts.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            self.parts.append(html_mod.escape(data))

        def handle_entityref(self, name: str) -> None:
            self.parts.append(f"&{name};")

        def handle_charref(self, name: str) -> None:
            self.parts.append(f"&#{name};")

    parser = SafeInlineHTMLParser()
    parser.feed(text or "")
    parser.close()
    cleaned = "".join(parser.parts)
    return re.sub(r"\s+</(a|strong|em)>", r"</\1>", cleaned)


def _parse_item_datetime(value: str | None) -> datetime | None:
    """Parse stored item date values into datetimes when possible."""
    if not value:
        return None

    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt)
        except ValueError:
            continue

    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None


def _display_item_date(value: str | None) -> str:
    """Return a stable Icelandic display date when possible."""
    parsed = _parse_item_datetime(value)
    if parsed:
        return parsed.strftime("%d.%m.%Y")
    if not value:
        return ""
    return value[:10] if len(value) > 10 else value


def _build_dek(item: dict, summary_html: str) -> str:
    """Create the short abstract shown under the title."""
    dek = item.get("dek_is") or ""
    dek = _sanitize_with_links(str(dek)).strip()
    if dek:
        return dek

    summary_text = re.sub(r"<[^>]+>", "", summary_html or "")
    summary_text = re.sub(r"\s+", " ", summary_text).strip()
    if not summary_text:
        return ""

    sentence_match = re.search(r"(.{55,}?[.!?])(?=\s+[A-ZÁÐÉÍÓÚÝÞÆÖ]|$)", summary_text)
    if sentence_match:
        candidate = sentence_match.group(1).strip()
    else:
        candidate = summary_text[:170].rstrip()
        if len(summary_text) > len(candidate):
            candidate = candidate.rsplit(" ", 1)[0].rstrip()
            candidate += "..."

    if len(candidate) > 190:
        candidate = candidate[:187].rsplit(" ", 1)[0].rstrip() + "..."
    return html_mod.escape(candidate)


def _append_item_html(lines: list[str], item: dict, region: str, region_label: str,
                      source_urls: dict[str, str] | None = None) -> None:
    """Append a single issue item as an HTML card."""
    title = html_mod.escape(item.get("title", "Ótitlað"))
    url = html_mod.escape(item.get("url", ""), quote=True)
    summary = _sanitize_with_links(item.get("summary_is", ""))
    dek = _build_dek(item, summary)
    # Support both "categories" (list, new) and "category" (string, legacy)
    raw_cats = item.get("categories") or []
    if not raw_cats:
        legacy = item.get("category", "")
        raw_cats = [legacy] if legacy else []
    category = ", ".join(str(c) for c in raw_cats)
    category = html_mod.escape(category)
    action = _sanitize_with_links(item.get("action_needed", ""))
    deadline = item.get("deadline")
    location = item.get("location")
    source = item.get("source_id", "")
    date = item.get("date", "")
    parsed_date = _parse_item_datetime(date)
    date_sort = parsed_date.date().isoformat() if parsed_date else ""

    # Build metadata line
    meta_parts = []
    if category:
        meta_parts.append(f"<strong>Flokkar:</strong> {category}" if ", " in category else f"<strong>Flokkur:</strong> {category}")
    if source:
        source_url = (source_urls or {}).get(source, "")
        source_escaped = html_mod.escape(source)
        if source_url:
            source_url_escaped = html_mod.escape(source_url, quote=True)
            meta_parts.append(f'<strong>Heimild:</strong> <a href="{source_url_escaped}">{source_escaped}</a>')
        else:
            meta_parts.append(f"<strong>Heimild:</strong> {source_escaped}")
    if date:
        meta_parts.append(f"<strong>Dagsetning:</strong> {html_mod.escape(_display_item_date(date))}")
    if location:
        meta_parts.append(f"<strong>Staðsetning:</strong> {html_mod.escape(str(location))}")

    source_safe = html_mod.escape(source, quote=True)
    # Store all categories as semicolon-separated for filtering
    cat_values = [html_mod.escape(c.strip().lower(), quote=True) for c in category.split(",") if c.strip()]
    category_safe = ";".join(cat_values)
    lines.append(
        f'<div class="issue-item" data-region="{region}" data-source="{source_safe}" '
        f'data-date="{html_mod.escape(date_sort, quote=True)}" data-category="{category_safe}">'
    )
    lines.append(f'<h3><a href="{url}">{title}</a></h3>')
    if dek:
        lines.append(f'<p class="dek">{dek}</p>')
    if meta_parts:
        meta_html = " &middot; ".join(meta_parts)
        lines.append(
            f'<div class="meta">{meta_html} &middot; '
            f'<span class="region-tag">{html_mod.escape(region_label)}</span></div>'
        )
    if deadline:
        lines.append(f'<p class="deadline">⏰ <strong>Frestur:</strong> {html_mod.escape(str(deadline))}</p>')
    if summary:
        lines.append(f'<p class="summary">{summary}</p>')
    if action:
        lines.append(f'<p class="action"><strong>Næstu skref:</strong> {action}</p>')
    lines.append("</div>")


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
        # New weekly report with Jekyll front matter
        week_start = now - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=6)
        lines.append("---")
        lines.append("layout: default")
        lines.append(f"title: Vika {week_num}, {year}")
        lines.append("---")
        lines.append("")
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
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Weekly report written to {report_path}")

    _update_weekly_index()

    return report_path


def _update_weekly_index() -> None:
    """Regenerate the weekly reports index page with links to all reports."""
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    # Find all weekly report files (not index.md, not .gitkeep)
    reports = sorted(
        [f for f in WEEKLY_DIR.glob("*.md") if f.name not in ("index.md",)],
        reverse=True,
    )

    lines = [
        "---",
        "layout: default",
        "title: Vikuskýrslur",
        "---",
        "",
        "# Vikuskýrslur",
        "",
    ]

    if reports:
        lines.append("| Vika | Skýrsla |")
        lines.append("|---|---|")
        for report in reports:
            name = report.stem  # e.g. "2026-V14"
            lines.append(f"| {name} | [{name}]({name}/) |")
    else:
        lines.append("*Engar skýrslur enn — fyrsta keyrsla hefur ekki átt sér stað.*")

    lines.append("")
    lines.append("---")
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

    index_path = WEEKLY_DIR / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Updated weekly index with {len(reports)} reports")


def _load_region_map() -> dict[str, str]:
    """Load source_id → region mapping from sources.yml."""
    try:
        with open(CONFIG_PATH) as f:
            sources = yaml.safe_load(f)
        return {sid: cfg.get("region", "landsvitt") for sid, cfg in sources.items()}
    except Exception:
        return {}


def _load_source_urls() -> dict[str, str]:
    """Load source_id → homepage URL mapping from sources.yml."""
    try:
        with open(CONFIG_PATH) as f:
            sources = yaml.safe_load(f)
        return {sid: cfg.get("url", "") for sid, cfg in sources.items() if cfg.get("url")}
    except Exception:
        return {}


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


def _load_dismissed() -> set[str]:
    """Load set of dismissed item IDs."""
    path = REPORTS_DIR / ".dismissed.json"
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            data = json.load(f)
        return set(data)
    except (json.JSONDecodeError, IOError):
        return set()


def _load_health_data() -> dict:
    """Load health data from the latest run if available."""
    path = REPORTS_DIR / ".health.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _item_sort_timestamp(item: dict) -> float:
    """Return a stable numeric timestamp for ordering items by date."""
    parsed = _parse_item_datetime(item.get("date"))
    if not parsed:
        return 0.0
    return parsed.timestamp()


def generate_home_page(active_items: list[dict], active_start: date) -> None:
    """Generate the landing page with current status and latest active items."""
    source_urls = _load_source_urls()
    health = _load_health_data()
    now = datetime.now()

    severity_counts = {
        "critical": sum(1 for item in active_items if item.get("severity") == "critical"),
        "important": sum(1 for item in active_items if item.get("severity") == "important"),
        "monitor": sum(1 for item in active_items if item.get("severity") == "monitor"),
    }

    latest_items = sorted(
        active_items,
        key=_item_sort_timestamp,
        reverse=True,
    )[:6]

    priority_items = sorted(
        [item for item in active_items if item.get("severity") in {"critical", "important"}],
        key=lambda item: (
            SEVERITY_ORDER.get(item.get("severity", "monitor"), 9),
            -_item_sort_timestamp(item),
        ),
    )[:3]

    health_sources = health.get("sources", {})
    total_sources = len(health_sources)
    healthy_sources = sum(
        1 for source in health_sources.values() if source.get("status") == "ok"
    )
    problem_sources = sum(
        1 for source in health_sources.values() if source.get("status") in {"empty", "no_scraper"}
    )

    run_start = health.get("run_start")
    run_display = (
        _parse_item_datetime(run_start).strftime("%d.%m.%Y kl. %H:%M")
        if _parse_item_datetime(run_start)
        else now.strftime("%d.%m.%Y kl. %H:%M")
    )

    lines = [
        "---",
        "layout: default",
        "title: Vaktin — Náttúruverndareftirlit",
        "---",
        "",
        "# Vaktin",
        "",
        "Vaktin sýnir ný og virk mál sem geta skipt náttúruverndarsamtök máli. Gögnin hér að neðan eru dregin beint úr nýjustu keyrslu kerfisins.",
        "",
        f"*Síðast uppfært: {now.strftime('%d.%m.%Y kl. %H:%M')}*",
        "",
        "## Staðan núna",
        "",
        f"Virk mál á forsíðu og í yfirlitum miðast við tímabilið frá <strong>{active_start.strftime('%d.%m.%Y')}</strong>.",
        "",
        "| Mælikvarði | Staða |",
        "|---|---:|",
        f"| Virk mál samtals | {len(active_items)} |",
        f"| Aðkallandi mál | {severity_counts['critical']} |",
        f"| Mikilvæg mál | {severity_counts['important']} |",
        f"| Til eftirlits | {severity_counts['monitor']} |",
    ]

    if total_sources:
        lines.extend([
            f"| Gagnalindir í lagi | {healthy_sources} af {total_sources} |",
            f"| Gagnalindir með frávik | {problem_sources} |",
        ])

    lines.extend([
        "",
        f"Nýjasta keyrsla hófst {run_display}.",
        "",
        "## Flýtileiðir",
        "",
        "| | |",
        "|---|---|",
        "| [**Virk mál**](reports/) | Öll virk mál með síum eftir landsvæði og tímabili |",
        "| [**SUNN**](sunn/) | Sértækt yfirlit fyrir Norðurland |",
        "| [**Gagnalindir**](sources/) | Hvaðan gögnin koma og hver staða vakta er |",
        "| [**Skjalasafn**](reports/archive/) | Eldri mál í skjalasafni |",
        "",
    ])

    if priority_items:
        lines.extend([
            "## Forgangsmál núna",
            "",
            "Þessi mál ættu að vera efst á blaði núna:",
            "",
        ])
        for item in priority_items:
            region = item.get("region", "landsvitt")
            region_label = REGION_LABELS.get(region, region)
            _append_item_html(lines, item, region, region_label, source_urls)
        lines.append("")

    if latest_items:
        lines.extend([
            "## Nýjustu mál",
            "",
            "Nýjustu færslurnar sem eru nú virkar í kerfinu:",
            "",
        ])
        for item in latest_items:
            region = item.get("region", "landsvitt")
            region_label = REGION_LABELS.get(region, region)
            _append_item_html(lines, item, region, region_label, source_urls)
        lines.append("")

    lines.extend([
        "---",
        "*Sjálfvirk forsíða frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*",
    ])

    HOME_PAGE.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Updated landing page")


# ── Sources page generation ────────────────────────────────

SOURCES_PAGE = Path(__file__).parent.parent / "sources.md"

REGION_ORDER = [
    ("landsvitt", "Ríkisstofnanir"),
    ("hofudborgarsvaedid", "Höfuðborgarsvæðið"),
    ("sudurnes", "Suðurnes"),
    ("vesturland", "Vesturland"),
    ("vestfirdir", "Vestfirðir"),
    ("nordurland", "Norðurland"),  # split by subregion in sources page
    ("austurland", "Austurland"),
    ("sudurland", "Suðurland"),
]

SUBREGION_ORDER = [
    ("Norðurland vestra", "Norðurland vestra"),
    ("Norðurland eystra", "Norðurland eystra"),
]

TYPE_LABELS = {
    "graphql_api": "GraphQL API",
    "island_news": "GraphQL API",
    "html_scrape": "HTML scrape",
    "prismic_api": "Prismic API",
    "xml_api": "XML API",
    "rss": "RSS",
    "wp_graphql": "WordPress GraphQL",
}


def generate_sources_page() -> None:
    """Generate sources.md from config/sources.yml + health status."""
    try:
        with open(CONFIG_PATH) as f:
            sources = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Cannot generate sources page: {e}")
        return

    # Load health status if available
    health_sources = {}
    health_path = REPORTS_DIR / ".health.json"
    if health_path.exists():
        try:
            with open(health_path) as f:
                health_sources = json.load(f).get("sources", {})
        except (json.JSONDecodeError, IOError):
            pass

    now = datetime.now()

    # Group by region
    by_region: dict[str, list[tuple[str, dict]]] = {}
    for sid, cfg in sources.items():
        region = cfg.get("region", "landsvitt")
        by_region.setdefault(region, []).append((sid, cfg))

    national = by_region.get("landsvitt", [])
    municipal = [(sid, cfg) for sid, cfg in sources.items() if cfg.get("region", "landsvitt") != "landsvitt"]

    lines = [
        "---",
        "layout: default",
        "title: Gagnalindir",
        "---",
        "",
        "# Gagnalindir Vaktarinnar",
        "",
        f"Yfirlit yfir allar gagnalindir sem Vaktin fylgist með. "
        f"**{len(national)} ríkisstofnanir** og **{len(municipal)} sveitarfélög** í vöktun.",
        "",
        "Allar gagnalindir eru sóttar daglega á virkum dögum á miðnætti.",
        "",
        f"*Síðast uppfært: {now.strftime('%d.%m.%Y')}*",
        "",
        "---",
        "",
    ]

    # ── National agencies ──
    lines.append(f"## Ríkisstofnanir ({len(national)})")
    lines.append("")
    lines.append("| Stofnun | Tegund | Staða |")
    lines.append("|---|---|---|")
    for sid, cfg in national:
        name = cfg.get("name", sid)
        url = cfg.get("url", "")
        note = cfg.get("note", "")
        src_type = TYPE_LABELS.get(cfg.get("type", ""), cfg.get("type", ""))
        status = _source_status(sid, health_sources)
        name_cell = f"[{name}]({url})" if url else name
        if note:
            name_cell += f" — {note}"
        lines.append(f"| {name_cell} | {src_type} | {status} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Municipalities by region ──
    lines.append(f"## Sveitarfélög ({len(municipal)})")
    lines.append("")

    for region_id, region_label in REGION_ORDER:
        if region_id == "landsvitt":
            continue
        region_sources = by_region.get(region_id, [])
        if not region_sources:
            continue

        # Split nordurland by subregion
        if region_id == "nordurland":
            sub_groups: dict[str, list] = {}
            for sid, cfg in region_sources:
                sub = cfg.get("subregion", "Norðurland eystra")
                sub_groups.setdefault(sub, []).append((sid, cfg))
            for sub_name, _ in SUBREGION_ORDER:
                sub_sources = sub_groups.get(sub_name, [])
                if not sub_sources:
                    continue
                lines.append(f"### {sub_name} ({len(sub_sources)})")
                lines.append("")
                _append_municipality_table(lines, sub_sources, health_sources)
                lines.append("")
        else:
            lines.append(f"### {region_label} ({len(region_sources)})")
            lines.append("")
            _append_municipality_table(lines, region_sources, health_sources)
            lines.append("")

    # ── Summary ──
    lines.append("---")
    lines.append("")
    ok_count = sum(1 for sid in sources if _source_status(sid, health_sources) == "Virkt")
    problem_count = len(sources) - ok_count
    lines.append("## Samantekt")
    lines.append("")
    lines.append("| | Fjöldi |")
    lines.append("|---|---|")
    lines.append(f"| Ríkisstofnanir | {len(national)} |")
    lines.append(f"| Sveitarfélög | {len(municipal)} |")
    lines.append(f"| Virk | {ok_count} |")
    if problem_count:
        lines.append(f"| Vandamál | {problem_count} |")
    lines.append(f"| **Samtals** | **{len(sources)}** |")
    lines.append("")
    lines.append("---")
    lines.append(f"*Sjálfvirk skýrsla frá [Vaktin](https://github.com/sunnuhvoll/vaktin)*")

    SOURCES_PAGE.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Updated sources page with {len(sources)} sources")


def _append_municipality_table(lines: list[str], sources: list[tuple[str, dict]],
                                health_sources: dict) -> None:
    """Append a municipality table with status and notes."""
    lines.append("| Sveitarfélag | Staða | Athugasemd |")
    lines.append("|---|---|---|")
    for sid, cfg in sources:
        name = cfg.get("name", sid)
        url = cfg.get("url", "")
        status = _source_status(sid, health_sources)
        note = cfg.get("note", "")
        name_cell = f"[{name}]({url})" if url else name
        lines.append(f"| {name_cell} | {status} | {note} |")


def _source_status(source_id: str, health_sources: dict) -> str:
    """Get human-readable Icelandic status for a source."""
    info = health_sources.get(source_id, {})
    status = info.get("status", "")
    if status == "ok":
        return "Virkt"
    if status == "empty":
        return "Tómt"
    if status == "no_scraper":
        return "Vantar scraper"
    if not status:
        return "Virkt"  # no health data yet
    return status


def _short_url(url: str) -> str:
    """Shorten URL for display: remove https:// and trailing slash."""
    return url.replace("https://", "").replace("http://", "").rstrip("/")
