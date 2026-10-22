"""Runs a source for a query and stores the results, recording the run in scrape_runs."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from jobscraper import db
from jobscraper.matching import unrelated_results
from jobscraper.models import JobPosting, SearchQuery, WorkMode
from jobscraper.sources import DetailsSource, JobSource, SourceError, details_source_name
from jobscraper.sources.errors import BlockedError, DisallowedByRobots

log = logging.getLogger(__name__)

Progress = Callable[[int, int], None]  # (done, total)


@dataclass(frozen=True)
class ScrapeOutcome:
    source: str
    query: str
    found: int = 0
    new: int = 0
    error: str | None = None
    described: int = 0  # full ads loaded right after the search
    details_failed: int = 0
    job_ids: tuple[str, ...] = ()  # every job the search found, new or already stored
    unrelated: int = 0  # general jobs the source returned for lack of matches; not stored


def run_source(
    conn: sqlite3.Connection,
    source: JobSource,
    query: SearchQuery,
    *,
    details: DetailsSource | None = None,
    max_details: int = 0,
    on_progress: Progress | None = None,
) -> ScrapeOutcome:
    """Search and store. With `details` (a free scraper, usually `source` itself), also load the
    full ad of up to `max_details` found jobs that do not have it yet.

    A source with no match for the search may answer with general jobs instead. Those are not
    kept: sources that match titles are judged on the list; scrapers, whose sites also match the
    ad text, after their full ads are loaded (the jobs this run added are then removed again)."""
    run_id = db.start_run(conn, source.name, query.keywords)
    try:
        jobs = source.fetch_jobs(query)
    except Exception as exc:  # one failing source must not stop the others; the UI shows the error
        log.exception("Source %s failed for %r", source.name, query.keywords)
        db.finish_run(conn, run_id, error=str(exc))
        conn.commit()
        return ScrapeOutcome(source.name, query.keywords, error=str(exc))

    if query.work_mode is WorkMode.REMOTE:
        # Sources get "remote" as a filter, not as a search word; unknown modes stay
        jobs = [job for job in jobs if job.work_mode in (WorkMode.REMOTE, None)]
    jobs = list({job.id: job for job in jobs}.values())  # pages can repeat a job
    unrelated = 0
    judge_full_ads = getattr(source, "searches_ad_text", False)
    if not judge_full_ads and unrelated_results(jobs, query.keywords):
        unrelated, jobs = len(jobs), []

    already_stored = db.stored_ids(conn, [job.id for job in jobs])
    db.upsert_jobs(conn, jobs)
    conn.commit()

    described = failed = 0
    if details is not None and max_details:
        described, failed = load_missing_details(conn, details, jobs, limit=max_details, on_progress=on_progress)
        if judge_full_ads:
            stored = [job for job in (db.get_job(conn, job.id) for job in jobs) if job is not None]
            if unrelated_results(stored, query.keywords):
                db.delete_jobs(conn, [job.id for job in jobs if job.id not in already_stored])
                unrelated, jobs = len(jobs), []
    new = len([job for job in jobs if job.id not in already_stored])
    if unrelated:
        log.info("%s %r: no match, %d unrelated jobs not kept", source.name, query.keywords, unrelated)

    db.finish_run(conn, run_id, jobs_found=len(jobs), jobs_new=new)
    conn.commit()
    log.info("%s %r: %d found, %d new", source.name, query.keywords, len(jobs), new)
    return ScrapeOutcome(
        source.name, query.keywords, found=len(jobs), new=new, described=described, details_failed=failed,
        job_ids=tuple(job.id for job in jobs), unrelated=unrelated,
    )


def load_missing_details(
    conn: sqlite3.Connection,
    source: DetailsSource,
    jobs: list[JobPosting],
    *,
    limit: int,
    on_progress: Progress | None = None,
) -> tuple[int, int]:
    """Full ads for those of `jobs` that `source` can describe and that have none yet, in listing
    order. Returns (loaded, failed). One bad page is skipped; a blocked site stops the loop,
    since every next request would be refused too."""
    todo = []
    for job in jobs:
        stored = db.get_job(conn, job.id)
        if stored is not None and details_source_name(stored) == source.name:
            todo.append(stored)
    todo = todo[:limit]

    loaded = failed = 0
    for done, job in enumerate(todo):
        if on_progress:
            on_progress(done, len(todo))
        try:
            load_details(conn, source, job)
        except (BlockedError, DisallowedByRobots):
            log.warning("%s refuses the detail pages; %d left unloaded", source.name, len(todo) - done)
            failed += len(todo) - done
            break
        except SourceError as exc:
            log.warning("Details for %s failed: %s", job.url, exc)
            failed += 1
        else:
            loaded += 1
    if on_progress:
        on_progress(len(todo), len(todo))
    log.info("%s: loaded %d full ads, %d failed", source.name, loaded, failed)
    return loaded, failed


def load_details(conn: sqlite3.Connection, source: DetailsSource, job: JobPosting) -> JobPosting:
    """Fetch the full ad and store it. Errors (SourceError, SourceUnavailable) propagate to the caller."""
    enriched = source.fetch_details(job)
    db.upsert_jobs(conn, [enriched])
    conn.commit()
    return db.get_job(conn, job.id) or enriched
