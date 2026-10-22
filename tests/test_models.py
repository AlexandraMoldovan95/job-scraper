import pytest
from pydantic import ValidationError

from jobscraper.config import PROJECT_ROOT
from jobscraper.models import Experience, JobPosting, TailoredCV, make_job_id
from jobscraper.profile import load_profile, save_profile


def make_job(**overrides) -> JobPosting:
    fields = {"source": "ejobs", "url": "https://www.ejobs.ro/user/locuri-de-munca/x/1", "title": "Dev"}
    return JobPosting(**(fields | overrides))


def test_job_id_prefers_external_id_over_url():
    a = make_job(external_id="123", url="https://example.com/a")
    b = make_job(external_id="123", url="https://example.com/b?utm=x")
    assert a.id == b.id == make_job_id("ejobs", "123", "ignored")


def test_job_id_differs_per_source():
    assert make_job(external_id="1").id != make_job(source="hipo", external_id="1").id


def test_linkedin_job_id_makes_the_id_source_independent():
    from_google = make_job(source="google_jobs", external_id="htidocid-abc", linkedin_job_id="3812345678")
    from_linkedin = make_job(source="linkedin", external_id="3812345678", linkedin_job_id="3812345678")
    assert from_google.id == from_linkedin.id


def test_salary_range_must_be_ordered():
    with pytest.raises(ValidationError):
        make_job(salary_min=6000, salary_max=4000)


def test_scraped_at_is_timezone_aware():
    assert make_job().scraped_at.tzinfo is not None


def test_experience_dates_are_year_month():
    with pytest.raises(ValidationError):
        Experience(role="Dev", company="X", start="2024-13")
    with pytest.raises(ValidationError):
        Experience(role="Dev", company="X", start="03.2024")


def test_example_profile_is_valid():
    profile = load_profile(PROJECT_ROOT / "profile.example.yaml")
    assert profile is not None
    assert profile.experience[0].start == "2022-09"
    assert profile.courses[0].provider == "StepIT Academy"


def test_profile_round_trip_keeps_diacritics(tmp_path):
    profile = load_profile(PROJECT_ROOT / "profile.example.yaml")
    path = tmp_path / "profile.yaml"
    save_profile(profile, path)
    assert "București" in path.read_text(encoding="utf-8")
    assert load_profile(path) == profile


def test_missing_profile_returns_none(tmp_path):
    assert load_profile(tmp_path / "nope.yaml") is None


def test_tailored_cv_has_json_schema():
    schema = TailoredCV.model_json_schema()
    assert {"experience", "skills", "gaps"} <= set(schema["properties"])
