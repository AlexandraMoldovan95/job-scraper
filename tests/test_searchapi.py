import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobscraper import db
from jobscraper.config import AppConfig
from jobscraper.keys import SEARCHAPI_API_KEY, KeyStore
from jobscraper.models import BUCHAREST_TZ, JobPosting, SearchQuery, WorkMode
from jobscraper.scrape import run_source
from jobscraper.sources import SourceUnavailable, create_source
from jobscraper.sources.searchapi import (
    SEARCH_URL,
    GoogleJobsSource,
    SearchApiClient,
    SearchApiError,
    parse_google_jobs,
)

FIXTURES = Path(__file__).parent / "fixtures" / "searchapi"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=BUCHAREST_TZ)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def page_two() -> dict:
    return {"jobs": [load_fixture("google_jobs_page1.json")["jobs"][1] | {"title": "Python, pagina 2"}]}


def make_client(handler) -> SearchApiClient:
    return SearchApiClient("test-key-123", transport=httpx.MockTransport(handler))


# --- parse_google_jobs --------------------------------------------------------


def test_parse_maps_fields_and_skips_jobs_without_title():
    first, second = parse_google_jobs(load_fixture("google_jobs_page1.json"), now=NOW)

    assert first.source == "google_jobs"
    assert first.title == "Python Developer"
    assert first.company == "Exemplu Software SRL"
    assert first.city == "București, România"  # cedilla fixed
    assert first.via == "LinkedIn"
    assert first.url == "https://www.linkedin.com/jobs/view/python-developer-at-exemplu-1111111111?utm_source=google_jobs_apply"
    assert first.external_id == "AbCdEf123=="
    assert first.linkedin_job_id == "1111111111"
    assert first.employment_type == "Normă întreagă"
    assert first.salary_text == "5.000–7.000 RON pe lună"
    assert first.requirements == ["Minimum 1 an experiență cu Python", "Cunoștințe SQL"]
    assert first.posted_at == NOW - timedelta(days=2)
    assert first.work_mode is None
    assert "Django" in first.description


def test_parse_second_job_remote_and_url_from_apply_links():
    _, second = parse_google_jobs(load_fixture("google_jobs_page1.json"), now=NOW)
    assert second.work_mode is WorkMode.REMOTE
    assert second.via == "BestJobs"
    assert second.url == "https://www.bestjobs.eu/loc-de-munca/junior-backend-developer-python"
    assert second.external_id == "XyZ789"
    assert second.linkedin_job_id is None
    assert second.requirements == ["Python", "Git"]
    assert second.posted_at == NOW - timedelta(days=1)


def test_linkedin_id_found_in_any_apply_link():
    raw = load_fixture("google_jobs_page1.json")["jobs"][1]
    raw["apply_links"].append({"link": "https://ro.linkedin.com/jobs/view/4470016722/", "source": "LinkedIn"})
    (job,) = parse_google_jobs({"jobs": [raw]}, now=NOW)
    assert job.url.startswith("https://www.bestjobs.eu/")
    assert job.linkedin_job_id == "4470016722"


def test_parse_empty_response():
    assert parse_google_jobs({}, now=NOW) == []


# --- HTTP ---------------------------------------------------------------------


def test_fetch_follows_pagination_and_sends_key_in_header():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "next_page_token" in request.url.params:
            return httpx.Response(200, json=page_two())
        return httpx.Response(200, json=load_fixture("google_jobs_page1.json"))

    source = GoogleJobsSource(make_client(handler))
    jobs = source.fetch_jobs(SearchQuery(keywords="python developer", city="Cluj", max_pages=5))

    assert [j.title for j in jobs] == ["Python Developer", "Junior Backend Developer (Python)", "Python, pagina 2"]
    assert len(requests) == 2  # page 2 has no next_page_token
    first = requests[0]
    assert str(first.url).startswith(SEARCH_URL)
    assert first.url.params["engine"] == "google_jobs"
    assert first.url.params["q"] == "python developer Cluj"
    assert first.url.params["gl"] == "ro"
    assert first.headers["Authorization"] == "Bearer test-key-123"
    assert "test-key-123" not in str(first.url)
    assert requests[1].url.params["next_page_token"] == "eyJmYyI6InBhZ2UyIn0="


def test_remote_is_added_to_query():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"jobs": []})

    GoogleJobsSource(make_client(handler)).fetch_jobs(SearchQuery(keywords="python", work_mode=WorkMode.REMOTE))
    assert requests[0].url.params["q"] == "python remote"


def test_fetch_stops_at_max_pages():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=load_fixture("google_jobs_page1.json"))

    GoogleJobsSource(make_client(handler)).fetch_jobs(SearchQuery(keywords="python", max_pages=1))
    assert calls == 1


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": "Invalid API key"}, "401"),
        (429, {"error": "Limit reached"}, "429"),
        (400, {"error": "Missing q"}, "Missing q"),
        (502, None, "502"),
    ],
)
def test_http_errors(status, body, expected):
    def handler(request):
        return httpx.Response(status, json=body) if body else httpx.Response(status)

    with pytest.raises(SearchApiError, match=expected) as info:
        make_client(handler).search({"engine": "google_jobs", "q": "python"})
    assert "test-key-123" not in str(info.value)


# --- create_source / run_source -------------------------------------------------


@pytest.mark.parametrize("name", ["google_jobs", "linkedin"])
def test_searchapi_sources_need_key(tmp_path, name):
    keys = KeyStore({}, env_path=tmp_path / ".env", environ={})
    with pytest.raises(SourceUnavailable):
        create_source(name, AppConfig(), keys)
    keys.set_for_session(SEARCHAPI_API_KEY, "abc")
    assert create_source(name, AppConfig(), keys).name == name


def test_create_unknown_source():
    with pytest.raises(SourceUnavailable):
        create_source("ejobs", AppConfig(), KeyStore({}, environ={}))


def test_run_source_stores_jobs_and_records_run(tmp_path):
    source = GoogleJobsSource(make_client(lambda request: httpx.Response(200, json=page_two())))
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, source, SearchQuery(keywords="python"))
        again = run_source(conn, source, SearchQuery(keywords="python"))

        assert (outcome.found, outcome.new, outcome.error) == (1, 1, None)
        assert (again.found, again.new) == (1, 0)
        assert [j.title for j in db.list_jobs(conn)] == ["Python, pagina 2"]
        assert db.recent_runs(conn)[0].jobs_found == 1


def test_general_jobs_returned_for_a_search_with_no_match_are_not_stored(tmp_path):
    # LinkedIn answers "ospatar" with whatever jobs it has; none of them is a waiter job
    source = GoogleJobsSource(make_client(lambda request: httpx.Response(200, json=load_fixture("google_jobs_page1.json"))))
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, source, SearchQuery(keywords="ospatar"))
        assert (outcome.found, outcome.new, outcome.unrelated, outcome.job_ids) == (0, 0, 2, ())
        assert db.list_jobs(conn) == [] and db.recent_runs(conn)[0].jobs_found == 0


class ListSource:
    """A source that returns the given jobs for any query."""

    name = "linkedin"

    def __init__(self, jobs: list[JobPosting]):
        self.jobs, self.queries = jobs, []

    def fetch_jobs(self, query: SearchQuery) -> list[JobPosting]:
        self.queries.append(query)
        return self.jobs


def test_remote_is_a_filter_not_a_search_word(tmp_path):
    jobs = [
        JobPosting(source="linkedin", external_id=str(n), url=f"https://x/{n}", title=f"Python Developer {n}", work_mode=mode)
        for n, mode in enumerate([WorkMode.REMOTE, WorkMode.HYBRID, WorkMode.ON_SITE, None])
    ]
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, ListSource(jobs), SearchQuery(keywords="python", work_mode=WorkMode.REMOTE))
        # known non-remote jobs go; an unknown mode stays (the preferences decide once it is known)
        assert sorted(job.title for job in db.list_jobs(conn)) == ["Python Developer 0", "Python Developer 3"]
        assert outcome.found == 2


def test_run_source_records_errors(tmp_path):
    source = GoogleJobsSource(make_client(lambda request: httpx.Response(401)))
    with db.open_db(tmp_path / "jobs.db") as conn:
        outcome = run_source(conn, source, SearchQuery(keywords="python"))
        assert "401" in outcome.error
        assert "401" in db.recent_runs(conn)[0].error
