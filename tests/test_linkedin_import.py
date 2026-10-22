"""LinkedIn PDF import (with a fake Claude client) and profile deletion. No network."""

import base64
from types import SimpleNamespace

import pytest
from anthropic.lib._parse._transform import transform_schema

from jobscraper.config import PROJECT_ROOT, LLMConfig
from jobscraper.linkedin_import import (
    ImportedContact,
    ImportedCourse,
    ImportedExperience,
    ImportedProfile,
    merge_into_profile,
    read_linkedin_pdf,
)
from jobscraper.llm import FALLBACK_BETA, LLMError
from jobscraper.models import TailoredCV
from jobscraper.profile import (
    delete_profile,
    linkedin_import_path,
    linkedin_pdf_path,
    load_profile,
    save_linkedin_pdf,
    save_profile,
)
from jobscraper.profile_form import from_form_state, key, to_form_state

PROFILE = load_profile(PROJECT_ROOT / "profile.example.yaml")
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"

def blank(model, **fields):
    """Every field is required in the schema; "" / [] stand for "not in the PDF"."""
    empty = {name: [] if "list" in str(info.annotation) else "" for name, info in model.model_fields.items()}
    return model(**(empty | fields))


IMPORTED = blank(
    ImportedProfile,
    contact=blank(ImportedContact, full_name="Ana Pop", email="ana@example.com", linkedin="https://www.linkedin.com/in/ana-pop"),
    headline="QA Engineer",
    experience=[
        blank(ImportedExperience, role="QA Engineer", company="Firma Nouă", start="2024-07", achievements=["Am scris teste."]),
        blank(ImportedExperience, role="Tester", company="Altă Firmă", start="2021"),  # the PDF shows only the year
    ],
    courses=[blank(ImportedCourse, name="ISTQB Foundation", provider="ISTQB")],
    skills=["Python", "Selenium"],
)


def schema_complexity(model) -> tuple[int, int]:
    """(optional, union-typed) fields in the schema the SDK sends, across all nested objects."""
    optional = unions = 0

    def walk(node):
        nonlocal optional, unions
        if isinstance(node, dict):
            for name, prop in node.get("properties", {}).items():
                optional += name not in node.get("required", [])
                unions += "anyOf" in prop or isinstance(prop.get("type"), list)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(transform_schema(model))
    return optional, unions


@pytest.mark.parametrize("model", [ImportedProfile, TailoredCV])
def test_schemas_stay_within_the_structured_output_limits(model):
    # The API rejects more than 24 optional or 16 union-typed fields ("compiled grammar is too large")
    assert schema_complexity(model) == (0, 0)


class FakeClaude:
    def __init__(self, response=None):
        self.requests: list[dict] = []
        self._response = response or SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=IMPORTED,
            model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=4000, output_tokens=900, cache_read_input_tokens=0),
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **request):
        self.requests.append(request)
        return self._response


def test_the_pdf_goes_to_claude_as_a_document_block():
    client = FakeClaude()
    result = read_linkedin_pdf(client, PDF, llm=LLMConfig())

    (request,) = client.requests
    assert request["output_format"] is ImportedProfile
    assert request["betas"] == [FALLBACK_BETA] and request["fallbacks"] == "default"
    assert "never infer" in request["system"]
    document, instruction = request["messages"][0]["content"]
    assert document["type"] == "document"
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(document["source"]["data"]) == PDF
    assert instruction["type"] == "text"
    assert result.output == IMPORTED and result.model == "claude-opus-5-5"


def test_not_a_pdf_is_rejected_before_calling_claude():
    client = FakeClaude()
    with pytest.raises(LLMError, match="nu e un PDF"):
        read_linkedin_pdf(client, b"<html>profil</html>", llm=LLMConfig())
    assert client.requests == []


def test_refusal_is_an_error_in_romanian():
    response = SimpleNamespace(stop_reason="refusal", parsed_output=None, model="m", usage=SimpleNamespace())
    with pytest.raises(LLMError, match="refuzat să genereze profilul"):
        read_linkedin_pdf(FakeClaude(response), PDF, llm=LLMConfig())


def test_merge_keeps_what_the_pdf_does_not_have():
    merged = merge_into_profile(IMPORTED, PROFILE)

    assert merged.contact.full_name == "Ana Pop" and merged.contact.email == "ana@example.com"
    assert merged.contact.phone == PROFILE.contact.phone  # not in the PDF: kept
    assert merged.headline == "QA Engineer" and merged.summary == PROFILE.summary
    assert [e.company for e in merged.experience] == ["Firma Nouă", "Altă Firmă", PROFILE.experience[0].company]
    assert [c.name for c in merged.courses] == ["ISTQB Foundation", *(c.name for c in PROFILE.courses)]
    python = next(s for s in merged.skills if s.name == "Python")
    assert python.level == PROFILE.skills[0].level  # level from the profile, the PDF has none
    assert "Selenium" in [s.name for s in merged.skills]
    assert merged.preferences == PROFILE.preferences  # LinkedIn has no job preferences


def test_merge_into_an_empty_profile_fills_a_form_that_saves():
    state = to_form_state(merge_into_profile(IMPORTED, None))
    assert state[key("contact", "full_name")] == "Ana Pop"
    profile = from_form_state(state)
    # A year without month stays as the PDF had it, and is a valid profile date
    assert [(e.start, e.end) for e in profile.experience] == [("2024-07", None), ("2021", None)]
    assert profile.contact.phone is None  # "" from the PDF means unknown


def test_delete_profile_removes_the_pdf_and_the_saved_import_too(tmp_path):
    path = tmp_path / "profile.yaml"
    save_profile(PROFILE, path)
    pdf = save_linkedin_pdf(PDF, path)
    assert pdf == linkedin_pdf_path(path) and pdf.read_bytes() == PDF
    saved_import = linkedin_import_path(path)
    saved_import.write_text(IMPORTED.model_dump_json(), encoding="utf-8")

    assert delete_profile(path) == [path, pdf, saved_import]
    assert not path.exists() and not pdf.exists() and not saved_import.exists()
    assert delete_profile(path) == []
