import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobscraper import db
from jobscraper.models import BUCHAREST_TZ, JobPosting, SearchQuery, WorkMode
from jobscraper.scrape import load_details, run_source
from jobscraper.sources import details_source_name
from jobscraper.sources.linkedin import LinkedInSource, apply_linkedin_details, parse_linkedin_jobs
from jobscraper.sources.searchapi import GoogleJobsSource, SearchApiClient, SearchApiError, parse_google_jobs

FIXTURES = Path(__file__).parent / "fixtures" / "searchapi"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=BUCHAREST_TZ)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(handler) -> SearchApiClient:
    return SearchApiClient("test-key-123", transport=httpx.MockTransport(handler))


def fixture_server(requests: list[httpx.Request]):
    """Answers each engine with its fixture; LinkedIn page 2 has no next page."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        engine = request.url.params["engine"]
        if engine == "linkedin_job_details":
            return httpx.Response(200, json=load_fixture("linkedin_job_details.json"))
        if engine == "linkedin_jobs":
            data = load_fixture("linkedin_jobs_page1.json")
            if request.url.params.get("page") != "1":
                data = {"jobs": data["jobs"][1:2], "pagination": {"current": 2}}
            return httpx.Response(200, json=data)
        return httpx.Response(200, json=load_fixture("google_jobs_page1.json"))

    return handler


# --- parsing ------------------------------------------------------------------------


def test_parse_list():
    first, second = parse_linkedin_jobs(load_fixture("linkedin_jobs_page1.json"), now=NOW)

    assert first.source == "linkedin"
    assert (first.external_id, first.linkedin_job_id) == ("1111111111", "1111111111")
    assert first.url == "https://ro.linkedin.com/jobs/view/python-developer-at-exemplu-1111111111"
    assert first.via == "LinkedIn"
    assert first.city == "București, România (Hibrid)"
    assert first.work_mode is WorkMode.HYBRID
    assert first.posted_at == datetime(2026, 10, 17, tzinfo=BUCHAREST_TZ)
    assert first.description == ""
    assert details_source_name(first) == "linkedin"

    assert second.posted_at == NOW - timedelta(hours=5)  # no `date`, relative text used


def test_apply_details():
    (job,) = parse_linkedin_jobs({"jobs": load_fixture("linkedin_jobs_page1.json")["jobs"][:1]}, now=NOW)
    enriched = apply_linkedin_details(job, load_fixture("linkedin_job_details.json")["job"], now=NOW)

    assert enriched.id == job.id
    assert enriched.description.startswith("Despre rol\nConstruim servicii în Python și Django.")
    assert "Cerințe" in enriched.description
    assert enriched.salary_text == "RON 6,000.00/month - RON 8,000.00/month"
    assert enriched.seniority == "Entry level"
    assert enriched.employment_type == "Full-time"
    assert enriched.details_fetched_at == NOW
    assert details_source_name(enriched) is None


# --- HTTP ---------------------------------------------------------------------------


def test_fetch_jobs_params_and_pagination():
    requests: list[httpx.Request] = []
    source = LinkedInSource(make_client(fixture_server(requests)), time_period="last_week")

    jobs = source.fetch_jobs(SearchQuery(keywords="python developer", city="Cluj", max_pages=5))

    assert len(requests) == 2
    params = requests[0].url.params
    assert params["engine"] == "linkedin_jobs"
    assert params["q"] == "python developer"
    assert params["location"] == "Cluj, Romania"
    assert params["time_period"] == "last_week"
    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    assert requests[0].headers["Authorization"] == "Bearer test-key-123"
    assert [j.linkedin_job_id for j in jobs] == ["1111111111", "3333333333", "3333333333"]


def test_fetch_jobs_without_city_or_time_period():
    requests: list[httpx.Request] = []
    LinkedInSource(make_client(fixture_server(requests))).fetch_jobs(SearchQuery(keywords="python", max_pages=1))
    assert requests[0].url.params["location"] == "Romania"
    assert "time_period" not in requests[0].url.params


def test_fetch_details_request():
    requests: list[httpx.Request] = []
    source = LinkedInSource(make_client(fixture_server(requests)))
    job = JobPosting(source="linkedin", url="https://x", title="Python Developer", linkedin_job_id="1111111111")

    enriched = source.fetch_details(job)

    assert requests[0].url.params["engine"] == "linkedin_job_details"
    assert requests[0].url.params["job_id"] == "1111111111"
    assert enriched.details_fetched_at is not None


def test_fetch_details_without_job_in_response():
    source = LinkedInSource(make_client(lambda request: httpx.Response(200, json={"search_metadata": {}})))
    job = JobPosting(source="linkedin", url="https://x", title="T", linkedin_job_id="1")
    with pytest.raises(SearchApiError):
        source.fetch_details(job)


# --- dedup with Google Jobs ---------------------------------------------------------


def test_same_linkedin_ad_from_both_sources_is_one_row(tmp_path):
    requests: list[httpx.Request] = []
    client = make_client(fixture_server(requests))
    google, linkedin = GoogleJobsSource(client), LinkedInSource(client)
    query = SearchQuery(keywords="python developer", max_pages=1)

    with db.open_db(tmp_path / "jobs.db") as conn:
        run_source(conn, google, query)  # 2 jobs, one of them links to LinkedIn ad 1111111111
        outcome = run_source(conn, linkedin, query)  # 2 jobs, 1111111111 is already stored

        assert (outcome.found, outcome.new) == (2, 1)
        jobs = db.list_jobs(conn)
        assert len(jobs) == 3
        merged = next(j for j in jobs if j.linkedin_job_id == "1111111111")
        assert "Django" in merged.description  # kept from Google Jobs: the LinkedIn list has none


def test_details_survive_a_later_list_scrape(tmp_path):
    requests: list[httpx.Request] = []
    linkedin = LinkedInSource(make_client(fixture_server(requests)))
    query = SearchQuery(keywords="python developer", max_pages=1)

    with db.open_db(tmp_path / "jobs.db") as conn:
        run_source(conn, linkedin, query)
        job = next(j for j in db.list_jobs(conn) if j.linkedin_job_id == "1111111111")

        stored = load_details(conn, linkedin, job)
        assert stored.salary_text and stored.description and stored.details_fetched_at

        run_source(conn, linkedin, query)  # list entries come without description or salary
        again = db.get_job(conn, job.id)
        assert again.description == stored.description
        assert again.salary_text == stored.salary_text
        assert again.details_fetched_at == stored.details_fetched_at
