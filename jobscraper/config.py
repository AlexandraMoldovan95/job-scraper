"""Loads config.yaml and .env into a validated AppConfig."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from jobscraper.models import WorkMode

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class SourceConfig(BaseModel):
    enabled: bool = True


class SearchConfig(BaseModel):
    queries: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    work_modes: list[WorkMode] = Field(default_factory=lambda: list(WorkMode))
    keywords_required: list[str] = Field(default_factory=list)
    keywords_excluded: list[str] = Field(default_factory=list)
    min_salary: int | None = Field(default=None, ge=0)


class ScrapingConfig(BaseModel):
    user_agent: str = "JobScraperPersonal/0.1 (personal use, low volume)"
    # Politeness floor from CLAUDE.md: never faster than one request per 2s per domain
    min_delay_seconds: float = Field(default=2.5, ge=2.0)
    timeout_seconds: float = Field(default=20, gt=0)
    cache_ttl_hours: float = Field(default=12, ge=0)
    max_pages_per_query: int = Field(default=3, ge=1, le=20)
    # Direct scrapers: load each new job's full ad right after the search (one polite request
    # per job, so capped per source and search). LinkedIn details cost a credit: never automatic.
    auto_details: bool = True
    max_auto_details: int = Field(default=40, ge=0)


LinkedInTimePeriod = Literal["last_hour", "last_day", "last_week", "last_month"]


class SearchApiConfig(BaseModel):
    """Parameters for the SearchApi.io engines (google_jobs, linkedin_jobs)."""

    location: str = "Romania"
    gl: str = "ro"  # Google Jobs country
    hl: str = "ro"  # Google Jobs interface language
    linkedin_time_period: LinkedInTimePeriod | None = None  # None = any posting date


class LLMConfig(BaseModel):
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = Field(default=16000, gt=0)


class PathsConfig(BaseModel):
    db: Path = Path("data/jobs.db")
    profile: Path = Path("data/profile.yaml")
    cache_dir: Path = Path("data/cache")
    output_dir: Path = Path("data/output")

    def resolve_against(self, base: Path) -> PathsConfig:
        return PathsConfig(
            **{name: (base / value).resolve() for name, value in self.model_dump().items()}
        )


class AppConfig(BaseModel):
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    search: SearchConfig = Field(default_factory=SearchConfig)
    scraping: ScrapingConfig = Field(default_factory=ScrapingConfig)
    searchapi: SearchApiConfig = Field(default_factory=SearchApiConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    def enabled_sources(self) -> list[str]:
        return [name for name, source in self.sources.items() if source.enabled]


def load_config(path: Path | None = None) -> AppConfig:
    """Read the YAML config; relative paths inside it are resolved against its folder.

    Without `path`, uses $JOBSCRAPER_CONFIG if set (e.g. a demo config with its own
    database), else config.yaml. Also loads .env from the project root.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    path = path or Path(os.environ.get("JOBSCRAPER_CONFIG") or DEFAULT_CONFIG_PATH)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(raw)
    config.paths = config.paths.resolve_against(path.parent)
    return config
