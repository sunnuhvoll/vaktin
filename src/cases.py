"""Group documents that concern the same case.

One project reaches Vaktin through many sources — Skipulagsgátt, municipal
minutes, Lögbirtingablaðið, appeals — and each document used to become its own
card (Hvammsvirkjun had 27). Analysis v2 gives every item `project_is` (a
canonical project name) and `case_refs` (official case numbers); items that
share either belong to one case. The site shows one card per case, and e-mails
go out for new cases rather than for every document.

Pure functions only: no I/O and no Claude calls.
"""

import re
from collections import Counter

SEVERITY_RANK = {"critical": 0, "important": 1, "monitor": 2}

# Case-number systems accepted in `case_refs` ("system:number"). Numbers that
# name a whole publication issue (e.g. a Lögbirtingablað issue) are left out
# on purpose: unrelated notices in the same issue would merge.
REF_SYSTEMS = {"skipulagsgatt", "samradsgatt", "uua", "althingi", "stjornartidindi"}
_REF_ALIASES = {
    "skipulagsgátt": "skipulagsgatt",
    "samráðsgátt": "samradsgatt",
    "úua": "uua",
    "úrskurðarnefnd": "uua",
    "alþingi": "althingi",
    "stjórnartíðindi": "stjornartidindi",
}

# Names that describe a kind of matter rather than one project; grouping on
# them would merge unrelated cases.
GENERIC_PROJECTS = {
    "aðalskipulag", "deiliskipulag", "skipulag", "skipulagsmál", "svæðisskipulag",
    "breyting á aðalskipulagi", "breyting á deiliskipulagi", "umhverfismat",
    "framkvæmdaleyfi", "byggingarleyfi", "starfsleyfi", "fiskeldi", "sjókvíaeldi",
    "vindorka", "vindorkuver", "vegagerð", "náttúruvernd", "friðlýsing", "skógrækt",
    "efnistaka", "fundargerð", "ýmis mál",
}


def normalize_project(name) -> str | None:
    """Matching form of a project name, or None if it can't identify a case."""
    if not isinstance(name, str):
        return None
    norm = name.casefold()
    norm = re.sub(r"[–—]", "-", norm)
    norm = re.sub(r"[\"'„“”«»().,;:!?]", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip(" -")
    if len(norm) < 4 or norm in GENERIC_PROJECTS:
        return None
    return norm


def normalize_ref(ref) -> str | None:
    """Canonical "system:NUMBER" form of a case reference, or None."""
    if not isinstance(ref, str) or ":" not in ref:
        return None
    system, number = ref.split(":", 1)
    system = system.strip().casefold()
    system = _REF_ALIASES.get(system, system)
    number = re.sub(r"\s+", "", number).upper()
    if system == "uua":
        number = number.removeprefix("UUA")
    if system not in REF_SYSTEMS or not re.search(r"\d", number):
        return None
    return f"{system}:{number}"


def case_keys(item: dict) -> set[str]:
    """Keys under which an item joins a case: its project and case numbers."""
    keys = set()
    project = normalize_project(item.get("project_is"))
    if project:
        keys.add("p:" + project)
    for ref in item.get("case_refs") or []:
        norm = normalize_ref(ref)
        if norm:
            keys.add("r:" + norm)
    return keys


def group_cases(items: list[dict], timestamp) -> list[list[dict]]:
    """Group items that share a case key (transitively).

    `timestamp(item)` returns a sortable number (0 when the date is unknown).
    Each group lists its primary item first — the most severe, newest among
    equals — then the rest newest first. Items without keys stay on their own.
    """
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, item in enumerate(items):
        for key in case_keys(item):
            if key in owner:
                parent[find(i)] = find(owner[key])
            else:
                owner[key] = i

    members: dict[int, list[dict]] = {}
    for i, item in enumerate(items):
        members.setdefault(find(i), []).append(item)

    groups = []
    for group in members.values():
        group.sort(key=lambda it: -timestamp(it))
        primary = min(group, key=lambda it: SEVERITY_RANK.get(it.get("severity"), 3))
        groups.append([primary] + [it for it in group if it is not primary])
    return groups


def known_projects(items: list[dict], limit: int = 300) -> list[str]:
    """Project names already in use, most common first.

    Given to Claude with each new document so related documents reuse the
    exact same name instead of a variant.
    """
    counts: Counter = Counter()
    names: dict[str, str] = {}
    for item in items:
        norm = normalize_project(item.get("project_is"))
        if norm:
            counts[norm] += 1
            names.setdefault(norm, item["project_is"].strip())
    return [names[norm] for norm, _ in counts.most_common(limit)]


def split_new_cases(results: list[dict], prior_items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split this run's results into new cases or events, and follow-ups.

    A result is a follow-up when an item stored before this run shares one of
    its case keys at the same or a higher severity, and it brings no new
    deadline. Of several results for the same new case in one run, the most
    severe counts as new and the rest as follow-ups.
    """
    best_rank: dict[str, int] = {}
    deadlines: dict[str, set[str]] = {}

    def remember(item: dict, keys: set[str]) -> None:
        rank = SEVERITY_RANK.get(item.get("severity"), 3)
        for key in keys:
            best_rank[key] = min(best_rank.get(key, 9), rank)
            if item.get("deadline"):
                deadlines.setdefault(key, set()).add(str(item["deadline"]))

    for item in prior_items:
        if item.get("relevant", True) is not False:
            remember(item, case_keys(item))

    new, followups = [], []
    for result in sorted(results, key=lambda r: SEVERITY_RANK.get(r.get("severity"), 3)):
        keys = case_keys(result)
        rank = SEVERITY_RANK.get(result.get("severity"), 3)
        known = [key for key in keys if key in best_rank]
        deadline = str(result.get("deadline") or "")
        new_deadline = bool(deadline) and not any(deadline in deadlines.get(key, ()) for key in keys)
        if known and min(best_rank[key] for key in known) <= rank and not new_deadline:
            followups.append(result)
        else:
            new.append(result)
        remember(result, keys)
    return new, followups
