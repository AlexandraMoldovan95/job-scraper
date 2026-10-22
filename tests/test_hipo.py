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
from jobscraper.sources.errors import ParseError
from jobscraper.sources.hipo import BASE_URL, HipoScraper
from jobscraper.sources.http import DomainRateLimiter, PoliteClient

FIXTURES = Path(__file__).parent / "fixtures" / "hipo"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=BUCHAREST_TZ)
PAGE1_URL = f"{BASE_URL}/locuri-de-munca/cautajob/Toate-Domeniile/Toate-Orasele/python"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def offline() -> HipoScraper:
    return HipoScraper(http=None)


def page1() -> list[JobPosting]:
    return offline().parse_listing(fixture("search_python_page1.html"), PAGE1_URL).jobs


# --- search URL -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "path"),
    [
        (SearchQuery(keywords="python"), "Toate-Orasele/python"),
        (SearchQuery(keywords="Python Developer", city="Cluj-Napoca"), "Cluj-Napoca/python-developer"),
        # "remote" is not a search word: Hipo would match every remote job
        (SearchQuery(keywords="data analyst", city="București", work_mode=WorkMode.REMOTE), "BUCURESTI/data-analyst"),
        (SearchQuery(keywords="c# developer", city="Iași"), "Iasi/c-developer"),
        (SearchQuery(keywords="tester", city="cluj"), "Cluj-Napoca/tester"),
    ],
)
def test_search_url(query, path):
    assert offline().search_url(query) == f"{BASE_URL}/locuri-de-munca/cautajob/Toate-Domeniile/{path}"


# --- listing ----------------------------------------------------------------------------------


def test_listing_cards():
    jobs = page1()
    assert len(jobs) == 8  # the ad slot between cards is skipped
    first = jobs[0]
    assert first.source == "hipo" and first.external_id == "264680"
    assert first.url == f"{BASE_URL}/locuri-de-munca/locuri_de_munca/264680/SCOR/Python-Full-Stack-Developer"
    assert (first.title, first.company, first.city) == ("Python Full-Stack Developer", "SCOR", "Bucuresti")
    assert first.posted_at == datetime(2026, 10, 19, tzinfo=BUCHAREST_TZ)
    assert first.description == "" and details_source_name(first) == "hipo"


def test_listing_location_can_be_a_work_mode():
    levi9 = next(j for j in page1() if j.external_id == "269206")
    assert levi9.city is None and levi9.work_mode is WorkMode.HYBRID
    bosch = next(j for j in page1() if j.external_id == "273067")
    assert bosch.city == "Cluj Napoca" and bosch.work_mode is None


def test_next_page():
    assert offline().parse_listing(fixture("search_python_page1.html"), PAGE1_URL).next_url == f"{PAGE1_URL}/2"
    # on page 2 the next one is 3; the fixture links to 2, 3 and 4
    assert offline().parse_listing(fixture("search_python_page1.html"), f"{PAGE1_URL}/2").next_url == f"{PAGE1_URL}/3"
    assert offline().parse_listing(fixture("search_python_page1.html"), f"{PAGE1_URL}/4").next_url is None
    cluj = offline().parse_listing(fixture("search_python_cluj.html"), PAGE1_URL.replace("Toate-Orasele", "Cluj-Napoca"))
    assert cluj.next_url is None and len(cluj.jobs) == 9


# --- detail -------------------------------------------------------------------------------------


def test_detail_from_table_json_ld_and_ad():
    job = offline().parse_detail(fixture("job_scor.html"), page1()[0], now=NOW)
    assert (job.company, job.city, job.work_mode) == ("SCOR", "Bucuresti", WorkMode.ON_SITE)
    assert (job.employment_type, job.seniority) == ("full-time", "1 - 5 ani experienta")
    assert job.posted_at == datetime(2026, 10, 19, tzinfo=BUCHAREST_TZ)
    assert job.description.startswith("Job Summary\nWe are seeking a seasoned, digital mindset development engineer")
    assert "- Design and implement scalable, business features" in job.description
    assert job.details_fetched_at == NOW and details_source_name(job) is None


def test_detail_trusts_the_table_over_the_title():
    bosch = next(j for j in page1() if j.external_id == "271798")  # "... - Remote" in the title
    job = offline().parse_detail(fixture("job_bosch.html"), bosch, now=NOW)
    assert job.work_mode is WorkMode.ON_SITE  # the details table says On-site
    assert job.city == "Timisoara" and job.company == "Bosch Romania"
    assert job.description.startswith("Company Description\n")


def test_detail_in_the_newer_layout_with_sections_and_salary():
    # No #anunt-content: the ad is in titled sections, the salary only in the JSON-LD
    listed = JobPosting(
        source="hipo", external_id="271621", title="Graphic Designer", company="Aleph News", city="Bucuresti",
        url=f"{BASE_URL}/locuri-de-munca/locuri_de_munca/271621/Aleph-News/Graphic-Designer",
    )
    job = offline().parse_detail(fixture("job_structured.html"), listed, now=NOW)
    assert job.description.startswith("Scurta descriere a companiei\nAleph News este o televiziune")
    assert "Cerinte\n- Cunoștințe avansate de Adobe Photoshop & Illustrator" in job.description
    assert "Responsabilitati\nCrearea de asseturi vizuale" in job.description
    assert (job.company, job.city, job.work_mode) == ("Aleph News", "Bucuresti", WorkMode.ON_SITE)  # "Orase:" row
    assert (job.employment_type, job.seniority) == ("full-time", "1 - 5 ani experienta")
    assert (job.salary_min, job.salary_max, job.currency, job.salary_text) == (4000, 6000, "RON", "4000 - 6000 RON/lună")
    assert job.posted_at == datetime(2026, 10, 22, tzinfo=BUCHAREST_TZ)
    assert job.details_fetched_at == NOW


def test_detail_without_ad():
    with pytest.raises(ParseError):
        offline().parse_detail("<html><body>Anunt expirat</body></html>", page1()[0], now=NOW)


# --- through the polite client ---------------------------------------------------------------------


def make_scraper(tmp_path, requests: list[httpx.Request], *, detail_status: int = 200) -> HipoScraper:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /public/prvfiles/\nDisallow: /angajator/\n")
        if "/cautajob/" in path:  # any keywords get the same results
            page_two = path.endswith("/2")
            return httpx.Response(200, text=fixture("search_python_cluj.html" if page_two else "search_python_page1.html"))
        if "/locuri_de_munca/" in path:
            return httpx.Response(detail_status, text=fixture("job_scor.html"))
        return httpx.Response(404)

    client = PoliteClient(
        user_agent="JobScraperPersonal/0.1",
        min_delay=2.5,
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        limiter=DomainRateLimiter(clock=lambda: 0.0, sleep=lambda seconds: None),
    )
    return HipoScraper(client)


def test_fetch_follows_pages_and_details_are_stored(tmp_path):
    requests: list[httpx.Request] = []
    scraper = make_scraper(tmp_path, requests)
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, scraper, SearchQuery(keywords="python", max_pages=5))
        assert (outcome.found, outcome.error) == (15, None)  # 17 cards, 2 of them on both pages
        # Unique ids, for "Vezi rezultatele": the two fixture pages share 2 jobs
        assert len(outcome.job_ids) == 15 and outcome.job_ids[0] == page1()[0].id
        assert [r.url.path.rsplit("/", 1)[-1] for r in requests] == ["robots.txt", "python", "2"]

        job = db.get_job(conn, page1()[0].id)
        stored = load_details(conn, scraper, job)
        assert stored.seniority == "1 - 5 ani experienta" and stored.description


def detail_requests(requests: list[httpx.Request]) -> int:
    return sum("/locuri_de_munca/" in r.url.path for r in requests)


def test_search_can_load_the_full_ads_of_the_jobs_found(tmp_path):
    requests: list[httpx.Request] = []
    scraper = make_scraper(tmp_path, requests)
    progress: list[tuple[int, int]] = []
    query = SearchQuery(keywords="python", max_pages=1)
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, scraper, query, details=scraper, max_details=3, on_progress=lambda *p: progress.append(p))
        assert (outcome.described, outcome.details_failed) == (3, 0)
        assert detail_requests(requests) == 3  # capped by max_details, in listing order
        assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
        described = [job for job in db.list_jobs(conn) if job.details_fetched_at is not None]
        assert {job.id for job in described} == {job.id for job in page1()[:3]}
        assert all(job.description for job in described)

        again = run_source(conn, scraper, query, details=scraper, max_details=5)
        assert again.described == 5 and detail_requests(requests) == 8  # the first 3 are not fetched again


def test_a_scraper_search_whose_full_ads_never_mention_the_words_is_undone(tmp_path):
    # Judged on the full ads, not the titles: the site matches the ad text too
    requests: list[httpx.Request] = []
    scraper = make_scraper(tmp_path, requests)
    with db.open_db(tmp_path / "jobs.db") as conn:
        known = page1()[0]
        db.upsert_jobs(conn, [known])  # stored before this search: it stays
        outcome = run_source(conn, scraper, SearchQuery(keywords="ospatar", max_pages=1), details=scraper, max_details=50)
        assert (outcome.found, outcome.new, outcome.job_ids) == (0, 0, ())
        assert outcome.unrelated == len(page1())
        assert [job.id for job in db.list_jobs(conn)] == [known.id]
        assert db.recent_runs(conn)[0].jobs_found == 0


def test_a_scraper_search_is_kept_when_the_full_ads_mention_the_words(tmp_path):
    requests: list[httpx.Request] = []
    scraper = make_scraper(tmp_path, requests)
    with db.open_db(tmp_path / "jobs.db") as conn:
        # "kubernetes" is in no title, but the SCOR ad (the fixture of every detail page) has it
        outcome = run_source(conn, scraper, SearchQuery(keywords="kubernetes", max_pages=1), details=scraper, max_details=50)
        assert outcome.found == len(page1()) and outcome.unrelated == 0
        # without full ads there is nothing to judge a scraper on: everything is kept
        again = run_source(conn, make_scraper(tmp_path, []), SearchQuery(keywords="ospatar", max_pages=1))
        assert again.found == len(page1())


def test_a_blocked_detail_page_stops_loading_the_others(tmp_path):
    requests: list[httpx.Request] = []
    scraper = make_scraper(tmp_path, requests, detail_status=403)
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, scraper, SearchQuery(keywords="python", max_pages=1), details=scraper, max_details=4)
    assert outcome.error is None and outcome.found > 0  # the search itself is kept
    assert (outcome.described, outcome.details_failed) == (0, 4)
    assert detail_requests(requests) == 1


def test_factories():
    keys = KeyStore({}, environ={})
    assert create_source("hipo", AppConfig(), keys).name == "hipo"
    assert create_details_source(page1()[0], AppConfig(), keys).name == "hipo"
