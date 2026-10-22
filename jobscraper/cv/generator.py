"""Calls Claude to turn the profile + one job ad into a TailoredCV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobscraper import llm as claude
from jobscraper.config import LLMConfig
from jobscraper.cv.prompts import SYSTEM_PROMPT, CVLanguage, build_user_content
from jobscraper.llm import FALLBACK_BETA, FALLBACK_MODELS, make_client
from jobscraper.models import JobPosting, Profile, TailoredCV

__all__ = ["FALLBACK_BETA", "FALLBACK_MODELS", "CVGenerationError", "GeneratedCV", "generate_cv", "make_client"]

CVGenerationError = claude.LLMError  # the UI catches either name


@dataclass(frozen=True)
class GeneratedCV:
    cv: TailoredCV
    model: str  # the model that actually answered (differs after a fallback)
    input_tokens: int
    output_tokens: int


def generate_cv(
    client: Any,
    profile: Profile,
    job: JobPosting | None,
    *,
    llm: LLMConfig,
    language: CVLanguage = "auto",
    focus: str | None = None,
) -> GeneratedCV:
    """A CV for one ad (optionally leaning towards `focus`), or with `job=None` for a type of job."""
    focus = (focus or "").strip() or None
    if job is None and focus is None:
        raise CVGenerationError("Alege un anunț sau un tip de job.")
    if job is not None and not job.description.strip():
        raise CVGenerationError("Jobul nu are descriere; încarcă întâi detaliile anunțului.")
    result = claude.parse(
        client,
        llm=llm,
        output_format=TailoredCV,
        system=SYSTEM_PROMPT,
        content=build_user_content(profile, job, language, focus=focus),
        what="acest CV",
    )
    return GeneratedCV(
        cv=result.output, model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens
    )
