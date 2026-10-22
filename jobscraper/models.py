"""Pydantic models shared by scrapers, the database, the UI and the CV generator."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

BUCHAREST_TZ = ZoneInfo("Europe/Bucharest")

# "2024-03", or just "2024" when only the year is known (LinkedIn shows studies as "2015 - 2019");
# never a guessed month
YearMonth = Annotated[str, Field(pattern=r"^\d{4}(-(0[1-9]|1[0-2]))?$")]


def now_local() -> datetime:
    return datetime.now(BUCHAREST_TZ)


def make_job_id(source: str, external_id: str | None, url: str, linkedin_job_id: str | None = None) -> str:
    # A LinkedIn ad gets the same id whichever source found it (LinkedIn engine or a
    # Google Jobs result linking to LinkedIn), so the upsert merges them into one row.
    key = f"linkedin:{linkedin_job_id}" if linkedin_job_id else f"{source}:{external_id or url}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


# --- Jobs -------------------------------------------------------------------


class WorkMode(StrEnum):
    ON_SITE = "on-site"
    HYBRID = "hybrid"
    REMOTE = "remote"


class JobStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"
    SAVED = "saved"
    APPLIED = "applied"
    REJECTED = "rejected"


class SearchQuery(BaseModel):
    keywords: str = Field(min_length=1)
    city: str | None = None
    work_mode: WorkMode | None = None
    max_pages: int = Field(default=3, ge=1, le=20)

    def as_text(self, *, include_city: bool = True) -> str:
        """Free-text form for sources with a single search box: "python Cluj remote"."""
        parts = [self.keywords]
        if include_city and self.city:
            parts.append(self.city)
        if self.work_mode is WorkMode.REMOTE:
            parts.append("remote")
        return " ".join(parts)


class JobPosting(BaseModel):
    source: str
    url: str
    external_id: str | None = None
    title: str
    company: str | None = None
    city: str | None = None
    via: str | None = None  # original job board when found through an aggregator, e.g. "LinkedIn"
    work_mode: WorkMode | None = None
    salary_text: str | None = None  # salary exactly as the ad shows it
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    currency: str | None = None
    salary_is_net: bool | None = None
    employment_type: str | None = None
    seniority: str | None = None
    description: str = ""
    requirements: list[str] = Field(default_factory=list)
    language: Literal["ro", "en"] | None = None
    posted_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=now_local)
    status: JobStatus = JobStatus.NEW
    linkedin_job_id: str | None = None
    # Set once the full ad was loaded, for sources whose search results lack the description
    details_fetched_at: datetime | None = None
    # Owned by the database (first insert); only filled when reading a stored job
    first_seen_at: datetime | None = None

    @property
    def listed_at(self) -> datetime | None:
        """Posting date, or when we first saw it for sources without one (BestJobs)."""
        return self.posted_at or self.first_seen_at

    @computed_field
    @property
    def id(self) -> str:
        return make_job_id(self.source, self.external_id, self.url, self.linkedin_job_id)

    @model_validator(mode="after")
    def _check_salary_range(self) -> JobPosting:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must not exceed salary_max")
        return self


# --- Profile (data/profile.yaml) --------------------------------------------

Required = Annotated[str, Field(min_length=1)]


class ProfilePart(BaseModel):
    """Profile text comes from a form: trim it, and an empty required field is an error."""

    model_config = ConfigDict(str_strip_whitespace=True)


class ContactInfo(ProfilePart):
    full_name: Required
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None


class Experience(ProfilePart):
    role: Required
    company: Required
    city: str | None = None
    start: YearMonth
    end: YearMonth | None = None  # None = current job
    description: str | None = None
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class Education(ProfilePart):
    institution: Required
    degree: Required
    field: str | None = None
    start: YearMonth | None = None
    end: YearMonth | None = None


class Course(ProfilePart):
    name: Required
    provider: Required
    completed: YearMonth | None = None
    description: str | None = None


class Project(ProfilePart):
    name: Required
    description: Required
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class Skill(ProfilePart):
    name: Required
    level: SkillLevel | None = None
    years: float | None = Field(default=None, ge=0)


class LanguageSkill(ProfilePart):
    name: Required
    level: Required  # CEFR (A1-C2) or "nativ"


class ExperienceLevel(StrEnum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class JobPreferences(ProfilePart):
    """What jobs to look for. Besides scoring, every non-empty field hides the jobs that clearly
    do not fit on the Joburi page (matching.hard_filter); jobs with unknown values stay."""

    roles: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    work_modes: list[WorkMode] = Field(default_factory=list)
    levels: list[ExperienceLevel] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)  # in the title
    min_salary: int | None = Field(default=None, ge=0)


class Profile(ProfilePart):
    contact: ContactInfo
    headline: str | None = None
    summary: str | None = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    languages: list[LanguageSkill] = Field(default_factory=list)
    preferences: JobPreferences = Field(default_factory=JobPreferences)


# --- Tailored CV (structured output from Claude) ----------------------------
# Profile entries are referenced by index, so companies, roles, dates and
# schools always come from the profile and the model cannot invent them.


class TailoredExperience(BaseModel):
    profile_index: int = Field(description="Index of the entry in profile.experience")
    bullets: list[str] = Field(
        description="Achievement bullets rephrased for this job, based only on that profile entry"
    )


class TailoredProject(BaseModel):
    profile_index: int = Field(description="Index of the entry in profile.projects")
    description: str = Field(description="Project description rephrased for this job")


class TailoredCV(BaseModel):
    language: Literal["ro", "en"] = Field(description="Language the CV is written in")
    headline: str = Field(description="One-line professional title")
    summary: str = Field(description="Short professional summary, 2-4 sentences")
    skills: list[str] = Field(
        description="Skills to show, most relevant first; only skills present in the profile"
    )
    experience: list[TailoredExperience] = Field(description="Experience entries in display order")
    projects: list[TailoredProject] = Field(description="Project entries in display order")
    education_indices: list[int] = Field(description="Indices into profile.education to include")
    course_indices: list[int] = Field(description="Indices into profile.courses to include")
    gaps: list[str] = Field(
        description="Job requirements the profile does not cover; shown to the user, never in the CV"
    )
