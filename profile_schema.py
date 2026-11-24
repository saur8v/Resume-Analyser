"""
Structured profile schema.

This is the single source of truth for a user's professional details. The
same model is used to:
  - validate every profile write (form edits and resume parsing), so the
    stored JSON always has the same shape,
  - define the structured output the LLM must return when parsing a resume,
  - serialize the profile that is sent to the LLM for analysis.

Every field is required (no defaults) because OpenAI structured outputs in
strict mode need all keys present. Empty strings / empty lists mean "unknown".
"""

import json

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

ShortText = Field(max_length=300)
LongText = Field(max_length=4000)


class _Strict(BaseModel):
    # Reject unknown keys so typos or stale clients can't silently add fields.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Link(_Strict):
    label: str = ShortText
    url: str = ShortText


class Basics(_Strict):
    full_name: str = ShortText
    headline: str = ShortText
    email: str = ShortText
    phone: str = ShortText
    location: str = ShortText
    summary: str = LongText
    links: list[Link] = Field(max_length=20)


class SkillGroup(_Strict):
    category: str = ShortText
    skills: list[str] = Field(max_length=60)


class Experience(_Strict):
    company: str = ShortText
    title: str = ShortText
    location: str = ShortText
    start_date: str = ShortText
    end_date: str = ShortText
    is_current: bool
    highlights: list[str] = Field(max_length=30)


class Project(_Strict):
    name: str = ShortText
    url: str = ShortText
    tech_stack: list[str] = Field(max_length=40)
    highlights: list[str] = Field(max_length=30)


class Education(_Strict):
    institution: str = ShortText
    degree: str = ShortText
    field_of_study: str = ShortText
    start_date: str = ShortText
    end_date: str = ShortText
    grade: str = ShortText
    highlights: list[str] = Field(max_length=20)


class Certification(_Strict):
    name: str = ShortText
    issuer: str = ShortText
    date: str = ShortText


class Profile(_Strict):
    basics: Basics
    skills: list[SkillGroup] = Field(max_length=30)
    experience: list[Experience] = Field(max_length=40)
    projects: list[Project] = Field(max_length=40)
    education: list[Education] = Field(max_length=20)
    certifications: list[Certification] = Field(max_length=40)
    achievements: list[str] = Field(max_length=40)
    additional_context: str = Field(
        max_length=4000,
        description=(
            "Extra facts the candidate wants considered that are not on the "
            "resume (e.g. client-facing work, tools used, availability)."
        ),
    )


def empty_profile() -> dict:
    return Profile(
        basics=Basics(
            full_name="", headline="", email="", phone="",
            location="", summary="", links=[],
        ),
        skills=[], experience=[], projects=[], education=[],
        certifications=[], achievements=[], additional_context="",
    ).model_dump()


# Fields that were removed from the schema. Profiles saved before the removal
# still contain them, so they are dropped instead of failing validation.
_LEGACY_PROJECT_KEYS = ("role",)


def drop_legacy_keys(data):
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return data
    projects = [
        {k: v for k, v in project.items() if k not in _LEGACY_PROJECT_KEYS}
        if isinstance(project, dict) else project
        for project in data["projects"]
    ]
    return {**data, "projects": projects}


def normalize_profile(data: dict) -> dict:
    """Validate arbitrary input and return the canonical JSON-ready dict.

    Raises pydantic.ValidationError if the data does not match the schema.
    Blank list entries are dropped so the stored JSON stays clean.
    """
    profile = Profile.model_validate(drop_legacy_keys(data))

    def clean(items):
        return [item for item in items if item]

    profile.achievements = clean(profile.achievements)
    for group in profile.skills:
        group.skills = clean(group.skills)
    for section in (profile.experience, profile.projects, profile.education):
        for entry in section:
            entry.highlights = clean(entry.highlights)
    for project in profile.projects:
        project.tech_stack = clean(project.tech_stack)

    return profile.model_dump()


def profile_has_content(data: dict) -> bool:
    """True once the profile has something worth analysing. Name and email
    alone (pre-filled at signup) don't count."""
    basics = {k: v for k, v in data["basics"].items() if k not in ("full_name", "email")}
    empty = empty_profile()
    empty_basics = {k: v for k, v in empty["basics"].items() if k not in ("full_name", "email")}
    rest = {k: v for k, v in data.items() if k != "basics"}
    empty_rest = {k: v for k, v in empty.items() if k != "basics"}
    return basics != empty_basics or rest != empty_rest


def profile_for_llm(data: dict) -> str:
    """Serialize a stored profile as compact-but-readable JSON for the prompt."""
    return json.dumps(
        {"schema_version": SCHEMA_VERSION, **data}, indent=2, ensure_ascii=False
    )
