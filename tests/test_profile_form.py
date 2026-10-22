import itertools

import pytest
from pydantic import ValidationError

from jobscraper.config import PROJECT_ROOT
from jobscraper.models import SkillLevel, WorkMode
from jobscraper.profile import load_profile
from jobscraper.profile_form import apply_editor_delta, describe_errors, entry_state, from_form_state, key, to_form_state

EXAMPLE = load_profile(PROJECT_ROOT / "profile.example.yaml")


def uids():
    counter = itertools.count(1)
    return lambda: f"u{next(counter)}"


def errors_for(state) -> list[str]:
    with pytest.raises(ValidationError) as info:
        from_form_state(state)
    return describe_errors(info.value)


def test_round_trip_keeps_the_profile():
    assert from_form_state(to_form_state(EXAMPLE)) == EXAMPLE


def test_widget_values_are_flat_and_lists_become_text():
    state = to_form_state(EXAMPLE, make_uid=uids())
    assert state[key("contact", "full_name")] == "Prenume Nume"
    assert state[key("experience")] == ["u1"]
    assert state[key("experience", "u1", "end")] == "2024-06"
    assert state[key("experience", "u1", "technologies")] == "Python, Excel"
    assert state[key("experience", "u1", "achievements")].startswith("Am automatizat")
    assert state[key("skills")][0] == {"name": "Python", "level": "intermediar", "years": 1.5}
    assert state[key("preferences", "min_salary")] == 4000
    assert state[key("preferences", "levels")] == ["junior", "mid"]
    assert state[key("preferences", "excluded_keywords")] == "call center, vânzări"
    assert "full_name: Prenume Nume" in state[key("yaml")]


def test_empty_form_needs_a_name():
    assert errors_for(to_form_state(None)) == ["Contact · Nume complet: e obligatoriu"]


def test_text_is_trimmed_and_lists_are_split():
    state = to_form_state(None, make_uid=uids())
    state[key("contact", "full_name")] = "  Ana Pop  "
    state[key("experience")] = ["u1"]
    state |= entry_state("experience", "u1")
    state |= {
        key("experience", "u1", "role"): "Developer",
        key("experience", "u1", "company"): "Firma",
        key("experience", "u1", "start"): " 2024-03 ",
        key("experience", "u1", "achievements"): "Prima realizare\n\n  A doua  \n",
        key("experience", "u1", "technologies"): "Python, , SQL ",
        key("preferences", "roles"): "Python Developer, Data Analyst",
        key("preferences", "work_modes"): [WorkMode.REMOTE],
    }
    profile = from_form_state(state)
    assert profile.contact.full_name == "Ana Pop"
    (job,) = profile.experience
    assert (job.start, job.end, job.city) == ("2024-03", None, None)
    assert job.achievements == ["Prima realizare", "A doua"]
    assert job.technologies == ["Python", "SQL"]
    assert profile.preferences.roles == ["Python Developer", "Data Analyst"]
    assert profile.preferences.min_salary is None  # 0 in the form means "no minimum"


def test_untouched_new_entries_are_dropped():
    state = to_form_state(EXAMPLE, make_uid=uids())
    state[key("projects")] = [*state[key("projects")], "new"]
    state |= entry_state("projects", "new")
    assert from_form_state(state).projects == EXAMPLE.projects


def test_errors_name_the_entry_and_field_in_romanian():
    state = to_form_state(EXAMPLE, make_uid=uids())
    state[key("experience", "u1", "start")] = "03.2024"
    state[key("experience", "u1", "company")] = " "
    assert errors_for(state) == [
        "Experiență #1 · Firmă: e obligatoriu",
        "Experiență #1 · Început: formatul trebuie să fie AAAA-LL sau doar AAAA (ex. 2024-03, 2019)",
    ]


def test_a_year_without_month_is_a_valid_date():
    # LinkedIn shows studies as "2015 - 2019"; the profile keeps that precision instead of guessing a month
    state = to_form_state(EXAMPLE, make_uid=uids())
    state[key("education", "u2", "start")] = "2015"
    state[key("education", "u2", "end")] = "2019"
    education = from_form_state(state).education[0]
    assert (education.start, education.end) == ("2015", "2019")


def test_editor_delta():
    rows = [{"name": "Python"}, {"name": "SQL"}, {"name": "Git"}]
    delta = {"edited_rows": {"0": {"name": "Python 3"}}, "deleted_rows": [1], "added_rows": [{"name": "Docker"}]}
    assert apply_editor_delta(rows, delta) == [{"name": "Python 3"}, {"name": "Git"}, {"name": "Docker"}]
    assert apply_editor_delta(rows, None) == rows


def test_skills_and_languages_come_from_the_tables():
    state = to_form_state(EXAMPLE)
    state[key("skills", "editor")] = {
        "edited_rows": {1: {"level": "avansat"}},
        "added_rows": [{"name": "Docker", "level": "începător", "years": float("nan")}, {}],
        "deleted_rows": [2],
    }
    state[key("languages", "editor")] = {"added_rows": [{"name": "Franceză", "level": "A2"}]}
    profile = from_form_state(state)
    assert [(s.name, s.level, s.years) for s in profile.skills] == [
        ("Python", SkillLevel.INTERMEDIATE, 1.5),
        ("SQL", SkillLevel.ADVANCED, None),
        ("Docker", SkillLevel.BEGINNER, None),
    ]
    assert [lang.name for lang in profile.languages] == ["Română", "Engleză", "Franceză"]


def test_language_without_level_is_reported():
    state = to_form_state(EXAMPLE)
    state[key("languages", "editor")] = {"added_rows": [{"name": "Germană"}]}
    assert errors_for(state) == ["Limbi străine #3 · Nivel: e obligatoriu"]
