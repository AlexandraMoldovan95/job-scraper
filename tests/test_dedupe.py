import pytest

from jobscraper.dedupe import dedup_key, group_duplicates, normalize_city, normalize_company, normalize_title
from jobscraper.models import JobPosting


def job(n: int, title="Python Developer", company="Firma SRL", city="București", source="bestjobs", **fields) -> JobPosting:
    return JobPosting(source=source, external_id=str(n), url=f"https://{source}/{n}", title=title, company=company, city=city, **fields)


@pytest.mark.parametrize(
    ("title", "normalized"),
    [
        ("Database & DevOps Engineer (all genders)", "database devops engineer"),
        ("Graduate AI Engineer (m/f/d)", "graduate ai engineer"),
        ("Dezvoltator  Python – Backend", "dezvoltator python backend"),
        ("C# / .NET Developer", "c# net developer"),
    ],
)
def test_normalize_title(title, normalized):
    assert normalize_title(title) == normalized


@pytest.mark.parametrize(
    ("company", "normalized"),
    [
        ("Firma Exemplu S.R.L.", "firma exemplu"),
        ("Booking Holdings Romania", "booking holdings"),
        ("meteocontrol GmbH", "meteocontrol"),
        ("SC Țară & Co SA", "sc tara"),
    ],
)
def test_normalize_company(company, normalized):
    assert normalize_company(company) == normalized


@pytest.mark.parametrize(
    ("city", "normalized"),
    [
        ("Șoseaua Pipera 42, București 077190, Romania", "bucuresti"),
        ("BUCURESTI", "bucuresti"),
        ("Bucharest, Romania", "bucuresti"),
        ("Cluj Napoca", "cluj napoca"),
        ("Cluj-Napoca, Cluj, România", "cluj napoca"),
        ("Cluj-Napoca, România; Tirgu Mures, România (+18)", "cluj napoca"),
        ("Str. Paul Morand, nr. 135, Mun. Timişoara, TIMIŞ", "timisoara"),
        ("Remote", ""),
        ("Comuna Exemplu, Romania", "comuna exemplu"),
        (None, ""),
    ],
)
def test_normalize_city(city, normalized):
    assert normalize_city(city) == normalized


def test_same_ad_on_two_boards_has_the_same_key():
    bestjobs = job(1, title="Python Developer (m/f/d)", company="Firma S.R.L.", city="Bd. Unirii 1, București, Romania")
    hipo = job(2, title="Python Developer", company="FIRMA SRL", city="BUCURESTI", source="hipo")
    assert dedup_key(bestjobs) == dedup_key(hipo) == "python developer|firma|bucuresti"


def test_different_city_or_company_or_no_company_never_merges():
    assert dedup_key(job(1)) != dedup_key(job(2, city="Cluj-Napoca"))
    assert dedup_key(job(1)) != dedup_key(job(2, company="Alta SRL"))
    assert dedup_key(job(1, company=None)) is None
    assert dedup_key(job(1, company="S.R.L.")) is None  # nothing left after the legal form


def test_grouping_picks_the_most_complete_copy_and_keeps_order():
    bare = job(1, source="hipo")
    rich = job(2, description="Descriere completă", salary_max=2000, currency="EUR")
    other = job(3, title="Data Engineer")
    no_company = job(4, company=None)
    groups = group_duplicates([bare, other, rich, no_company])

    assert [g.primary.external_id for g in groups] == ["2", "3", "4"]
    assert [j.external_id for j in groups[0].others] == ["1"]
    assert [j.external_id for j in groups[0].members] == ["2", "1"]
