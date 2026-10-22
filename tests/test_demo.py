"""Demo data: offline, valid against the example profile, safe to run twice."""

from jobscraper import db
from jobscraper.config import AppConfig, PathsConfig
from jobscraper.cv.validate import check_cv
from jobscraper.demo import seed_demo
from jobscraper.profile import load_profile, save_profile


def demo_config(tmp_path) -> AppConfig:
    return AppConfig(paths=PathsConfig().resolve_against(tmp_path))


def test_demo_fills_an_empty_app(tmp_path):
    config = demo_config(tmp_path)
    result = seed_demo(config)
    assert result.profile_created and result.cvs == 2
    assert result.jobs == result.new_jobs > 20

    profile = load_profile(config.paths.profile)
    assert profile.contact.full_name == "Prenume Nume"  # fictional
    with db.open_db(config.paths.db) as conn:
        assert {job.source for job in db.list_jobs(conn)} == {"bestjobs", "hipo", "linkedin"}
        assert any(job.description for job in db.list_jobs(conn))
        cvs = db.all_cvs(conn)
    assert {cv.focus for cv in cvs} == {None, "Testare / QA"}
    for stored in cvs:  # the hand-written CVs only use facts from the example profile
        check = check_cv(stored.cv, profile)
        assert check.ok and not check.warnings


def test_demo_runs_again_without_duplicates_and_keeps_a_real_profile(tmp_path):
    config = demo_config(tmp_path)
    seed_demo(config)
    profile = load_profile(config.paths.profile)
    save_profile(profile.model_copy(update={"headline": "Profilul meu"}), config.paths.profile)

    again = seed_demo(config)
    assert (again.new_jobs, again.cvs, again.profile_created) == (0, 0, False)
    assert load_profile(config.paths.profile).headline == "Profilul meu"
