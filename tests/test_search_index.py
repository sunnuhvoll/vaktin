"""The search index (reports/leit.json) lists every visible document with its page."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import reporter


class SearchIndexTests(unittest.TestCase):
    def write(self, active, archived):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(reporter, "REPORTS_DIR", Path(temp)), \
             patch.object(reporter, "_source_name", side_effect=lambda source: source.upper()):
            reporter._write_search_index(active, archived)
            return json.loads((Path(temp) / "leit.json").read_text(encoding="utf-8"))["docs"]

    def test_documents_carry_page_text_and_filter_fields(self):
        active = [{
            "item_id": "uua_1", "source_id": "urskurdarnefnd", "severity": "critical",
            "date": "2026-10-09", "title": "UUA2607013", "headline_is": "ÚUA fellir úr gildi synjun HMS",
            "summary_is": "Landeigendur í <strong>Seyðisfirði</strong>.", "dek_is": "HMS metur aftur.",
            "location": "Seyðisfjörður", "case_refs": ["uua:2607013"], "region": "austurland",
            "categories": ["Fiskeldi og sjávarútvegur"],
        }]
        archived = {"2026-05": [{
            "item_id": "town_9", "source_id": "vogar", "severity": "irrelevant",
            "date": "2026-05-21", "title": "Bæjarráð - 452.&nbsp;fundur",
        }]}
        docs = self.write(active, archived)
        self.assertEqual([d["id"] for d in docs], ["uua_1", "town_9"])  # newest first
        current, old = docs
        self.assertEqual((current["p"], old["p"]), ("", "archive/2026-05/"))
        self.assertEqual(current["t"], "ÚUA fellir úr gildi synjun HMS")
        self.assertIn("UUA2607013", current["x"])  # the source title, under the headline
        self.assertIn("Landeigendur í Seyðisfirði.", current["x"])  # tags stripped
        self.assertIn("uua:2607013", current["x"])
        self.assertEqual((current["r"], current["so"], current["src"]),
                         ("austurland", "urskurdarnefnd", "URSKURDARNEFND"))
        self.assertEqual(current["c"], "fiskeldi og sjávarútvegur")
        self.assertEqual(old["t"], "Bæjarráð - 452. fundur")  # entity decoded
        self.assertEqual((old["s"], old["d"], old["r"]), ("monitor", "2026-05-21", "landsvitt"))


if __name__ == "__main__":
    unittest.main()
