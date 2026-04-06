"""Content analysis using Claude CLI (claude -p).

Sends scraped items to Claude for nature conservation relevance analysis.
Returns structured results with statistics on successes/failures.
"""

import json
import logging
import re
import subprocess

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

Svaraðu EINGÖNGU með gilt JSON (engin önnur texti):
{{
  "relevant": true/false,
  "severity": "critical" | "important" | "monitor" | "irrelevant",
  "category": "einn af flokkunum að ofan eða 'annað'",
  "summary_is": "Stutt samantekt á íslensku (2-3 setningar) um hvað málið snýst og hvers vegna það skiptir máli fyrir náttúruvernd",
  "action_needed": "Hvað þurfa náttúruverndarsamtök að gera? (t.d. senda umsögn, mæta á fund, fylgjast með)",
  "deadline": "Ef frestur er til umsagnar eða athugasemda, hvaða dagsetning? Annars null",
  "location": "Staðsetning ef hægt er að greina, annars null"
}}

## Málið:
Titill: {title}
Heimild: {source}
Dagsetning: {date}
Slóð: {url}

Efni:
{content}
"""

# Required fields in Claude's JSON response
REQUIRED_FIELDS = {"relevant", "severity", "summary_is"}


def analyze_item(item: ScrapedItem) -> dict | None:
    """Analyze a single scraped item using claude -p.

    Returns parsed JSON analysis or None on failure.
    """
    prompt = ANALYSIS_PROMPT.format(
        title=item.title,
        source=item.metadata.get("municipality", item.source_id),
        date=item.date,
        url=item.url,
        content=item.content[:10000],
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
            logger.error(f"claude -p failed for {item.item_id}: {result.stderr[:500]}")
            return None

        # Parse the response — claude -p with --output-format json wraps in a JSON object
        try:
            response = json.loads(result.stdout)
            response_text = response.get("result", result.stdout)
        except json.JSONDecodeError:
            # If outer JSON fails, treat entire stdout as the response text
            response_text = result.stdout

        # Extract the analysis JSON from the response text
        analysis = _extract_json(response_text)
        if not analysis:
            logger.error(
                f"Could not extract JSON from Claude response for {item.item_id}. "
                f"Response (first 300 chars): {response_text[:300]}"
            )
            return None

        # Validate required fields
        missing = REQUIRED_FIELDS - set(analysis.keys())
        if missing:
            logger.error(
                f"Claude response for {item.item_id} missing fields: {missing}. "
                f"Got keys: {list(analysis.keys())}"
            )
            return None

        # Attach item metadata to analysis
        analysis["item_id"] = item.item_id
        analysis["source_id"] = item.source_id
        analysis["title"] = item.title
        analysis["url"] = item.url
        analysis["date"] = item.date
        return analysis

    except subprocess.TimeoutExpired:
        logger.error(f"claude -p timed out (120s) for {item.item_id}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error analyzing {item.item_id}: {e}")
        return None


MAX_CONSECUTIVE_FAILURES = 3


def analyze_batch(items: list[ScrapedItem]) -> tuple[list[dict], dict, list[ScrapedItem]]:
    """Analyze a batch of items.

    Returns (relevant_results, stats_dict, failed_items).
    failed_items contains items that could not be analyzed (for retry).
    If MAX_CONSECUTIVE_FAILURES consecutive failures occur, remaining
    items are returned as failed (likely a token/system issue).
    """
    results = []
    failed_items = []
    stats = {
        "total": len(items),
        "relevant": 0,
        "not_relevant": 0,
        "failed": 0,
        "skipped_no_content": 0,
    }
    consecutive_failures = 0

    for i, item in enumerate(items):
        if not item.content:
            logger.warning(f"Skipping {item.item_id} — no content extracted from page")
            stats["skipped_no_content"] += 1
            continue

        logger.info(f"[{i+1}/{len(items)}] Analyzing [{item.source_id}]: {item.title[:80]}")
        analysis = analyze_item(item)

        if analysis is None:
            stats["failed"] += 1
            failed_items.append(item)
            consecutive_failures += 1

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                # Likely a token/system issue — save remaining items for retry
                remaining = [
                    it for it in items[i + 1:]
                    if it.content  # only items with content
                ]
                failed_items.extend(remaining)
                stats["failed"] += len(remaining)
                logger.error(
                    f"Stopping analysis after {MAX_CONSECUTIVE_FAILURES} consecutive "
                    f"failures — likely token/system issue. "
                    f"{len(remaining)} remaining items saved for retry."
                )
                break
        elif analysis.get("relevant", False):
            results.append(analysis)
            stats["relevant"] += 1
            consecutive_failures = 0
            logger.info(f"  RELEVANT ({analysis.get('severity', '?')}): {analysis.get('category', '?')}")
        else:
            stats["not_relevant"] += 1
            consecutive_failures = 0
            logger.info(f"  Not relevant")

    return results, stats, failed_items


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from text that might contain other content."""
    if not text or not text.strip():
        return None

    # Try direct parse first
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Try to find a JSON block between ```json ... ``` markers
    code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the outermost { ... } in the text
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None
