"""Report generation — creates markdown reports and updates the index."""

import html as html_mod
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent / "reports"
WEEKLY_DIR = REPORTS_DIR / "weekly"
ARCHIVE_DIR = REPORTS_DIR / "archive"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "sources.yml"

SEVERITY_EMOJI = {
    "critical": "🔴",
    "important": "🟡",
    "monitor": "🔵",
}

SEVERITY_ORDER = {"critical": 0, "important": 1, "monitor": 2}

INDEX_MAX_AGE_DAYS = 30  # Auto-expire items older than this from the index

REGION_LABELS = {
    "hofudborgarsvaedid": "Höfuðborgarsvæðið",
    "sudurnes": "Suðurnes",
    "vesturland": "Vesturland",
    "vestfirdir": "Vestfirðir",
    "nordurland": "Norðurland",
    "austurland": "Austurland",
    "sudurland": "Suðurland",
    "landsvitt": "Landsvítt",
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
    """Update reports/index.md with current active issues."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    region_map = _load_region_map()
    source_urls = _load_source_urls()

    # Load existing index items if any
    existing = _load_existing_index()

    # Expire items older than INDEX_MAX_AGE_DAYS
    cutoff = (datetime.now() - timedelta(days=INDEX_MAX_AGE_DAYS)).isoformat()
    expired_ids = [
        item_id for item_id, item in existing.items()
        if item.get("date", "") and item["date"] < cutoff
    ]
    if expired_ids:
        for item_id in expired_ids:
            del existing[item_id]
        logger.info(f"Expired {len(expired_ids)} items older than {INDEX_MAX_AGE_DAYS} days from index")

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

    # Sort by severity then date
    sorted_items = sorted(
        existing.values(),
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
        f'<p>Fjöldi virkra mála: <strong><span id="total-count">{len(sorted_items)}</span></strong></p>',
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

    # Tag items with org relevance before saving
    _tag_orgs(existing)

    # Save structured data for future runs
    _save_index_data(existing)

    # Generate org-specific views
    for slug, org_config in ORG_VIEWS.items():
        generate_org_view(slug, org_config, existing)

    # Regenerate sources page from sources.yml
    generate_sources_page()


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


def generate_org_view(slug: str, org_config: dict, all_items: dict) -> None:
    """Generate a filtered index page for a specific organization."""
    org_dir = Path(__file__).parent.parent / slug
    org_dir.mkdir(parents=True, exist_ok=True)

    region_map = _load_region_map()
    source_urls = _load_source_urls()

    # Filter items tagged for this org
    filtered = [item for item in all_items.values() if slug in item.get("orgs", [])]

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


def _sanitize_with_links(text: str) -> str:
    """Escape HTML but preserve safe <a href="...">...</a> links."""
    import re
    # Extract <a> tags, escape everything else, then restore links
    links = []
    def save_link(m):
        href = m.group(1)
        inner = html_mod.escape(m.group(2))
        # Only allow http/https links
        if href.startswith(("http://", "https://")):
            links.append(f'<a href="{html_mod.escape(href, quote=True)}" target="_blank">{inner}</a>')
        else:
            links.append(html_mod.escape(m.group(0)))
        return f"\x00LINK{len(links)-1}\x00"

    cleaned = re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', save_link, text, flags=re.DOTALL)
    cleaned = html_mod.escape(cleaned)
    for i, link in enumerate(links):
        cleaned = cleaned.replace(f"\x00LINK{i}\x00", link)
    return cleaned


def _append_item_html(lines: list[str], item: dict, region: str, region_label: str,
                      source_urls: dict[str, str] | None = None) -> None:
    """Append a single issue item as an HTML card."""
    title = html_mod.escape(item.get("title", "Ótitlað"))
    url = html_mod.escape(item.get("url", ""), quote=True)
    summary = _sanitize_with_links(item.get("summary_is", ""))
    category = html_mod.escape(item.get("category", ""))
    action = _sanitize_with_links(item.get("action_needed", ""))
    deadline = item.get("deadline")
    location = item.get("location")
    source = item.get("source_id", "")
    date = item.get("date", "")

    # Build metadata line
    meta_parts = []
    if category:
        meta_parts.append(f"<strong>Flokkur:</strong> {category}")
    if source:
        source_url = (source_urls or {}).get(source, "")
        source_escaped = html_mod.escape(source)
        if source_url:
            source_url_escaped = html_mod.escape(source_url, quote=True)
            meta_parts.append(f'<strong>Heimild:</strong> <a href="{source_url_escaped}">{source_escaped}</a>')
        else:
            meta_parts.append(f"<strong>Heimild:</strong> {source_escaped}")
    if date:
        display_date = date[:10] if len(date) > 10 else date
        meta_parts.append(f"<strong>Dagsetning:</strong> {html_mod.escape(display_date)}")
    if location:
        meta_parts.append(f"<strong>Staðsetning:</strong> {html_mod.escape(str(location))}")

    source_safe = html_mod.escape(source, quote=True)
    lines.append(f'<div class="issue-item" data-region="{region}" data-source="{source_safe}">')
    lines.append(f'<h3><a href="{url}">{title}</a></h3>')
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
        lines.append(f'<p class="action"><strong>Aðgerð:</strong> {action}</p>')
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
