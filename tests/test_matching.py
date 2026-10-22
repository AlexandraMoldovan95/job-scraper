from datetime import date

import pytest

from jobscraper.config import PROJECT_ROOT, SearchConfig
from jobscraper.matching import (
    Level,
    MatchSettings,
    exclusion_kind,
    experience_years,
    hard_filter,
    job_level,
    mentions_search,
    profile_terms,
    score_job,
    search_words,
    unrelated_results,
)
from jobscraper.models import JobPosting, WorkMode
from jobscraper.profile import load_profile

TODAY = date(2026, 10, 19)
PROFILE = load_profile(PROJECT_ROOT / "profile.example.yaml")  # Python, SQL, Git; 1.8 years; București
SETTINGS = MatchSettings.from_sources(PROFILE, SearchConfig())


def job(title="Python Developer", description="", **fields) -> JobPosting:
    return JobPosting(source="bestjobs", url=f"https://x/{title}", title=title, description=description, **fields)


def test_settings_prefer_profile_and_fall_back_to_config():
    assert SETTINGS.roles == ["Python Developer", "Junior Developer"]
    assert SETTINGS.min_salary_ron == 4000
    assert SETTINGS.levels == [Level.JUNIOR, Level.MID] and SETTINGS.filter_by_role
    empty_prefs = PROFILE.model_copy(update={"preferences": PROFILE.preferences.model_copy(update={"roles": []})})
    settings = MatchSettings.from_sources(empty_prefs, SearchConfig(queries=["data analyst"], keywords_excluded=["senior"]))
    assert settings.roles == ["data analyst"] and not settings.filter_by_role  # config queries only score
    assert settings.keywords_excluded == ["senior", "call center", "vânzări"]  # config + profile


def test_profile_terms_include_technologies_with_canonical_names():
    terms = profile_terms(PROFILE)
    assert terms["python"] == "Python" and terms["excel"] == "Excel" and terms["sqlite"] == "SQLite"


def test_experience_years():
    assert experience_years(PROFILE, TODAY) == pytest.approx(1.75)  # 2022-09 .. 2024-06
    years_only = PROFILE.model_copy(
        update={"experience": [PROFILE.experience[0].model_copy(update={"start": "2020", "end": "2023"})]}
    )
    assert experience_years(years_only, TODAY) == pytest.approx(3)  # mid-year to mid-year


@pytest.mark.parametrize(
    ("title", "description", "seniority", "level"),
    [
        ("Junior Python Developer", "", None, Level.JUNIOR),
        ("Junior Project Manager", "", None, Level.JUNIOR),
        ("Senior Backend Engineer", "", None, Level.SENIOR),
        ("Engineering Manager", "", None, Level.SENIOR),
        ("Backend Developer", "", "Middle (2-5 ani)", Level.MID),
        ("Backend Developer", "Cerem minim 3+ ani de experiență.", None, Level.MID),
        ("Backend Developer", "You have 6 years with Java.", None, Level.SENIOR),
        ("Backend Developer", "Lucrezi cu colegi seniori.", None, None),
        ("Backend Developer", "", "1 - 5 ani experienta", Level.JUNIOR),  # Hipo: at least 1 year
        ("Backend Developer", "", "Student / Absolvent, 0-1 an experienta", Level.JUNIOR),
        ("Backend Developer", "Avem 2 angajati in echipa.", None, None),  # "an" inside a word
        ("Working Student in Embedded Software", "", None, Level.JUNIOR),
    ],
)
def test_job_level(title, description, seniority, level):
    assert job_level(job(title, description, seniority=seniority)) is level


def test_strong_match():
    result = score_job(
        job("Junior Python Developer", "Lucrăm cu Python, SQL și Git.", city="București", work_mode=WorkMode.HYBRID),
        PROFILE, SETTINGS, today=TODAY,
    )
    assert result.score == 100
    assert result.matched_skills == ["Python", "SQL", "Git"] and result.missing_skills == []
    assert "+ Remote" not in result.reasons
    assert result.excluded_by is None


def test_missing_skills_lower_the_score_and_are_listed():
    result = score_job(
        job("Python Developer", "Python, Docker, Kubernetes și AWS.", work_mode=WorkMode.REMOTE), PROFILE, SETTINGS, today=TODAY
    )
    assert result.matched_skills == ["Python"]
    assert result.missing_skills == ["docker", "kubernetes", "aws"]
    assert "− Cerute, dar nu sunt în profil: docker, kubernetes, aws" in result.reasons
    assert "+ Remote" in result.reasons
    # skills 50 * 1/4 + role 20 + unknown level 10 + remote 15
    assert result.score == round(12.5 + 20 + 10 + 15)


def test_terms_match_whole_words_only():
    result = score_job(job("Dev", "JavaScript and PostgreSQL; the rest of the team."), PROFILE, SETTINGS, today=TODAY)
    assert set(result.missing_skills) == {"javascript", "postgresql"}  # not java, sql or rest api


def test_aliases():
    result = score_job(job("Dev", "We use Postgres, k8s and Node.js."), PROFILE, SETTINGS, today=TODAY)
    assert set(result.missing_skills) == {"postgresql", "kubernetes", "node.js"}


def test_senior_role_and_other_city_hurt():
    result = score_job(
        job("Senior Java Consultant", "Java", city="Iași", work_mode=WorkMode.ON_SITE), PROFILE, SETTINGS, today=TODAY
    )
    assert any(r.startswith("− Anunțul cere nivel senior") for r in result.reasons)
    assert any("on-site nu e printre preferințele tale" in r for r in result.reasons)
    assert "− Titlul nu seamănă cu rolurile căutate" in result.reasons
    assert result.score < 30


def test_hard_filters():
    settings = MatchSettings(keywords_excluded=["senior"], keywords_required=["python"], min_salary_ron=6000)
    assert hard_filter(job("Senior Python Dev", "python"), settings) == "titlul conține „senior”"
    assert hard_filter(job("Python Dev", "works with senior people; python"), settings) is None
    assert hard_filter(job("Java Dev", "java"), settings) == "nu conține „python”"
    # 1000 EUR ~ 5000 RON < 6000; a job without a salary is never excluded for it
    assert "sub minimul" in hard_filter(job("Python Dev", "python", salary_max=1000, currency="EUR"), settings)
    assert hard_filter(job("Python Dev", "python", salary_max=1500, currency="EUR"), settings) is None
    assert hard_filter(job("Python Dev", "python", salary_max=2000, currency="RON"), settings) is not None
    assert hard_filter(job("Python Dev", "python", salary_max=2000), settings) is None  # unknown currency
    assert hard_filter(job("Python Dev", "python"), settings) is None


def test_role_synonyms_and_city_aliases_count_for_the_score():
    result = score_job(job("Python Engineer", "Python", city="Bucharest, Romania"), PROFILE, SETTINGS, today=TODAY)
    assert "+ Titlul se potrivește cu rolul căutat „Python Developer”" in result.reasons
    assert "+ Într-un oraș preferat" in result.reasons


# Example profile preferences: roles Python/Junior Developer, București, remote or hybrid,
# junior or mid, no "call center" / "vânzări" in the title, at least 4000 RON.
@pytest.mark.parametrize(
    ("posting", "reason"),
    [
        (job("Python Developer", city="Cluj-Napoca", work_mode=WorkMode.HYBRID), "orașul (Cluj-Napoca) nu e printre preferințele tale"),
        (job("Python Developer", city="Iași", work_mode=WorkMode.ON_SITE), "modul de lucru (on-site) nu e printre preferințele tale"),
        (job("Senior Python Developer", city="București"), "cere nivel senior"),
        (job("Python Developer", "Minim 6 ani de experiență.", city="București"), "cere nivel senior"),
        (job("Contabil", city="București"), "titlul nu se potrivește cu rolurile căutate"),
        (job("Agent call center", city="București"), "titlul conține „call center”"),
    ],
)
def test_profile_preferences_exclude_jobs_that_do_not_fit(posting, reason):
    assert hard_filter(posting, SETTINGS) == reason


@pytest.mark.parametrize(
    "posting",
    [
        job("Python Developer", city="Cluj-Napoca", work_mode=WorkMode.REMOTE),  # remote: any city
        job("Software Engineer", city="Bucharest, Romania", work_mode=WorkMode.HYBRID),  # synonym + alias
        job("QA Programator", city="Romania"),  # country only: city unknown
        job("Backend Developer"),  # unknown city, mode and level
        job("Junior Developer", city="Șoseaua Pipera 42, București"),
    ],
)
def test_unknown_or_matching_values_are_never_excluded(posting):
    assert hard_filter(posting, SETTINGS) is None


def test_exclusion_kinds_name_the_preference():
    kinds = {exclusion_kind(hard_filter(posting, SETTINGS)) for posting in (
        job("Python Developer", city="Cluj-Napoca", work_mode=WorkMode.HYBRID),
        job("Python Developer", city="Iași", work_mode=WorkMode.ON_SITE),
        job("Senior Python Developer", city="București"),
        job("Contabil", city="București"),
        job("Agent call center", city="București"),
    )}
    assert kinds == {"oraș", "mod de lucru", "nivel", "rol", "cuvinte excluse"}


def test_search_words_leave_out_level_and_generic_words():
    assert search_words("Junior Ospătar remote") == ["ospatar"]
    assert search_words("python developer") == ["python", "developer"]
    assert search_words("junior job") == []  # nothing to judge results on


@pytest.mark.parametrize(
    ("title", "description", "mentioned"),
    [
        ("Ospătar / Ospătăriță", "", True),
        ("Waiter (Hotel)", "", True),  # English title
        ("Ospătari pentru restaurant", "", True),  # a longer form of the word
        ("Barman", "Cerințe: experiență ca ospătar.", True),  # loaded ad text counts
        ("Test Automation Engineer", "", False),
        ("Șef bucătar", "", False),
    ],
)
def test_mentions_search(title, description, mentioned):
    assert mentions_search(job(title, description), ["ospatar"]) is mentioned


def test_unrelated_results():
    assert unrelated_results([job("Test Automation Engineer"), job("SEO Analyst")], "ospatar")
    assert not unrelated_results([job("SEO Analyst"), job("Ospătar")], "ospatar")  # one real match: the source had some
    assert not unrelated_results([job("SEO Analyst")], "junior")  # generic words only: not judged
    assert not unrelated_results([], "ospatar")


def test_roles_from_config_do_not_filter():
    settings = MatchSettings(roles=["python developer"])
    assert hard_filter(job("Contabil"), settings) is None


def test_no_known_skills_in_the_ad_is_neutral():
    result = score_job(job("Operator call center", "Program flexibil."), PROFILE, SETTINGS, today=TODAY)
    assert "· Anunțul nu menționează skill-uri pe care să le recunosc" in result.reasons
    assert 0 < result.score < 60
