"""Streamlit pages run headless with AppTest against a temporary database. No network."""

from datetime import timedelta

import pytest
from streamlit.testing.v1 import AppTest

from jobscraper import db
from jobscraper.config import PROJECT_ROOT
from jobscraper.models import JobPosting, JobStatus, TailoredCV, TailoredExperience, WorkMode, now_local
from jobscraper.profile import load_profile

APP = str(PROJECT_ROOT / "app.py")


def make_job(n: int, **fields) -> JobPosting:
    base = {
        "source": "bestjobs",
        "external_id": str(n),
        "url": f"https://www.bestjobs.eu/loc-de-munca/job-{n}",
        "title": f"Job {n}",
        "posted_at": now_local() - timedelta(days=n),
    }
    return JobPosting(**(base | fields))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point the app at a temporary config/DB and make sure no real API key is used."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sources:\n  linkedin: {enabled: true}\n  bestjobs: {enabled: false}\n"
        "search:\n  queries: [python]\n"
        "paths:\n  db: jobs.db\n  cache_dir: cache\n  profile: profile.yaml\n  output_dir: output\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBSCRAPER_CONFIG", str(config_path))
    # Present but empty: load_dotenv keeps it, so a key saved in the real .env is never used
    monkeypatch.setenv("SEARCHAPI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    return tmp_path / "jobs.db"


@pytest.fixture
def seeded(app_env):
    jobs = [
        make_job(1, title="Python Developer", company="Firma București", city="București", work_mode=WorkMode.HYBRID,
                 description="Python, Django și PostgreSQL", salary_text="2000 €/lună", salary_max=2000, currency="EUR"),
        make_job(2, title="Java Developer", city="Cluj-Napoca", work_mode=WorkMode.REMOTE),
        make_job(3, title="Tester", source="linkedin", linkedin_job_id="4470016722", via="LinkedIn"),
        make_job(4, title="Job vechi respins"),
    ]
    with db.open_db(app_env) as conn:
        db.upsert_jobs(conn, jobs)
        db.set_status(conn, jobs[3].id, JobStatus.REJECTED)
    return app_env, jobs


def run_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=15).run()


def table_titles(at: AppTest) -> list[str]:
    return list(at.dataframe[0].value["Titlu"])


def test_empty_database_points_to_search(app_env):
    at = run_app()
    assert not at.exception
    assert "Nu ai încă niciun job salvat." in at.info[0].value


def test_jobs_page_counts_and_default_filter(seeded):
    at = run_app()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Nou"] == "3" and metrics["Respins"] == "1"
    # rejected jobs are hidden by default; newest first
    assert table_titles(at) == ["Python Developer", "Java Developer", "Tester"]


def test_filters_narrow_the_table(seeded):
    at = run_app()
    at.text_input(key="f_text").set_value("bucuresti django").run()
    assert table_titles(at) == ["Python Developer"]

    at.text_input(key="f_text").set_value("").run()
    at.multiselect(key="f_modes").set_value([WorkMode.REMOTE]).run()
    assert table_titles(at) == ["Java Developer"]

    at.multiselect(key="f_modes").set_value([]).run()
    at.multiselect(key="f_status").set_value([JobStatus.REJECTED]).run()
    assert table_titles(at) == ["Job vechi respins"]


def test_opening_a_job_marks_it_seen_and_status_can_change(seeded):
    db_path, jobs = seeded
    at = AppTest.from_file(APP, default_timeout=15)
    at.session_state["selected_job_id"] = jobs[0].id
    at.run()
    assert not at.exception
    assert any(h.value == "Python Developer" for h in at.subheader)
    with db.open_db(db_path) as conn:
        assert db.get_job(conn, jobs[0].id).status is JobStatus.SEEN

    at.segmented_control[0].set_value(JobStatus.SAVED).run()
    with db.open_db(db_path) as conn:
        assert db.get_job(conn, jobs[0].id).status is JobStatus.SAVED
    assert any(h.value == "Python Developer" for h in at.subheader)  # panel stays open


def test_linkedin_details_without_key_shows_error(seeded):
    _, jobs = seeded
    at = AppTest.from_file(APP, default_timeout=15)
    at.session_state["selected_job_id"] = jobs[2].id
    at.run()
    details = next(b for b in at.button if b.label.startswith("Încarcă detaliile"))
    details.click().run()
    assert any("Lipsește cheia SearchApi.io" in e.value for e in at.error)


def test_keys_page_sets_a_session_key(app_env):
    at = run_app()
    at.switch_page("app_pages/keys.py").run()
    assert not at.exception
    at.text_input(key="key_input:SEARCHAPI_API_KEY").set_value("test-session-key-0000").run()
    at.button(key="use:SEARCHAPI_API_KEY").click().run()

    assert at.session_state["byok:SEARCHAPI_API_KEY"] == "test-session-key-0000"
    assert at.text_input(key="key_input:SEARCHAPI_API_KEY").value == ""  # input cleared
    assert any("test…0000 (din sesiune)" in m.value for m in at.markdown)


def profile_page(app_env) -> AppTest:
    at = run_app()
    at.switch_page("app_pages/profile.py").run()
    assert not at.exception
    return at


def test_profile_starts_from_example_and_saves(app_env):
    profile_path = app_env.parent / "profile.yaml"
    at = profile_page(app_env)
    assert any("Nu ai salvat încă un profil." in i.value for i in at.info)

    next(b for b in at.button if b.label == "Pornește de la exemplu").click().run()
    assert at.text_input(key="p.contact.full_name").value == "Prenume Nume"
    assert not profile_path.exists()  # loading the example does not save it

    at.text_input(key="p.contact.full_name").set_value("Ana Pop").run()
    next(b for b in at.button if b.label == "Salvează profilul").click().run()

    assert any(s.value == "Profil salvat." for s in at.success)
    saved = load_profile(profile_path)
    assert saved.contact.full_name == "Ana Pop"
    assert saved.courses[0].provider == "StepIT Academy"


def test_profile_validation_errors_are_shown_and_nothing_is_saved(app_env):
    at = profile_page(app_env)
    next(b for b in at.button if b.label == "Salvează profilul").click().run()
    assert any("Contact · Nume complet: e obligatoriu" in e.value for e in at.error)
    assert not (app_env.parent / "profile.yaml").exists()


def test_profile_add_and_remove_experience(app_env):
    at = profile_page(app_env)
    at.text_input(key="p.contact.full_name").set_value("Ana Pop").run()
    at.button(key="add:experience").click().run()
    (uid,) = at.session_state["p.experience"]
    at.text_input(key=f"p.experience.{uid}.role").set_value("Junior Developer")
    at.text_input(key=f"p.experience.{uid}.company").set_value("Firma SRL")
    at.text_input(key=f"p.experience.{uid}.start").set_value("2025-07").run()
    next(b for b in at.button if b.label == "Salvează profilul").click().run()

    saved = load_profile(app_env.parent / "profile.yaml")
    assert [(e.role, e.company, e.start) for e in saved.experience] == [("Junior Developer", "Firma SRL", "2025-07")]

    (uid,) = at.session_state["p.experience"]  # the form was rebuilt from the saved file
    at.button(key=f"remove:experience:{uid}").click().run()
    next(b for b in at.button if b.label == "Salvează profilul").click().run()
    assert load_profile(app_env.parent / "profile.yaml").experience == []


def test_profile_import_needs_a_pdf(app_env):
    at = profile_page(app_env)
    next(b for b in at.button if b.label == "Completează din PDF").click().run()
    assert any("Alege întâi PDF-ul" in e.value for e in at.error)


def test_profile_can_be_refilled_from_the_saved_import_without_claude(app_env):
    from tests.test_linkedin_import import IMPORTED

    (app_env.parent / "profile_linkedin.json").write_text(IMPORTED.model_dump_json(), encoding="utf-8")
    at = profile_page(app_env)
    next(b for b in at.button if b.label == "Refă din ultimul import").click().run()

    assert at.text_input(key="p.contact.full_name").value == "Ana Pop"
    assert any("fără un apel nou la Claude" in s.value for s in at.success)
    next(b for b in at.button if b.label == "Salvează profilul").click().run()
    saved = load_profile(app_env.parent / "profile.yaml")
    assert [(e.company, e.start) for e in saved.experience] == [("Firma Nouă", "2024-07"), ("Altă Firmă", "2021")]


def test_delete_profile_with_its_pdf_and_cvs(seeded):
    db_path, jobs = seeded
    profile_path = db_path.parent / "profile.yaml"
    profile_path.write_text((PROJECT_ROOT / "profile.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    pdf_path = db_path.parent / "profile_linkedin.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")
    cv = TailoredCV(language="ro", headline="Dev", summary="Rezumat.", skills=["Python"], experience=[],
                    projects=[], education_indices=[], course_indices=[], gaps=[])
    with db.open_db(db_path) as conn:
        db.add_cv(conn, jobs[0].id, cv, "claude-opus-5-5")

    at = profile_page(db_path)
    assert any("PDF atașat profilului" in c.value for c in at.caption)
    assert at.text_input(key="p.contact.full_name").value == "Prenume Nume"

    at.checkbox(key="delete_cvs_too").check().run()
    next(b for b in at.button if b.label == "Da, șterge").click().run()

    assert not profile_path.exists() and not pdf_path.exists()
    assert any("Profilul și PDF-ul atașat au fost șterse, împreună cu un CV generat." in s.value for s in at.success)
    assert at.text_input(key="p.contact.full_name").value == ""
    with db.open_db(db_path) as conn:
        assert db.latest_cv(conn, jobs[0].id) is None


def cv_page(db_path, job_id: str | None = None) -> AppTest:
    at = run_app()
    if job_id:
        at.session_state["cv_job_id"] = job_id
    at.switch_page("app_pages/cv.py").run()
    assert not at.exception
    return at


def test_cv_page_needs_a_profile(seeded):
    db_path, _ = seeded
    at = cv_page(db_path)
    assert any("Completează întâi profilul" in i.value for i in at.info)


@pytest.fixture
def with_profile(seeded):
    db_path, jobs = seeded
    (db_path.parent / "profile.yaml").write_text(
        (PROJECT_ROOT / "profile.example.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return db_path, jobs


def test_cv_generation_without_key_shows_error(with_profile):
    db_path, jobs = with_profile
    at = cv_page(db_path, jobs[0].id)  # has a description
    next(b for b in at.button if b.label == "Generează CV").click().run()
    assert any("Lipsește cheia Anthropic" in e.value for e in at.error)


def test_cv_with_invented_skill_cannot_be_exported(with_profile):
    db_path, jobs = with_profile
    cv = TailoredCV(
        language="ro", headline="Dev", summary="Rezumat.", skills=["Python", "Kubernetes"],
        experience=[TailoredExperience(profile_index=0, bullets=["Am verificat date."])],
        projects=[], education_indices=[0], course_indices=[], gaps=["Docker"],
    )
    with db.open_db(db_path) as conn:
        db.add_cv(conn, jobs[0].id, cv, "claude-opus-5-5")

    at = cv_page(db_path, jobs[0].id)
    assert any("Kubernetes" in e.value for e in at.error)
    assert next(b for b in at.button if b.label == "Exportă PDF").disabled
    assert any("Docker" in i.value for i in at.info)  # gaps are shown, not put in the CV


def focus_cv_page() -> AppTest:
    at = run_app()
    at.session_state["cv_mode"] = "focus"
    at.switch_page("app_pages/cv.py").run()
    assert not at.exception
    return at


def test_cv_for_a_type_of_job_without_key_shows_error(with_profile):
    at = focus_cv_page()
    assert at.selectbox(key="cv_focus_choice").value == "Testare / QA"
    next(b for b in at.button if b.label == "Generează CV").click().run()
    assert any("Lipsește cheia Anthropic" in e.value for e in at.error)


def test_saved_cv_for_a_type_of_job_is_shown_with_its_gaps(with_profile):
    db_path, _ = with_profile
    cv = TailoredCV(
        language="ro", headline="Tester junior", summary="Rezumat.", skills=["Python"],
        experience=[TailoredExperience(profile_index=0, bullets=["Am verificat datele rapoartelor."])],
        projects=[], education_indices=[0], course_indices=[], gaps=["ISTQB"],
    )
    with db.open_db(db_path) as conn:
        db.add_cv(conn, None, cv, "claude-opus-5-5", focus="Testare / QA")

    at = focus_cv_page()
    assert next(b for b in at.button if b.label == "Generează din nou")
    assert at.text_input(key=next(k for k in at.session_state if str(k).endswith(".headline"))).value == "Tester junior"
    assert any("Ce se cere de obicei la acest tip de job" in i.value and "ISTQB" in i.value for i in at.info)


def make_cv(headline: str, language: str = "ro") -> TailoredCV:
    return TailoredCV(
        language=language, headline=headline, summary="Rezumat.", skills=["Python"],
        experience=[TailoredExperience(profile_index=0, bullets=["Am verificat datele rapoartelor."])],
        projects=[], education_indices=[0], course_indices=[], gaps=[],
    )


def library_page() -> AppTest:
    at = run_app()
    at.switch_page("app_pages/cvs.py").run()
    assert not at.exception
    return at


@pytest.fixture
def with_cvs(with_profile):
    db_path, jobs = with_profile
    with db.open_db(db_path) as conn:
        old = db.add_cv(conn, None, make_cv("Tester junior (prima versiune)"), "claude-opus-5-5", focus="Testare / QA")
        db.add_cv(conn, None, make_cv("Tester junior"), "claude-opus-5-5", focus="Testare / QA")
        db.add_cv(conn, jobs[0].id, make_cv("Python Developer", "en"), "claude-opus-5-5")
    return db_path, jobs, old


def card_titles(at: AppTest) -> list[str]:
    return [h.value for h in at.get("html") if h.value.startswith('<div class="jl-card-title">')]


def test_library_lists_every_cv_and_filters_by_type(with_cvs):
    at = library_page()
    assert any(m.label == "Total" and m.value == "3" for m in at.metric)
    assert len(card_titles(at)) == 3

    at.multiselect(key="l_types").set_value(["Dezvoltare software"]).run()
    (title,) = card_titles(at)
    assert "Python Developer" in title and "1 din 3 CV-uri" in at.caption[0].value

    at.multiselect(key="l_types").set_value([]).run()
    at.radio(key="l_target").set_value("focus").run()
    assert len(card_titles(at)) == 2


def test_library_opens_the_chosen_version_on_the_cv_page(with_cvs):
    _, _, old = with_cvs
    at = library_page()
    at.button(key=f"open:{old}").click().run()

    assert not at.exception
    assert at.session_state["cv_mode"] == "focus"
    assert at.selectbox(key="cv_focus_choice").value == "Testare / QA"
    headline = next(k for k in at.session_state if str(k).endswith(".headline"))
    assert at.text_input(key=headline).value == "Tester junior (prima versiune)"  # not the newest one
    assert any(s.label == "Versiune" for s in at.selectbox)


def test_library_deletes_a_cv(with_cvs):
    db_path, jobs, _ = with_cvs
    at = library_page()
    with db.open_db(db_path) as conn:
        (job_cv,) = db.cv_versions(conn, job_id=jobs[0].id)
    at.button(key=f"delete:{job_cv.id}").click().run()

    assert any("Am șters CV-ul „Python Developer”" in s.value for s in at.success)
    assert len(card_titles(at)) == 2
    with db.open_db(db_path) as conn:
        assert db.cv_versions(conn, job_id=jobs[0].id) == []


def test_cv_page_shows_the_ad(with_profile):
    _, jobs = with_profile
    at = cv_page(None, jobs[0].id)
    (expander,) = [e for e in at.expander if e.label == "Anunțul"]
    assert any("Python, Django și PostgreSQL" in h.value for h in expander.get("html"))


def test_jobs_are_ranked_by_match_with_a_profile(with_profile):
    at = run_app()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table.columns[:2]) == ["Potrivire", "Titlu"]
    assert list(table["Potrivire"]) == sorted(table["Potrivire"], reverse=True)
    assert table["Titlu"].iloc[0] == "Python Developer"  # Python + Django, București, matches the roles


def test_preferences_and_excluded_keywords_hide_jobs_and_say_how_many(with_profile):
    db_path, _ = with_profile
    config_path = db_path.parent / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("search:\n", "search:\n  keywords_excluded: [java]\n"),
        encoding="utf-8",
    )
    at = run_app()
    # "java" from config.yaml; "Tester" is not one of the profile roles (Python / Junior Developer)
    assert table_titles(at) == ["Python Developer"]
    assert any("2 ascunse de preferințe" in c.value for c in at.caption)

    at.toggle(key="f_hide_excluded").set_value(False).run()
    assert set(table_titles(at)) == {"Python Developer", "Java Developer", "Tester"}


def test_hidden_jobs_can_be_shown_with_one_click(with_profile):
    at = run_app()
    assert table_titles(at) == ["Python Developer", "Java Developer"]  # "Tester" fits none of the profile roles
    next(b for b in at.button if b.label == "Arată-le").click().run()
    assert at.toggle(key="f_hide_excluded").value is False
    assert set(table_titles(at)) == {"Python Developer", "Java Developer", "Tester"}


def test_search_results_open_in_joburi_even_when_preferences_hide_them(with_profile):
    _, jobs = with_profile
    found = [jobs[0].id, jobs[2].id]  # Python Developer fits the profile; Tester fits none of its roles
    at = run_app()
    at.session_state["last_search"] = {"keywords": "python tester", "ids": found}
    at.switch_page("app_pages/search.py").run()
    assert any("1** se potrivesc preferințelor" in m.value and "rol (1)" in m.value for m in at.markdown)

    next(b for b in at.button if b.label == "Vezi toate cele 2, fără preferințe").click().run()
    assert not at.exception
    assert set(table_titles(at)) == {"Python Developer", "Tester"}  # only this search, preferences off
    assert any("Doar rezultatele căutării „python tester”" in c.value for c in at.caption)

    # AppTest reruns the page it was last switched to; the browser stays on Joburi after st.switch_page
    at.switch_page("app_pages/jobs.py").run()
    next(b for b in at.button if b.label == "Arată toate joburile").click().run()
    assert set(table_titles(at)) == {"Python Developer", "Java Developer", "Tester"}


def test_detail_explains_the_score(with_profile):
    _, jobs = with_profile
    at = AppTest.from_file(APP, default_timeout=15)
    at.session_state["selected_job_id"] = jobs[0].id
    at.run()
    (expander,) = [e for e in at.expander if e.label.startswith("Potrivire")]
    assert expander.label.endswith("% · de ce?")
    assert any("Titlul se potrivește" in m.value for m in expander.markdown)


def test_copies_of_one_ad_are_one_row(app_env):
    bestjobs = make_job(1, title="Data Engineer", company="Firma S.R.L.", city="Bd. Unirii 1, București",
                        description="Descriere completă")
    hipo = make_job(2, title="Data Engineer (m/f/d)", company="FIRMA SRL", city="BUCURESTI", source="hipo",
                    url="https://www.hipo.ro/locuri-de-munca/locuri_de_munca/2/Firma/Data-Engineer-(m/f/d)")
    with db.open_db(app_env) as conn:
        db.upsert_jobs(conn, [bestjobs, hipo])

    at = run_app()
    table = at.dataframe[0].value
    assert list(table["Titlu"]) == ["Data Engineer"]  # the copy with a description is shown
    assert list(table["Prin"]) == ["BestJobs + Hipo"]
    assert any("1 duplicate unite" in c.value for c in at.caption)

    at.session_state["selected_job_id"] = bestjobs.id
    at.run()
    (copies,) = [m for m in at.markdown if "Același anunț și pe" in m.value]
    assert "[Hipo](https://www.hipo.ro/locuri-de-munca/locuri_de_munca/2/Firma/Data-Engineer-%28m/f/d%29)" in copies.value


def test_search_page_without_key(app_env):
    at = run_app()
    at.switch_page("app_pages/search.py").run()
    assert not at.exception
    assert at.multiselect[0].value == ["linkedin"]
    next(b for b in at.button if b.label == "Caută").click().run()
    assert any("Lipsește cheia SearchApi.io" in e.value for e in at.error)
