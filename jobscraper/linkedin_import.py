"""LinkedIn "Save to PDF" profile -> Profile, with Claude reading the PDF.

The result only fills the Profil form: the user checks it and saves it. Fields Claude could
not read stay empty and dates stay as precise as the PDF has them (a year without month is
a valid profile date), so nothing is guessed.
"""

from __future__ import annotations

import base64
from typing import Any

from pydantic import BaseModel, Field

from jobscraper import llm as claude
from jobscraper.config import LLMConfig
from jobscraper.models import (
    ContactInfo,
    Course,
    Education,
    Experience,
    JobPreferences,
    LanguageSkill,
    Profile,
    Project,
    Skill,
)

MAX_PDF_BYTES = 10 * 1024 * 1024  # LinkedIn exports are well under 1 MB

SYSTEM_PROMPT = """\
You read a LinkedIn profile exported with "Save to PDF" and return its contents as \
structured data for the candidate's own CV tool.

Copy, do not write: every value must come from the PDF. Keep the PDF's language and \
wording (do not translate or polish), fix only line breaks and page-break artifacts. When \
the PDF does not contain a field, use an empty string or an empty list; never infer an \
email, phone, city, date, skill or number.

Mapping:
- contact: name, email, phone, location (city only) and profile links from the Contact \
section. `linkedin` is the linkedin.com URL.
- headline: the line under the name. summary: the About / Summary section.
- experience: one entry per position, most recent first. Several positions at one company \
are separate entries. Dates as YYYY-MM; when the PDF gives only a year, write just the year \
(e.g. "2021"); `end` empty for a current position ("Present", "Prezent"). Bullet points of \
the position description go to `achievements` (one per bullet); remaining prose goes to \
`description`. `technologies`: only tools explicitly named in that position.
- education: one entry per school; `degree` is the diploma or program, `field` the field of \
study.
- courses: entries from Certifications, Courses and Honors-Awards that are trainings or \
certificates; `provider` is the issuing organization, or empty when not shown.
- projects: only from a Projects section.
- skills: every name from Top Skills and Skills.
- languages: name and proficiency as written in the PDF.
"""

INSTRUCTION = "Extract this LinkedIn profile."


# Every field is required and a plain string or list ("" / [] when the PDF lacks it): structured
# outputs compile the schema to a grammar, and optional or nullable fields make it grow
# exponentially (the API allows at most 24 optional and 16 union-typed fields per request).


class ImportedContact(BaseModel):
    full_name: str
    email: str
    phone: str
    city: str
    linkedin: str
    github: str
    website: str


class ImportedExperience(BaseModel):
    role: str
    company: str
    city: str
    start: str = Field(description="YYYY-MM, or YYYY when the PDF shows only the year")
    end: str = Field(description='YYYY-MM or YYYY; "" for a current position')
    description: str
    achievements: list[str]
    technologies: list[str]


class ImportedEducation(BaseModel):
    institution: str
    degree: str
    field: str
    start: str
    end: str


class ImportedCourse(BaseModel):
    name: str
    provider: str
    completed: str
    description: str


class ImportedProject(BaseModel):
    name: str
    description: str
    technologies: list[str]
    url: str


class ImportedLanguage(BaseModel):
    name: str
    level: str


class ImportedProfile(BaseModel):
    """Lenient on purpose (plain strings, no date format): checking is the form's job."""

    contact: ImportedContact
    headline: str
    summary: str
    experience: list[ImportedExperience]
    education: list[ImportedEducation]
    courses: list[ImportedCourse]
    projects: list[ImportedProject]
    skills: list[str]
    languages: list[ImportedLanguage]


def check_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF"):
        raise claude.LLMError("Fișierul nu e un PDF. Pe LinkedIn: profilul tău → Mai mult → Salvează ca PDF.")
    if len(data) > MAX_PDF_BYTES:
        raise claude.LLMError("PDF-ul e prea mare (maximum 10 MB).")


def build_content(pdf: bytes) -> list[dict[str, Any]]:
    return [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(pdf).decode()},
        },
        {"type": "text", "text": INSTRUCTION},
    ]


def read_linkedin_pdf(client: Any, pdf: bytes, *, llm: LLMConfig) -> claude.Parsed[ImportedProfile]:
    check_pdf(pdf)
    return claude.parse(
        client, llm=llm, output_format=ImportedProfile, system=SYSTEM_PROMPT, content=build_content(pdf), what="profilul"
    )


def _pick(new: Any, old: Any) -> Any:
    return new if new not in (None, "", []) else old


def _merge(model: type[BaseModel], new: list[Any], old: list[Any], *key_fields: str) -> list[Any]:
    """PDF entries first, each completed from the profile entry with the same key; then the
    profile entries the PDF does not have (a StepIT course is not on LinkedIn)."""

    def key(item: Any) -> tuple[str, ...]:
        return tuple(" ".join(str(getattr(item, f, "") or "").casefold().split()) for f in key_fields)

    old_by_key = {key(item): item for item in old}
    merged = []
    for item in new:
        match = old_by_key.pop(key(item), None)
        merged.append(model.model_construct(**{f: _pick(getattr(item, f, None), getattr(match, f, None)) for f in model.model_fields}))
    return merged + list(old_by_key.values())


def merge_into_profile(imported: ImportedProfile, current: Profile | None) -> Profile:
    """The imported data merged into the current profile: the PDF wins where it has a value,
    the profile fills the rest (skill levels, entries LinkedIn does not have), and the job
    preferences, which LinkedIn does not export, stay as they are. Not validated: the result
    fills the form, which validates on save."""
    old = current or Profile.model_construct(
        contact=ContactInfo.model_construct(), headline=None, summary=None, experience=[], education=[],
        courses=[], projects=[], skills=[], languages=[], preferences=JobPreferences(),
    )
    contact = ContactInfo.model_construct(
        **{f: _pick(getattr(imported.contact, f), getattr(old.contact, f, None)) for f in ContactInfo.model_fields}
    )
    skills = [Skill.model_construct(name=name, level=None, years=None) for name in imported.skills]
    return Profile.model_construct(
        contact=contact,
        headline=_pick(imported.headline, old.headline),
        summary=_pick(imported.summary, old.summary),
        experience=_merge(Experience, imported.experience, old.experience, "role", "company"),
        education=_merge(Education, imported.education, old.education, "institution", "degree"),
        courses=_merge(Course, imported.courses, old.courses, "name"),
        projects=_merge(Project, imported.projects, old.projects, "name"),
        skills=_merge(Skill, skills, old.skills, "name"),
        languages=_merge(LanguageSkill, imported.languages, old.languages, "name"),
        preferences=old.preferences,
    )
