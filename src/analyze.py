"""Content analysis using Claude CLI (claude -p).

Sends scraped items to Claude for nature conservation relevance analysis.
Returns structured results with statistics on successes/failures.
"""

import json
import logging
import re
import subprocess
from pathlib import Path

from scrapers.base import ScrapedItem

logger = logging.getLogger(__name__)

PRIORITIES_PATH = Path(__file__).parent.parent / "config" / "forgangur.md"


def _load_priorities() -> str:
    """Load priority guidelines from config/priorities.md."""
    try:
        return PRIORITIES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

ANALYSIS_PROMPT = """Þú ert sérfræðingur í íslenskum náttúruverndarmálum. Þú vinnur fyrir íslensk náttúruverndarsamtök (eins og Landvernd og SUNN).

Greindu eftirfarandi mál og svaraðu á JSON sniði.

## Viðfangsefni sem þarf að flokka (má velja fleiri en eitt):
- **Skipulagsmál** — breytingar á aðalskipulagi, deiliskipulag, landnotkun
- **Umhverfismat** — mat á umhverfisáhrifum framkvæmda eða áætlana
- **Orkuframkvæmdir** — virkjanir, jarðvarmi, raflínur
- **Vindorka** — vindmyllur, vindorkuver, vindorkugarðar, vindmylluflutningar
- **Náttúruvernd** — friðlýsingar, vernd tegunda, verndarsvæði
- **Vatnsvernd** — vatnsból, ár, vötn, grunnvatn
- **Votlendi** — mýrar, flóar, votlendisvernd, framræsla
- **Jökulár** — jökulár, vötn, vatnafar, árásar á vatnasvæði
- **Víðerni** — óbyggðir, hálendi, ósnortin svæði, miðhálendið
- **Líffræðilegur fjölbreytileiki** — vistkerfi, tegundir, búsvæði, invasive tegundir
- **Fuglalíf** — fuglavernd, varpsvæði, fuglaathuganir, lundi
- **Skógrækt** — skógur, landgræðsla, endurheimt vistkerfa, birkiskógar
- **Mengun** — losun, úrgangur, hættuleg efni
- **Fiskeldi og sjávarútvegur** — sjókvíaeldi, veiðar, hafsvæði
- **Ferðaþjónusta** — mannvirkjagerð í ósnortinni náttúru, gönguleiðir
- **Vegagerð** — vegir, brýr, jarðgöng í/við náttúrusvæði
- **Loftslagsmál** — losun gróðurhúsalofttegunda, kolefnisjöfnun

{priorities}

## Mikilvægt:
- Ef málið snertir EKKI náttúruvernd á nokkurn hátt, merktu það "irrelevant"
- Ef þú ert í vafa, merktu það "review" frekar en að sleppa því
- Mettu alvarleika: "critical" (þarf strax athygli), "important" (þarf athygli), "monitor" (fylgjast með)
- Fylgdu forgangsröðuninni hér að ofan vandlega — ef mál fellur undir "Alltaf aðkallandi" þá VERÐUR severity að vera "critical"
- Í samantekt og aðgerðum: ef vísað er í skýrslur, reglugerðir, matsáætlanir, umsagnir eða önnur mikilvæg skjöl, settu beina HTML tengla (<a href="...">nafn skjals</a>) ef slóðin kemur fram í efninu. Þetta auðveldar notendum að nálgast gögnin beint.
- Hafðu uppsetningu samræmda. Notaðu aðeins þessi HTML merki inni í textasviðum: <a href="...">...</a>, <strong>...</strong> og <em>...</em>. Ekki nota önnur HTML merki.
- Búðu til mjög stutta millifyrirsögn (`dek_is`) í 1-2 stuttum línum sem segir lesanda strax hvað skiptir mestu máli. Hún á að vera hnitmiðuð, skýr og ekki endurtaka titilinn.
- `summary_is` á að vera meginmál samantektar, venjulega 2 stuttar málsgreinar eða 2-3 setningar. Fyrsta setningin má ekki bara endurtaka `dek_is`.

Svaraðu EINGÖNGU með gilt JSON (engin önnur texti):
{{
  "relevant": true/false,
  "severity": "critical" | "important" | "monitor" | "irrelevant",
  "categories": ["einn eða fleiri af flokkunum að ofan"],
  "dek_is": "Mjög stutt millifyrirsögn/abstract á íslensku, 1-2 stuttar línur.",
  "summary_is": "Stutt samantekt á íslensku (2-3 setningar). Settu HTML tengla á mikilvæg skjöl sem nefnd eru í efninu.",
  "action_needed": "Hvað þurfa náttúruverndarsamtök að gera? Settu HTML tengla á viðeigandi síður (umsagnargátt, skýrslur o.fl.) ef slóðirnar eru í efninu.",
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


_priorities_cache = None


def analyze_item(item: ScrapedItem) -> dict | None:
    """Analyze a single scraped item using claude -p.

    Returns parsed JSON analysis or None on failure.
    """
    global _priorities_cache
    if _priorities_cache is None:
        _priorities_cache = _load_priorities()

    prompt = ANALYSIS_PROMPT.format(
        title=item.title,
        source=item.metadata.get("municipality", item.source_id),
        date=item.date,
        url=item.url,
        content=item.content[:10000],
        priorities=_priorities_cache,
    )

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "claude-opus-4-6", "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()[:500]
            stdout = result.stdout.strip()[:300]
            logger.error(
                f"claude -p failed for {item.item_id} "
                f"(exit={result.returncode}): {stderr or stdout or '(no output)'}"
            )
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
            resp_len = len(response_text) if response_text else 0
            last_chars = response_text[-100:] if resp_len > 100 else response_text
            stderr_hint = result.stderr.strip()[:200] if result.stderr else ""
            first_chars = response_text[:200] if resp_len > 200 else response_text
            logger.error(
                f"Could not extract JSON from Claude response for {item.item_id}. "
                f"Response length: {resp_len}, "
                f"first 200 chars: {first_chars!r}, "
                f"last 100 chars: ...{last_chars!r}"
                f"{f', stderr: {stderr_hint}' if stderr_hint else ''}"
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


def analyze_batch(
    items: list[ScrapedItem],
    checkpoint_fn=None,
    checkpoint_interval: int = 100,
) -> tuple[list[dict], dict, list[ScrapedItem]]:
    """Analyze a batch of items.

    Returns (relevant_results, stats_dict, failed_items).
    failed_items contains items that could not be analyzed (for retry).
    If MAX_CONSECUTIVE_FAILURES consecutive failures occur, remaining
    items are returned as failed (likely a token/system issue).

    checkpoint_fn: called every checkpoint_interval items with
        (results_so_far, remaining_items, completed_count, total_count)
        to save intermediate progress.
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
    last_checkpoint = 0

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
            cats = analysis.get('categories') or [analysis.get('category', '?')]
            logger.info(f"  RELEVANT ({analysis.get('severity', '?')}): {', '.join(str(c) for c in cats)}")
        else:
            stats["not_relevant"] += 1
            consecutive_failures = 0
            logger.info(f"  Not relevant")

        # Periodic checkpoint
        completed = i + 1
        if checkpoint_fn and completed - last_checkpoint >= checkpoint_interval and completed < len(items):
            remaining = [it for it in items[i + 1:] if it.content]
            checkpoint_fn(results, remaining, completed, len(items))
            last_checkpoint = completed

    return results, stats, failed_items


def _extract_json(text: str) -> dict | None:
    """Extract JSON object from text that might contain other content."""
    if not text or not text.strip():
        return None

    # Strip code block markers if present (when response is ONLY a code block)
    stripped = re.sub(r'^```(?:json)?\s*', '', text.strip())
    stripped = re.sub(r'\s*```\s*$', '', stripped).strip()
    if stripped != text.strip():
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Try direct parse
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Extract from markdown code block (handles preamble text before ```json)
    code_block = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if code_block:
        try:
            obj = json.loads(code_block.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Try to find the outermost { ... } in the text
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Fix common Claude issue: literal newlines inside JSON string values
        # Collapsing all newlines to spaces still produces valid JSON
        fixed = re.sub(r'\s+', ' ', candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    return None
