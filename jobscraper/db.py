"""SQLite storage for job postings and scrape runs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from jobscraper.dedupe import dedup_key
from jobscraper.models import JobPosting, JobStatus, TailoredCV, now_local

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    external_id       TEXT,
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    company           TEXT,
    city              TEXT,
    work_mode         TEXT,
    salary_min        INTEGER,
    salary_max        INTEGER,
    currency          TEXT,
    salary_is_net     INTEGER,
    employment_type   TEXT,
    seniority         TEXT,
    description       TEXT NOT NULL DEFAULT '',
    requirements      TEXT NOT NULL DEFAULT '[]',  -- JSON list of strings
    language          TEXT,
    posted_at         TEXT,                        -- ISO 8601 with UTC offset
    scraped_at        TEXT NOT NULL,
    first_seen_at     TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'new',
    status_updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    query       TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    jobs_found  INTEGER NOT NULL DEFAULT 0,
    jobs_new    INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
"""

# Append-only: never edit a migration that has shipped; add a new one instead.
_MIGRATIONS = (
    _SCHEMA_V1,
    "ALTER TABLE jobs ADD COLUMN via TEXT;",
    """
    ALTER TABLE jobs ADD COLUMN salary_text TEXT;
    ALTER TABLE jobs ADD COLUMN linkedin_job_id TEXT;
    ALTER TABLE jobs ADD COLUMN details_fetched_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_jobs_linkedin_job_id ON jobs(linkedin_job_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS cvs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        model      TEXT,
        cv_json    TEXT NOT NULL  -- TailoredCV
    );
    CREATE INDEX IF NOT EXISTS idx_cvs_job_id ON cvs(job_id);
    """,
    # Python step (late-bound, the function is defined below): add the column and fill it.
    # If dedup_key() changes, add a migration that calls refresh_dedup_keys() again.
    lambda conn: _add_dedup_keys(conn),
    # CVs for a type of job (focus) have no ad: rebuild the table so job_id may be NULL
    """
    ALTER TABLE cvs RENAME TO cvs_v5;
    CREATE TABLE cvs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id     TEXT,          -- the ad the CV is tailored to, or NULL
        focus      TEXT,          -- type of job to emphasize ("Testare / QA"), or NULL
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        model      TEXT,
        cv_json    TEXT NOT NULL  -- TailoredCV
    );
    INSERT INTO cvs (id, job_id, created_at, updated_at, model, cv_json)
        SELECT id, job_id, created_at, updated_at, model, cv_json FROM cvs_v5;
    DROP TABLE cvs_v5;
    CREATE INDEX IF NOT EXISTS idx_cvs_job_id ON cvs(job_id);
    CREATE INDEX IF NOT EXISTS idx_cvs_focus ON cvs(focus);
    """,
)
SCHEMA_VERSION = len(_MIGRATIONS)

# Columns written by a scrape. status, first_seen_at and status_updated_at are
# owned by the user / the first insert and are never overwritten on re-scrape.
_SCRAPED_COLUMNS = (
    "source",
    "external_id",
    "url",
    "title",
    "company",
    "city",
    "via",
    "work_mode",
    "salary_text",
    "salary_min",
    "salary_max",
    "currency",
    "salary_is_net",
    "employment_type",
    "seniority",
    "description",
    "requirements",
    "language",
    "posted_at",
    "scraped_at",
    "linkedin_job_id",
    "details_fetched_at",
    "dedup_key",  # computed from title + company + city (dedupe.dedup_key), not a model field
)

# How each column stores "unknown", so a poorer re-scrape (e.g. a LinkedIn list
# entry without description) never wipes data an earlier, richer fetch saved.
_EMPTY_VALUES = {"description": "''", "requirements": "'[]'"}


def _keep_known(column: str) -> str:
    new = f"excluded.{column}"
    if column in _EMPTY_VALUES:
        new = f"NULLIF({new}, {_EMPTY_VALUES[column]})"
    return f"{column} = COALESCE({new}, jobs.{column})"


_UPSERT_SQL = f"""
INSERT INTO jobs (id, {", ".join(_SCRAPED_COLUMNS)}, first_seen_at, status)
VALUES (:id, {", ".join(":" + c for c in _SCRAPED_COLUMNS)}, :scraped_at, :status)
ON CONFLICT(id) DO UPDATE SET
    {", ".join(_keep_known(c) for c in _SCRAPED_COLUMNS)}
"""


@dataclass(frozen=True)
class UpsertResult:
    inserted: int
    updated: int


@dataclass(frozen=True)
class StoredCV:
    id: int
    job_id: str | None  # None for a CV written for a type of job
    focus: str | None
    created_at: str
    updated_at: str
    model: str | None
    cv: TailoredCV


@dataclass(frozen=True)
class ScrapeRun:
    id: int
    source: str
    query: str | None
    started_at: str
    finished_at: str | None
    jobs_found: int
    jobs_new: int
    error: str | None


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL lets the Streamlit UI read while a scrape is writing
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    return conn


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Connection that commits on success, rolls back on error and always closes."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the migrations this database has not seen yet (tracked in PRAGMA user_version)."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for number, step in enumerate(_MIGRATIONS[version:], start=version + 1):
        if callable(step):
            step(conn)
        else:
            conn.executescript(step)
        conn.execute(f"PRAGMA user_version = {number}")
    conn.commit()


def _add_dedup_keys(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE jobs ADD COLUMN dedup_key TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs(dedup_key)")
    refresh_dedup_keys(conn)


def refresh_dedup_keys(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT * FROM jobs").fetchall():
        conn.execute("UPDATE jobs SET dedup_key = ? WHERE id = ?", (dedup_key(_row_to_job(row)), row["id"]))


# --- Jobs -------------------------------------------------------------------


def _job_to_params(job: JobPosting) -> dict[str, object]:
    params = job.model_dump(mode="json", include={"id", "status", *_SCRAPED_COLUMNS})
    params["requirements"] = json.dumps(job.requirements, ensure_ascii=False)
    params["dedup_key"] = dedup_key(job)
    if job.salary_is_net is not None:
        params["salary_is_net"] = int(job.salary_is_net)
    return params


def _row_to_job(row: sqlite3.Row) -> JobPosting:
    data = dict(row)
    data["requirements"] = json.loads(data["requirements"])
    return JobPosting.model_validate(data)


def upsert_jobs(conn: sqlite3.Connection, jobs: Iterable[JobPosting]) -> UpsertResult:
    inserted = updated = 0
    for job in jobs:
        exists = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job.id,)).fetchone()
        params = _job_to_params(job)
        if not exists and params["dedup_key"]:
            # A copy of an ad already marked on another board starts with that status,
            # so a job you applied to does not come back as "new" from a second source.
            known = conn.execute(
                "SELECT status FROM jobs WHERE dedup_key = ? AND status != ? ORDER BY status_updated_at DESC LIMIT 1",
                (params["dedup_key"], str(JobStatus.NEW)),
            ).fetchone()
            if known:
                params["status"] = known["status"]
        conn.execute(_UPSERT_SQL, params)
        if exists:
            updated += 1
        else:
            inserted += 1
    return UpsertResult(inserted=inserted, updated=updated)


def stored_ids(conn: sqlite3.Connection, job_ids: list[str]) -> set[str]:
    """Which of these jobs are already in the database."""
    found: set[str] = set()
    for start in range(0, len(job_ids), 500):  # SQLite caps the number of parameters
        chunk = job_ids[start : start + 500]
        rows = conn.execute(f"SELECT id FROM jobs WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        found.update(row["id"] for row in rows)
    return found


def delete_jobs(conn: sqlite3.Connection, job_ids: list[str]) -> int:
    """Only for jobs a search has just added by mistake (see scrape.run_source)."""
    return sum(conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount for job_id in job_ids)


def get_job(conn: sqlite3.Connection, job_id: str) -> JobPosting | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    conn: sqlite3.Connection,
    *,
    statuses: Iterable[JobStatus] | None = None,
    sources: Iterable[str] | None = None,
    text: str | None = None,
    limit: int = 500,
) -> list[JobPosting]:
    """Newest first; jobs without a posting date (BestJobs) count from when we first saw them.

    `text` matches title, company or description (case-insensitive for ASCII only).
    """
    where: list[str] = []
    params: list[object] = []
    if statuses is not None:
        values = [str(s) for s in statuses]
        where.append(f"status IN ({', '.join('?' * len(values))})")
        params.extend(values)
    if sources is not None:
        values = list(sources)
        where.append(f"source IN ({', '.join('?' * len(values))})")
        params.extend(values)
    if text:
        where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        params.extend([f"%{text}%"] * 3)

    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(posted_at, first_seen_at) DESC, scraped_at DESC LIMIT ?"
    params.append(limit)
    return [_row_to_job(row) for row in conn.execute(sql, params)]


def set_status(conn: sqlite3.Connection, job_id: str, status: JobStatus) -> bool:
    """Set the status of the job and of its copies on other boards. False if the job does not exist."""
    cursor = conn.execute(
        """
        UPDATE jobs SET status = ?, status_updated_at = ?
        WHERE id = ? OR (dedup_key IS NOT NULL AND dedup_key = (SELECT dedup_key FROM jobs WHERE id = ?))
        """,
        (str(status), now_local().isoformat(), job_id, job_id),
    )
    return cursor.rowcount >= 1


def duplicates_of(conn: sqlite3.Connection, job_id: str) -> list[JobPosting]:
    """The same ad on other boards (same dedup_key), not including the job itself."""
    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE dedup_key = (SELECT dedup_key FROM jobs WHERE id = ?) AND id != ?
        ORDER BY source
        """,
        (job_id, job_id),
    )
    return [_row_to_job(row) for row in rows]


def stored_sources(conn: sqlite3.Connection) -> list[str]:
    return [source for (source,) in conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source")]


def count_by_status(conn: sqlite3.Connection) -> dict[JobStatus, int]:
    rows = conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
    return {JobStatus(status): count for status, count in rows}


# --- Tailored CVs -------------------------------------------------------------
# Every generation is a new row (it cost money); edits update that row in place.


def add_cv(
    conn: sqlite3.Connection, job_id: str | None, cv: TailoredCV, model: str | None, *, focus: str | None = None
) -> int:
    """A CV for one ad (job_id, optionally with a focus) or for a type of job (focus only)."""
    if job_id is None and not focus:
        raise ValueError("a CV needs a job or a focus")
    now = now_local().isoformat()
    cursor = conn.execute(
        "INSERT INTO cvs (job_id, focus, created_at, updated_at, model, cv_json) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, focus or None, now, now, model, cv.model_dump_json()),
    )
    return cursor.lastrowid


def update_cv(conn: sqlite3.Connection, cv_id: int, cv: TailoredCV) -> None:
    conn.execute(
        "UPDATE cvs SET cv_json = ?, updated_at = ? WHERE id = ?",
        (cv.model_dump_json(), now_local().isoformat(), cv_id),
    )


def delete_all_cvs(conn: sqlite3.Connection) -> int:
    """Generated CVs hold the profile's personal data; deleting the profile can take them too."""
    return conn.execute("DELETE FROM cvs").rowcount


def latest_cv(conn: sqlite3.Connection, job_id: str) -> StoredCV | None:
    row = conn.execute("SELECT * FROM cvs WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    return _row_to_cv(row) if row else None


def latest_focus_cv(conn: sqlite3.Connection, focus: str) -> StoredCV | None:
    """The newest CV written for this type of job (not for an ad)."""
    row = conn.execute(
        "SELECT * FROM cvs WHERE job_id IS NULL AND focus = ? ORDER BY id DESC LIMIT 1", (focus,)
    ).fetchone()
    return _row_to_cv(row) if row else None


def cv_versions(conn: sqlite3.Connection, *, job_id: str | None = None, focus: str | None = None) -> list[StoredCV]:
    """Every generation for one ad (job_id) or one type of job (focus, no ad), newest first."""
    if job_id is not None:
        rows = conn.execute("SELECT * FROM cvs WHERE job_id = ? ORDER BY id DESC", (job_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cvs WHERE job_id IS NULL AND focus = ? ORDER BY id DESC", (focus,)).fetchall()
    return [_row_to_cv(row) for row in rows]


def all_cvs(conn: sqlite3.Connection) -> list[StoredCV]:
    """Every generated CV, newest first (the "CV-urile mele" page)."""
    return [_row_to_cv(row) for row in conn.execute("SELECT * FROM cvs ORDER BY id DESC").fetchall()]


def delete_cv(conn: sqlite3.Connection, cv_id: int) -> bool:
    return conn.execute("DELETE FROM cvs WHERE id = ?", (cv_id,)).rowcount > 0


def cv_focuses(conn: sqlite3.Connection) -> list[str]:
    """Types of job that have a CV of their own, most recently generated first."""
    rows = conn.execute(
        "SELECT focus FROM cvs WHERE job_id IS NULL AND focus IS NOT NULL GROUP BY focus ORDER BY MAX(id) DESC"
    ).fetchall()
    return [row["focus"] for row in rows]


def _row_to_cv(row: sqlite3.Row) -> StoredCV:
    data = dict(row)
    return StoredCV(cv=TailoredCV.model_validate_json(data.pop("cv_json")), **data)


# --- Scrape runs ------------------------------------------------------------


def start_run(conn: sqlite3.Connection, source: str, query: str | None = None) -> int:
    cursor = conn.execute(
        "INSERT INTO scrape_runs (source, query, started_at) VALUES (?, ?, ?)",
        (source, query, now_local().isoformat()),
    )
    return cursor.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    jobs_found: int = 0,
    jobs_new: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE scrape_runs SET finished_at = ?, jobs_found = ?, jobs_new = ?, error = ? WHERE id = ?",
        (now_local().isoformat(), jobs_found, jobs_new, error, run_id),
    )


def recent_runs(conn: sqlite3.Connection, limit: int = 20) -> list[ScrapeRun]:
    rows = conn.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?", (limit,))
    return [ScrapeRun(**dict(row)) for row in rows]
