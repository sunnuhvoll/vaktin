"""Public org feed follows the same case groups as the rendered page."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import reporter


class PublicFeedTests(unittest.TestCase):
    def setUp(self):
        self.primary = {
            "item_id": "primary", "source_id": "town", "severity": "critical",
            "title": "Fundur", "headline_is": "Skipulag við á", "date": "2026-09-01",
            "url": "https://town.is/fundur", "region": "nordurland", "orgs": ["sunn"],
            "summary_is": "Viðkvæmt <strong>svæði</strong> og <a href='https://town.is/'>á</a>.",
            "dek_is": "Skipulag við á.", "action_needed": "Kynna sér gögn.",
            "categories": ["Skipulagsmál"], "location": "Norðurland", "project_is": "Á við Fjall",
            "deadline": "2026-10-01",
        }
        self.related = {
            "item_id": "related", "source_id": "agency", "severity": "monitor",
            "title": "Ný gögn", "date": "2026-10-15", "url": "https://agency.is/gogn",
            "region": "nordurland", "orgs": ["sunn"], "categories": ["Vatnsvernd"],
            "project_is": "Á við Fjall", "deadline": "2026-10-28",
        }
        self.names = patch.object(reporter, "_source_name", side_effect=lambda source: source)
        self.names.start()
        self.addCleanup(self.names.stop)

    def test_group_count_latest_activity_and_explicit_deadline(self):
        feed = reporter._org_public_feed("sunn", [[self.primary, self.related]],
                                         datetime(2026, 10, 16, 2, 31), {}, {})
        self.assertEqual((feed["case_count"], feed["document_count"]), (1, 2))
        self.assertEqual(feed["updated_at"], "2026-10-16T02:31")
        issue = feed["issues"][0]
        self.assertEqual(issue["severity"], "critical")
        self.assertEqual(issue["date"], "2026-09-01")
        self.assertEqual(issue["latest_date"], "2026-10-15")
        self.assertEqual(issue["deadline"], "2026-10-28")
        self.assertEqual(issue["related"][0]["id"], "related")
        self.assertEqual(issue["category_labels"], ["Skipulagsmál", "Vatnsvernd"])

    def test_plain_text_and_no_invented_deadline_or_internal_fields(self):
        self.primary.pop("deadline")
        self.primary["scraped_text"] = "private scraper state"
        issue = reporter._org_public_feed("sunn", [[self.primary]], datetime(2026, 10, 16), {}, {})["issues"][0]
        self.assertEqual(issue["summary"], "Viðkvæmt svæði og á.")
        self.assertIsNone(issue["deadline"])
        self.assertNotIn("scraped_text", issue)
        self.assertEqual(issue["vaktin_url"], "https://vaktin.logverndarsjodur.org/sunn/#primary")

    def test_generate_org_view_writes_static_json_alongside_page(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(reporter, "__file__", str(Path(temp) / "src/reporter.py")), \
                 patch.object(reporter, "_load_region_map", return_value={}), \
                 patch.object(reporter, "_load_source_urls", return_value={}):
                reporter.generate_org_view("sunn", {"name": "SUNN", "short_name": "SUNN"},
                                           [self.primary, self.related])
            feed_path = Path(temp) / "sunn/mal.json"
            feed = json.loads(feed_path.read_text(encoding="utf-8"))
            page = (Path(temp) / "sunn/index.md").read_text(encoding="utf-8")
            self.assertEqual(feed["schema_version"], 1)
            self.assertEqual((feed["case_count"], feed["document_count"]), (1, 2))
            self.assertIn(feed["updated_text"], page)
            self.assertFalse(feed_path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
