import pytest
from pydantic import ValidationError

from jobscraper.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, ScrapingConfig, load_config


def test_project_config_loads(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_CONFIG", raising=False)
    config = load_config()
    assert config.enabled_sources() == ["linkedin", "bestjobs", "hipo"]  # Google Jobs does not support gl=ro
    assert config.searchapi.linkedin_time_period is None
    assert {"ejobs", "bestjobs", "hipo", "olx"} <= set(config.sources)
    assert (config.searchapi.gl, config.searchapi.hl) == ("ro", "ro")
    assert config.llm.model == "claude-opus-5-5"


def test_paths_resolve_relative_to_config_file():
    config = load_config(DEFAULT_CONFIG_PATH)
    assert config.paths.db == (PROJECT_ROOT / "data" / "jobs.db").resolve()
    assert config.paths.db.is_absolute()


def test_config_in_other_folder_resolves_there(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("paths:\n  db: x/jobs.db\nsources:\n  ejobs: {enabled: false}\n", encoding="utf-8")
    config = load_config(path)
    assert config.paths.db == (tmp_path / "x" / "jobs.db").resolve()
    assert config.paths.profile == (tmp_path / "data" / "profile.yaml").resolve()
    assert config.enabled_sources() == []


def test_env_var_selects_another_config(tmp_path, monkeypatch):
    path = tmp_path / "demo.yaml"
    path.write_text("paths:\n  db: demo.db\n", encoding="utf-8")
    monkeypatch.setenv("JOBSCRAPER_CONFIG", str(path))
    assert load_config().paths.db == (tmp_path / "demo.db").resolve()


def test_scraping_delay_cannot_go_below_two_seconds():
    with pytest.raises(ValidationError):
        ScrapingConfig(min_delay_seconds=0.5)
