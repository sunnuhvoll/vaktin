"""Content analysis using Claude CLI (claude -p).

Sends scraped items to Claude for nature conservation relevance analysis.
"""

import json
import logging
import subprocess
from pathlib import Path

from scrapers.base import ScrapedItem

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Þú ert sérfræðingur í íslenskum náttúruverndarmálum. Þú vinnur fyrir íslensk náttúruverndarsamtök (eins og Landvernd og SUNN).

Greindu eftirfarandi mál og svaraðu á JSON sniði.

## Viðfangsefni sem þarf að flokka:
- **Skipulagsmál** — breytingar á aðalskipulagi, deiliskipulag, landnotkun
- **Umhverfismat** — mat á umhverfisáhrifum framkvæmda eða áætlana
- **Orkuframkvæmdir** — virkjanir, vindmyllur, jarðvarmi, raflínur
- **Náttúruvernd** — friðlýsingar, vernd tegunda, verndarsvæði
- **Vatnsvernd** — vatnsból, ár, vötn, grunnvatn, votlendi
- **Mengun** — losun, úrgangur, hættuleg efni
- **Fiskeldi og sjávarútvegur** — sjókvíaeldi, veiðar, hafsvæði
- **Ferðaþjónusta** — mannvirkjagerð í ósnortinni náttúru, gönguleiðir
- **Vegagerð** — vegir, brýr, jarðgöng í/við náttúrusvæði
- **Loftslagsmál** — losun gróðurhúsalofttegunda, kolefnisjöfnun

## Mikilvægt:
- Ef málið snertir EKKI náttúruvernd á nokkurn hátt, merktu það "irrelevant"
- Ef þú ert í vafa, merktu það "review" frekar en að sleppa því
- Mettu alvarleika: "critical" (þarf strax athygli), "important" (þarf athygli), "monitor" (fylgjast með)

Svaraðu EINGÖNGU með JSON:
{
  "relevant": true/false,
  "severity": "critical" | "important" | "monitor" | "irrelevant",
  "category": "einn af flokkunum að ofan eða 'annað'",
  "summary_is": "Stutt samantekt á íslensku (2-3 setningar) um hvað málið snýst og hvers vegna það skiptir máli fyrir náttúruvernd",
  "action_needed": "Hvað þurfa náttúruverndarsamtök að gera? (t.d. senda umsögn, mæta á fund, fylgjast með)",
  "deadline": "Ef frestur er til umsagnar eða athugasemda, hvaða dagsetning? Annars null",
  "location": "Staðsetning ef hægt er að greina, annars null"
}

## Málið:
Titill: {title}
Heimild: {source}
Dagsetning: {date}
Slóð: {url}

Efni:
{content}
"""


def analyze_item(item: ScrapedItem) -> dict | None:
    """Analyze a single scraped item using claude -p.

    Returns parsed JSON analysis or None on failure.
    """
    prompt = ANALYSIS_PROMPT.format(
        title=item.title,
        source=item.metadata.get("municipality", item.source_id),
        date=item.date,
        url=item.url,
        content=item.content[:10000],  # Limit content size
    )

    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            logger.error(f"claude -p failed for {item.item_id}: {result.stderr}")
            return None

        # Parse the response - claude -p with --output-format json wraps in a JSON object
        response = json.loads(result.stdout)
        # The actual text is in the "result" field
        response_text = response.get("result", result.stdout)

        # Extract JSON from the response text
        analysis = _extract_json(response_text)
        if analysis:
            analysis["item_id"] = item.item_id
            analysis["source_id"] = item.source_id
            analysis["title"] = item.title
            analysis["url"] = item.url
            analysis["date"] = item.date
        return analysis

    except subprocess.TimeoutExpired:
        logger.error(f"claude -p timed out for {item.item_id}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse claude response for {item.item_id}: {e}")
        return None


def analyze_batch(items: list[ScrapedItem]) -> list[dict]:
    """Analyze a batch of items, returning only relevant results."""
    results = []

    for item in items:
        if not item.content:
            logger.warning(f"Skipping {item.item_id} — no content")
            continue

        logger.info(f"Analyzing: {item.title[:80]}")
        analysis = analyze_item(item)

        if analysis and analysis.get("relevant", False):
            results.append(analysis)
            logger.info(f"  → RELEVANT ({analysis.get('severity', '?')}): {analysis.get('category', '?')}")
        elif analysis:
            logger.info(f"  → Not relevant")
        else:
            logger.warning(f"  → Analysis failed")

    return results


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from text that might contain other content."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block in text
    import re
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return None
