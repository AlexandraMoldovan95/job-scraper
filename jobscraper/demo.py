"""Demo data, to try the whole app without API keys or live searches.

- Jobs: parsed offline from the test fixtures (real public ads saved from BestJobs, Hipo and
  LinkedIn), some with their full ad.
- Profile: profile.example.yaml (fictional person), only if there is no profile yet.
- CVs: two written by hand from that profile, one for an ad and one for a type of job, so the
  CV pages, the editor and the export work without calling Claude.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jobscraper import db
from jobscraper.config import PROJECT_ROOT, AppConfig
from jobscraper.models import JobPosting, TailoredCV, TailoredExperience, TailoredProject, now_local
from jobscraper.sources.bestjobs import BASE_URL as BESTJOBS_URL
from jobscraper.sources.bestjobs import JOBS_API, BestJobsScraper
from jobscraper.sources.hipo import BASE_URL as HIPO_URL
from jobscraper.sources.hipo import HipoScraper
from jobscraper.sources.linkedin import apply_linkedin_details, parse_linkedin_jobs

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
EXAMPLE_PROFILE = PROJECT_ROOT / "profile.example.yaml"

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_HIPO_DETAILS = {"264680": "job_scor.html", "271798": "job_bosch.html"}  # listing id -> saved ad
_AD_CV_SLUG = "programatori-juniori-entry-level-1"  # the BestJobs ad the demo CV is written for


@dataclass(frozen=True)
class DemoResult:
    jobs: int
    new_jobs: int
    profile_created: bool
    cvs: int


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _bestjobs_ad(body: str) -> JobPosting:
    """The listing entry for a saved BestJobs ad page (the page itself has the title and slug)."""
    ad = json.loads(_NEXT_DATA.search(body).group(1))["props"]["pageProps"]["job"]
    return JobPosting(source="bestjobs", external_id=ad["slug"], url=f"{BESTJOBS_URL}/loc-de-munca/{ad['slug']}", title=ad["title"])


def demo_jobs(now: datetime) -> list[JobPosting]:
    bestjobs, hipo = BestJobsScraper(http=None), HipoScraper(http=None)
    jobs = bestjobs.parse_listing(_read("bestjobs/api_jobs_python_cluj.json"), f"{JOBS_API}?keyword=python").jobs
    for name in ("job_onsite.html", "job_hybrid.html", "job_remote.html"):
        body = _read(f"bestjobs/{name}")
        jobs.append(bestjobs.parse_detail(body, _bestjobs_ad(body), now=now))

    search = f"{HIPO_URL}/locuri-de-munca/cautajob/Toate-Domeniile/Toate-Orasele/python"
    for name in ("search_python_page1.html", "search_python_cluj.html"):
        for job in hipo.parse_listing(_read(f"hipo/{name}"), search).jobs:
            saved = _HIPO_DETAILS.get(job.external_id or "")
            jobs.append(hipo.parse_detail(_read(f"hipo/{saved}"), job, now=now) if saved else job)

    linkedin = parse_linkedin_jobs(json.loads(_read("searchapi/linkedin_jobs_page1.json")), now=now)
    details = json.loads(_read("searchapi/linkedin_job_details.json"))
    jobs += [apply_linkedin_details(linkedin[0], details, now=now), *linkedin[1:]]
    return list({job.id: job for job in jobs}.values())


def demo_cvs() -> list[tuple[str | None, str | None, TailoredCV]]:
    """(job slug or None, focus or None, CV) written from profile.example.yaml only."""
    for_ad = TailoredCV(
        language="ro",
        headline="Programator junior Python",
        summary=(
            "Programator junior care a automatizat cu Python rapoarte reale la locul de muncă și a absolvit "
            "cursul Python Developer de la StepIT Academy. Caut un prim rol de programator într-o echipă unde să învăț rapid."
        ),
        skills=["Python", "SQL", "Git", "SQLite", "Streamlit", "Excel"],
        experience=[TailoredExperience(profile_index=0, bullets=[
            "Am automatizat un raport săptămânal cu un script Python, economisind 3 ore pe săptămână.",
            "Am introdus și verificat date în sistemul intern al firmei.",
        ])],
        projects=[TailoredProject(profile_index=0, description=(
            "Aplicație în Python și Streamlit care extrage anunțuri de pe site-uri de joburi, le salvează în SQLite "
            "și generează CV-uri adaptate."
        ))],
        education_indices=[0],
        course_indices=[0],
        gaps=["Experiență cu framework-uri web (Django sau Flask)", "Teste automate"],
    )
    for_qa = TailoredCV(
        language="ro",
        headline="Tester junior cu bază de programare în Python",
        summary=(
            "Am lucrat cu date și le-am verificat zilnic, iar rapoartele pe care le-am automatizat în Python au redus munca "
            "manuală. Am pregătire de programator (StepIT Academy) și vreau să lucrez în testare software."
        ),
        skills=["Python", "SQL", "Excel", "Git"],
        experience=[TailoredExperience(profile_index=0, bullets=[
            "Am verificat zilnic datele introduse în sistemul intern al firmei.",
            "Am automatizat un raport săptămânal cu un script Python, economisind 3 ore pe săptămână.",
        ])],
        projects=[TailoredProject(profile_index=0, description="Aplicație Python cu bază de date SQLite și interfață Streamlit.")],
        education_indices=[0],
        course_indices=[0],
        gaps=["Certificare ISTQB", "Instrumente de testare automată (de ex. Selenium)", "Scrierea cazurilor de test"],
    )
    return [(_AD_CV_SLUG, None, for_ad), (None, "Testare / QA", for_qa)]


def seed_demo(config: AppConfig) -> DemoResult:
    """Fill the database with demo data. Never replaces an existing profile; safe to run again."""
    now = now_local()
    profile_path: Path = config.paths.profile
    profile_created = not profile_path.exists()
    if profile_created:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE_PROFILE, profile_path)

    jobs = demo_jobs(now)
    by_slug = {job.url.rsplit("/", 1)[-1]: job for job in jobs if job.source == "bestjobs"}
    cvs = 0
    with db.open_db(config.paths.db) as conn:
        result = db.upsert_jobs(conn, jobs)
        for slug, focus, cv in demo_cvs():
            job_id = by_slug[slug].id if slug else None
            existing = db.cv_versions(conn, job_id=job_id) if job_id else db.cv_versions(conn, focus=focus)
            if not existing:  # running the demo again does not stack copies
                db.add_cv(conn, job_id, cv, "demo (scris de mână)", focus=focus)
                cvs += 1
    return DemoResult(jobs=len(jobs), new_jobs=result.inserted, profile_created=profile_created, cvs=cvs)
