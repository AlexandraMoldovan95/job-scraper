"""The "CV-urile mele" page: every generated CV with its type of job, and the filters over them. Pure."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from jobscraper.cv.prompts import FOCUS_PRESETS
from jobscraper.db import StoredCV
from jobscraper.models import JobPosting
from jobscraper.normalize import strip_diacritics

OTHER_TYPE = "Altele"

# A CV for an ad without an explicit focus gets its type from the job title. Order matters:
# "QA Engineer" is QA and "DevOps Engineer" DevOps before "engineer" makes them development.
_TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "Testare / QA": ("qa", "test", "tester", "testare", "testing", "quality assurance"),
    "DevOps / Cloud": ("devops", "cloud", "sre", "site reliability", "infrastructure", "platform engineer"),
    "Date / BI": ("data", "date", "bi", "business intelligence", "analyst", "analist", "reporting"),
    "Suport IT": ("support", "suport", "helpdesk", "help desk", "service desk"),
    "Dezvoltare software": (
        "developer", "dezvoltator", "programmer", "programator", "engineer", "inginer software", "dev",
        "software", "backend", "frontend", "full stack", "fullstack",
    ),
}

Target = Literal["all", "job", "focus"]


def _plain(text: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", strip_diacritics(text or "").lower()).split())


def type_from_title(title: str | None) -> str:
    words = f" {_plain(title)} "
    for cv_type, keywords in _TYPE_WORDS.items():
        if any(f" {keyword} " in words for keyword in keywords):
            return cv_type
    return OTHER_TYPE


@dataclass(frozen=True)
class LibraryEntry:
    stored: StoredCV
    job: JobPosting | None  # None for a CV written for a type of job, or when the ad was deleted
    cv_type: str

    @property
    def for_job(self) -> bool:
        return self.stored.job_id is not None

    @property
    def title(self) -> str:
        """What the CV was written for: the ad's title, or the type of job."""
        if self.job is not None:
            return self.job.title
        return self.stored.focus or self.stored.cv.headline


def library_entries(cvs: Iterable[StoredCV], jobs: dict[str, JobPosting]) -> list[LibraryEntry]:
    """The focus chosen at generation is the type; otherwise it comes from the ad's title."""
    entries = []
    for stored in cvs:
        job = jobs.get(stored.job_id) if stored.job_id else None
        cv_type = stored.focus or type_from_title(job.title if job else stored.cv.headline)
        entries.append(LibraryEntry(stored=stored, job=job, cv_type=cv_type))
    return entries


def library_types(entries: Iterable[LibraryEntry]) -> list[str]:
    """The presets first, then custom types, "Altele" last; only types that have a CV."""
    present = {entry.cv_type for entry in entries}
    order = [*FOCUS_PRESETS, *sorted(present - set(FOCUS_PRESETS) - {OTHER_TYPE}), OTHER_TYPE]
    return [cv_type for cv_type in order if cv_type in present]


@dataclass(frozen=True)
class LibraryFilter:
    types: frozenset[str] = field(default_factory=frozenset)  # empty = all
    target: Target = "all"
    language: Literal["ro", "en"] | None = None
    text: str = ""


def filter_entries(entries: Iterable[LibraryEntry], wanted: LibraryFilter) -> list[LibraryEntry]:
    words = _plain(wanted.text).split()
    result = []
    for entry in entries:
        if wanted.types and entry.cv_type not in wanted.types:
            continue
        if wanted.target == "job" and not entry.for_job or wanted.target == "focus" and entry.for_job:
            continue
        if wanted.language and entry.stored.cv.language != wanted.language:
            continue
        cv = entry.stored.cv
        haystack = _plain(" ".join(filter(None, (
            cv.headline, cv.summary, entry.stored.focus, entry.cv_type,
            entry.job.title if entry.job else None, entry.job.company if entry.job else None,
        ))))
        if all(word in haystack for word in words):
            result.append(entry)
    return result
