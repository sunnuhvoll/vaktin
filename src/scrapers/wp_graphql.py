"""Scraper for WordPress sites with GraphQL API.

Used for municipalities that run headless WordPress with a public GraphQL
endpoint (e.g. Tjörneshreppur at admin.tjorneshreppur.is/graphql).
"""

import logging
from datetime import datetime

from .base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class WpGraphqlScraper(BaseScraper):
    """Fetches fundargerðir from a WordPress GraphQL API."""

    def scrape(self) -> list[ScrapedItem]:
        state = self.load_state()
        seen_ids: set[str] = set(state.get("seen_ids", []))
        items = []

        graphql_url = self.config.get("graphql_url", "")
        if not graphql_url:
            logger.error(f"[{self.source_id}] No 'graphql_url' in config — skipping")
            return []

        base_url = self.config.get("url", "")
        entries = self._fetch_fundargerdir(graphql_url)

        for entry in entries:
            db_id = str(entry.get("databaseId", ""))
            slug = entry.get("slug", "")
            if not db_id:
                continue

            item_id = f"{self.source_id}_{db_id}"
            if item_id in seen_ids:
                continue

            title = entry.get("title", "").strip()
            if not title:
                continue

            date_str = entry.get("date", "")
            # Extract custom fields
            fields = entry.get("fundargerdFields", {}) or {}
            dagsetning = fields.get("dagsetning", "")

            # Build URL
            url = f"{base_url}/fundargerdir/{slug}" if slug else base_url

            # Extract PDF URL if available
            pdf_url = ""
            pdf_node = fields.get("pdfDocument", {}) or {}
            if isinstance(pdf_node, dict):
                node = pdf_node.get("node", {}) or {}
                pdf_url = node.get("mediaItemUrl", "")

            # Extract committee name
            committees = entry.get("committees", {}) or {}
            committee_nodes = committees.get("nodes", [])
            committee = committee_nodes[0].get("name", "") if committee_nodes else ""

            content_parts = []
            if committee:
                content_parts.append(f"Nefnd: {committee}")
            if dagsetning:
                content_parts.append(f"Dagsetning: {dagsetning}")

            # Try to fetch PDF content if available
            if pdf_url:
                pdf_content = self._fetch_pdf_text(pdf_url)
                if pdf_content:
                    content_parts.append(pdf_content)

            items.append(ScrapedItem(
                source_id=self.source_id,
                item_id=item_id,
                title=title,
                url=url,
                date=date_str or datetime.now().isoformat(),
                content="\n".join(content_parts),
                metadata={
                    "source_type": "wp_graphql",
                    "committee": committee,
                    "municipality": self.config.get("name", self.source_id),
                },
            ))

        # Update state — cap at 300
        new_seen = seen_ids | {item.item_id for item in items}
        if len(new_seen) > 300:
            new_seen = set(list(new_seen)[-300:])

        self.save_state({
            "seen_ids": list(new_seen),
            "last_check": datetime.now().isoformat(),
        })

        return items

    def _fetch_fundargerdir(self, graphql_url: str) -> list[dict]:
        """Fetch fundargerðir from WordPress GraphQL API."""
        query = """
        {
          fundargerdir(first: 20) {
            nodes {
              title
              slug
              date
              databaseId
              fundargerdFields {
                dagsetning
                pdfDocument {
                  node {
                    mediaItemUrl
                  }
                }
              }
              committees {
                nodes {
                  name
                }
              }
            }
          }
        }
        """
        try:
            resp = self.session.post(
                graphql_url,
                json={"query": query},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("fundargerdir", {}).get("nodes", [])
        except Exception as e:
            logger.error(f"[{self.source_id}] GraphQL query failed: {e}")
            return []

    def _fetch_pdf_text(self, url: str) -> str:
        """Download and extract text from a PDF."""
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.debug(f"[{self.source_id}] Could not download PDF {url}: {e}")
            return ""

        import io
        methods_tried = []

        # Method 1: pdftotext (poppler)
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", "-", "-"],
                input=resp.content, capture_output=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                text = result.stdout.decode("utf-8", errors="replace").strip()
                if text:
                    return text[:12000] if len(text) > 12000 else text
            methods_tried.append("pdftotext (no text)")
        except FileNotFoundError:
            methods_tried.append("pdftotext (not installed)")
        except subprocess.TimeoutExpired:
            methods_tried.append("pdftotext (timeout)")

        # Method 2: pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(resp.content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if text:
                return text[:12000] if len(text) > 12000 else text
            methods_tried.append("pypdf (no text)")
        except ImportError:
            methods_tried.append("pypdf (not installed)")
        except Exception as e:
            methods_tried.append(f"pypdf ({e})")

        # Method 3: pdfminer
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract
            text = pdfminer_extract(io.BytesIO(resp.content)).strip()
            if text:
                return text[:12000] if len(text) > 12000 else text
            methods_tried.append("pdfminer (no text)")
        except ImportError:
            methods_tried.append("pdfminer (not installed)")
        except Exception as e:
            methods_tried.append(f"pdfminer ({e})")

        logger.warning(
            f"[{self.source_id}] All PDF extraction methods failed for {url}: "
            f"{', '.join(methods_tried)}"
        )
        return ""
