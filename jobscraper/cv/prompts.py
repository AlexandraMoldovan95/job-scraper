"""Prompt for the tailored CV. Order for prompt caching: fixed system prompt, then the
profile (changes rarely), then the job ad (changes every call) last."""

from __future__ import annotations

import json
from typing import Literal

from jobscraper.models import JobPosting, Profile

CVLanguage = Literal["auto", "ro", "en"]

# Long ads are mostly boilerplate; the cap keeps the cost of one CV predictable.
MAX_JOB_DESCRIPTION_CHARS = 20_000

# Types of job a CV can focus on (labels shown in the UI -> what the model should look for).
# The user can also type their own focus.
FOCUS_PRESETS = {
    "Testare / QA": "software testing and quality assurance: test cases, manual and automated testing, "
    "bug reports, test tools, attention to detail",
    "Dezvoltare software": "software development: writing, debugging and shipping code, back end or front end, "
    "APIs, databases, version control",
    "Date / BI": "data analysis and business intelligence: SQL, reports, dashboards, cleaning and explaining data",
    "DevOps / Cloud": "DevOps and cloud: CI/CD, automation and scripting, infrastructure, deployment, monitoring",
    "Suport IT": "IT support and helpdesk: troubleshooting, helping users, installing and configuring, documentation",
}

SYSTEM_PROMPT = """\
You write tailored CVs for a candidate in Romania. You receive the candidate's profile and \
either one job ad, a type of job to focus on (<focus>), or both, and you return the CV as \
structured data.

The profile is the only source of facts. You may select, reorder, rephrase and emphasize \
what it contains, but never add an employer, title, date, school, certification, skill, \
tool or number that is not in it. A CV that claims experience the candidate does not have \
can cost them the job later, so when the ad asks for something the profile does not show, \
put that requirement in `gaps` (short, in the CV language) instead of the CV.

Writing for a type of job (a focus, e.g. QA / testing or software development):
- With no ad, write a CV the candidate can send to any job of that type. Present the \
candidate through that lens: lead with the experience, studies, courses and projects that \
show it, and describe each entry from that angle (for QA, the checking, test data and bugs \
found in a development job; for development, the code written in a testing job).
- Reframe only what is true: describe what the candidate actually did, never a role, task \
or responsibility the profile does not mention.
- `gaps` lists what employers usually ask for in that type of job and the profile does not \
show.
- With both an ad and a focus, write for the ad and give the focus extra weight where the \
ad allows it.

How the output maps to the profile:
- Experience, projects, education and courses are referenced by their `index` in the \
profile. Companies, roles, dates and schools are filled in from the profile afterwards, so \
you only write the bullets and descriptions.
- `skills` may only contain names from the profile's skills or from the technologies of its \
experience and projects, spelled as in the profile. Put the ones the ad asks for first.

Writing:
- Each experience entry gets 2-5 bullets that start with an action verb and show results; \
use the profile's achievements and description for that entry only. Keep numbers exactly as \
the profile states them.
- The headline is one line naming the role the candidate targets with this application.
- The summary is 2-4 sentences connecting the candidate's real background to this job or \
type of job.
- Leave out entries that do not help for this job, but keep the experience section non-empty \
when the profile has experience. The whole CV must fit on two pages.
- Use the ad's vocabulary where it truthfully describes the candidate: CVs in Romania are \
often screened by ATS keyword matching.
"""


def profile_for_prompt(profile: Profile) -> str:
    """Profile as JSON with explicit indices; deterministic, so the cached prefix stays stable."""
    data = profile.model_dump(mode="json", exclude_none=True, exclude={"preferences"})
    data["contact"] = {"full_name": profile.contact.full_name}  # the model does not need contact details
    for section in ("experience", "education", "courses", "projects"):
        data[section] = [{"index": i, **entry} for i, entry in enumerate(data.get(section, []))]
    return json.dumps(data, ensure_ascii=False, indent=1)


def job_for_prompt(job: JobPosting) -> str:
    lines = [f"Title: {job.title}"]
    for label, value in (
        ("Company", job.company),
        ("Location", job.city),
        ("Work mode", job.work_mode),
        ("Employment type", job.employment_type),
        ("Seniority", job.seniority),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if job.requirements:
        lines.append("Requirements:\n" + "\n".join(f"- {item}" for item in job.requirements))
    description = job.description[:MAX_JOB_DESCRIPTION_CHARS]
    lines.append(f"Description:\n{description}")
    return "\n".join(lines)


def language_instruction(language: CVLanguage, *, has_ad: bool = True) -> str:
    if language == "en":
        return "Write the CV in English (language: en)."
    if language == "ro" or not has_ad:
        return "Write the CV in Romanian (language: ro)."
    return "Write the CV in the language the job ad is written in: Romanian (ro) or English (en)."


def focus_for_prompt(focus: str) -> str:
    """The preset's description helps the model; a custom focus is the user's own words."""
    hint = FOCUS_PRESETS.get(focus)
    return f"Type of job: {focus}" + (f"\nWhat it involves: {hint}" if hint else "")


def build_user_content(
    profile: Profile, job: JobPosting | None, language: CVLanguage, *, focus: str | None = None
) -> list[dict]:
    """Profile (cached), then the ad and/or the focus. At least one of `job` and `focus`."""
    if job is None and not focus:
        raise ValueError("a CV needs a job ad or a focus")
    parts = []
    if job is not None:
        parts.append(f"<job_ad>\n{job_for_prompt(job)}\n</job_ad>")
    if focus:
        parts.append(f"<focus>\n{focus_for_prompt(focus)}\n</focus>")
    if job is None:
        parts.append("Write a CV for this type of job, not for one specific ad.")
    parts.append(language_instruction(language, has_ad=job is not None))
    return [
        {
            "type": "text",
            "text": f"<profile>\n{profile_for_prompt(profile)}\n</profile>",
            # Cache breakpoint after the profile: several CVs in a row reuse it
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "\n\n".join(parts)},
    ]
