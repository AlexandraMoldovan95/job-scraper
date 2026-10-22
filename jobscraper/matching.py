"""Deterministic match score (0-100) between the profile and a job, with reasons in Romanian.

Pure and cheap (no LLM, no network), so it runs on every job shown in the UI.

    skills     50  share of the skills/technologies the ad asks for that the profile has
    role       20  job title vs the roles in the profile preferences
    seniority  15  level the ad asks for vs years of experience in the profile
    location   15  remote / preferred cities / preferred work modes

Hard filters do not change the score; they mark the job as excluded, with the reason:
excluded keywords in the title, required keywords, and the profile preferences (work mode,
city, level, role, minimum salary). A job whose value is unknown is never excluded for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum
from functools import lru_cache

from jobscraper.config import SearchConfig
from jobscraper.dedupe import normalize_city
from jobscraper.models import JobPosting, Profile, WorkMode
from jobscraper.normalize import strip_diacritics

SKILL_POINTS, ROLE_POINTS, SENIORITY_POINTS, LOCATION_POINTS = 50, 20, 15, 15

# Rough conversion for the minimum-salary filter only (BestJobs shows EUR, the profile uses RON)
RON_PER_CURRENCY = {"RON": 1.0, "EUR": 5.0, "USD": 4.6}

# Canonical skill -> other spellings. Terms that are also common words ("go", "r", "c") are left out.
_TECH_TERMS: dict[str, tuple[str, ...]] = {
    "python": (), "java": (), "javascript": ("js",), "typescript": (), "c#": ("csharp",), "c++": ("cpp",),
    ".net": ("dotnet", "asp.net"), "php": (), "ruby": (), "golang": (), "rust": (), "kotlin": (), "swift": (),
    "scala": (), "html": ("html5",), "css": ("css3",), "sql": (), "postgresql": ("postgres",), "mysql": (),
    "sql server": ("mssql",), "oracle": (), "mongodb": ("mongo",), "redis": (), "elasticsearch": (),
    "react": ("react.js", "reactjs"), "angular": (), "vue": ("vue.js", "vuejs"), "node.js": ("nodejs", "node"),
    "django": (), "flask": (), "fastapi": (), "spring": ("spring boot",), "laravel": (), "docker": (),
    "kubernetes": ("k8s",), "terraform": (), "ansible": (), "jenkins": (), "ci/cd": ("cicd",), "git": (),
    "linux": (), "bash": (), "aws": ("amazon web services",), "azure": (), "gcp": ("google cloud",),
    "rest api": ("restful", "rest apis"), "graphql": (), "kafka": (), "rabbitmq": (), "pandas": (), "numpy": (),
    "spark": ("pyspark",), "airflow": (), "tensorflow": (), "pytorch": (), "machine learning": ("ml",),
    "power bi": ("powerbi",), "tableau": (), "excel": ("ms excel",), "sap": (), "jira": (), "scrum": (),
    "agile": (), "selenium": (), "playwright": (), "figma": (), "android": (), "ios": (), "flutter": (),
    "streamlit": (), "sqlite": (), "etl": (), "salesforce": (), "autocad": (),
}

_JUNIOR = ("junior", "entry", "intern", "internship", "stagiu", "stagiar", "trainee", "absolvent", "graduate", "student")
_MID = ("mid", "middle", "medior", "intermediate")
_SENIOR = ("senior", "lead", "principal", "architect", "head", "manager", "staff")
_LEVEL_WORDS = frozenset((*_JUNIOR, *_MID, *_SENIOR))

# Role words that mean the same job: a "Python Developer" search also wants "Python Engineer",
# and job titles in Romania are as often in English as in Romanian
_ROLE_SYNONYMS = (
    ("developer", "dezvoltator", "programmer", "programator", "engineer", "inginer", "dev"),
    ("analyst", "analist"),
    ("tester", "qa"),
    ("ospatar", "ospatarita", "waiter", "waitress"),
    ("bucatar", "cook", "chef"),
    ("contabil", "accountant"),
    ("sofer", "driver"),
    ("vanzator", "vanzatoare", "sales"),
)
_SYNONYMS = {word: group for group in _ROLE_SYNONYMS for word in group}
# "1 - 5 ani" asks for at least 1 year (Hipo levels look like this), "3+ years" for at least 3
_YEARS_RANGE = re.compile(r"(\d{1,2})\s*(?:-|–|to|pana la)\s*\d{1,2}\s*(?:ani|an|years?)\b")
_YEARS_MIN = re.compile(r"(\d{1,2})\s*\+?\s*(?:ani|an|years?)\b")


class Level(IntEnum):
    JUNIOR = 0
    MID = 1
    SENIOR = 2


LEVEL_LABELS = {Level.JUNIOR: "junior", Level.MID: "mid", Level.SENIOR: "senior"}


@dataclass(frozen=True)
class MatchSettings:
    """What the user wants; profile preferences first, config.search as fallback."""

    roles: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    work_modes: list[WorkMode] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)
    min_salary_ron: int | None = None
    keywords_required: list[str] = field(default_factory=list)
    keywords_excluded: list[str] = field(default_factory=list)
    # Roles hide unrelated titles only when they come from the profile: config queries are search terms
    filter_by_role: bool = False

    @classmethod
    def from_sources(cls, profile: Profile, search: SearchConfig) -> MatchSettings:
        prefs = profile.preferences
        return cls(
            roles=prefs.roles or search.queries,
            cities=prefs.cities or search.cities,
            work_modes=prefs.work_modes or search.work_modes,
            levels=[Level[level.name] for level in prefs.levels],
            min_salary_ron=prefs.min_salary or search.min_salary,
            keywords_required=search.keywords_required,
            keywords_excluded=list(dict.fromkeys([*search.keywords_excluded, *prefs.excluded_keywords])),
            filter_by_role=bool(prefs.roles),
        )


@dataclass(frozen=True)
class MatchResult:
    score: int
    matched_skills: list[str]
    missing_skills: list[str]
    reasons: list[str]  # "+ …" helps, "− …" hurts
    excluded_by: str | None = None


# --- text helpers --------------------------------------------------------------------------


def _plain(text: str | None) -> str:
    return " ".join(strip_diacritics(text or "").lower().split())


_BEFORE, _AFTER = r"(?<![a-z0-9.])", r"(?![a-z0-9#+])"


def _contains(haystack: str, term: str) -> bool:
    """Whole-term match that also works for c#, .net, node.js, ci/cd ("js" must not match in "node.js")."""
    return re.search(rf"{_BEFORE}{re.escape(term)}{_AFTER}", haystack) is not None


@lru_cache(maxsize=16)
def _term_finder(spellings: tuple[str, ...]) -> re.Pattern[str]:
    """One regex for all spellings, longest first ("spring boot" before "spring")."""
    alternatives = "|".join(re.escape(s) for s in sorted(spellings, key=len, reverse=True))
    return re.compile(rf"{_BEFORE}(?:{alternatives}){_AFTER}")


def _has_role_word(title: str, word: str) -> bool:
    return any(_contains(title, spelling) for spelling in _SYNONYMS.get(word, (word,)))


def _role_words(role: str) -> list[str]:
    return [w for w in _plain(role).split() if len(w) > 2]


def fits_a_role(title: str, roles: list[str]) -> bool:
    """The title has a word of some role, level words aside ("Junior Developer" -> any developer)."""
    plain = _plain(title)
    for role in roles:
        words = [w for w in _role_words(role) if w not in _LEVEL_WORDS]
        if not words or any(_has_role_word(plain, w) for w in words):
            return True
    return False


# Search words that do not say what the job is
_GENERIC_SEARCH_WORDS = frozenset({
    "remote", "hybrid", "hibrid", "job", "jobs", "joburi", "loc", "locuri", "munca", "full", "part", "time",
    "and", "the", "for", "with", "din", "pentru",
})


def search_words(keywords: str) -> list[str]:
    """The words of a search that name the job or skill: level and generic words aside."""
    words = re.findall(r"[a-z0-9+#]+", _plain(keywords))
    return [w for w in words if len(w) >= 3 and w not in _LEVEL_WORDS and w not in _GENERIC_SEARCH_WORDS]


def mentions_search(job: JobPosting, words: list[str]) -> bool:
    """The title (or the description, when loaded) has one of the words, a longer form of
    it ("ospatar" in "ospatarita") or a synonym ("waiter")."""
    found = set(re.findall(r"[a-z0-9+#]+", _plain(f"{job.title} {job.description}")))
    for word in words:
        for spelling in _SYNONYMS.get(word, (word,)):
            if spelling in found or (len(spelling) >= 4 and any(token.startswith(spelling) for token in found)):
                return True
    return False


def unrelated_results(jobs: list[JobPosting], keywords: str) -> bool:
    """A source with no match for a search answers with general jobs instead (LinkedIn, Hipo):
    none of them mentions any search word. Searches of generic words only are never judged."""
    words = search_words(keywords)
    return bool(words and jobs) and not any(mentions_search(job, words) for job in jobs)


def city_matches(city: str | None, wanted: list[str]) -> bool | None:
    """Whether the job's city is one of `wanted` ("Bucharest" = "București"); None when the job
    names no city we can tell (remote, "Romania", missing)."""
    found = normalize_city(city)
    if not found:
        return None
    plain = _plain(city)
    return any(normalize_city(w) == found or _contains(plain, _plain(w)) for w in wanted)


def _canonical(term: str) -> str:
    plain = _plain(term)
    for canonical, aliases in _TECH_TERMS.items():
        if plain == canonical or plain in aliases:
            return canonical
    return plain


def _ad_text(job: JobPosting) -> str:
    return _plain(" ".join([job.title, job.description, *job.requirements]))


# --- profile facts -------------------------------------------------------------------------


def profile_terms(profile: Profile) -> dict[str, str]:
    """canonical -> name as written in the profile, for every skill and technology."""
    names = [s.name for s in profile.skills]
    for entry in [*profile.experience, *profile.projects]:
        names.extend(entry.technologies)
    return {_canonical(name): name for name in reversed(names)}  # first spelling wins


def _year_month(value: str) -> tuple[int, int]:
    """"2022-09" -> (2022, 9); a year alone counts from mid-year, the least biased guess."""
    year, _, month = value.partition("-")
    return int(year), int(month) if month else 7


def experience_years(profile: Profile, today: date) -> float:
    months = 0
    for job in profile.experience:
        start_year, start_month = _year_month(job.start)
        end_year, end_month = _year_month(job.end) if job.end else (today.year, today.month)
        months += max(0, (end_year - start_year) * 12 + end_month - start_month)
    return months / 12


def candidate_level(years: float) -> Level:
    return Level.JUNIOR if years < 2 else Level.MID if years < 5 else Level.SENIOR


def job_level(job: JobPosting) -> Level | None:
    head = _plain(f"{job.title} {job.seniority or ''}")
    # An explicit "junior" wins ("Junior Project Manager"); then senior words, then mid
    for words, level in ((_JUNIOR, Level.JUNIOR), (_SENIOR, Level.SENIOR), (_MID, Level.MID)):
        if any(_contains(head, word) for word in words):
            return level
    for text in (job.seniority, job.description):
        asked = years_asked(_plain(text))
        if asked:
            return candidate_level(max(asked))
    return None


def years_asked(text: str) -> list[int]:
    """Minimum years of experience the text mentions: lower bound of ranges, else "N+ years"."""
    lows = [int(n) for n in _YEARS_RANGE.findall(text)]
    lows += [int(n) for n in _YEARS_MIN.findall(_YEARS_RANGE.sub(" ", text))]
    return [n for n in lows if n <= 15]


# --- scoring -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProfileFacts:
    """Everything scoring needs from the profile, computed once per batch of jobs."""

    have: dict[str, str]
    spelling_to_term: dict[str, str]
    finder: re.Pattern[str]
    order: list[str]
    years: float


def _profile_facts(profile: Profile, today: date) -> _ProfileFacts:
    have = profile_terms(profile)
    spelling_to_term = {spelling: term for term, aliases in _TECH_TERMS.items() for spelling in (term, *aliases)}
    spelling_to_term |= {term: term for term in have}
    return _ProfileFacts(
        have=have,
        spelling_to_term=spelling_to_term,
        finder=_term_finder(tuple(sorted(spelling_to_term))),
        order=list(dict.fromkeys([*_TECH_TERMS, *have])),
        years=experience_years(profile, today),
    )


def score_jobs(
    jobs: list[JobPosting], profile: Profile, settings: MatchSettings, *, today: date
) -> dict[str, MatchResult]:
    facts = _profile_facts(profile, today)
    return {job.id: _score(job, facts, settings) for job in jobs}


def score_job(job: JobPosting, profile: Profile, settings: MatchSettings, *, today: date) -> MatchResult:
    return _score(job, _profile_facts(profile, today), settings)


def _score(job: JobPosting, facts: _ProfileFacts, settings: MatchSettings) -> MatchResult:
    text = _ad_text(job)
    reasons: list[str] = []

    # Skills: which terms the ad mentions (known tech + anything in the profile), which we have
    have = facts.have
    found = {facts.spelling_to_term[m.group(0)] for m in facts.finder.finditer(text)}
    asked = [term for term in facts.order if term in found]
    matched = [have[t] for t in asked if t in have]
    missing = [t for t in asked if t not in have]
    if asked:
        skill_points = SKILL_POINTS * len(matched) / len(asked)
        if matched:
            reasons.append(f"+ Skill-uri cerute pe care le ai: {', '.join(matched)} ({len(matched)} din {len(asked)})")
        if missing:
            reasons.append(f"− Cerute, dar nu sunt în profil: {', '.join(missing)}")
    else:
        skill_points = SKILL_POINTS / 2
        reasons.append("· Anunțul nu menționează skill-uri pe care să le recunosc")

    # Role
    title = _plain(job.title)
    role_points, best_role = 0.0, None
    for role in settings.roles:
        words = _role_words(role)
        if words:
            share = sum(_has_role_word(title, w) for w in words) / len(words)
            if share > role_points / ROLE_POINTS:
                role_points, best_role = ROLE_POINTS * share, role
    if not settings.roles:
        role_points = ROLE_POINTS / 2
    elif best_role and role_points == ROLE_POINTS:
        reasons.append(f"+ Titlul se potrivește cu rolul căutat „{best_role}”")
    elif best_role:
        reasons.append(f"· Titlul seamănă parțial cu „{best_role}”")
    else:
        reasons.append("− Titlul nu seamănă cu rolurile căutate")

    # Seniority
    years = facts.years
    mine, theirs = candidate_level(years), job_level(job)
    if theirs is None:
        seniority_points = SENIORITY_POINTS * 2 / 3
    else:
        gap = abs(theirs - mine)
        seniority_points = (SENIORITY_POINTS, SENIORITY_POINTS / 2, 0)[min(gap, 2)]
        if gap == 0:
            reasons.append(f"+ Nivel potrivit ({LEVEL_LABELS[theirs]})")
        else:
            reasons.append(f"− Anunțul cere nivel {LEVEL_LABELS[theirs]}; tu ai ~{years:.1f} ani de experiență")

    # Location and work mode
    location_points = LOCATION_POINTS * 7 / 15
    in_city = city_matches(job.city, settings.cities) if settings.cities else None
    if job.work_mode is WorkMode.REMOTE and (not settings.work_modes or WorkMode.REMOTE in settings.work_modes):
        location_points = LOCATION_POINTS
        reasons.append("+ Remote")
    elif settings.work_modes and job.work_mode is not None and job.work_mode not in settings.work_modes:
        location_points = 0
        reasons.append(f"− Mod de lucru {job.work_mode} nu e printre preferințele tale")
    elif in_city:
        location_points = LOCATION_POINTS
        reasons.append("+ Într-un oraș preferat")
    elif in_city is False:
        location_points = 0
        reasons.append(f"− Oraș în afara preferințelor ({job.city})")

    score = round(skill_points + role_points + seniority_points + location_points)
    return MatchResult(
        score=max(0, min(100, score)),
        matched_skills=matched,
        missing_skills=missing,
        reasons=reasons,
        excluded_by=hard_filter(job, settings, text),
    )


# Start of each hard_filter reason -> the preference it comes from, for short summaries
_EXCLUSION_KINDS = (
    ("titlul conține", "cuvinte excluse"),
    ("nu conține", "cuvinte obligatorii"),
    ("modul de lucru", "mod de lucru"),
    ("orașul", "oraș"),
    ("cere nivel", "nivel"),
    ("titlul nu se potrivește", "rol"),
    ("salariul", "salariu"),
)


def exclusion_kind(reason: str) -> str:
    """"cere nivel senior" -> "nivel"."""
    return next((kind for start, kind in _EXCLUSION_KINDS if reason.startswith(start)), reason)


def hard_filter(job: JobPosting, settings: MatchSettings, text: str | None = None) -> str | None:
    """Reason the job is excluded, or None. Excluded words are checked in the title only:
    "senior" in "you will work with senior engineers" must not hide a junior job."""
    title = _plain(job.title)
    for word in settings.keywords_excluded:
        if _contains(title, _plain(word)):
            return f"titlul conține „{word}”"
    text = text if text is not None else _ad_text(job)
    for word in settings.keywords_required:
        if not _contains(text, _plain(word)):
            return f"nu conține „{word}”"
    if settings.work_modes and job.work_mode is not None and job.work_mode not in settings.work_modes:
        return f"modul de lucru ({job.work_mode}) nu e printre preferințele tale"
    if settings.cities and job.work_mode is not WorkMode.REMOTE and city_matches(job.city, settings.cities) is False:
        return f"orașul ({job.city}) nu e printre preferințele tale"
    if settings.levels and (level := job_level(job)) is not None and level not in settings.levels:
        return f"cere nivel {LEVEL_LABELS[level]}"
    if settings.filter_by_role and not fits_a_role(job.title, settings.roles):
        return "titlul nu se potrivește cu rolurile căutate"
    # Only with a known currency: guessing RON for "2000" would hide a 2000 EUR job
    rate = RON_PER_CURRENCY.get(job.currency or "")
    if settings.min_salary_ron and job.salary_max is not None and rate:
        if job.salary_max * rate < settings.min_salary_ron:
            return f"salariul maxim ({job.salary_max} {job.currency}) e sub minimul tău"
    return None
