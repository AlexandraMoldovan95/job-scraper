"""Filtering and sorting of stored jobs for the Joburi page (pure, no Streamlit)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from jobscraper.models import JobPosting, WorkMode
from jobscraper.normalize import strip_diacritics


class SortOrder(StrEnum):
    MATCH = "match"
    NEWEST = "newest"
    SALARY = "salary"
    COMPANY = "company"


@dataclass(frozen=True)
class JobFilter:
    text: str = ""  # every word must appear (diacritics and case ignored)
    work_modes: frozenset[WorkMode] = field(default_factory=frozenset)
    with_salary: bool = False  # only jobs that show the employer's salary range
    max_age_days: int | None = None


def _plain(text: str | None) -> str:
    return strip_diacritics(text or "").lower()


def matches(job: JobPosting, job_filter: JobFilter, *, now: datetime) -> bool:
    if job_filter.text:
        haystack = _plain(" ".join(filter(None, (job.title, job.company, job.city, job.description))))
        if not all(word in haystack for word in _plain(job_filter.text).split()):
            return False
    if job_filter.work_modes and job.work_mode not in job_filter.work_modes:
        return False
    if job_filter.with_salary and job.salary_max is None:
        return False
    if job_filter.max_age_days is not None:
        listed = job.listed_at
        if listed is None or now - listed > timedelta(days=job_filter.max_age_days):
            return False
    return True


def filter_jobs(jobs: list[JobPosting], job_filter: JobFilter, *, now: datetime) -> list[JobPosting]:
    return [job for job in jobs if matches(job, job_filter, now=now)]


def sort_jobs(jobs: list[JobPosting], order: SortOrder, scores: Mapping[str, int] | None = None) -> list[JobPosting]:
    """NEWEST keeps the database order (newest first). Ties keep their relative order."""
    if order is SortOrder.MATCH and scores is not None:
        return sorted(jobs, key=lambda job: -scores.get(job.id, 0))
    if order is SortOrder.SALARY:
        return sorted(jobs, key=lambda job: (job.salary_max is None, -(job.salary_max or 0)))
    if order is SortOrder.COMPANY:
        return sorted(jobs, key=lambda job: (job.company is None, _plain(job.company)))
    return list(jobs)
