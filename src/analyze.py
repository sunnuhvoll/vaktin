"""Content analysis using Claude CLI (claude -p).

Sends scraped items to Claude for nature conservation relevance analysis.
Returns structured results with statistics on successes/failures.
"""

import json
import logging
import re
import subprocess
from datetime import datetime
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

## Flokkunarreglur:
- Veldu ALLA flokka sem eiga við — ekki velja aðeins einn þegar málið snertir fleiri. T.d. ef mál fjallar um vindorku OG orkuframkvæmdir, veldu bæði "Vindorka" og "Orkuframkvæmdir". Ef orðið "vindorka" kemur fyrir í efninu á flokkurinn "Vindorka" alltaf að vera valinn.
- Greining á rammaáætlun, verndar- og orkunýtingaráætlun, eða flokkun orkukosta telst bæði "Vindorka" og "Orkuframkvæmdir" ef vindorka er nefnd.

## Mikilvægt:
- Ef málið snertir EKKI náttúruvernd á nokkurn hátt, merktu það "irrelevant"
- Ef þú ert í vafa, merktu það "review" frekar en að sleppa því
- Mettu alvarleika: "critical" (þarf strax athygli), "important" (þarf athygli), "monitor" (fylgjast með)
- Fylgdu forgangsröðuninni hér að ofan vandlega — ef mál fellur undir "Alltaf aðkallandi" þá VERÐUR severity að vera "critical"
- Í samantekt og aðgerðum: ef vísað er í skýrslur, reglugerðir, matsáætlanir, umsagnir eða önnur mikilvæg skjöl, settu beina HTML tengla (<a href='...'>nafn skjals</a>) ef slóðin kemur fram í efninu. Þetta auðveldar notendum að nálgast gögnin beint.
- Hafðu uppsetningu samræmda. Notaðu aðeins þessi HTML merki inni í textasviðum: <a href='...'>...</a>, <strong>...</strong> og <em>...</em>. Ekki nota önnur HTML merki. Notaðu einfaldar gæsalappir (') í HTML eigindum til að trufla ekki JSON sniðið.
- Búðu til mjög stutta millifyrirsögn (`dek_is`) í 1-2 stuttum línum sem segir lesanda strax hvað skiptir mestu máli. Hún á að vera hnitmiðuð, skýr og ekki endurtaka titilinn.
- `summary_is` á að vera meginmál samantektar, venjulega 2 stuttar málsgreinar eða 2-3 setningar. Fyrsta setningin má ekki bara endurtaka `dek_is`.

Svaraðu EINGÖNGU með gilt JSON (engin önnur texti):
{{
  "relevant": true/false,
  "severity": "critical" | "important" | "monitor" | "irrelevant",
  "categories": ["einn eða fleiri af flokkunum að ofan"],
  "dek_is": "Mjög stutt millifyrirsögn/abstract á íslensku, 1-2 stuttar línur.",
  "summary_is": "Stutt samantekt á íslensku (2-3 setningar). Settu HTML tengla á mikilvæg skjöl sem nefnd eru í efninu.",
  "action_needed": "Stutt og skýrt: hvað nákvæmlega þarf að gera? Ekki giska, ekki bulla — aðeins ef skýrt er af efninu. Tenglar ef slóð er í efni. Null ef ekkert þarf að gera.",
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

FAILED_RESPONSES_PATH = Path(__file__).parent.parent / "state" / "failed_responses.json"


def _save_failed_response(item_id: str, response_text: str, parse_err: str) -> None:
    """Save failed Claude response for self-heal diagnosis.

    Keeps last 20 failures so self-heal can see exactly what went wrong.
    """
    try:
        existing = json.loads(FAILED_RESPONSES_PATH.read_text()) if FAILED_RESPONSES_PATH.exists() else []
    except Exception:
        existing = []

    existing.append({
        "item_id": item_id,
        "timestamp": datetime.now().isoformat(),
        "parse_error": parse_err,
        "response_first_500": (response_text or "")[:500],
        "response_last_200": (response_text or "")[-200:],
    })
    # Keep only last 20
    existing = existing[-20:]

    FAILED_RESPONSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FAILED_RESPONSES_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


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
        analysis, parse_err = _extract_json(response_text)
        if not analysis:
            resp_len = len(response_text) if response_text else 0
            last_chars = response_text[-100:] if resp_len > 100 else response_text
            stderr_hint = result.stderr.strip()[:200] if result.stderr else ""
            first_chars = response_text[:200] if resp_len > 200 else response_text
            logger.error(
                f"Could not extract JSON from Claude response for {item.item_id}. "
                f"Response length: {resp_len}, "
                f"parse error: {parse_err}, "
                f"first 200 chars: {first_chars!r}, "
                f"last 100 chars: ...{last_chars!r}"
                f"{f', stderr: {stderr_hint}' if stderr_hint else ''}"
            )
            # Save failed response for self-heal diagnosis
            _save_failed_response(item.item_id, response_text, parse_err)
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
        "no_content_sources": {},
        "no_content_urls": {},
        "failed_sources": {},
        "failed_details": [],
    }
    consecutive_failures = 0
    last_checkpoint = 0

    for i, item in enumerate(items):
        if not item.content:
            logger.warning(f"Skipping {item.item_id} — no content extracted from page")
            stats["skipped_no_content"] += 1
            src = item.source_id
            stats["no_content_sources"][src] = stats["no_content_sources"].get(src, 0) + 1
            stats["no_content_urls"].setdefault(src, []).append(item.url)
            continue

        logger.info(f"[{i+1}/{len(items)}] Analyzing [{item.source_id}]: {item.title[:80]}")
        analysis = analyze_item(item)

        if analysis is None:
            stats["failed"] += 1
            src = item.source_id
            stats["failed_sources"][src] = stats["failed_sources"].get(src, 0) + 1
            stats["failed_details"].append({
                "item_id": item.item_id,
                "source_id": src,
                "url": item.url,
            })
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


def _fix_internal_quotes(json_str: str) -> str:
    """Fix unescaped double quotes inside JSON string values.

    Tracks object/array context so key-closing quotes (followed by :)
    and value-closing quotes (followed by , } ]) are distinguished
    correctly. This prevents false positives when string values contain
    text like  "word": "other"  which looks structural but isn't.
    """
    result = []
    i = 0
    n = len(json_str)
    in_string = False
    is_key = False
    context_stack = []  # True = object, False = array
    expecting_key = False

    while i < n:
        ch = json_str[i]

        if ch == '\\' and in_string and i + 1 < n:
            result.append(json_str[i:i + 2])
            i += 2
            continue

        if ch == '"':
            if not in_string:
                in_string = True
                is_key = bool(expecting_key and context_stack and context_stack[-1])
                result.append(ch)
            else:
                rest = json_str[i + 1:].lstrip()
                is_closing = False
                if not rest:
                    is_closing = True
                elif is_key and rest[0] == ':':
                    is_closing = True
                elif not is_key and rest[0] in ',}]':
                    is_closing = True

                if is_closing:
                    in_string = False
                    result.append(ch)
                else:
                    result.append('\\"')
            i += 1
            continue

        if not in_string:
            if ch == '{':
                context_stack.append(True)
                expecting_key = True
            elif ch == '[':
                context_stack.append(False)
                expecting_key = False
            elif ch in '}]':
                if context_stack:
                    context_stack.pop()
            elif ch == ':':
                expecting_key = False
            elif ch == ',':
                expecting_key = bool(context_stack and context_stack[-1])

        result.append(ch)
        i += 1

    return ''.join(result)


def _extract_json(text: str) -> tuple[dict | None, str]:
    """Extract JSON object from text that might contain other content.

    Returns (parsed_dict, error_msg). error_msg describes why parsing failed.
    """
    if not text or not text.strip():
        return None, "empty response"

    last_err = ""

    # Strip code block markers if present (when response is ONLY a code block)
    stripped = re.sub(r'^\s*```(?:json)?\s*', '', text.strip())
    stripped = re.sub(r'\s*```\s*$', '', stripped).strip()
    if stripped != text.strip():
        try:
            obj = json.loads(stripped, strict=False)
            if isinstance(obj, dict):
                return obj, ""
        except json.JSONDecodeError as e:
            last_err = f"code-block strip: {e}"
            fixed = _fix_internal_quotes(stripped)
            if fixed != stripped:
                try:
                    obj = json.loads(fixed, strict=False)
                    if isinstance(obj, dict):
                        return obj, ""
                except json.JSONDecodeError as e:
                    last_err = f"code-block strip + quote fix: {e}"

    # Try direct parse
    try:
        obj = json.loads(text.strip(), strict=False)
        if isinstance(obj, dict):
            return obj, ""
    except json.JSONDecodeError:
        pass

    # Extract from markdown code block (handles preamble text before ```json)
    code_block = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if code_block:
        block_content = code_block.group(1)
        try:
            obj = json.loads(block_content, strict=False)
            if isinstance(obj, dict):
                return obj, ""
        except json.JSONDecodeError as e:
            last_err = f"code-block regex: {e}"
            fixed = _fix_internal_quotes(block_content)
            if fixed != block_content:
                try:
                    obj = json.loads(fixed, strict=False)
                    if isinstance(obj, dict):
                        return obj, ""
                except json.JSONDecodeError as e:
                    last_err = f"code-block regex + quote fix: {e}"

    # Try to find the outermost { ... } in the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate, strict=False), ""
        except json.JSONDecodeError as e:
            last_err = f"braces extract: {e}"

        # Try fixing internal quotes on multiline content first
        fixed_ml = _fix_internal_quotes(candidate)
        if fixed_ml != candidate:
            try:
                return json.loads(fixed_ml, strict=False), ""
            except json.JSONDecodeError as e:
                last_err = f"braces + multiline quote fix: {e}"

        # Fix common Claude issue: literal newlines inside JSON string values
        # Collapsing all whitespace to spaces still produces valid JSON
        fixed = re.sub(r'\s+', ' ', candidate)
        try:
            return json.loads(fixed), ""
        except json.JSONDecodeError as e:
            last_err = f"whitespace collapse: {e}"

        # Fix unescaped quotes inside JSON string values
        # Replace „" (Icelandic) and "" (smart quotes) with escaped \"
        fixed2 = fixed.replace('\u201e', '\\"').replace('\u201c', '\\"')
        fixed2 = fixed2.replace('\u201d', '\\"').replace('\u2018', "'").replace('\u2019', "'")
        if fixed2 != fixed:
            try:
                return json.loads(fixed2), ""
            except json.JSONDecodeError as e:
                last_err = f"smart quotes: {e}"

        # Fix unescaped double quotes inside string values (e.g. HTML href="...")
        current = fixed2 if fixed2 != fixed else fixed
        fixed3 = _fix_internal_quotes(current)
        if fixed3 != current:
            try:
                return json.loads(fixed3), ""
            except json.JSONDecodeError as e:
                last_err = f"internal quotes fix: {e}"
                current = fixed3

        # Last resort: iteratively fix unescaped quotes at error positions
        for attempt_num in range(20):
            try:
                return json.loads(current), ""
            except json.JSONDecodeError as e:
                if e.pos is not None and e.pos < len(current):
                    current = current[:e.pos] + '\\"' + current[e.pos + 1:]
                else:
                    last_err = f"positional fix attempt {attempt_num + 1}: {e}"
                    break
        else:
            # Exhausted all attempts
            try:
                json.loads(current)
            except json.JSONDecodeError as e:
                last_err = f"positional fix exhausted (20 attempts): {e}"

    return None, last_err
