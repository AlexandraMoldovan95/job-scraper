from datetime import datetime, timedelta

from jobscraper.filters import JobFilter, SortOrder, filter_jobs, sort_jobs
from jobscraper.models import BUCHAREST_TZ, JobPosting, WorkMode

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=BUCHAREST_TZ)


def job(n: int, **fields) -> JobPosting:
    base = {"source": "bestjobs", "external_id": str(n), "url": f"https://x/{n}", "title": f"Job {n}"}
    return JobPosting(**(base | fields))


JOBS = [
    job(1, title="Python Developer", company="Firma București", city="București", work_mode=WorkMode.HYBRID,
        salary_max=3000, posted_at=NOW - timedelta(days=1)),
    job(2, title="Java Developer", company="Alta", city="Cluj-Napoca", work_mode=WorkMode.REMOTE,
        description="Lucrăm și cu Python", first_seen_at=NOW - timedelta(days=10)),
    job(3, title="Contabil", company=None, city="Iași", salary_max=5000, posted_at=NOW - timedelta(days=40)),
]


def ids(jobs: list[JobPosting]) -> list[str]:
    return [j.external_id for j in jobs]


def test_no_filter_keeps_everything():
    assert ids(filter_jobs(JOBS, JobFilter(), now=NOW)) == ["1", "2", "3"]


def test_text_ignores_case_and_diacritics_and_searches_description():
    assert ids(filter_jobs(JOBS, JobFilter(text="PYTHON"), now=NOW)) == ["1", "2"]
    assert ids(filter_jobs(JOBS, JobFilter(text="python bucuresti"), now=NOW)) == ["1"]
    assert ids(filter_jobs(JOBS, JobFilter(text="iasi"), now=NOW)) == ["3"]


def test_work_modes():
    remote_or_hybrid = frozenset({WorkMode.REMOTE, WorkMode.HYBRID})
    assert ids(filter_jobs(JOBS, JobFilter(work_modes=remote_or_hybrid), now=NOW)) == ["1", "2"]


def test_with_salary():
    assert ids(filter_jobs(JOBS, JobFilter(with_salary=True), now=NOW)) == ["1", "3"]


def test_age_uses_first_seen_when_there_is_no_posting_date():
    assert ids(filter_jobs(JOBS, JobFilter(max_age_days=7), now=NOW)) == ["1"]
    assert ids(filter_jobs(JOBS, JobFilter(max_age_days=30), now=NOW)) == ["1", "2"]


def test_sorting():
    assert ids(sort_jobs(JOBS, SortOrder.SALARY)) == ["3", "1", "2"]
    assert ids(sort_jobs(JOBS, SortOrder.COMPANY)) == ["2", "1", "3"]  # no company last
    assert ids(sort_jobs(JOBS, SortOrder.NEWEST)) == ["1", "2", "3"]  # database order kept
