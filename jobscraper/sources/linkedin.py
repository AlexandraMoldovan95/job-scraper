"""LinkedIn Jobs through SearchApi.io (we never scrape LinkedIn ourselves).

- List: https://www.searchapi.io/docs/linkedin-jobs-api (no description)
- Details: https://www.searchapi.io/docs/linkedin-job-details-api (one credit per job,
  so they are only loaded when the user asks for them; see sources.details_source_name)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from jobscraper.config import LinkedInTimePeriod
from jobscraper.models import BUCHAREST_TZ, JobPosting, SearchQuery, now_local
from jobscraper.normalize import clean_text, parse_relative_date, work_mode_from_text
from jobscraper.sources.searchapi import SearchApiClient, SearchApiError

log = logging.getLogger(__name__)


class LinkedInSource:
    name = "linkedin"

    def __init__(
        self,
        client: SearchApiClient,
        *,
        location: str = "Romania",
        time_period: LinkedInTimePeriod | None = None,
    ):
        self._client = client
        self._location = location
        self._time_period = time_period

    def fetch_jobs(self, query: SearchQuery) -> list[JobPosting]:
        params = {
            "engine": "linkedin_jobs",
            # Not "remote": as a keyword it brings any remote job, and the engine has no
            # work-mode parameter (scrape.run_source keeps only remote results instead)
            "q": query.keywords,
            # LinkedIn matches free text to one of its places; "Cluj, Romania" is unambiguous
            "location": f"{query.city}, {self._location}" if query.city else self._location,
        }
        if self._time_period:
            params["time_period"] = self._time_period

        jobs: list[JobPosting] = []
        for page in range(1, query.max_pages + 1):
            data = self._client.search({**params, "page": str(page)})
            if page == 1:
                log.info("LinkedIn searched in %r", (data.get("search_parameters") or {}).get("location_used"))
            jobs.extend(parse_linkedin_jobs(data, now=now_local()))
            if not (data.get("pagination") or {}).get("next"):
                break
        return jobs

    def fetch_details(self, job: JobPosting) -> JobPosting:
        if not job.linkedin_job_id:
            raise ValueError("job has no LinkedIn job id")
        data = self._client.search({"engine": "linkedin_job_details", "job_id": job.linkedin_job_id})
        details = data.get("job")
        if not details:
            raise SearchApiError("SearchApi.io nu a întors detaliile acestui job LinkedIn.")
        return apply_linkedin_details(job, details, now=now_local())


def parse_linkedin_jobs(data: dict[str, Any], *, now: datetime) -> list[JobPosting]:
    jobs = []
    for raw in data.get("jobs") or []:
        job_id = str(raw.get("job_id") or "").strip()
        title = clean_text(raw.get("title"))
        if not job_id or not title:
            log.warning("Skipping LinkedIn result without id or title: %r", raw.get("title"))
            continue
        location = clean_text(raw.get("location"))
        jobs.append(
            JobPosting(
                source=LinkedInSource.name,
                external_id=job_id,
                linkedin_job_id=job_id,
                url=raw.get("link") or f"https://www.linkedin.com/jobs/view/{job_id}",
                title=title,
                company=clean_text(raw.get("company_name")),
                city=location,
                via="LinkedIn",
                work_mode=work_mode_from_text(location),
                posted_at=_posted_at(raw, now),
            )
        )
    return jobs


def apply_linkedin_details(job: JobPosting, details: dict[str, Any], *, now: datetime) -> JobPosting:
    """Return `job` enriched with the fields only the details endpoint has."""
    return job.model_copy(
        update={
            "description": clean_text(details.get("description")) or job.description,
            "salary_text": clean_text(details.get("salary")) or job.salary_text,
            "employment_type": clean_text(details.get("employment_type")) or job.employment_type,
            "seniority": clean_text(details.get("seniority_level")) or job.seniority,
            "company": job.company or clean_text(details.get("company_name")),
            "city": job.city or clean_text(details.get("location")),
            "posted_at": job.posted_at or parse_relative_date(details.get("posted_at"), now),
            "details_fetched_at": now,
            "scraped_at": now,
        }
    )


def _posted_at(raw: dict[str, Any], now: datetime) -> datetime | None:
    """Prefer the exact `date` ("2026-10-04"); fall back to the relative `posted_at`."""
    try:
        day = date.fromisoformat(raw.get("date") or "")
    except ValueError:
        return parse_relative_date(raw.get("posted_at"), now)
    return datetime(day.year, day.month, day.day, tzinfo=BUCHAREST_TZ)
