"""Profile <-> flat widget state for the Profil page. Pure: no Streamlit import.

Every widget value lives in session state under a "p." key. List entries
(experience, education...) get a stable uid so removing one entry does not shift
the widgets of the others. Skills and languages are edited in st.data_editor
tables, whose session state holds only the edits; apply_editor_delta() replays them.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from jobscraper.models import (
    ContactInfo,
    Course,
    Education,
    Experience,
    ExperienceLevel,
    LanguageSkill,
    Profile,
    Project,
    Skill,
    SkillLevel,
    WorkMode,
)
from jobscraper.profile import profile_to_yaml

PREFIX = "p."

LIST_SECTIONS: dict[str, type[BaseModel]] = {
    "experience": Experience,
    "education": Education,
    "courses": Course,
    "projects": Project,
}
LINE_LIST_FIELDS = {"achievements"}  # text area, one item per line
COMMA_LIST_FIELDS = {"technologies", "roles", "cities", "excluded_keywords"}  # text input, comma separated

SKILL_LEVEL_LABELS = {
    SkillLevel.BEGINNER: "începător",
    SkillLevel.INTERMEDIATE: "intermediar",
    SkillLevel.ADVANCED: "avansat",
    SkillLevel.EXPERT: "expert",
}
_SKILL_LEVEL_BY_LABEL = {label: level for level, label in SKILL_LEVEL_LABELS.items()}

SECTION_LABELS = {
    "contact": "Contact",
    "experience": "Experiență",
    "education": "Educație",
    "courses": "Cursuri",
    "projects": "Proiecte",
    "skills": "Skill-uri",
    "languages": "Limbi străine",
    "preferences": "Preferințe",
}
FIELD_LABELS = {
    "full_name": "Nume complet",
    "email": "Email",
    "phone": "Telefon",
    "city": "Oraș",
    "linkedin": "LinkedIn",
    "github": "GitHub",
    "website": "Site",
    "headline": "Titlu profesional",
    "summary": "Rezumat",
    "role": "Rol",
    "company": "Firmă",
    "start": "Început",
    "end": "Sfârșit",
    "description": "Descriere",
    "achievements": "Realizări",
    "technologies": "Tehnologii",
    "institution": "Instituție",
    "degree": "Diplomă",
    "field": "Domeniu",
    "name": "Nume",
    "provider": "Organizator",
    "completed": "Finalizat",
    "url": "Link",
    "level": "Nivel",
    "years": "Ani",
    "roles": "Roluri căutate",
    "cities": "Orașe",
    "work_modes": "Mod de lucru",
    "levels": "Nivel de experiență",
    "excluded_keywords": "Cuvinte excluse",
    "min_salary": "Salariu minim",
}


def key(*parts: str) -> str:
    return PREFIX + ".".join(parts)


def new_uid() -> str:
    return uuid.uuid4().hex[:8]


# --- Profile -> widget state ---------------------------------------------------------


def to_form_state(profile: Profile | None, *, make_uid: Callable[[], str] = new_uid) -> dict[str, Any]:
    """Initial session-state values for every widget of the Profil page."""
    state: dict[str, Any] = {}
    contact = profile.contact if profile else None
    for name in ContactInfo.model_fields:
        state[key("contact", name)] = (getattr(contact, name) if contact else None) or ""
    state[key("headline")] = (profile.headline if profile else None) or ""
    state[key("summary")] = (profile.summary if profile else None) or ""

    for section in LIST_SECTIONS:
        uids = []
        for entry in getattr(profile, section) if profile else []:
            uid = make_uid()
            uids.append(uid)
            state |= entry_state(section, uid, entry)
        state[key(section)] = uids

    state[key("skills")] = [
        {"name": s.name, "level": SKILL_LEVEL_LABELS.get(s.level), "years": s.years} for s in profile.skills
    ] if profile else []
    state[key("languages")] = [{"name": lang.name, "level": lang.level} for lang in profile.languages] if profile else []

    prefs = profile.preferences if profile else None
    state[key("preferences", "roles")] = ", ".join(prefs.roles) if prefs else ""
    state[key("preferences", "cities")] = ", ".join(prefs.cities) if prefs else ""
    state[key("preferences", "work_modes")] = list(prefs.work_modes) if prefs else []
    state[key("preferences", "levels")] = list(prefs.levels) if prefs else []
    state[key("preferences", "excluded_keywords")] = ", ".join(prefs.excluded_keywords) if prefs else ""
    state[key("preferences", "min_salary")] = (prefs.min_salary if prefs else None) or 0

    state[key("yaml")] = profile_to_yaml(profile) if profile else ""
    return state


def entry_state(section: str, uid: str, entry: BaseModel | None = None) -> dict[str, Any]:
    """Widget values for one list entry; empty when `entry` is None (the "Adaugă" button)."""
    state = {}
    for name in LIST_SECTIONS[section].model_fields:
        value = getattr(entry, name) if entry is not None else None
        if name in LINE_LIST_FIELDS:
            value = "\n".join(value or [])
        elif name in COMMA_LIST_FIELDS:
            value = ", ".join(value or [])
        state[key(section, uid, name)] = value or ""
    return state


# --- widget state -> Profile ---------------------------------------------------------


def from_form_state(state: Mapping[str, Any]) -> Profile:
    """Build and validate the profile; raises pydantic.ValidationError (see describe_errors)."""
    data: dict[str, Any] = {
        "contact": {
            name: _text(state.get(key("contact", name)), required=field.is_required())
            for name, field in ContactInfo.model_fields.items()
        },
        "headline": _text(state.get(key("headline"))),
        "summary": _text(state.get(key("summary"))),
    }

    for section, model in LIST_SECTIONS.items():
        entries = []
        for uid in state.get(key(section)) or []:
            entry = {
                name: _field_value(name, state.get(key(section, uid, name)), required=field.is_required())
                for name, field in model.model_fields.items()
            }
            if any(value not in (None, "", []) for value in entry.values()):  # skip untouched blank entries
                entries.append(entry)
        data[section] = entries

    data["skills"] = [
        {
            "name": _text(row.get("name"), required=True),
            "level": _SKILL_LEVEL_BY_LABEL.get(row.get("level")),
            "years": _number(row.get("years")),
        }
        for row in apply_editor_delta(state.get(key("skills")) or [], state.get(key("skills", "editor")))
        if not _blank_row(row)
    ]
    data["languages"] = [
        {"name": _text(row.get("name"), required=True), "level": _text(row.get("level"), required=True)}
        for row in apply_editor_delta(state.get(key("languages")) or [], state.get(key("languages", "editor")))
        if not _blank_row(row)
    ]

    min_salary = state.get(key("preferences", "min_salary")) or 0
    data["preferences"] = {
        "roles": _field_value("roles", state.get(key("preferences", "roles"))),
        "cities": _field_value("cities", state.get(key("preferences", "cities"))),
        "work_modes": [WorkMode(mode) for mode in state.get(key("preferences", "work_modes")) or []],
        "levels": [ExperienceLevel(level) for level in state.get(key("preferences", "levels")) or []],
        "excluded_keywords": _field_value("excluded_keywords", state.get(key("preferences", "excluded_keywords"))),
        "min_salary": int(min_salary) if min_salary else None,
    }
    return Profile.model_validate(data)


def apply_editor_delta(rows: list[dict[str, Any]], delta: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Replay st.data_editor edits: {"edited_rows": {i: {...}}, "added_rows": [...], "deleted_rows": [i]}.

    Edited and deleted indices refer to the original rows; added rows go at the end.
    """
    if not delta:
        return [dict(row) for row in rows]
    deleted = {int(i) for i in delta.get("deleted_rows") or []}
    edited = {int(i): changes for i, changes in (delta.get("edited_rows") or {}).items()}
    result = [{**row, **edited.get(i, {})} for i, row in enumerate(rows) if i not in deleted]
    result += [dict(row) for row in delta.get("added_rows") or []]
    return result


def _field_value(name: str, value: Any, *, required: bool = False) -> Any:
    if name in LINE_LIST_FIELDS:
        return [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if name in COMMA_LIST_FIELDS:
        return [item.strip() for item in str(value or "").split(",") if item.strip()]
    return _text(value, required=required)


def _text(value: Any, *, required: bool = False) -> str | None:
    """Empty optional text -> None; empty required text stays "" so validation names the field."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        value = ""
    text = str(value).strip()
    return text if text or required else None


def _number(value: Any) -> float | None:
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def _blank_row(row: Mapping[str, Any]) -> bool:
    return all(_text(value) is None for value in row.values())


# --- errors in Romanian -----------------------------------------------------------------


def describe_errors(error: ValidationError) -> list[str]:
    """"Experiență #2 · Început: formatul trebuie să fie AAAA-LL sau doar AAAA (ex. 2024-03, 2019)"."""
    messages = []
    for item in error.errors():
        where = []
        for part in item["loc"]:
            if isinstance(part, int) and where:
                where[-1] += f" #{part + 1}"
            else:
                where.append(SECTION_LABELS.get(part) or FIELD_LABELS.get(part, str(part)))
        messages.append(f"{' · '.join(where)}: {_message(item)}")
    return messages


def _message(item: Mapping[str, Any]) -> str:
    kind = item["type"]
    if kind in ("missing", "string_too_short", "string_type"):
        return "e obligatoriu"
    if kind == "string_pattern_mismatch":
        return "formatul trebuie să fie AAAA-LL sau doar AAAA (ex. 2024-03, 2019)"
    if kind in ("greater_than_equal", "greater_than"):
        return "nu poate fi negativ"
    if kind in ("float_parsing", "int_parsing", "float_type", "int_type"):
        return "trebuie să fie un număr"
    return item.get("msg", "valoare invalidă")
