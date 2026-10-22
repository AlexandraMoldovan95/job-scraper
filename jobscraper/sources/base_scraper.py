"""Common flow of a direct scraper: result pages -> jobs, and the detail page on demand."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from jobscraper.models import JobPosting, SearchQuery, now_local
from jobscraper.sources.http import PoliteClient


@dataclass(frozen=True)
class ListingPage:
    jobs: list[JobPosting] = field(default_factory=list)
    next_url: str | None = None


class BaseScraper(ABC):
    name: ClassVar[str]

    # The site matches searches against the whole ad (or its domain), so a result whose title
    # lacks the search words can still be right: scrape.run_source judges it on the full ad.
    searches_ad_text = True

    def __init__(self, http: PoliteClient) -> None:
        self.http = http

    @abstractmethod
    def search_url(self, query: SearchQuery) -> str:
        """URL of the first results page."""

    @abstractmethod
    def parse_listing(self, body: str, url: str) -> ListingPage:
        """Jobs on one results page (without description) and the next page's URL."""

    @abstractmethod
    def parse_detail(self, body: str, job: JobPosting, *, now: datetime) -> JobPosting:
        """`job` enriched from its detail page, with details_fetched_at = now."""

    def fetch_jobs(self, query: SearchQuery) -> list[JobPosting]:
        url: str | None = self.search_url(query)
        jobs: list[JobPosting] = []
        for _ in range(query.max_pages):
            # Results change often; only detail pages go through the cache
            page = self.parse_listing(self.http.get_text(url, use_cache=False), url)
            jobs.extend(page.jobs)
            url = page.next_url
            if not url or not page.jobs:
                break
        return jobs

    def fetch_details(self, job: JobPosting) -> JobPosting:
        return self.parse_detail(self.http.get_text(job.url), job, now=now_local())
