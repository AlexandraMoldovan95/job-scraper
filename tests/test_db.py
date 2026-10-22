import sqlite3
from datetime import datetime

import pytest

from jobscraper import db
from jobscraper.models import BUCHAREST_TZ, JobPosting, JobStatus, WorkMode


@pytest.fixture
def conn(tmp_path):
    with db.open_db(tmp_path / "jobs.db") as conn:
        yield conn


def make_job(n: int, **overrides) -> JobPosting:
    fields = {
        "source": "ejobs",
        "external_id": str(n),
        "url": f"https://www.ejobs.ro/job/{n}",
        "title": f"Python Developer {n}",
        "company": "Firma SRL",
        "city": "București",
        "posted_at": datetime(2026, 10, n, 9, 0, tzinfo=BUCHAREST_TZ),
    }
    return JobPosting(**(fields | overrides))


def test_round_trip_keeps_all_fields(conn):
    job = make_job(
        1,
        via="LinkedIn",
        work_mode=WorkMode.HYBRID,
        salary_min=5000,
        salary_max=7000,
        currency="RON",
        salary_is_net=True,
        requirements=["Python 3", "Cunoștințe SQL"],
        language="ro",
        salary_text="5.000 - 7.000 RON net",
        linkedin_job_id="3812345678",
        details_fetched_at=datetime(2026, 10, 18, 15, 30, tzinfo=BUCHAREST_TZ),
    )
    db.upsert_jobs(conn, [job])
    stored = db.get_job(conn, job.id)
    assert stored.first_seen_at == job.scraped_at  # filled by the database on first insert
    assert stored.model_copy(update={"first_seen_at": None}) == job


def test_upsert_counts_inserted_and_updated(conn):
    assert db.upsert_jobs(conn, [make_job(1), make_job(2)]) == db.UpsertResult(inserted=2, updated=0)
    assert db.upsert_jobs(conn, [make_job(2), make_job(3)]) == db.UpsertResult(inserted=1, updated=1)


def test_rescrape_updates_content_but_keeps_user_status(conn):
    db.upsert_jobs(conn, [make_job(1)])
    job_id = make_job(1).id
    assert db.set_status(conn, job_id, JobStatus.APPLIED)

    db.upsert_jobs(conn, [make_job(1, title="Python Developer (actualizat)")])

    stored = db.get_job(conn, job_id)
    assert stored.title == "Python Developer (actualizat)"
    assert stored.status is JobStatus.APPLIED


def test_rescrape_with_missing_fields_keeps_known_values(conn):
    rich = make_job(1, description="Descriere completă", requirements=["Python"], salary_text="6.000 RON")
    db.upsert_jobs(conn, [rich])

    db.upsert_jobs(conn, [make_job(1, title="Titlu nou")])  # no description, requirements or salary

    stored = db.get_job(conn, rich.id)
    assert stored.title == "Titlu nou"
    assert (stored.description, stored.requirements, stored.salary_text) == (
        "Descriere completă",
        ["Python"],
        "6.000 RON",
    )


def test_set_status_on_missing_job(conn):
    assert db.set_status(conn, "missing", JobStatus.SEEN) is False


def test_list_jobs_newest_first_undated_by_first_seen(conn):
    seen_on_4th = datetime(2026, 10, 4, 12, 0, tzinfo=BUCHAREST_TZ)
    db.upsert_jobs(conn, [make_job(1), make_job(5), make_job(3), make_job(9, posted_at=None, scraped_at=seen_on_4th)])
    titles = [job.title for job in db.list_jobs(conn)]
    assert titles == ["Python Developer 5", "Python Developer 9", "Python Developer 3", "Python Developer 1"]


def test_list_jobs_filters(conn):
    db.upsert_jobs(conn, [make_job(1), make_job(2, source="hipo", company="Alta"), make_job(3)])
    db.set_status(conn, make_job(3).id, JobStatus.REJECTED)

    assert {j.external_id for j in db.list_jobs(conn, sources=["hipo"])} == {"2"}
    assert {j.external_id for j in db.list_jobs(conn, statuses=[JobStatus.NEW])} == {"1", "2"}
    assert {j.external_id for j in db.list_jobs(conn, text="alta")} == {"2"}
    assert db.list_jobs(conn, statuses=[]) == []


def test_stored_sources(conn):
    db.upsert_jobs(conn, [make_job(1), make_job(2, source="linkedin"), make_job(3)])
    assert db.stored_sources(conn) == ["ejobs", "linkedin"]


def test_count_by_status(conn):
    db.upsert_jobs(conn, [make_job(1), make_job(2)])
    db.set_status(conn, make_job(2).id, JobStatus.SAVED)
    assert db.count_by_status(conn) == {JobStatus.NEW: 1, JobStatus.SAVED: 1}


def test_scrape_runs(conn):
    run_id = db.start_run(conn, "ejobs", "python")
    db.finish_run(conn, run_id, jobs_found=10, jobs_new=4)
    failed_id = db.start_run(conn, "olx")
    db.finish_run(conn, failed_id, error="HTTP 403")

    latest, first = db.recent_runs(conn)
    assert (latest.source, latest.error) == ("olx", "HTTP 403")
    assert (first.jobs_found, first.jobs_new, first.finished_at is not None) == (10, 4, True)


def test_open_db_rolls_back_on_error(tmp_path):
    path = tmp_path / "jobs.db"
    with pytest.raises(RuntimeError):
        with db.open_db(path) as conn:
            db.upsert_jobs(conn, [make_job(1)])
            raise RuntimeError("boom")
    with db.open_db(path) as conn:
        assert db.list_jobs(conn) == []


def test_copy_on_another_board_inherits_status_and_status_changes_sync(conn):
    bestjobs = make_job(1, source="bestjobs", city="Șoseaua Pipera 42, București")
    db.upsert_jobs(conn, [bestjobs])
    db.set_status(conn, bestjobs.id, JobStatus.APPLIED)

    hipo = make_job(1, source="hipo", city="BUCURESTI")
    other = make_job(2, source="hipo")  # different title: not a copy
    db.upsert_jobs(conn, [hipo, other])
    assert db.get_job(conn, hipo.id).status is JobStatus.APPLIED  # does not come back as "new"
    assert db.get_job(conn, other.id).status is JobStatus.NEW

    assert db.set_status(conn, hipo.id, JobStatus.REJECTED)
    assert db.get_job(conn, bestjobs.id).status is JobStatus.REJECTED
    assert db.get_job(conn, other.id).status is JobStatus.NEW
    assert [j.source for j in db.duplicates_of(conn, bestjobs.id)] == ["hipo"]
    assert db.duplicates_of(conn, other.id) == []


def test_jobs_without_company_are_never_linked(conn):
    a, b = make_job(1, company=None), make_job(1, company=None, source="hipo")
    db.upsert_jobs(conn, [a, b])
    db.set_status(conn, a.id, JobStatus.SAVED)
    assert db.get_job(conn, b.id).status is JobStatus.NEW
    assert db.duplicates_of(conn, a.id) == []


def test_v4_database_gets_dedup_keys(tmp_path):
    path = tmp_path / "jobs.db"
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row
    for step in db._MIGRATIONS[:4]:
        old.executescript(step)
    old.execute("PRAGMA user_version = 4")
    old.execute(
        "INSERT INTO jobs (id, source, url, title, company, city, scraped_at, first_seen_at, status)"
        " VALUES ('x', 'bestjobs', 'https://b/1', 'Python Developer', 'Firma SRL', 'București',"
        " '2026-10-01T10:00:00+03:00', '2026-10-01T10:00:00+03:00', 'saved')"
    )
    old.commit()
    old.close()

    with db.open_db(path) as conn:
        (key,) = conn.execute("SELECT dedup_key FROM jobs WHERE id = 'x'").fetchone()
        assert key == "python developer|firma|bucuresti"
        copy = make_job(9, title="Python Developer", company="FIRMA S.R.L.", city="BUCURESTI", source="hipo")
        db.upsert_jobs(conn, [copy])
        assert db.get_job(conn, copy.id).status is JobStatus.SAVED


CV_JSON = (
    '{"language": "ro", "headline": "Dev", "summary": "Rezumat.", "skills": [], "experience": [], '
    '"projects": [], "education_indices": [], "course_indices": [], "gaps": []}'
)


def test_v5_database_keeps_its_cvs_and_allows_cvs_without_a_job(tmp_path):
    path = tmp_path / "jobs.db"
    with db.open_db(path) as conn:
        pass
    old = sqlite3.connect(path)
    old.executescript("DROP TABLE cvs;" + db._MIGRATIONS[3])  # the v5 cvs table
    old.execute("PRAGMA user_version = 5")
    old.execute(
        "INSERT INTO cvs (job_id, created_at, updated_at, model, cv_json) VALUES ('x', '2026-10-01', '2026-10-01', 'm', ?)",
        (CV_JSON,),
    )
    old.commit()
    old.close()

    with db.open_db(path) as conn:
        stored = db.latest_cv(conn, "x")
        assert (stored.job_id, stored.focus, stored.cv.headline) == ("x", None, "Dev")
        db.add_cv(conn, None, stored.cv, "m", focus="Testare / QA")
        db.add_cv(conn, None, stored.cv, "m", focus="Dezvoltare software")
        db.add_cv(conn, "x", stored.cv, "m", focus="Testare / QA")  # an ad with a focus is not a focus CV
        assert db.cv_focuses(conn) == ["Dezvoltare software", "Testare / QA"]
        assert db.latest_focus_cv(conn, "Testare / QA").job_id is None
        assert db.latest_cv(conn, "x").focus == "Testare / QA"
        with pytest.raises(ValueError):
            db.add_cv(conn, None, stored.cv, "m")


def test_schema_version_is_set(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_v1_database_is_migrated_and_keeps_rows(tmp_path):
    path = tmp_path / "jobs.db"
    old = sqlite3.connect(path)
    old.executescript(db._MIGRATIONS[0])
    old.execute("PRAGMA user_version = 1")
    old.execute(
        "INSERT INTO jobs (id, source, url, title, scraped_at, first_seen_at, status)"
        " VALUES ('x', 'ejobs', 'https://e/1', 'Vechi', '2026-10-01T10:00:00+03:00', '2026-10-01T10:00:00+03:00', 'saved')"
    )
    old.commit()
    old.close()

    with db.open_db(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        (job,) = db.list_jobs(conn)
        assert (job.title, job.status, job.via) == ("Vechi", JobStatus.SAVED, None)
