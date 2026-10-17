"""ÚUA rulings are analysed decision first, so a cut text never loses it."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import analyze
from scrapers.base import ScrapedItem
from scrapers.rss import RssScraper

RULING = "\n".join([
    "Fyrir var tekið mál nr. UUA2607013, kæra á synjun HMS um aðild.",
    "Málsatvik",
    "HMS synjaði kærendum um aðild. Niðurstaða HMS var að mannvirkin væru utan netlaga.",
    "Málsrök kæranda",
    "Kærendur krefjast þess að ákvörðunin verði felld úr gildi.",
    "Niðurstaða",
    "Óvarlegt var af HMS að neita kærendum um aðild.",
    "Úrskurðarorð",
    "Felld er úr gildi ákvörðun HMS frá 9. júlí 2026.",
    "Sérálit Jóns Jónssonar:",
    "Ég tel að vísa eigi málinu frá.",
])


class DecisionFirstTests(unittest.TestCase):
    def setUp(self):
        self.scraper = RssScraper("urskurdarnefnd", {"ruling": True})

    def test_orders_reasoning_dissent_then_case(self):
        text = self.scraper._decision_first(RULING, "https://uua.is/x/")
        labels = [line for line in text.split("\n") if line.startswith("===")]
        self.assertEqual([label.split(" ")[1] for label in labels],
                         ["ÚRSKURÐARORÐ", "NIÐURSTAÐA", "SÉRÁLIT", "MÁLIÐ"])
        decision = text.split("===")[2]
        self.assertIn("Felld er úr gildi", decision)
        self.assertNotIn("vísa eigi", decision)  # the dissent is not the decision
        # "Niðurstaða HMS var …" in running text is not the heading
        self.assertIn("Niðurstaða HMS var", text.split("=== MÁLIÐ")[1])

    def test_spaced_heading_with_colon(self):
        text = self.scraper._decision_first(
            "Málsatvik\nKæra.\nÚ r s k u r ð a r o r ð :\nKæru er vísað frá.", "u")
        self.assertTrue(text.startswith("=== ÚRSKURÐARORÐ"))
        self.assertIn("Kæru er vísað frá.", text.split("=== MÁLIÐ")[0])

    def test_without_heading_text_is_unchanged(self):
        with self.assertLogs("scrapers.rss", "WARNING"):
            self.assertEqual(self.scraper._decision_first("Frétt um fund.", "u"), "Frétt um fund.")


class RulingPromptTests(unittest.TestCase):
    def analyse(self, item):
        captured = {}

        def fake_run(prompt, schema, timeout, label):
            captured["prompt"] = prompt
            return None, ""

        with patch.object(analyze, "_run_claude", fake_run), \
             patch.object(analyze, "_priorities_cache", ""):
            analyze.analyze_item(item)
        return captured["prompt"]

    def test_ruling_gets_notes_and_longer_text(self):
        item = ScrapedItem("urskurdarnefnd", "u1", "UUA1", "https://uua.is/u1/", "2026-10-09",
                           "x" * 20000 + "ENDIR", {"ruling": True, "max_chars": 30000})
        prompt = self.analyse(item)
        self.assertIn("lestu niðurstöðuna fyrst", prompt)
        self.assertIn("ENDIR", prompt)

    def test_other_items_unchanged(self):
        item = ScrapedItem("rss", "r1", "Frétt", "https://x.is/", "2026-10-09",
                           "x" * 20000 + "ENDIR")
        prompt = self.analyse(item)
        self.assertNotIn("lestu niðurstöðuna fyrst", prompt)
        self.assertNotIn("ENDIR", prompt)


if __name__ == "__main__":
    unittest.main()
