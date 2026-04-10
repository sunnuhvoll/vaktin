"""Lögbirtingablaðið scraper — Icelandic Official Gazette PDF issues.

Downloads new PDF issues from files.logbirtingablad.is, extracts text,
and splits into individual notices for analysis.

URL pattern: https://files.logbirtingablad.is/adverts/issues/{year}/lbl-{nr}-{year}.pdf
Issues are numbered sequentially per year (typically 3-5 per week).
"""

import io
import logging
import re
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

PDF_URL_TEMPLATE = "https://files.logbirtingablad.is/adverts/issues/{year}/lbl-{nr}-{year}.pdf"

# Max issues to fetch in one run (prevent huge backlogs on first run)
MAX_NEW_ISSUES = 30


class LogbirtingabladScraper(BaseScraper):
    """Fetches new PDF issues from Lögbirtingablaðið and extracts notices."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        year = datetime.now().year
        last_issue_data = state.get("last_issue", {})
        last_issue = last_issue_data.get(str(year), 0)

        # First run: find the latest issue and start from there
        # (don't process the entire year's backlog)
        if last_issue == 0:
            last_issue = self._find_latest_issue(year)
            if last_issue > 0:
                # Process only the latest issue on first run
                last_issue -= 1
                logger.info(f"[{self.source_id}] First run — starting from issue {last_issue + 1}")

        items = []
        issues_checked = 0
        consecutive_misses = 0

        # Probe for new issues starting from last known + 1
        nr = last_issue + 1
        while issues_checked < MAX_NEW_ISSUES and consecutive_misses < 3:
            url = PDF_URL_TEMPLATE.format(year=year, nr=nr)
            logger.debug(f"[{self.source_id}] Checking issue {nr}...")

            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 404:
                    consecutive_misses += 1
                    nr += 1
                    continue
                resp.raise_for_status()
            except Exception as e:
                logger.debug(f"[{self.source_id}] Could not fetch issue {nr}: {e}")
                consecutive_misses += 1
                nr += 1
                continue

            consecutive_misses = 0
            issues_checked += 1

            # Extract text from PDF
            text = self._extract_pdf_text(resp.content)
            if not text:
                logger.warning(f"[{self.source_id}] No text extracted from issue {nr}")
                nr += 1
                continue

            # Split into individual notices and create items
            notices = self._split_notices(text, nr, year)
            if notices:
                items.extend(notices)
                logger.info(
                    f"[{self.source_id}] Issue {nr}: {len(notices)} notices extracted"
                )
            else:
                # If splitting fails, submit the whole issue as one item
                item_id = f"lbl_{year}_{nr}"
                items.append(ScrapedItem(
                    source_id=self.source_id,
                    item_id=item_id,
                    title=f"Lögbirtingablaðið nr. {nr}/{year}",
                    url=url,
                    date=datetime.now().strftime("%d.%m.%Y"),
                    content=text[:15000],
                    metadata={
                        "source_type": "logbirtingablad",
                        "issue_nr": nr,
                        "year": year,
                    },
                ))

            # Update last known issue
            if "last_issue" not in state:
                state["last_issue"] = {}
            state["last_issue"][str(year)] = nr
            nr += 1

        if items:
            logger.info(f"[{self.source_id}] Found {len(items)} new items from {issues_checked} issues")
        else:
            fetched = nr - last_issue - 1
            logger.info(f"[{self.source_id}] No new issues found (checked up to nr. {nr - 1})")
            pass

        self.save_state(state)
        return items

    def _find_latest_issue(self, year: int) -> int:
        """Find the latest available issue number using binary search."""
        low, high = 1, 200
        latest = 0

        while low <= high:
            mid = (low + high) // 2
            url = PDF_URL_TEMPLATE.format(year=year, nr=mid)
            try:
                resp = self.session.head(url, timeout=10)
                if resp.status_code == 200:
                    latest = mid
                    low = mid + 1
                else:
                    high = mid - 1
            except Exception:
                high = mid - 1

        logger.info(f"[{self.source_id}] Latest issue for {year}: nr. {latest}")
        return latest

    def _extract_pdf_text(self, content: bytes) -> str:
        """Extract text from PDF bytes. Reuses the proven multi-method approach."""
        # Method 1: pdftotext (poppler) — fast, handles most PDFs well
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", "-layout", "-", "-"],
                input=content, capture_output=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                text = result.stdout.decode("utf-8", errors="replace").strip()
                if text:
                    return text
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Method 2: pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if text:
                return text
        except Exception:
            pass

        # Method 3: pdfminer
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract
            text = pdfminer_extract(io.BytesIO(content)).strip()
            if text:
                return text
        except Exception:
            pass

        return ""

    def _split_notices(self, text: str, issue_nr: int, year: int) -> list[ScrapedItem]:
        """Split a full issue text into individual notices.

        Each notice in Lögbirtingablaðið starts with a line containing
        'Útgáfud.:' (publication date). Page headers like '3373 Lögbirtingablað'
        are stripped.
        """
        # Remove page headers (e.g. "3373  Lögbirtingablað  Nr. 60")
        cleaned = re.sub(r'^\s*\d{4}\s+Lögbirtingablað\s+.*$', '', text, flags=re.MULTILINE)

        # Split on "Útgáfud.:" lines — each notice starts after one
        parts = re.split(r'Útgáfud\.:\s*\d{1,2}\.\s*\w+\s*\d{4}', cleaned)

        # First part is the issue header — skip it
        notice_texts = [p.strip() for p in parts[1:] if p.strip() and len(p.strip()) > 50]

        if not notice_texts:
            return []

        items = []
        for i, notice in enumerate(notice_texts):
            # Extract title from first non-empty line
            title_line = ""
            for line in notice.split("\n"):
                line = line.strip()
                if line and len(line) > 10:
                    title_line = line[:120]
                    break

            # Extract reference number (last line, format like 20260409009A)
            ref_match = re.search(r'(\d{11}[A-Z])\s*$', notice)
            ref = ref_match.group(1) if ref_match else f"{i + 1}"

            item_id = f"lbl_{year}_{issue_nr}_{ref}"

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=f"Lbl. {issue_nr}/{year}: {title_line}",
                url=PDF_URL_TEMPLATE.format(year=year, nr=issue_nr),
                date=datetime.now().strftime("%d.%m.%Y"),
                content=notice[:10000],
                metadata={
                    "source_type": "logbirtingablad",
                    "issue_nr": issue_nr,
                    "year": year,
                    "reference": ref,
                },
            ))

        return items
