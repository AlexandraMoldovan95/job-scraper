"""Job sources: SearchApi.io engines and direct scrapers, behind one interface."""

from __future__ import annotations

from typing import Protocol

from jobscraper.config import AppConfig
from jobscraper.keys import SEARCHAPI_API_KEY, KeyStore
from jobscraper.models import JobPosting, SearchQuery
from jobscraper.sources.base_scraper import BaseScraper
from jobscraper.sources.bestjobs import BestJobsScraper
from jobscraper.sources.errors import SourceError, SourceUnavailable
from jobscraper.sources.hipo import HipoScraper
from jobscraper.sources.http import PoliteClient
from jobscraper.sources.linkedin import LinkedInSource
from jobscraper.sources.searchapi import GoogleJobsSource, SearchApiClient

__all__ = [
    "IMPLEMENTED_SOURCES",
    "PAID_DETAILS",
    "PAID_SEARCH",
    "SCRAPERS",
    "SOURCE_LABELS",
    "DetailsSource",
    "JobSource",
    "SourceError",
    "SourceUnavailable",
    "create_details_source",
    "create_source",
    "details_source_name",
]

SOURCE_LABELS = {
    GoogleJobsSource.name: "Google Jobs",
    LinkedInSource.name: "LinkedIn",
    BestJobsScraper.name: "BestJobs",
    HipoScraper.name: "Hipo",
    "ejobs": "eJobs",
    "olx": "OLX",
}

# Direct scrapers: free, polite (PoliteClient), details loaded from the job page on demand
SCRAPERS: dict[str, type[BaseScraper]] = {BestJobsScraper.name: BestJobsScraper, HipoScraper.name: HipoScraper}

IMPLEMENTED_SOURCES = (GoogleJobsSource.name, LinkedInSource.name, *SCRAPERS)

# Requests to SearchApi.io cost the user a credit, so the UI says so: per results page
# for these sources, and per job when loading LinkedIn details on demand.
PAID_SEARCH = {GoogleJobsSource.name, LinkedInSource.name}
PAID_DETAILS = {LinkedInSource.name}


class JobSource(Protocol):
    name: str

    def fetch_jobs(self, query: SearchQuery) -> list[JobPosting]: ...


class DetailsSource(Protocol):
    name: str

    def fetch_details(self, job: JobPosting) -> JobPosting: ...


def create_source(name: str, config: AppConfig, keys: KeyStore) -> JobSource:
    if name == GoogleJobsSource.name:
        return GoogleJobsSource(
            _searchapi_client(config, keys),
            location=config.searchapi.location,
            gl=config.searchapi.gl,
            hl=config.searchapi.hl,
        )
    if name == LinkedInSource.name:
        return _linkedin(config, keys)
    if name in SCRAPERS:
        return SCRAPERS[name](_polite_client(config))
    raise SourceUnavailable(f"Sursa „{SOURCE_LABELS.get(name, name)}” nu e implementată încă.")


def details_source_name(job: JobPosting) -> str | None:
    """Which source can load the full ad for `job`, or None if there is nothing to load."""
    if job.details_fetched_at is not None:
        return None
    if job.linkedin_job_id:
        return LinkedInSource.name
    if job.source in SCRAPERS:
        return job.source
    return None


def create_details_source(job: JobPosting, config: AppConfig, keys: KeyStore) -> DetailsSource:
    name = details_source_name(job)
    if name == LinkedInSource.name:
        return _linkedin(config, keys)
    if name in SCRAPERS:
        return SCRAPERS[name](_polite_client(config))
    raise SourceUnavailable("Pentru acest job nu există detalii de încărcat.")


def _linkedin(config: AppConfig, keys: KeyStore) -> LinkedInSource:
    return LinkedInSource(
        _searchapi_client(config, keys),
        location=config.searchapi.location,
        time_period=config.searchapi.linkedin_time_period,
    )


def _searchapi_client(config: AppConfig, keys: KeyStore) -> SearchApiClient:
    api_key = keys.get(SEARCHAPI_API_KEY)
    if not api_key:
        raise SourceUnavailable("Lipsește cheia SearchApi.io: adaug-o pe pagina „Chei API” sau în .env.")
    return SearchApiClient(api_key, timeout=config.scraping.timeout_seconds)


def _polite_client(config: AppConfig) -> PoliteClient:
    return PoliteClient(
        user_agent=config.scraping.user_agent,
        min_delay=config.scraping.min_delay_seconds,
        timeout=config.scraping.timeout_seconds,
        cache_dir=config.paths.cache_dir,
        cache_ttl_hours=config.scraping.cache_ttl_hours,
    )
