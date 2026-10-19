"""Stored texts written as advice to the NGOs are rewritten, active items first."""
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import migrate
import reporter

LINK = "<a href='https://www.skipulagsgatt.is/issues/2026/1204/'>Skipulagsgátt</a>"


class RewrittenFieldsTests(unittest.TestCase):
    item = {
        "dek_is": "", "summary_is": f"Tillaga að deiliskipulagi við Sogið. Gögnin eru í {LINK}.",
        "action_needed": "Senda athugasemd fyrir 6. nóvember 2026.",
    }

    def test_rewrite_is_taken(self):
        row = {"dek_is": "Ný millifyrirsögn", "summary_is": self.item["summary_is"],
               "action_needed": "Athugasemdum má skila fyrir 6. nóvember 2026."}
        new = migrate._rewritten_fields(self.item, row)
        self.assertEqual(new["action_needed"], "Athugasemdum má skila fyrir 6. nóvember 2026.")
        self.assertEqual(new["dek_is"], "")  # an empty text stays empty

    def test_null_action_is_none(self):
        row = {"dek_is": "", "summary_is": self.item["summary_is"], "action_needed": "null"}
        self.assertIsNone(migrate._rewritten_fields(self.item, row)["action_needed"])

    def test_lost_link_or_summary_is_refused(self):
        no_link = {"dek_is": "", "summary_is": "Tillaga að deiliskipulagi við Sogið. Gögnin eru í Skipulagsgátt.",
                   "action_needed": None}
        self.assertIsNone(migrate._rewritten_fields(self.item, no_link))
        short = {"dek_is": "", "summary_is": "Tillaga.", "action_needed": LINK}
        self.assertIsNone(migrate._rewritten_fields(self.item, short))


class RewriteStoredTextsTests(unittest.TestCase):
    def test_active_first_and_marks(self):
        today = date.today().isoformat()
        data = {
            "old": {"item_id": "old", "date": "2020-01-15", "summary_is": "Eldra mál.",
                    "action_needed": "Fylgjast með málinu."},
            "new": {"item_id": "new", "date": today, "summary_is": "Nýtt mál.",
                    "action_needed": "Senda umsögn."},
            "plain": {"item_id": "plain", "date": today, "summary_is": "Framkvæmdaleyfi var veitt."},
            "hidden": {"item_id": "hidden", "date": today, "relevant": False, "action_needed": "Skoða."},
            "done": {"item_id": "done", "date": today, "text_v": migrate.TEXT_VERSION, "action_needed": "Frestur."},
        }
        sent = []

        def fake_rewrite(items):
            sent.extend(it["item_id"] for it in items)
            return {
                "new": {"item_id": "new", "dek_is": "", "summary_is": "Nýtt mál.",
                        "action_needed": "Umsagnarfrestur er til 1. nóvember."},
                "old": {"item_id": "old", "dek_is": "", "summary_is": "Eldra mál.", "action_needed": None},
            }

        with tempfile.TemporaryDirectory() as temp, \
             patch.object(reporter, "REPORTS_DIR", Path(temp)), \
             patch.object(migrate, "rewrite_texts", side_effect=fake_rewrite):
            (Path(temp) / ".index_data.json").write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(migrate.rewrite_stored_texts(), 2)
            stored = json.loads((Path(temp) / ".index_data.json").read_text(encoding="utf-8"))

        self.assertEqual(sent, ["new", "old"])  # active before archived; no call for "plain"
        self.assertEqual(stored["new"]["action_needed"], "Umsagnarfrestur er til 1. nóvember.")
        self.assertIsNone(stored["old"]["action_needed"])
        for item_id in ("new", "old", "plain"):
            self.assertEqual(stored[item_id]["text_v"], migrate.TEXT_VERSION)
        self.assertNotIn("text_v", stored["hidden"])
        self.assertEqual(stored["done"]["action_needed"], "Frestur.")


if __name__ == "__main__":
    unittest.main()
