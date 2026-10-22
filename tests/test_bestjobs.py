from datetime import datetime
from pathlib import Path

import httpx
import pytest

from jobscraper import db
from jobscraper.config import AppConfig
from jobscraper.keys import KeyStore
from jobscraper.models import BUCHAREST_TZ, JobPosting, SearchQuery, WorkMode
from jobscraper.scrape import load_details, run_source
from jobscraper.sources import create_details_source, create_source, details_source_name
from jobscraper.sources.bestjobs import JOBS_API, BestJobsScraper
from jobscraper.sources.errors import ParseError
from jobscraper.sources.http import DomainRateLimiter, PoliteClient

FIXTURES = Path(__file__).parent / "fixtures" / "bestjobs"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=BUCHAREST_TZ)
LIST_URL = f"{JOBS_API}?limit=24&keyword=python+Cluj-Napoca"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def offline_scraper() -> BestJobsScraper:
    """Parsers do not touch the network."""
    return BestJobsScraper(http=None)


def listing_jobs() -> list[JobPosting]:
    return offline_scraper().parse_listing(fixture("api_jobs_python_cluj.json"), LIST_URL).jobs


# --- listing ------------------------------------------------------------------------


def test_listing_maps_cards():
    jobs = listing_jobs()
    assert len(jobs) == 8

    first = jobs[0]
    assert first.source == "bestjobs"
    assert first.external_id == "52070975"
    assert first.url == "https://www.bestjobs.eu/loc-de-munca/ai-prompt-specialist"
    assert (first.title, first.company, first.city) == ("AI Prompt Specialist", "PROHUMAN APT", "Cluj-Napoca, România")
    assert first.salary_text == "1795 - 1985 €/lună (estimare BestJobs)"
    assert first.salary_min is None  # an estimate is not the employer's salary
    assert first.description == ""
    assert details_source_name(first) == "bestjobs"


def test_listing_salary_and_remote():
    android = next(j for j in listing_jobs() if j.external_id == "52073369")
    assert android.work_mode is WorkMode.REMOTE
    assert (android.salary_min, android.salary_max, android.currency) == (4500, 6500, "EUR")
    assert android.salary_text == "4500 - 6500 €/lună"

    no_company = next(j for j in listing_jobs() if j.external_id == "52069292")
    assert no_company.company is None
    assert (no_company.salary_min, no_company.salary_max) == (5000, 5000)


def test_listing_shortens_long_location_lists():
    cad = next(j for j in listing_jobs() if j.external_id == "52073522")
    assert cad.city == "Cluj-Napoca, România; Tirgu Mures, România; Alba Iulia, România (+18)"


def test_listing_next_url_carries_cursor():
    page = offline_scraper().parse_listing(fixture("api_jobs_python_cluj.json"), LIST_URL)
    params = httpx.URL(page.next_url).params
    assert params["keyword"] == "python Cluj-Napoca"
    assert params["cursor"].startswith("eyJj")


def test_listing_without_cursor_is_last_page():
    page = offline_scraper().parse_listing('{"items": [], "nextCursor": ""}', LIST_URL)
    assert page.next_url is None and page.jobs == []


def test_listing_rejects_non_json():
    with pytest.raises(ParseError):
        offline_scraper().parse_listing("<html>blocked</html>", LIST_URL)


def test_search_url_uses_free_text_keyword():
    scraper = offline_scraper()
    url = httpx.URL(scraper.search_url(SearchQuery(keywords="python", city="Cluj-Napoca", work_mode=WorkMode.REMOTE)))
    assert str(url).startswith(JOBS_API)
    assert url.params["keyword"] == "python Cluj-Napoca remote"
    assert url.params["limit"] == "24"


# --- detail pages -------------------------------------------------------------------


def detail(name: str, base: JobPosting | None = None) -> JobPosting:
    base = base or JobPosting(source="bestjobs", external_id="1", url="https://www.bestjobs.eu/loc-de-munca/x", title="T")
    return offline_scraper().parse_detail(fixture(name), base, now=NOW)


def test_detail_on_site_job():
    job = detail("job_onsite.html")
    assert job.company == "SBA Total"
    assert job.city == "București, România"
    assert job.work_mode is WorkMode.ON_SITE
    assert job.employment_type == "Full time"
    assert (job.salary_text, job.salary_min, job.salary_max, job.currency) == ("1000 - 2000 €/luna", 1000, 2000, "EUR")
    assert job.requirements == ["Limbă: Română (Nativ)", "Limbă: Engleză (Avansat)"]
    assert job.description.startswith("Programatori Juniori / Entry-Level\nExperiență: 0–2 ani\n")
    assert job.details_fetched_at == NOW
    assert details_source_name(job) is None


def test_detail_remote_job_lists_responsibilities_as_lines():
    job = detail("job_remote.html")
    assert job.work_mode is WorkMode.REMOTE
    assert "\nResponsabilități\n- dezvoltarea aplicației Android" in job.description


def test_detail_hybrid_without_salary_keeps_listing_estimate():
    base = next(j for j in listing_jobs() if j.external_id == "52071767")  # has an estimate
    job = detail("job_hybrid.html", base)
    assert job.work_mode is WorkMode.HYBRID
    assert job.seniority == "Senior (5-10 ani)"
    assert job.company == "Flutter International"
    assert job.salary_text == base.salary_text
    assert job.description.startswith("Senior Data Engineer - Sportsbet, Hybrid\n")


def test_detail_page_without_data():
    with pytest.raises(ParseError):
        offline_scraper().parse_detail("<html><body>Anunț expirat</body></html>", listing_jobs()[0], now=NOW)


# --- through the polite client -------------------------------------------------------


def fake_site(requests: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=fixture_robots())
        if request.url.path == "/api/proxy/v2/jobs":
            if "cursor" in request.url.params:
                return httpx.Response(200, json={"items": [], "nextCursor": ""})
            return httpx.Response(200, text=fixture("api_jobs_python_cluj.json"))
        if request.url.path.startswith("/loc-de-munca/"):
            return httpx.Response(200, text=fixture("job_onsite.html"))
        return httpx.Response(404)

    return handler


def fixture_robots() -> str:
    return "User-agent: *\nDisallow:\n\nUser-agent: AhrefsBot\nDisallow: /\n"


def make_scraper(tmp_path, requests) -> BestJobsScraper:
    client = PoliteClient(
        user_agent="JobScraperPersonal/0.1",
        min_delay=2.5,
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(fake_site(requests)),
        limiter=DomainRateLimiter(clock=lambda: 0.0, sleep=lambda seconds: None),
    )
    return BestJobsScraper(client)


def test_fetch_jobs_pages_until_empty(tmp_path):
    requests: list[httpx.Request] = []
    jobs = make_scraper(tmp_path, requests).fetch_jobs(SearchQuery(keywords="python", city="Cluj-Napoca", max_pages=5))

    assert len(jobs) == 8
    assert [r.url.path for r in requests] == ["/robots.txt", "/api/proxy/v2/jobs", "/api/proxy/v2/jobs"]
    assert requests[1].headers["User-Agent"] == "JobScraperPersonal/0.1"


def test_details_on_demand_are_stored(tmp_path):
    requests: list[httpx.Request] = []
    scraper = make_scraper(tmp_path, requests)
    with db.open_db(tmp_path / "jobs.db") as conn:
        run_source(conn, scraper, SearchQuery(keywords="python", max_pages=1))
        job = db.get_job(conn, listing_jobs()[0].id)
        assert job.description == ""

        stored = load_details(conn, scraper, job)

        assert stored.company == "SBA Total"  # from the (fake) detail page
        assert stored.description.startswith("Programatori Juniori")
        assert details_source_name(stored) is None
    assert requests[-1].url.path == "/loc-de-munca/ai-prompt-specialist"


def test_factories(tmp_path):
    keys = KeyStore({}, environ={})  # BestJobs needs no key
    assert create_source("bestjobs", AppConfig(), keys).name == "bestjobs"
    assert create_details_source(listing_jobs()[0], AppConfig(), keys).name == "bestjobs"
