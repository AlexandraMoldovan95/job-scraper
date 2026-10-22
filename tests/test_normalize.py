from datetime import datetime, timedelta

import pytest

from jobscraper.models import BUCHAREST_TZ, WorkMode
from jobscraper.normalize import (
    clean_text,
    fix_romanian_diacritics,
    html_to_text,
    linkedin_job_id_from_url,
    parse_relative_date,
    strip_diacritics,
    work_mode_from_text,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=BUCHAREST_TZ)


def test_cedilla_becomes_comma_below():
    assert fix_romanian_diacritics("Bucureşti, Timişoara, Ţară") == "București, Timișoara, Țară"


def test_strip_diacritics():
    assert strip_diacritics("Brașov, Iași, Târgu Mureș") == "Brasov, Iasi, Targu Mures"


def test_html_to_text_drops_styles_scripts_loaders_and_invisible_characters():
    html = (
        "<div><ul><li>Please wait...</li></ul><style>.socialbutton { display:none }</style>"
        "<script>track()</script><p>​Who we are</p><ul><li>Python</li><li></li></ul></div>"
    )
    assert html_to_text(html) == "Who we are\n- Python"


def test_clean_text():
    assert clean_text("  Inginer   software  Bucureşti ") == "Inginer software București"
    assert clean_text("   ") is None
    assert clean_text(None) is None


@pytest.mark.parametrize(
    ("text", "ago"),
    [
        ("1 day ago", timedelta(days=1)),
        ("30+ days ago", timedelta(days=30)),
        ("an hour ago", timedelta(hours=1)),
        ("5 hours ago", timedelta(hours=5)),
        ("2 weeks ago", timedelta(weeks=2)),
        ("acum 2 zile", timedelta(days=2)),
        ("Acum o zi", timedelta(days=1)),
        ("acum 3 ore", timedelta(hours=3)),
        ("acum o oră", timedelta(hours=1)),
        ("acum 15 minute", timedelta(minutes=15)),
        ("acum 1 săptămână", timedelta(weeks=1)),
        ("acum 2 săptămâni", timedelta(weeks=2)),
        ("acum o lună", timedelta(days=30)),
        ("Postat acum 4 zile", timedelta(days=4)),
        ("ieri", timedelta(days=1)),
        ("Azi", timedelta(0)),
        ("acum câteva secunde", timedelta(0)),
    ],
)
def test_relative_dates(text, ago):
    assert parse_relative_date(text, NOW) == NOW - ago


@pytest.mark.parametrize("text", [None, "", "Full-time", "12 octombrie"])
def test_not_a_relative_date(text):
    assert parse_relative_date(text, NOW) is None


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("București, România (Hibrid)", WorkMode.HYBRID),
        ("Bucharest, Romania (Hybrid)", WorkMode.HYBRID),
        ("Romania (Remote)", WorkMode.REMOTE),
        ("Oriunde", WorkMode.REMOTE),
        ("Cluj (On-site)", WorkMode.ON_SITE),
        ("Iași", None),
        (None, None),
    ],
)
def test_work_mode_from_text(text, mode):
    assert work_mode_from_text(text) is mode


@pytest.mark.parametrize(
    ("url", "job_id"),
    [
        ("https://www.linkedin.com/jobs/view/3812345678", "3812345678"),
        ("https://ro.linkedin.com/jobs/view/3812345678/", "3812345678"),
        (
            "https://ro.linkedin.com/jobs/view/python-3-developer-at-firma-2024-3812345678?utm_source=google_jobs_apply",
            "3812345678",
        ),
        ("https://www.linkedin.com/jobs/search/?currentJobId=3812345678&geoId=106670623", "3812345678"),
        ("https://www.ejobs.ro/user/locuri-de-munca/python-developer/2222222", None),
        ("https://www.linkedin.com/company/exemplu", None),
        (None, None),
    ],
)
def test_linkedin_job_id_from_url(url, job_id):
    assert linkedin_job_id_from_url(url) == job_id
