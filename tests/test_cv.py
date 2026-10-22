"""CV generation (with a fake Claude client), validation and rendering. No network."""

import json
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from docx import Document

from jobscraper import db
from jobscraper.config import PROJECT_ROOT, LLMConfig
from jobscraper.cv.document import build_document, period
from jobscraper.cv.generator import FALLBACK_BETA, CVGenerationError, generate_cv, make_client
from jobscraper.cv.prompts import profile_for_prompt
from jobscraper.cv.render import file_stem, focus_file_stem, render_html, write_docx, write_pdf
from jobscraper.cv.validate import allowed_skills, check_cv
from jobscraper.keys import KeyStore
from jobscraper.models import JobPosting, TailoredCV, TailoredExperience, TailoredProject
from jobscraper.profile import load_profile

PROFILE = load_profile(PROJECT_ROOT / "profile.example.yaml")
JOB = JobPosting(
    source="bestjobs",
    external_id="1",
    url="https://www.bestjobs.eu/loc-de-munca/python",
    title="Python Developer",
    company="Firma SRL",
    city="Cluj-Napoca",
    description="Căutăm un Python Developer. Cerințe: Python, SQL, Docker.",
    requirements=["Limbă: Engleză (Avansat)"],
)


def make_cv(**overrides) -> TailoredCV:
    fields = {
        "language": "ro",
        "headline": "Junior Python Developer",
        "summary": "Programator junior cu experiență în automatizări Python.",
        "skills": ["Python", "SQL"],
        "experience": [TailoredExperience(profile_index=0, bullets=["Am automatizat un raport, economisind 3 ore pe săptămână."])],
        "projects": [TailoredProject(profile_index=0, description="Aplicație de căutat joburi.")],
        "education_indices": [0],
        "course_indices": [0],
        "gaps": ["Docker"],
    }
    return TailoredCV(**(fields | overrides))


class FakeClaude:
    """Stands in for anthropic.Anthropic: records the request, returns a canned response."""

    def __init__(self, response=None, error: Exception | None = None):
        self.requests: list[dict] = []
        self._response = response or SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=make_cv(),
            model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=3000, output_tokens=800, cache_read_input_tokens=0),
        )
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **request):
        self.requests.append(request)
        if self._error:
            raise self._error
        return self._response


# --- generation -----------------------------------------------------------------------


def test_request_shape():
    client = FakeClaude()
    result = generate_cv(client, PROFILE, JOB, llm=LLMConfig(), language="en")

    (request,) = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert request["output_format"] is TailoredCV
    assert request["output_config"] == {"effort": "medium"}
    assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
    assert "temperature" not in request and "thinking" not in request
    profile_block, job_block = request["messages"][0]["content"]
    assert profile_block["cache_control"] == {"type": "ephemeral"}
    assert '"index": 0' in profile_block["text"]
    assert "Python Developer" in job_block["text"] and "Docker" in job_block["text"]
    assert "Write the CV in English" in job_block["text"]
    assert result.cv == make_cv() and result.model == "claude-opus-5-5"


def test_cv_for_a_type_of_job_has_no_ad():
    client = FakeClaude()
    generate_cv(client, PROFILE, None, llm=LLMConfig(), language="auto", focus="Testare / QA")

    request_text = client.requests[0]["messages"][0]["content"][1]["text"]
    assert "<job_ad>" not in request_text
    assert "Type of job: Testare / QA" in request_text and "What it involves: software testing" in request_text
    assert "not for one specific ad" in request_text
    assert "Write the CV in Romanian" in request_text  # "auto" has no ad to follow
    assert "Reframe only what is true" in client.requests[0]["system"]


def test_cv_for_an_ad_can_lean_towards_a_custom_focus():
    client = FakeClaude()
    generate_cv(client, PROFILE, JOB, llm=LLMConfig(), focus=" Automation tester cu Python ")
    request_text = client.requests[0]["messages"][0]["content"][1]["text"]
    assert "<job_ad>" in request_text
    assert "Type of job: Automation tester cu Python\n</focus>" in request_text  # no preset description


def test_cv_needs_an_ad_or_a_focus():
    with pytest.raises(CVGenerationError, match="tip de job"):
        generate_cv(FakeClaude(), PROFILE, None, llm=LLMConfig(), focus="  ")


def test_models_without_fallback_support_get_no_fallback():
    client = FakeClaude()
    generate_cv(client, PROFILE, JOB, llm=LLMConfig(model="claude-haiku-5-5"))
    assert "fallbacks" not in client.requests[0] and "betas" not in client.requests[0]


def test_prompt_profile_hides_contact_details_and_preferences():
    data = json.loads(profile_for_prompt(PROFILE))
    assert data["contact"] == {"full_name": "Prenume Nume"}
    assert "preferences" not in data
    assert [e["index"] for e in data["experience"]] == [0]


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "message"),
    [("refusal", None, "refuzat"), ("max_tokens", None, "tăiat"), ("end_turn", None, "valid")],
)
def test_unusable_responses(stop_reason, parsed, message):
    response = SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, model="m", usage=SimpleNamespace())
    with pytest.raises(CVGenerationError, match=message):
        generate_cv(FakeClaude(response), PROFILE, JOB, llm=LLMConfig())


def test_job_without_description_is_not_sent():
    client = FakeClaude()
    with pytest.raises(CVGenerationError, match="descriere"):
        generate_cv(client, PROFILE, JOB.model_copy(update={"description": ""}), llm=LLMConfig())
    assert client.requests == []


def test_api_errors_become_romanian_messages():
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    error = anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)
    with pytest.raises(CVGenerationError, match="ANTHROPIC_API_KEY"):
        generate_cv(FakeClaude(error=error), PROFILE, JOB, llm=LLMConfig())


def test_client_needs_a_key():
    with pytest.raises(CVGenerationError, match="Chei API"):
        make_client(KeyStore({}, environ={}))
    assert make_client(KeyStore({"byok:ANTHROPIC_API_KEY": "sk-test"}, environ={})).api_key == "sk-test"


# --- validation -------------------------------------------------------------------------


def test_valid_cv_passes():
    check = check_cv(make_cv(), PROFILE)
    assert check.ok and check.warnings == []


def test_skills_may_come_from_technologies():
    assert {"Excel", "SQLite"} <= set(allowed_skills(PROFILE))
    assert check_cv(make_cv(skills=["python", "Excel", "SQLite"]), PROFILE).ok  # case-insensitive


def test_invented_skill_and_bad_indices_are_errors():
    check = check_cv(
        make_cv(
            skills=["Python", "Docker"],
            experience=[TailoredExperience(profile_index=0, bullets=["x"]), TailoredExperience(profile_index=0, bullets=["y"])],
            education_indices=[3],
        ),
        PROFILE,
    )
    assert check.errors == [
        "Experiență: intrarea #1 apare de 2 ori.",
        "Educație: intrarea #4 nu există în profil.",
        "Skill-ul „Docker” nu apare în profil. Șterge-l din CV sau adaugă-l în profil.",
    ]


def test_numbers_not_in_profile_are_warnings():
    cv = make_cv(experience=[TailoredExperience(profile_index=0, bullets=["Am crescut vânzările cu 40% în 3 luni."])])
    assert check_cv(cv, PROFILE).warnings == ["Cifra „40” nu apare în profil; verifică să nu fie inventată."]


def test_dropping_all_experience_is_a_warning():
    assert check_cv(make_cv(experience=[]), PROFILE).warnings == ["CV-ul nu include nicio experiență din profil."]


# --- document and rendering -----------------------------------------------------------------


def test_document_takes_facts_from_the_profile():
    document = build_document(PROFILE, make_cv())
    (job,) = document.experience
    assert (job.title, job.organization, job.period) == ("Operator date", "Firma Exemplu SRL", "09.2022 – 06.2024")
    assert document.education[0].title == "Licență, Informatică economică"
    assert document.courses[0].period == "06.2025"
    assert document.languages == ["Română – nativ", "Engleză – B2"]
    assert document.labels["experience"] == "Experiență profesională"


def test_document_skips_indices_outside_the_profile():
    document = build_document(PROFILE, make_cv(experience=[TailoredExperience(profile_index=7, bullets=["x"])]))
    assert document.experience == []


def test_period():
    assert period("2024-03", None, "prezent") == "03.2024 – prezent"
    assert period(None, "2021-07") == "07.2021"
    assert period("2015", "2019") == "2015 – 2019"  # years only, as LinkedIn shows studies


def test_html_is_escaped_and_localized():
    english = make_cv(language="en", summary="Uses <script>alert(1)</script> & more")
    html = render_html(build_document(PROFILE, english))
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "Professional experience" in html and 'lang="en"' in html


def test_file_stem():
    job = JOB.model_copy(update={"company": "Firmă & Asociații S.R.L.", "title": "Dezvoltator Python / Django"})
    assert file_stem(job, date(2026, 10, 19)) == "2026-10-19_firma-asociatii-s-r-l_dezvoltator-python-django"
    assert focus_file_stem("Testare / QA", date(2026, 10, 19)) == "2026-10-19_cv_testare-qa"


def test_docx(tmp_path):
    path = write_docx(build_document(PROFILE, make_cv()), tmp_path / "cv.docx")
    paragraphs = Document(str(path)).paragraphs
    texts = [p.text for p in paragraphs]
    assert texts[0] == "Prenume Nume"
    assert "EXPERIENȚĂ PROFESIONALĂ" in texts
    bullets = [p.text for p in paragraphs if p.style.name == "List Bullet"]
    assert bullets == ["Am automatizat un raport, economisind 3 ore pe săptămână."]


def test_pdf_with_real_chromium(tmp_path):
    path = write_pdf(build_document(PROFILE, make_cv()), tmp_path / "cv.pdf")
    assert path.read_bytes().startswith(b"%PDF")


# --- storage ------------------------------------------------------------------------------


def test_cv_storage(tmp_path):
    with db.open_db(tmp_path / "jobs.db") as conn:
        assert db.latest_cv(conn, JOB.id) is None
        first = db.add_cv(conn, JOB.id, make_cv(), "claude-opus-5-5")
        second = db.add_cv(conn, JOB.id, make_cv(headline="Altul"), "claude-opus-5-5")
        db.update_cv(conn, second, make_cv(headline="Editat"))

        latest = db.latest_cv(conn, JOB.id)
        assert latest.id == second > first
        assert latest.cv.headline == "Editat"
        assert latest.model == "claude-opus-5-5"
