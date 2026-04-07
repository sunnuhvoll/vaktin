"""Scraper for Borgarbyggð municipality (borgarbyggd.is).

Borgarbyggð uses a Next.js frontend backed by Fundagátt.is. Meeting minutes
are publicly accessible at /fundargerdir but data is embedded in SSR JSON
payloads (__next_f chunks), not in regular HTML elements.

This scraper extracts meeting data from the SSR payload and fetches
individual meeting content from /fundargerdir/{id}.
"""

import json
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class BorgarbyggdScraper(BaseScraper):
    """Fetches meeting minutes from Borgarbyggð via Next.js SSR payload."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []
        base_url = self.config.get("url", "https://borgarbyggd.is")

        html = self.fetch_page(f"{base_url}/fundargerdir")
        if not html:
            logger.warning(f"[{self.source_id}] Could not fetch fundargerdir page")
            return []

        meetings = self._parse_ssr_meetings(html)
        self._total_fetched = len(meetings)

        for meeting in meetings:
            meeting_id = str(meeting.get("id", ""))
            if not meeting_id:
                continue

            item_id = f"borgarbyggd_{meeting_id}"
            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            # Parse date from unix timestamp (milliseconds)
            date_str = ""
            dt_start = meeting.get("dt_start")
            if dt_start:
                try:
                    ts = int(dt_start)
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    date_str = dt.isoformat()
                except (ValueError, TypeError, OSError):
                    pass

            if self._is_too_old(date_str):
                continue

            subject = meeting.get("subject", "").strip()
            meeting_number = meeting.get("meetingNumber", "")
            title = f"{subject} - fundur {meeting_number}" if meeting_number else subject
            if not title:
                continue

            url = f"{base_url}/fundargerdir/{meeting_id}"
            content = self._fetch_meeting_content(url)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "borgarbyggd",
                    "section": "Fundargerðir",
                    "municipality": "Borgarbyggð",
                },
            ))

        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 300:
            new_seen = set(list(new_seen)[-300:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _parse_ssr_meetings(self, html: str) -> list[dict]:
        """Extract meeting data from Next.js SSR __next_f payload."""
        meetings = []

        # Find all __next_f.push data chunks
        pattern = re.compile(r'self\.__next_f\.push\(\[[\d,]*"(.+?)"\]\)', re.DOTALL)
        for match in pattern.finditer(html):
            chunk = match.group(1)
            # Unescape JSON string escapes (preserve UTF-8)
            chunk = chunk.replace('\\"', '"').replace('\\\\', '\\')

            # Find meeting objects with dt_start, id, subject
            meeting_pattern = re.compile(
                r'"dt_start":"(\d+)".*?"meetingType":(\d+).*?"id":(\d+).*?"meetingNumber":(\d+).*?"subject":"([^"]*)"'
            )
            for m in meeting_pattern.finditer(chunk):
                meetings.append({
                    "dt_start": m.group(1),
                    "meetingType": int(m.group(2)),
                    "id": int(m.group(3)),
                    "meetingNumber": int(m.group(4)),
                    "subject": m.group(5),
                })

        logger.info(f"[{self.source_id}] Found {len(meetings)} meetings in SSR payload")
        return meetings

    def _fetch_meeting_content(self, url: str) -> str:
        """Fetch full content from a meeting page."""
        html = self.fetch_page(url)
        if not html:
            return ""

        # Try to extract from SSR payload first
        content_parts = []
        pattern = re.compile(r'self\.__next_f\.push\(\[[\d,]*"(.+?)"\]\)', re.DOTALL)
        for match in pattern.finditer(html):
            chunk = match.group(1)
            try:
                chunk = chunk.encode().decode('unicode_escape')
            except Exception:
                continue
            # Look for agenda item text
            if 'agendaItem' in chunk or 'description' in chunk:
                # Extract readable text, strip JSON artifacts
                text = re.sub(r'\\n', '\n', chunk)
                text = re.sub(r'\\[trfb]', ' ', text)
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'"[a-zA-Z_]+":', '', text)
                text = re.sub(r'[{}\[\]",]', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 100:
                    content_parts.append(text)

        if content_parts:
            content = "\n\n".join(content_parts)
        else:
            # Fallback: parse rendered HTML
            soup = BeautifulSoup(html, "html.parser")
            main = soup.select_one("main") or soup.select_one("article")
            if main:
                for tag in main.find_all(["script", "style", "nav"]):
                    tag.decompose()
                content = main.get_text(separator="\n", strip=True)
            else:
                content = ""

        if len(content) > 15000:
            content = content[:15000] + "\n\n[Texti styttur]"
        return content
