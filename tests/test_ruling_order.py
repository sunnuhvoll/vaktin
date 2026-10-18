"""Rulings and judgments are analysed decision first, so a cut text never loses it."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import analyze
from scrapers.base import ScrapedItem
from scrapers.domstolar import DomstolarScraper, _rich_text
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

# Landsréttur publishes the appealed judgment below its own, with its own dómsorð
APPEAL = "\n".join([
    "Úrskurður Landsréttar",
    "Landsnet krafðist umráða yfir landi.",
    "Niðurstaða",
    "Umráð hafa þegar verið fengin.",
    "Úrskurðarorð:",
    "Máli þessu er vísað frá Landsrétti.",
    "",
    "Úrskurður Héraðsdómur Reykjaness 3. júlí 2026.",
    "Sóknaraðili krefst umráða.",
    "V. Niðurstaða",
    "Fallist er á kröfuna.",
    "Úrskurðarorð:",
    "Sóknaraðila er heimilt að fá landið afhent.",
])


def labels(text):
    return [line.split(" ")[1] for line in text.split("\n") if line.startswith("=== ")]


def section(text, name):
    """Body of the section whose label starts with `name`."""
    return text.split(f"=== {name}", 1)[1].split("\n=== ", 1)[0]


class DecisionFirstTests(unittest.TestCase):
    def setUp(self):
        self.scraper = RssScraper("urskurdarnefnd", {"ruling": True})

    def test_orders_reasoning_dissent_then_case(self):
        text = self.scraper._decision_first(RULING, "https://uua.is/x/")
        self.assertEqual(labels(text), ["ÚRSKURÐARORÐ", "FORSENDUR", "SÉRÁLIT", "MÁLIÐ"])
        decision = section(text, "ÚRSKURÐARORÐ")
        self.assertIn("Felld er úr gildi", decision)
        self.assertNotIn("vísa eigi", decision)  # the dissent is not the decision
        # "Niðurstaða HMS var …" in running text is not the heading
        self.assertIn("Niðurstaða HMS var", section(text, "MÁLIÐ"))

    def test_spaced_heading_with_colon(self):
        text = self.scraper._decision_first(
            "Málsatvik\nKæra.\nÚ r s k u r ð a r o r ð :\nKæru er vísað frá.", "u")
        self.assertTrue(text.startswith("=== ÚRSKURÐARORÐ"))
        self.assertIn("Kæru er vísað frá.", section(text, "ÚRSKURÐARORÐ"))

    def test_without_heading_text_is_unchanged(self):
        with self.assertLogs("scrapers.base", "WARNING"):
            self.assertEqual(self.scraper._decision_first("Frétt um fund.", "u"), "Frétt um fund.")

    def test_appeal_keeps_earlier_judgment_apart(self):
        text = DomstolarScraper("landsrettur", {})._decision_first(APPEAL, "u", appeal=True)
        self.assertEqual(labels(text), ["ÚRSKURÐARORÐ", "FORSENDUR", "NIÐURSTAÐA", "MÁLIÐ", "FYRRI"])
        self.assertIn("vísað frá Landsrétti", section(text, "ÚRSKURÐARORÐ"))
        self.assertNotIn("heimilt", section(text, "ÚRSKURÐARORÐ"))
        self.assertIn("heimilt að fá landið", section(text, "NIÐURSTAÐA FYRRA"))

    def test_numbered_reasons_heading(self):
        text = DomstolarScraper("heradsdomar", {})._decision_first(
            "Málsatvik\nStefnt.\nV. Niðurstaða\nRök dómsins.\nD ó m s o r ð :\nStefndi er sýkn.",
            "u", appeal=True)
        self.assertEqual(labels(text), ["DÓMSORÐ", "FORSENDUR", "MÁLIÐ"])
        self.assertIn("Rök dómsins.", section(text, "FORSENDUR"))


class CourtTests(unittest.TestCase):
    def test_rich_text_blocks_become_lines(self):
        doc = {"nodeType": "document", "content": [
            {"nodeType": "heading-2", "content": [{"nodeType": "text", "value": "Dómsorð:"}]},
            {"nodeType": "paragraph", "content": [
                {"nodeType": "text", "value": "Hinn áfrýjaði dómur skal "},
                {"nodeType": "hyperlink", "content": [{"nodeType": "text", "value": "óraskaður"}]},
                {"nodeType": "text", "value": "."},
            ]},
        ]}
        self.assertEqual(_rich_text(doc), "Dómsorð:\nHinn áfrýjaði dómur skal óraskaður.")

    def test_full_text_only_for_possible_nature_cases(self):
        scraper = DomstolarScraper("heradsdomar", {})
        self.assertTrue(scraper._needs_full_text(
            {"title": "Jón gegn Landsneti hf.", "keywords": ["Aðfarargerð"]}))
        self.assertTrue(scraper._needs_full_text({"title": "A gegn B", "keywords": ["Jörð", "Sameign"]}))
        # "jörð" in a name is not enough
        self.assertFalse(scraper._needs_full_text(
            {"title": "Ákæruvaldið gegn Sigurjörð", "keywords": ["Umferðarlagabrot"]}))


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

    def test_ruling_gets_notes(self):
        item = ScrapedItem("urskurdarnefnd", "u1", "UUA1", "https://uua.is/u1/", "2026-10-09",
                           "Úrskurður.", {"ruling": True})
        self.assertIn("lestu niðurstöðuna fyrst", self.analyse(item))

    def test_long_text_read_to_the_limit(self):
        # The end of 27k minutes was never read with the old 10k limit
        item = ScrapedItem("town", "t1", "Fundargerð", "https://x.is/", "2026-10-09",
                           "x" * 27000 + "LIÐUR30" + "y" * 10000 + "ENDIR")
        prompt = self.analyse(item)
        self.assertNotIn("lestu niðurstöðuna fyrst", prompt)
        self.assertIn("LIÐUR30", prompt)
        self.assertNotIn("ENDIR", prompt)


if __name__ == "__main__":
    unittest.main()
