"""Hipo (hipo.ro): server-rendered HTML, robots.txt allows the pages we use.

- Results: GET /locuri-de-munca/cautajob/<Domeniu>/<Oraș>/<cuvinte-cheie>[/<pagina>], the same
  URLs the site links to itself (the search form only redirects there). No description.
- Details: /locuri-de-munca/locuri_de_munca/<id>/<Firma>/<Titlu> has a JSON-LD JobPosting (date,
  employer, salary) plus a details table and the ad: formatted in #anunt-content, or, in the newer
  layout, as titled sections (company, requirements, responsibilities, salary) in
  .content-block-content > .content. The JSON-LD text is double-encoded by Hipo, so only its
  dates, names and numbers are used.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from jobscraper.models import BUCHAREST_TZ, JobPosting, SearchQuery
from jobscraper.normalize import clean_text, html_to_text, strip_diacritics, work_mode_from_text
from jobscraper.sources.base_scraper import BaseScraper, ListingPage
from jobscraper.sources.errors import ParseError

log = logging.getLogger(__name__)

BASE_URL = "https://www.hipo.ro"
SEARCH_PATH = "/locuri-de-munca/cautajob/Toate-Domeniile"
_JOB_ID = re.compile(r"/locuri_de_munca/(\d+)/")
_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_WORK_MODE_WORDS = {"remote", "hybrid", "hibrid", "on-site", "onsite"}
_MAX_LOCATIONS_SHOWN = 3

# City segment as Hipo spells it in its own URLs (the search form's city list)
_CITY_SEGMENTS = {"bucuresti": "BUCURESTI", "bucharest": "BUCURESTI", "cluj": "Cluj-Napoca"}

# Labels of the details table, in English and Romanian
_FIELDS = {
    "employer": "company", "angajator": "company",
    "job type": "employment_type", "tip job": "employment_type",
    "job level": "seniority", "nivel job": "seniority", "nivel": "seniority",
    "location": "city", "locatie": "city", "oras": "city", "orase": "city",
    "remote work": "work_mode", "lucru remote": "work_mode", "mod de lucru": "work_mode",
}


class HipoScraper(BaseScraper):
    name = "hipo"

    def search_url(self, query: SearchQuery) -> str:
        city = _city_segment(query.city) if query.city else "Toate-Orasele"
        # Not "remote": Hipo matches any of the words, so it would bring every remote job
        # (scrape.run_source keeps only remote results instead)
        keywords = _slug(query.keywords)
        return f"{BASE_URL}{SEARCH_PATH}/{city}/{keywords}"

    def parse_listing(self, body: str, url: str) -> ListingPage:
        soup = BeautifulSoup(body, "lxml")
        jobs: dict[str, JobPosting] = {}
        for card in soup.select("div.job-item"):
            job = _job_from_card(card)
            if job is not None:
                jobs.setdefault(job.external_id, job)  # promoted ads repeat jobs from the list
        return ListingPage(jobs=list(jobs.values()), next_url=_next_page(soup, url))

    def parse_detail(self, body: str, job: JobPosting, *, now: datetime) -> JobPosting:
        soup = BeautifulSoup(body, "lxml")
        content = soup.select_one("#anunt-content") or soup.select_one(".main-content .content-block-content > .content")
        if content is None:
            raise ParseError(f"Pagina Hipo {job.url} nu conține anunțul (format nou sau anunț expirat).")

        fields: dict[str, Any] = {"description": html_to_text(str(content)) or job.description}
        for row in soup.select(".companie-info tr"):
            cells = row.find_all("td")
            if len(cells) != 2:
                continue
            label = strip_diacritics(cells[0].get_text(" ", strip=True)).lower().strip(" :")
            field = _FIELDS.get(label)
            values = [clean_text(li.get_text(" ", strip=True)) for li in cells[1].find_all("li")]
            value = ", ".join(v for v in values if v) or clean_text(cells[1].get_text(" ", strip=True))
            if not field or not value:
                continue
            if field == "work_mode":
                fields["work_mode"] = work_mode_from_text(value) or job.work_mode
            elif field == "city":
                fields["city"] = _join_locations([_pretty_city(c) for c in value.split(",")]) or job.city
            else:
                fields[field] = value

        posting = _job_posting_ld(soup)
        if posting:
            if not fields.get("company"):
                fields["company"] = clean_text((posting.get("hiringOrganization") or {}).get("name"))
            if posted := _iso_date(posting.get("datePosted")):
                fields["posted_at"] = posted
            if not job.salary_text:
                fields |= _salary(posting)
        return job.model_copy(update={k: v for k, v in fields.items() if v} | {"details_fetched_at": now, "scraped_at": now})


def _job_from_card(card: Tag) -> JobPosting | None:
    link = card.select_one("a.job-title")
    if link is None:
        return None  # ad slot, not a job
    match = _JOB_ID.search(link.get("href", ""))
    title = clean_text(link.get("title") or link.get_text(" ", strip=True))
    if not match or not title:
        log.warning("Skipping Hipo card without id or title: %r", title)
        return None

    company = card.select_one(".company-name")
    places = [
        clean_text(marker.parent.get_text(" ", strip=True))
        for marker in card.select("i.fa-map-marker-alt")
        if marker.parent is not None
    ]
    modes = [p for p in places if p and strip_diacritics(p).lower() in _WORK_MODE_WORDS]
    cities = [_pretty_city(p) for p in places if p and p not in modes]
    date_text = card.select_one("small")

    return JobPosting(
        source=HipoScraper.name,
        external_id=match.group(1),
        url=urljoin(BASE_URL, link["href"]),
        title=title,
        company=clean_text(company.get_text(" ", strip=True)) if company else None,
        city=_join_locations(cities),
        work_mode=work_mode_from_text(" ".join(modes)),
        posted_at=_dmy(date_text.get_text(" ", strip=True)) if date_text else None,
    )


def _next_page(soup: BeautifulSoup, url: str) -> str | None:
    """Pages are /<keywords>/2, /3...; the next one exists only if the page links to it."""
    path = urlsplit(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1]
    base, page = (path.rsplit("/", 1)[0], int(last)) if last.isdigit() else (path, 1)
    wanted = f"{base}/{page + 1}"
    for link in soup.find_all("a", href=True):
        if urlsplit(urljoin(BASE_URL, link["href"])).path.rstrip("/") == wanted:
            return f"{BASE_URL}{wanted}"
    return None


def _job_posting_ld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "", strict=False)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _salary(posting: dict[str, Any]) -> dict[str, Any]:
    """JSON-LD baseSalary {currency, value: {minValue, maxValue, unitText}} -> JobPosting fields."""
    salary = posting.get("baseSalary") or {}
    value = salary.get("value") or {}
    low, high = _int(value.get("minValue")), _int(value.get("maxValue"))
    currency = clean_text(salary.get("currency")) or None
    if not (low or high) or (low and high and low > high):
        return {}
    shown = f"{low} - {high}" if low and high and low != high else str(low or high)
    per = {"MONTH": "/lună", "HOUR": "/oră", "YEAR": "/an"}.get(str(value.get("unitText", "")).upper(), "")
    return {
        "salary_min": low,
        "salary_max": high,
        "currency": currency,
        "salary_text": f"{shown} {currency or ''}{per}".replace(" /", "/").strip(),
    }


def _int(text: Any) -> int | None:
    try:
        number = int(float(str(text).replace(",", ".")))
    except ValueError:
        return None
    return number if number > 0 else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", strip_diacritics(text).lower()).strip("-")


def _city_segment(city: str) -> str:
    plain = strip_diacritics(city).strip()
    return _CITY_SEGMENTS.get(plain.lower(), "-".join(word.capitalize() for word in re.split(r"[\s-]+", plain)))


def _pretty_city(city: str) -> str:
    city = city.strip(" ,")
    return city.title() if city.isupper() else city


def _join_locations(cities: list[str]) -> str | None:
    cities = [c for c in cities if c]
    if not cities:
        return None
    shown = ", ".join(cities[:_MAX_LOCATIONS_SHOWN])
    return f"{shown} (+{len(cities) - _MAX_LOCATIONS_SHOWN})" if len(cities) > _MAX_LOCATIONS_SHOWN else shown


def _dmy(text: str) -> datetime | None:
    match = _DATE.search(text)
    if not match:
        return None
    day, month, year = map(int, match.groups())
    return datetime(year, month, day, tzinfo=BUCHAREST_TZ)


def _iso_date(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=BUCHAREST_TZ) if text else None
    except ValueError:
        return None
