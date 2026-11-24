"""
Merge a freshly parsed resume into an existing profile without losing data.

Uploading a new resume never replaces the profile. Instead the new resume is
parsed into a profile of its own and merged into the stored one here. The
merge is plain code (no LLM), so it is predictable and testable.

To tell a field the user edited by hand from one copied off the last resume,
the merge uses a *baseline*: exactly what the LLM parsed from the previous
resume. For every value:

    new resume has nothing          -> keep the current value
    current == baseline (untouched) -> take the new resume's value
    current != baseline (edited)    -> keep the user's edit
    no baseline known               -> only fill fields that are empty

Lists of entries (experience, projects, ...) are paired by a normalized key
(e.g. company + title). Paired entries merge field by field with the rules
above; new entries are added; entries missing from the new resume are kept.
Entries and list items the user deleted (in the baseline but no longer in the
profile) are not brought back. `additional_context` is always kept.
"""

import re
import typing

from pydantic import BaseModel

from profile_schema import Profile, normalize_profile

# Fields that identify an entry, per entry model. Entries whose key fields are
# all empty can't be paired and are added as new.
ENTRY_KEYS = {
    "Link": ("url",),
    "SkillGroup": ("category",),
    "Experience": ("company", "title"),
    "Project": ("name",),
    "Education": ("institution", "degree"),
    "Certification": ("name",),
}

# Profile sections reported in the merge summary, with a readable label.
SECTION_LABELS = {
    "experience": "Experience",
    "projects": "Projects",
    "education": "Education",
    "certifications": "Certifications",
    "skills": "Skills",
}


def _norm(value) -> str:
    """Lowercase, punctuation-free form used to compare text."""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _entry_key(model: type[BaseModel], entry: dict) -> str:
    fields = ENTRY_KEYS.get(model.__name__, ())
    return "|".join(_norm(entry.get(field, "")) for field in fields).strip("|")


def _max_len(model: type[BaseModel], field: str):
    for meta in model.model_fields[field].metadata:
        if hasattr(meta, "max_length"):
            return meta.max_length
    return None


def _list_item_type(annotation):
    if typing.get_origin(annotation) is list:
        return typing.get_args(annotation)[0]
    return None


def _merge_scalar(current, new, base, has_base):
    if new in ("", None):
        return current
    if not has_base:
        # Without a baseline any filled value may be a manual edit: only fill blanks.
        return new if current in ("", None) else current
    return new if current == base else current


def _merge_strings(current: list, new: list, base, limit):
    """Union of string lists, current items first. Items the user deleted
    (in the baseline but not in the current list) are not re-added."""
    seen = {_norm(item) for item in current}
    deleted = {_norm(item) for item in (base or [])} - seen
    merged = list(current)
    for item in new:
        key = _norm(item)
        if key and key not in seen and key not in deleted:
            merged.append(item)
            seen.add(key)
    return merged[:limit] if limit else merged


def _merge_model(model, current: dict, new: dict, base):
    has_base = base is not None
    base = base or {}
    merged = {}
    for field, info in model.model_fields.items():
        annotation = info.annotation
        cur_val, new_val, base_val = current.get(field), new.get(field), base.get(field)
        item_type = _list_item_type(annotation)
        limit = _max_len(model, field)

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            merged[field] = _merge_model(annotation, cur_val or {}, new_val or {}, base_val if has_base else None)
        elif item_type is str:
            merged[field] = _merge_strings(cur_val or [], new_val or [], base_val if has_base else None, limit)
        elif item_type is not None:
            merged[field] = _merge_entries(item_type, cur_val or [], new_val or [], base_val if has_base else None, limit)[0]
        else:
            merged[field] = _merge_scalar(cur_val, new_val, base_val, has_base and field in base)
    return merged


def _merge_entries(model, current: list, new: list, base, limit):
    """Merge lists of entries. Returns (merged, report) where report lists the
    entries that were added, updated, kept although missing from the new
    resume, and skipped because the user had deleted them."""
    base_by_key = {}
    for entry in base or []:
        key = _entry_key(model, entry)
        if key:
            base_by_key.setdefault(key, entry)
    current_keys = {_entry_key(model, entry) for entry in current}

    merged = [dict(entry) for entry in current]
    index_by_key = {}
    for i, entry in enumerate(merged):
        key = _entry_key(model, entry)
        if key:
            index_by_key.setdefault(key, i)

    report = {"added": [], "updated": [], "not_on_resume": [], "skipped": []}
    matched = set()
    for entry in new:
        key = _entry_key(model, entry)
        if key and key in index_by_key:
            i = index_by_key[key]
            matched.add(i)
            combined = _merge_model(model, merged[i], entry, base_by_key.get(key) if base is not None else None)
            if combined != merged[i]:
                report["updated"].append(combined)
            merged[i] = combined
        elif key and key in base_by_key and key not in current_keys:
            report["skipped"].append(entry)  # the user deleted it; don't bring it back
        elif entry in merged:
            matched.add(merged.index(entry))  # unkeyed duplicate of an existing entry
        else:
            merged.append(dict(entry))
            report["added"].append(entry)
            matched.add(len(merged) - 1)
            if key:
                index_by_key[key] = len(merged) - 1

    report["not_on_resume"] = [entry for i, entry in enumerate(merged) if i not in matched]
    if limit and len(merged) > limit:
        dropped = merged[limit:]
        merged = merged[:limit]
        report["added"] = [entry for entry in report["added"] if entry not in dropped]
    return merged, report


def _label(section: str, entry: dict) -> str:
    if section == "experience":
        parts = [entry.get("title"), entry.get("company")]
        return " at ".join(part for part in parts if part) or "Untitled role"
    if section == "education":
        return entry.get("degree") or entry.get("institution") or "Untitled entry"
    if section == "skills":
        return entry.get("category") or "Skills"
    return entry.get("name") or "Untitled entry"


def merge_profiles(current: dict, parsed: dict, baseline: dict | None = None):
    """Merge `parsed` (from the new resume) into `current` (the stored profile).

    `baseline` is what was parsed from the previous resume, or None when it
    isn't known. Returns (merged_profile, summary). The merged profile is
    validated against the schema; summary holds readable labels:
    {"added": [...], "updated": [...], "not_on_resume": [...], "skipped": [...]}.
    """
    merged = _merge_model(Profile, current, parsed, baseline)
    merged["additional_context"] = current.get("additional_context", "")

    summary = {"added": [], "updated": [], "not_on_resume": [], "skipped": []}
    if merged["basics"] != current.get("basics"):
        summary["updated"].append("Basics")
    for section, label in SECTION_LABELS.items():
        model = _list_item_type(Profile.model_fields[section].annotation)
        _, report = _merge_entries(
            model, current.get(section) or [], parsed.get(section) or [],
            baseline.get(section) if baseline is not None else None,
            _max_len(Profile, section),
        )
        for kind, entries in report.items():
            summary[kind].extend(f"{label}: {_label(section, entry)}" for entry in entries)
    for section in ("achievements",):
        before = len(current.get(section) or [])
        added = len(merged[section]) - before
        if added > 0:
            summary["added"].append(f"{added} achievement{'s' if added != 1 else ''}")

    return normalize_profile(merged), summary
