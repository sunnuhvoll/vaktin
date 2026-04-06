"""Scraper for Icelandic municipality websites (sveitarfélög).

Handles meeting minutes (fundargerðir) from municipal councils and committees.
Each municipality has a different website but many follow similar patterns.
Supports document files (.pdf, .docx, .odt) — downloads and extracts text.
"""

import io
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class SveitarfelagScraper(BaseScraper):
    """Generic scraper for Icelandic municipality websites."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []
        base_url = self.config.get("url", "")

        if not base_url:
            logger.error(f"[{self.source_id}] No 'url' in config — skipping")
            return []

        for section in self.config.get("sections", []):
            section_name = section.get("name", "unknown")
            url = base_url + section.get("path", "")

            html = self.fetch_page_auto(url)
            if not html:
                logger.warning(f"[{self.source_id}] Could not fetch {url}")
                continue

            soup = BeautifulSoup(html, "html.parser")
            section_items = self._parse_meeting_list(soup, base_url, section_name, seen_ids)
            items.extend(section_items)

        # Update state
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 300:
            logger.info(f"[{self.source_id}] Truncating seen_ids from {len(new_seen)} to 300")
            new_seen = set(list(new_seen)[-300:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        no_content = getattr(self, "_no_content_count", 0)
        if no_content:
            logger.warning(
                f"[{self.source_id}] No content element on {no_content} pages "
                f"(titles + URLs still captured)"
            )

        return items

    def _parse_meeting_list(self, soup: BeautifulSoup, base_url: str,
                            section_name: str, seen_ids: set[str]) -> list[ScrapedItem]:
        """Parse meeting minutes list from a municipality page."""
        items = []

        meeting_elements = (
            soup.select(".fundargerdir-list .item")
            or soup.select("table.meetings tr")
            or soup.select(".meeting-list a")
            or soup.select("article.meeting")
            or soup.select(".list-group .list-group-item")
            or soup.select("ul.document-list li")
            # Tables used by many municipalities (e.g. Vesturbyggð)
            or self._find_table_rows(soup)
            or self._find_meeting_links(soup)
        )

        if not meeting_elements:
            logger.warning(
                f"[{self.source_id}] No meeting elements found for '{section_name}' — "
                f"CSS selectors may need updating"
            )
            return []

        self._total_fetched += len(meeting_elements)

        for element in meeting_elements:
            link = element if element.name == "a" else element.find("a", href=True)
            if not link or not link.get("href"):
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = f"{base_url}{href}" if href.startswith("/") else f"{base_url}/{href}"

            item_id = f"{self.source_id}_{href.rstrip('/').split('/')[-1]}"
            if item_id in seen_ids:
                self._skipped_seen += 1
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 3:
                continue

            if self._is_navigation_link(title, href):
                continue

            date_str = self._extract_date(element)

            # Skip items older than MAX_AGE_DAYS (protects against first-run floods)
            if self._is_too_old(date_str):
                self._skipped_old += 1
                continue

            content = self._fetch_meeting_content(href)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=href,
                date=date_str or datetime.now().isoformat(),
                content=content,
                metadata={
                    "source_type": "sveitarfelag",
                    "section": section_name,
                    "municipality": self.config.get("name", self.source_id),
                },
            ))

        return items

    def _find_table_rows(self, soup: BeautifulSoup) -> list:
        """Find table rows that contain links (common meeting list format)."""
        # Only match tables that look like meeting lists — check if any cell
        # or the page URL mentions fundargerðir-related keywords.
        for table in soup.find_all("table"):
            rows = table.select("tr")
            has_links = any(row.find("a", href=True) for row in rows)
            if has_links and len(rows) > 1:
                # Skip the header row
                return rows[1:] if rows[0].find("th") else rows
        return []

    def _find_meeting_links(self, soup: BeautifulSoup) -> list:
        """Fallback: find links that look like meeting minutes."""
        results = []
        keywords = ["fundargerð", "fundargerðir", "fundur", "bókun"]
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True).lower()
            href = link.get("href", "").lower()
            # Match by link text
            if any(kw in text for kw in keywords):
                results.append(link)
            # URL-based match: links with fundargerdir in path pointing to sub-pages
            elif "/fundargerdir/" in href and len(text) > 3:
                results.append(link)
        return results

    def _is_navigation_link(self, title: str, href: str) -> bool:
        """Filter out navigation and irrelevant links."""
        skip_patterns = [
            "forsíða", "heim", "hafa samband", "leit", "english",
            "login", "innskráning",
        ]
        title_lower = title.lower()
        href_lower = href.lower()
        return any(p in title_lower or p in href_lower for p in skip_patterns)

    def _extract_date(self, element) -> str:
        """Try to extract a date from a meeting element."""
        time_el = element.find("time")
        if time_el:
            return time_el.get("datetime", "") or time_el.get_text(strip=True)

        date_el = element.find(class_=lambda c: c and "date" in c.lower() if c else False)
        if date_el:
            return date_el.get_text(strip=True)

        text = element.get_text()
        date_match = re.search(r'\d{1,2}\.\d{1,2}\.\d{4}', text)
        if date_match:
            return date_match.group()
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', text)
        if date_match:
            return date_match.group()

        # Try Icelandic month names in text: "2. júlí '25", "4. feb 2026"
        from .base import _parse_icelandic_date
        dt = _parse_icelandic_date(text)
        if dt:
            return dt.strftime("%d.%m.%Y")

        return ""

    def _extract_content(self, soup: BeautifulSoup) -> str | None:
        """Try to extract content from parsed HTML. Returns None if not found."""
        content_el = (
            soup.select_one(".fundargerdir-content")
            or soup.select_one("article .content")
            or soup.select_one(".meeting-content")
            or soup.select_one("article")
            or soup.select_one("main .content")
            or soup.select_one("main")
            # Common Icelandic municipality CMS patterns
            or soup.select_one("#contentContainer .contentWrap")
            or soup.select_one("[role=main]")
            or soup.select_one("#contentContainer")
            or soup.select_one(".region-content")
        )
        if not content_el:
            return None
        for tag in content_el.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return content_el.get_text(separator="\n", strip=True)

    # File extensions that are documents (not web pages)
    DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".odt", ".doc", ".xlsx", ".pptx"}

    def _fetch_meeting_content(self, url: str) -> str:
        """Fetch and extract content from a meeting minutes page or document.

        Handles both HTML pages and downloadable documents (.pdf, .docx, .odt).
        For HTML: tries requests first, falls back to Playwright for JS-rendered sites.
        For documents: downloads and extracts text.
        """
        # Check if URL points to a document file
        url_lower = url.lower().split("?")[0]
        if any(url_lower.endswith(ext) for ext in self.DOCUMENT_EXTENSIONS):
            return self._extract_document_text(url)

        html = self.fetch_page(url)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        text = self._extract_content(soup)
        if text:
            return text if len(text) <= 15000 else text[:15000] + "\n\n[Texti styttur]"

        # Content not found in static HTML — likely JS-rendered, try Playwright
        js_html = self.fetch_page_js(url)
        if js_html:
            soup = BeautifulSoup(js_html, "html.parser")
            text = self._extract_content(soup)
            if text:
                return text if len(text) <= 15000 else text[:15000] + "\n\n[Texti styttur]"

        logger.debug(f"[{self.source_id}] No content element found on {url}")
        return ""

    def _extract_document_text(self, url: str) -> str:
        """Download a document file and extract its text content."""
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.debug(f"[{self.source_id}] Could not download document {url}: {e}")
            return ""

        url_lower = url.lower()
        content = resp.content
        text = ""

        try:
            if url_lower.endswith(".pdf"):
                text = self._extract_pdf(content)
            elif url_lower.endswith(".docx"):
                text = self._extract_docx(content)
            elif url_lower.endswith(".odt"):
                text = self._extract_odt(content)
            else:
                logger.debug(f"[{self.source_id}] Unsupported document format: {url}")
                return ""
        except Exception as e:
            logger.warning(f"[{self.source_id}] Failed to extract text from {url}: {e}")
            return ""

        if not text or len(text.strip()) < 50:
            logger.warning(f"[{self.source_id}] No/minimal text extracted from {url}")
            return ""

        if len(text) > 15000:
            text = text[:15000] + "\n\n[Texti styttur]"
        return text

    @staticmethod
    def _extract_pdf(content: bytes) -> str:
        """Extract text from PDF bytes using multiple methods.

        Tries pdftotext (poppler) → pypdf → pdfminer, returning the first
        result that yields meaningful text. Never silently gives up.
        """
        methods_tried = []

        # Method 1: pdftotext (poppler) — fast, handles most PDFs well
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", "-", "-"],
                input=content, capture_output=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                text = result.stdout.decode("utf-8", errors="replace").strip()
                if text:
                    return text
            methods_tried.append("pdftotext (no text)")
        except FileNotFoundError:
            methods_tried.append("pdftotext (not installed)")
        except subprocess.TimeoutExpired:
            methods_tried.append("pdftotext (timeout)")

        # Method 2: pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if text:
                return text
            methods_tried.append("pypdf (no text)")
        except ImportError:
            methods_tried.append("pypdf (not installed)")
        except Exception as e:
            methods_tried.append(f"pypdf ({e})")

        # Method 3: pdfminer — handles some corrupt PDFs better
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract
            text = pdfminer_extract(io.BytesIO(content)).strip()
            if text:
                return text
            methods_tried.append("pdfminer (no text)")
        except ImportError:
            methods_tried.append("pdfminer (not installed)")
        except Exception as e:
            methods_tried.append(f"pdfminer ({e})")

        # Method 4: OCR via tesseract — for scanned/image PDFs
        try:
            from pdf2image import convert_from_bytes
            import pytesseract
            images = convert_from_bytes(content, dpi=200)
            ocr_parts = []
            for i, img in enumerate(images):
                page_text = pytesseract.image_to_string(img, lang="isl+eng")
                if page_text.strip():
                    ocr_parts.append(page_text.strip())
            text = "\n".join(ocr_parts).strip()
            if text:
                logger.info(f"PDF extracted via OCR ({len(images)} pages, {len(text)} chars)")
                return text
            methods_tried.append("tesseract OCR (no text)")
        except ImportError as e:
            methods_tried.append(f"tesseract OCR (missing: {e.name})")
        except Exception as e:
            methods_tried.append(f"tesseract OCR ({e})")

        logger.warning(
            f"All PDF extraction methods failed: {', '.join(methods_tried)}. "
            f"PDF size: {len(content)} bytes"
        )
        return ""

    @staticmethod
    def _extract_docx(content: bytes) -> str:
        """Extract text from DOCX bytes."""
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        except ImportError:
            # Fallback: unzip and read XML directly
            import zipfile
            from xml.etree import ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                xml = z.read("word/document.xml")
            root = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            texts = [t.text for t in root.iter(f"{{{ns['w']}}}t") if t.text]
            return " ".join(texts).strip()

    @staticmethod
    def _extract_odt(content: bytes) -> str:
        """Extract text from ODT bytes."""
        import zipfile
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            xml = z.read("content.xml")
        root = ET.fromstring(xml)
        # ODT uses text:p elements for paragraphs
        texts = []
        for elem in root.iter():
            if elem.text:
                texts.append(elem.text)
            if elem.tail:
                texts.append(elem.tail)
        return "\n".join(line for line in " ".join(texts).split("  ") if line.strip()).strip()
