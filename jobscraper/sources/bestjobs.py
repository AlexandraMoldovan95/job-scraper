"""BestJobs (bestjobs.eu), robots.txt allows everything.

- Results: the JSON endpoint the site's own infinite scroll uses,
  /api/proxy/v2/jobs?limit=24&keyword=...&cursor=... Its free-text `keyword` is split
  by BestJobs into keyword + city ("python Cluj-Napoca"). No description in results.
- Details: the job page (/loc-de-munca/<slug>) embeds the full ad in __NEXT_DATA__.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobscraper.models import JobPosting, SearchQuery, WorkMode
from jobscraper.normalize import clean_text, html_to_text, work_mode_from_text
from jobscraper.sources.base_scraper import BaseScraper, ListingPage
from jobscraper.sources.errors import ParseError

log = logging.getLogger(__name__)

BASE_URL = "https://www.bestjobs.eu"
JOBS_API = f"{BASE_URL}/api/proxy/v2/jobs"
PAGE_SIZE = 24
_MAX_LOCATIONS_SHOWN = 3
_NUMBER = re.compile(r"\d[\d.,]*")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)
_CURRENCIES = (("EUR", ("€", "eur")), ("RON", ("lei", "ron")), ("USD", ("$", "usd")))


class BestJobsScraper(BaseScraper):
    name = "bestjobs"

    def search_url(self, query: SearchQuery) -> str:
        return f"{JOBS_API}?{urlencode({'limit': PAGE_SIZE, 'keyword': query.as_text()})}"

    def parse_listing(self, body: str, url: str) -> ListingPage:
        data = _load_json(body)
        jobs = [job for item in data.get("items") or [] if (job := _job_from_card(item)) is not None]
        cursor = data.get("nextCursor")
        return ListingPage(jobs=jobs, next_url=_with_param(url, "cursor", cursor) if cursor else None)

    def parse_detail(self, body: str, job: JobPosting, *, now: datetime) -> JobPosting:
        match = _NEXT_DATA.search(body)
        try:
            ad = json.loads(match.group(1))["props"]["pageProps"]["job"] if match else None
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseError(f"Pagina BestJobs {job.url} are un format nou.") from exc
        if not isinstance(ad, dict):
            raise ParseError(f"Pagina BestJobs {job.url} nu conține anunțul (format nou sau anunț expirat).")
        return job.model_copy(update=_detail_fields(ad, job) | {"details_fetched_at": now, "scraped_at": now})


def _load_json(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ParseError("BestJobs a întors un răspuns care nu e JSON.") from exc
    if not isinstance(data, dict):
        raise ParseError("BestJobs a întors un răspuns într-un format nou.")
    return data


def _job_from_card(item: dict[str, Any]) -> JobPosting | None:
    job_id = str(item.get("id") or "").strip()
    slug = item.get("slug")
    title = clean_text(item.get("title"))
    if not job_id or not slug or not title:
        log.warning("Skipping BestJobs card without id, slug or title: %r", item.get("title"))
        return None

    locations = [name for loc in item.get("locations") or [] if (name := clean_text(loc.get("name")))]
    salary = clean_text(item.get("salary"))
    estimated = clean_text(item.get("estimatedSalary"))
    low, high = _salary_range(salary)
    if salary:
        salary_text = f"{salary} €/lună"  # BestJobs results show EUR per month (the job page says "€/luna")
    elif estimated:
        salary_text = f"{estimated} €/lună (estimare BestJobs)"
    else:
        salary_text = None

    return JobPosting(
        source=BestJobsScraper.name,
        external_id=job_id,
        url=f"{BASE_URL}/loc-de-munca/{slug}",
        title=title,
        company=clean_text(item.get("companyName")),
        city=_join_locations(locations),
        work_mode=work_mode_from_text(" ".join(locations)),
        salary_text=salary_text,
        salary_min=low,
        salary_max=high,
        currency="EUR" if low is not None else None,
    )


def _detail_fields(ad: dict[str, Any], job: JobPosting) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "description": html_to_text(ad.get("description")) or job.description,
        "work_mode": WorkMode.REMOTE if ad.get("remote") else WorkMode.HYBRID if ad.get("partialRemote") else WorkMode.ON_SITE,
    }

    salary = None if ad.get("salaryConfidential") else clean_text(ad.get("salary"))
    if salary:
        low, high = _salary_range(salary)
        fields |= {"salary_text": salary, "salary_min": low, "salary_max": high, "currency": _currency(salary)}

    employer = ad.get("employer") or {}
    if not employer.get("confidential") and (company := clean_text(employer.get("employerName"))):
        fields["company"] = company

    locations = [name for loc in ad.get("locations") or [] if (name := clean_text(loc.get("tooltip") or loc.get("name")))]
    if locations:
        fields["city"] = _join_locations(locations)

    if employment := _names(ad.get("employmentTypes")):
        fields["employment_type"] = employment
    if levels := _names(ad.get("careerLevels")):
        fields["seniority"] = levels

    languages = [
        f"Limbă: {name} ({level})" if (level := clean_text(lang.get("levelName"))) else f"Limbă: {name}"
        for lang in ad.get("spokenLanguages") or []
        if (name := clean_text(lang.get("name")))
    ]
    if languages:
        fields["requirements"] = languages
    return fields


def _names(values: list[Any] | None) -> str | None:
    """["Full time"] or [{"name": "Senior (5-10 ani)"}] -> "Full time" / "Senior (5-10 ani)"."""
    names = [clean_text(v if isinstance(v, str) else (v or {}).get("name")) for v in values or []]
    return ", ".join(n for n in names if n) or None


def _join_locations(locations: list[str]) -> str | None:
    if not locations:
        return None
    shown = "; ".join(locations[:_MAX_LOCATIONS_SHOWN])
    hidden = len(locations) - _MAX_LOCATIONS_SHOWN
    return f"{shown} (+{hidden})" if hidden > 0 else shown


def _salary_range(text: str | None) -> tuple[int | None, int | None]:
    """"1000 - 2000 €/luna" -> (1000, 2000); "4.500" -> (4500, 4500)."""
    numbers = [int(re.sub(r"[.,]", "", n)) for n in _NUMBER.findall(text or "")]
    if not numbers:
        return None, None
    return min(numbers), max(numbers)


def _currency(text: str) -> str | None:
    lowered = text.lower()
    for code, markers in _CURRENCIES:
        if any(marker in lowered for marker in markers):
            return code
    return None


def _with_param(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query)) | {name: value}
    return urlunsplit(parts._replace(query=urlencode(params)))
