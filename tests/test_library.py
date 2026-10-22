"""The CV-uri page logic: type of each CV and the filters. Pure, no Streamlit."""

import pytest

from jobscraper.cv.library import (
    OTHER_TYPE,
    LibraryFilter,
    filter_entries,
    library_entries,
    library_types,
    type_from_title,
)
from jobscraper.db import StoredCV
from jobscraper.models import JobPosting, TailoredCV


def make_cv(cv_id: int, *, job_id=None, focus=None, headline="Dev", summary="Rezumat.", language="ro") -> StoredCV:
    cv = TailoredCV(
        language=language, headline=headline, summary=summary, skills=[], experience=[], projects=[],
        education_indices=[], course_indices=[], gaps=[],
    )
    return StoredCV(id=cv_id, job_id=job_id, focus=focus, created_at="2026-10-22T10:00:00+03:00",
                    updated_at="2026-10-22T10:00:00+03:00", model="claude-opus-5-5", cv=cv)


def make_job(job_id: str, title: str, company: str = "Firma") -> JobPosting:
    return JobPosting(source="bestjobs", external_id=job_id, url=f"https://x/{job_id}", title=title, company=company)


@pytest.mark.parametrize(
    ("title", "cv_type"),
    [
        ("Strong Junior QA Manual Engineer", "Testare / QA"),
        ("Automation Tester", "Testare / QA"),
        ("DevOps Engineer", "DevOps / Cloud"),
        ("Data Analyst (Remote)", "Date / BI"),
        ("Senior Backend Developer (Python)", "Dezvoltare software"),
        ("Dezvoltator Web", "Dezvoltare software"),
        ("Application Support / SQL", "Suport IT"),
        ("Contabil", OTHER_TYPE),
        ("Latest Developer", "Dezvoltare software"),  # "test" only as a whole word
    ],
)
def test_type_from_title(title, cv_type):
    assert type_from_title(title) == cv_type


JOBS = {"j1": make_job("j1", "Junior QA Engineer", "Firma QA"), "j2": make_job("j2", "Python Developer", "Proxify")}
CVS = [
    make_cv(4, job_id="j2", focus="Testare / QA", headline="QA cu Python"),  # the chosen focus wins over the title
    make_cv(3, job_id="j2", headline="Python Developer", language="en"),
    make_cv(2, job_id="j1", headline="Tester junior"),
    make_cv(1, focus="Automation tester cu Python", headline="Automation tester"),
]
ENTRIES = library_entries(CVS, JOBS)


def test_entries_get_a_type_and_a_title():
    assert [(e.stored.id, e.cv_type, e.title) for e in ENTRIES] == [
        (4, "Testare / QA", "Python Developer"),
        (3, "Dezvoltare software", "Python Developer"),
        (2, "Testare / QA", "Junior QA Engineer"),
        (1, "Automation tester cu Python", "Automation tester cu Python"),
    ]
    assert library_types(ENTRIES) == ["Testare / QA", "Dezvoltare software", "Automation tester cu Python"]


def test_a_cv_whose_ad_is_gone_takes_its_type_from_the_headline():
    (entry,) = library_entries([make_cv(9, job_id="gone", headline="Data Analyst")], {})
    assert (entry.cv_type, entry.title, entry.for_job) == ("Date / BI", "Data Analyst", True)


@pytest.mark.parametrize(
    ("wanted", "ids"),
    [
        (LibraryFilter(), [4, 3, 2, 1]),
        (LibraryFilter(types=frozenset({"Testare / QA"})), [4, 2]),
        (LibraryFilter(target="focus"), [1]),
        (LibraryFilter(target="job", types=frozenset({"Testare / QA"})), [4, 2]),
        (LibraryFilter(language="en"), [3]),
        (LibraryFilter(text="proxify"), [4, 3]),  # company of the ad
        (LibraryFilter(text="automation pyth"), [1]),  # every word, anywhere
        (LibraryFilter(text="tester"), [2, 1]),
    ],
)
def test_filters(wanted, ids):
    assert [e.stored.id for e in filter_entries(ENTRIES, wanted)] == ids
