"""
Rule-based markup of the candidate's resume for one job posting (no LLM).

The resume is rebuilt from the profile snapshot an analysis used, then marked
up like a reviewer would with a highlighter and a red pen:

  - yellow ("good"): phrases that already work for this role. These come from
    the job insights' skills match (each matched skill carries a quote from
    the profile, already verified) plus bullets that are quantified.
  - red ("fix"): weak openers ("Contributed to…"), first person, very long
    bullets, roles with no numbers, projects with no link, an unfinished
    degree not marked "expected", and the role's key skills the resume
    doesn't show.

The output is one presentation-ready structure that both the web page and the
PDF render, so they always agree:

  {"header": {...}, "counts": {"good": n, "fix": n}, "sections": [
     {"id", "title", "notes": [note], "entries": [
        {"heading", "sub", "dates", "style", "lines": [[segment]], "notes": [note]}]}]}

  segment = {"text": str, "mark": None | {"kind": "good"|"fix", "n": int}}
  note    = {"n": int | None, "kind": "good"|"fix", "text": str}
"""

import re
from datetime import date

GOOD, FIX = "good", "fix"

# Per-rule caps keep the page readable: a few strong marks beat a red mess.
CAPS = {"skill": 5, "opener": 3, "first_person": 2, "long": 2, "number": 2}
MAX_INLINE_MARKS = 12
MAX_ENTRY_NOTES = 4

WEAK_OPENER = re.compile(
    r"^\s*(?:contributed to|participated in|responsible for|helped(?: (?:with|to|in))?|worked on"
    r"|assisted(?: (?:with|in))?|involved in|tasked with|duties included)\b",
    re.IGNORECASE,
)
FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me)\b")
# A metric plus up to two words after it ("150+ test queries", "0.92 MRR", "40%").
# Years and numbers inside names ("GPT-5.4-mini", "3D") don't count.
NUMBER = re.compile(
    r"(?<![\w.\-/])(?:[$€£₹]\s?)?(?!(?:19|20)\d\d\b)\d[\d,]*(?:\.\d+)?(?:%|x\b|\+|k\b|K\b)?(?![\w\-.]*[A-Za-z])"
    r"(?:\s+[A-Za-z][\w-]*){0,2}"
)
TRAILING_FILLER = re.compile(r"(?:\s+(?:and|or|in|of|for|to|the|a|an|on|with|by|at|from|per|across|over))+$", re.IGNORECASE)
MAX_MISSING_SHOWN = 4
YEAR = re.compile(r"\b((?:19|20)\d\d)\b")
LONG_BULLET_WORDS = 32


def _join(*parts, sep=" · "):
    return sep.join(part for part in parts if part)


def _dates(item):
    end = "Present" if item.get("is_current") else item.get("end_date", "")
    return _join(item.get("start_date", ""), end, sep=" – ")


def _entry(lines, style="bullets", heading=None, sub=None, dates=None, kind=None, ref=None):
    return {"heading": heading, "sub": sub, "dates": dates, "style": style,
            "lines": [line for line in lines if line], "notes": [], "_kind": kind, "_ref": ref}


def _build_sections(profile):
    """Lay the profile out as resume sections with plain-text lines."""
    b = profile["basics"]
    sections = []

    def add(section_id, title, entries):
        if entries:
            sections.append({"id": section_id, "title": title, "notes": [], "entries": entries})

    add("summary", "Summary", [_entry([b["summary"]], style="text")] if b["summary"] else [])
    add("experience", "Experience", [
        _entry(item["highlights"], heading=item["title"] or item["company"],
               sub=_join(item["company"] if item["title"] else "", item["location"]),
               dates=_dates(item), kind="experience", ref=item)
        for item in profile["experience"]
    ])
    add("projects", "Projects", [
        _entry(item["highlights"], heading=item["name"], sub=", ".join(item["tech_stack"]),
               dates=item["url"] or None, kind="project", ref=item)
        for item in profile["projects"]
    ])
    add("skills", "Skills", [
        _entry([", ".join(group["skills"])], style="skills", heading=group["category"] or "Skills")
        for group in profile["skills"] if group["skills"]
    ])
    add("education", "Education", [
        _entry(item["highlights"], heading=_join(item["degree"], item["field_of_study"], sep=", "),
               sub=_join(item["institution"], item["grade"]), dates=_dates(item), kind="education", ref=item)
        for item in profile["education"]
    ])
    add("certifications", "Certifications", [
        _entry([_join(c["name"], _join(c["issuer"], c["date"], sep=", "), sep=" — ") for c in profile["certifications"]])
    ] if profile["certifications"] else [])
    add("achievements", "Achievements", [_entry(profile["achievements"])] if profile["achievements"] else [])
    return sections


class _Marker:
    """Collects non-overlapping inline marks and entry/section notes."""

    def __init__(self, sections):
        self.sections = sections
        self.marks = {}            # (si, ei, li) -> [(start, end, kind, note)]
        self.used = {key: 0 for key in CAPS}
        self.total = 0

    def lines(self, styles=None):
        for si, section in enumerate(self.sections):
            for ei, entry in enumerate(section["entries"]):
                if styles and entry["style"] not in styles:
                    continue
                for li, text in enumerate(entry["lines"]):
                    yield si, ei, li, text, entry

    def add(self, rule, key, start, end, kind, note):
        if self.used[rule] >= CAPS[rule] or self.total >= MAX_INLINE_MARKS or end <= start:
            return False
        spans = self.marks.setdefault(key, [])
        if any(start < e and s < end for s, e, _, _ in spans):
            return False
        spans.append((start, end, kind, note))
        self.used[rule] += 1
        self.total += 1
        return True


def _phrase_pattern(text):
    words = re.findall(r"[\w+#.]+", text)
    return re.compile(r"\W+".join(map(re.escape, words)), re.IGNORECASE) if words else None


def _mark_skill_evidence(marker, insights):
    """Yellow: where the resume already shows the role's key skills."""
    for item in insights.get("skills_matched", []):
        skill, note = item["skill"], f"{item['skill']}: the role asks for this."
        placed = False
        for pattern in (_phrase_pattern(item.get("evidence", "")),
                        re.compile(rf"(?<!\w){re.escape(skill)}(?!\w)", re.IGNORECASE)):
            if not pattern:
                continue
            for si, ei, li, text, _ in marker.lines():
                match = pattern.search(text)
                if match and marker.add("skill", (si, ei, li), match.start(), match.end(), GOOD, note):
                    placed = True
                    break
            if placed or marker.used["skill"] >= CAPS["skill"]:
                break


def _mark_writing(marker):
    """Rules about how bullets are written."""
    bullets = list(marker.lines(styles={"bullets"}))
    for si, ei, li, text, _ in bullets:
        match = WEAK_OPENER.match(text)
        if match:
            start = len(text) - len(text.lstrip())  # skip leading spaces
            marker.add("opener", (si, ei, li), start, match.end(), FIX,
                       "What did you do here? Lead with the action.")
    for si, ei, li, text, _ in bullets:
        match = FIRST_PERSON.search(text)
        if match:
            marker.add("first_person", (si, ei, li), match.start(), match.end(), FIX,
                       f"Drop the “{match.group()}”; resumes skip first person.")
    for si, ei, li, text, _ in bullets:
        words = text.split()
        if len(words) > LONG_BULLET_WORDS:
            # Underline only the opening words; a fully red paragraph is unreadable.
            opening = re.match(r"\s*(?:\S+\s+){5}\S+", text)
            marker.add("long", (si, ei, li), 0, opening.end() if opening else len(text), FIX,
                       f"Long ({len(words)} words). Split it or trim it.")
    for si, ei, li, text, _ in bullets:
        match = NUMBER.search(text)
        if match:
            phrase = TRAILING_FILLER.sub("", match.group())  # "0.92 MRR and" -> "0.92 MRR"
            marker.add("number", (si, ei, li), match.start(), match.start() + len(phrase), GOOD, "Good: quantified.")


def _entry_notes(sections):
    """Red notes about a whole entry rather than one phrase."""
    added = 0
    this_year = date.today().year
    for section in sections:
        for entry in section["entries"]:
            if added >= MAX_ENTRY_NOTES:
                return
            item, kind = entry["_ref"], entry["_kind"]
            if kind == "experience" and entry["lines"] and not any(NUMBER.search(line) for line in entry["lines"]):
                entry["notes"].append({"n": None, "kind": FIX, "text": "No numbers here. Add one if you have it: time saved, users, accuracy."})
                added += 1
            elif kind == "project" and not item["url"]:
                entry["notes"].append({"n": None, "kind": FIX, "text": "No link. Add GitHub or a live demo if you can."})
                added += 1
            elif kind == "education":
                years = [int(y) for y in YEAR.findall(item["end_date"])]
                text = f"{item['end_date']} {item['degree']}".lower()
                if years and max(years) > this_year and "expect" not in text:
                    entry["notes"].append({"n": None, "kind": FIX, "text": "Mark this as “expected” so it doesn't read as finished."})
                    added += 1


def _missing_skills_note(sections, insights):
    missing = insights.get("skills_missing", [])
    if not missing:
        return
    shown = ", ".join(missing[:MAX_MISSING_SHOWN])
    more = f" and {len(missing) - MAX_MISSING_SHOWN} more" if len(missing) > MAX_MISSING_SHOWN else ""
    note = {"n": None, "kind": FIX,
            "text": f"Not shown: {shown}{more}. Add them only if you've really used them."}
    for section in sections:
        if section["id"] == "skills":
            section["notes"].append(note)
            return
    sections.append({"id": "skills", "title": "Skills", "notes": [note], "entries": []})


def build_markup(profile, insights):
    """Mark up a profile snapshot for one job posting. `insights` may be None."""
    insights = insights or {}
    sections = _build_sections(profile)
    marker = _Marker(sections)
    _mark_skill_evidence(marker, insights)
    _mark_writing(marker)
    _entry_notes(sections)
    _missing_skills_note(sections, insights)

    # Number the inline marks in reading order and split lines into segments.
    n = 0
    for si, section in enumerate(sections):
        for ei, entry in enumerate(section["entries"]):
            inline_notes, segmented = [], []
            for li, text in enumerate(entry["lines"]):
                segments, cursor = [], 0
                for start, end, kind, note in sorted(marker.marks.get((si, ei, li), [])):
                    n += 1
                    if start > cursor:
                        segments.append({"text": text[cursor:start], "mark": None})
                    segments.append({"text": text[start:end], "mark": {"kind": kind, "n": n}})
                    inline_notes.append({"n": n, "kind": kind, "text": note})
                    cursor = end
                if cursor < len(text):
                    segments.append({"text": text[cursor:], "mark": None})
                segmented.append(segments)
            entry["lines"] = segmented
            entry["notes"] = inline_notes + entry["notes"]
            entry.pop("_kind")
            entry.pop("_ref")

    # A section-wide note (e.g. missing skills) sits beside the section's first
    # entry rather than in its own row, so it doesn't leave an empty gap.
    for section in sections:
        if section["entries"] and section["notes"]:
            section["entries"][0]["notes"].extend(section["notes"])
            section["notes"] = []

    notes = [note for s in sections for note in s["notes"] + [x for e in s["entries"] for x in e["notes"]]]
    b = profile["basics"]
    return {
        "header": {
            "name": b["full_name"], "headline": b["headline"],
            "contact": [part for part in (b["location"], b["email"], b["phone"]) if part],
            "links": [link["label"] or link["url"] for link in b["links"]],
        },
        "counts": {"good": sum(note["kind"] == GOOD for note in notes),
                   "fix": sum(note["kind"] == FIX for note in notes)},
        "sections": sections,
    }
