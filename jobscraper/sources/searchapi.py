"""SearchApi.io client and its Google Jobs source: https://www.searchapi.io/docs/google-jobs

Every request (one results page, one job's details) costs one search credit on the
user's SearchApi.io account.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from jobscraper.models import JobPosting, SearchQuery, WorkMode, now_local
from jobscraper.normalize import (
    clean_text,
    linkedin_job_id_from_url,
    parse_relative_date,
    strip_diacritics,
    work_mode_from_text,
)
from jobscraper.sources.errors import SourceError

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.searchapi.io/api/v1/search"

# Titles of the job_highlights block that lists requirements (EN and RO interface)
_REQUIREMENT_TITLES = ("qualif", "calific", "cerint", "requir")
_VIA_PREFIX = re.compile(r"^(via|prin)\s+", re.IGNORECASE)


class SearchApiError(SourceError):
    pass


class SearchApiClient:
    def __init__(self, api_key: str, *, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        # Key goes in the Authorization header, never in the URL (URLs end up in logs)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout
        self._transport = transport

    def search(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx.Client(headers=self._headers, timeout=self._timeout, transport=self._transport) as client:
                response = client.get(SEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            raise SearchApiError(f"Nu am putut contacta SearchApi.io: {exc}") from exc
        if response.is_success:
            return response.json()
        raise SearchApiError(_error_message(response))


def _error_message(response: httpx.Response) -> str:
    if response.status_code == 401:
        return "SearchApi.io a respins cheia API (401). Verifică SEARCHAPI_API_KEY."
    if response.status_code == 429:
        return "SearchApi.io: limita de căutări a contului a fost atinsă (429)."
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("error") if isinstance(body, dict) else None
    return f"SearchApi.io a răspuns cu {response.status_code}: {detail or response.reason_phrase}"


# --- Google Jobs -------------------------------------------------------------------


class GoogleJobsSource:
    name = "google_jobs"

    def __init__(self, client: SearchApiClient, *, location: str = "Romania", gl: str = "ro", hl: str = "ro"):
        self._client = client
        self._location = location
        self._gl = gl
        self._hl = hl

    def fetch_jobs(self, query: SearchQuery) -> list[JobPosting]:
        params = {
            "engine": "google_jobs",
            "q": query.as_text(),
            "location": self._location,
            "gl": self._gl,
            "hl": self._hl,
        }
        jobs: list[JobPosting] = []
        for _ in range(query.max_pages):
            data = self._client.search(params)
            jobs.extend(parse_google_jobs(data, now=now_local()))
            token = (data.get("pagination") or {}).get("next_page_token")
            if not token:
                break
            params = {**params, "next_page_token": token}
        return jobs


def parse_google_jobs(data: dict[str, Any], *, now: datetime) -> list[JobPosting]:
    jobs = []
    for raw in data.get("jobs") or []:
        job = _parse_google_job(raw, now)
        if job is not None:
            jobs.append(job)
    return jobs


def _parse_google_job(raw: dict[str, Any], now: datetime) -> JobPosting | None:
    title = clean_text(raw.get("title"))
    links = _links(raw)
    if not title or not links:
        log.warning("Skipping Google Jobs result without title or link: %r", raw.get("title"))
        return None

    extensions = raw.get("detected_extensions") or {}
    location = clean_text(raw.get("location"))
    return JobPosting(
        source=GoogleJobsSource.name,
        external_id=_google_job_id(raw.get("sharing_link")),
        url=links[0],
        title=title,
        company=clean_text(raw.get("company_name")),
        city=location,
        via=_clean_via(raw.get("via")),
        work_mode=WorkMode.REMOTE if extensions.get("work_from_home") else work_mode_from_text(location),
        salary_text=clean_text(extensions.get("salary")),
        employment_type=clean_text(extensions.get("schedule")),
        description=clean_text(raw.get("description")) or "",
        requirements=_requirements(raw.get("job_highlights") or []),
        posted_at=parse_relative_date(extensions.get("posted_at"), now),
        linkedin_job_id=next(filter(None, map(linkedin_job_id_from_url, links)), None),
    )


def _links(raw: dict[str, Any]) -> list[str]:
    """Job board links first (the board's own page beats the Google sharing link)."""
    links = [raw.get("apply_link")]
    links += [link.get("link") for link in raw.get("apply_links") or []]
    links.append(raw.get("sharing_link"))
    return list(dict.fromkeys(link for link in links if link))


def _google_job_id(sharing_link: str | None) -> str | None:
    """Google's job id (htidocid) lives in the sharing link's query or fragment."""
    if not sharing_link:
        return None
    parts = urlsplit(sharing_link)
    for section in (parts.query, parts.fragment):
        values = parse_qs(section).get("htidocid")
        if values:
            return values[0]
    return None


def _clean_via(via: str | None) -> str | None:
    cleaned = clean_text(via)
    return _VIA_PREFIX.sub("", cleaned) if cleaned else None


def _requirements(highlights: list[dict[str, Any]]) -> list[str]:
    for block in highlights:
        title = strip_diacritics(block.get("title") or "").lower()
        if title.startswith(_REQUIREMENT_TITLES):
            return [item for item in map(clean_text, block.get("items") or []) if item]
    return []
