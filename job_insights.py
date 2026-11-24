"""
Structured "job insights" returned alongside each analysis:
  - at_a_glance: company, role, location, pay and work type, copied from the
    job description in its own wording,
  - a skills match: every relevant skill the role asks for, split into the
    ones the profile shows and the ones it doesn't, most important first.

The LLM fills these Pydantic models via strict structured output (the
schema is generated from them, so the model can't return other fields).
clean_insights() then checks every "matched" skill against the profile so
the model can't credit the candidate with a skill they don't show.
"""

import re

from pydantic import BaseModel, ConfigDict, Field

NOT_SPECIFIED = "Not specified"
# Safety bounds only: the model is asked for every relevant skill, and the
# card shows the first few with a toggle for the rest.
MAX_MATCHED = 25
MAX_MISSING = 25

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AtAGlance(_Strict):
    company: str = Field(description='Hiring company, or "Undisclosed (via <recruiter>)".')
    role: str = Field(description="Job title.")
    location: str = Field(description="City / region / country as stated.")
    pay: str = Field(description='Pay as stated, with currency and period, e.g. "$4.28–$17.15 / hour".')
    work_mode: str = Field(description='Where the work happens, in the posting\'s exact words, e.g. "On-site", "Remote", "Hybrid".')


class MatchedSkill(_Strict):
    skill: str
    evidence: str = Field(description="Short exact quote from the profile that shows the skill.")


class JobInsights(_Strict):
    at_a_glance: AtAGlance
    skills_matched: list[MatchedSkill] = Field(description="Most important first.")
    skills_missing: list[str] = Field(description="Most important first.")


GLANCE_KEYS = ("company", "role", "location", "pay", "work_mode")


def normalize_glance(glance: dict) -> dict:
    """At-a-glance in the current five-field shape, also for analyses stored
    before it changed (they had employment_type/experience). Values are kept
    exactly as worded in the posting; blanks become "Not specified"."""
    return {key: (glance.get(key) or "").strip() or NOT_SPECIFIED for key in GLANCE_KEYS}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w+#.\s]", " ", text.lower())).strip()


def _profile_text(profile: dict) -> str:
    """Every string value in the profile, joined, for evidence checks."""
    parts = []

    def walk(node):
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(profile)
    return _normalize(" \n ".join(parts))


def clean_insights(raw: dict, profile: dict) -> dict:
    """Validate the LLM output, keep the lists short, and move any "matched"
    skill whose evidence isn't actually in the profile to "missing"."""
    insights = JobInsights.model_validate(raw)
    haystack = _profile_text(profile)

    glance = normalize_glance(insights.at_a_glance.model_dump())

    matched, missing, seen = [], [], set()
    for item in insights.skills_matched:
        name = item.skill
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        evidence = _normalize(item.evidence)
        if (evidence and evidence in haystack) or _normalize(name) in haystack:
            matched.append({"skill": name, "evidence": item.evidence})
        else:
            missing.append(name)
    for name in insights.skills_missing:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            missing.append(name)

    return reconcile_skills({
        "at_a_glance": glance,
        "skills_matched": matched[:MAX_MATCHED],
        "skills_missing": missing[:MAX_MISSING],
    }, profile)


def _alternatives(name: str) -> list[str]:
    """'Cloud platforms (AWS/GCP/Azure)' -> ['Cloud platforms', 'AWS', 'GCP', 'Azure']."""
    parts = re.split(r"[/,()]|\s+or\s+|(?<=\w)-(?=[A-Z])", name)
    return [part.strip() for part in parts if len(part.strip()) >= 2]


def _skill_terms(profile: dict) -> list[str]:
    """Normalized skills and tech-stack items: the only places a skill counts as listed."""
    items = [s for group in profile.get("skills", []) for s in group.get("skills", [])]
    items += [t for project in profile.get("projects", []) for t in project.get("tech_stack", [])]
    return [f" {_normalize(item)} " for item in items if item]


def reconcile_skills(insights: dict | None, profile: dict) -> dict | None:
    """Fix a model mistake the evidence check can't catch: a "missing" skill the
    profile plainly lists. If any alternative named in a missing skill
    ('Cloud platforms (AWS/GCP/Azure)' -> AWS) is one of the profile's listed
    skills or tech-stack items, move it to "matched" with that as evidence.
    Free text is deliberately not searched: "test" in "150+ test queries"
    must not count as a testing skill. Safe to run more than once; also used
    on read, so older stored analyses get corrected too."""
    if not insights:
        return insights
    insights = {**insights, "at_a_glance": normalize_glance(insights.get("at_a_glance") or {})}
    terms = _skill_terms(profile)
    matched = list(insights["skills_matched"])
    missing = []
    for name in insights["skills_missing"]:
        found = next((alt for alt in _alternatives(name)
                      if any(f" {_normalize(alt)} " in term for term in terms)), None)
        if found:
            matched.append({"skill": name, "evidence": found})
        else:
            missing.append(name)
    return {**insights, "skills_matched": matched, "skills_missing": missing}


def insights_title(insights: dict | None) -> str | None:
    """'Company - Role' from the insights, or None if either is unknown."""
    if not insights:
        return None
    glance = insights["at_a_glance"]
    company, role = glance["company"], glance["role"]
    if NOT_SPECIFIED in (company, role):
        return None
    return f"{company} - {role}"
