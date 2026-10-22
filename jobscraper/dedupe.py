"""The same ad posted on several job boards: one key from title + company + city.

Conservative on purpose: without a company there is no key, because generic titles
("Data Engineer", "București") from different employers must never be merged.
LinkedIn ads found through two sources are merged earlier, by id (models.make_job_id).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobscraper.models import JobPosting
from jobscraper.normalize import strip_diacritics

_GENDER_TAG = re.compile(r"\((?:[mfwhdx]\s*/\s*)+[mfwhdx]\)|\(all genders\)|\(toate genurile\)")
_NOT_WORD = re.compile(r"[^a-z0-9+#]+")
_LEGAL_WORDS = {
    "srl", "sa", "srld", "gmbh", "ltd", "llc", "inc", "sas", "bv", "ag", "plc", "spa", "kft", "sro",
    "romania", "ro", "the", "co", "company",
}
_PLACE_WORDS = {"romania", "mun", "municipiul", "jud", "judetul", "oras", "sector"}
_NO_CITY = {"remote", "oriunde", "anywhere", "hybrid", "hibrid"}

# Biggest Romanian cities, to find the city inside full addresses
# ("Șoseaua Pipera 42, București 077190") and agree on one spelling.
_CITIES = (
    "bucuresti", "cluj napoca", "timisoara", "iasi", "brasov", "constanta", "craiova", "galati",
    "ploiesti", "oradea", "sibiu", "arad", "pitesti", "bacau", "targu mures", "baia mare", "buzau",
    "satu mare", "botosani", "suceava", "ramnicu valcea", "drobeta turnu severin", "piatra neamt",
    "targoviste", "focsani", "tulcea", "alba iulia", "deva", "hunedoara", "zalau", "bistrita", "resita",
    "slatina", "calarasi", "giurgiu", "alexandria", "slobozia", "vaslui", "miercurea ciuc",
    "sfantu gheorghe", "voluntari", "otopeni", "popesti leordeni", "chiajna", "bragadiru", "apahida",
    "floresti", "ghiroda", "dumbravita", "selimbar",
)
_CITY_ALIASES = {"bucharest": "bucuresti", "cluj": "cluj napoca", "tirgu mures": "targu mures"}
_CITY_FINDER = re.compile(
    r"(?<![a-z])(" + "|".join(re.escape(c) for c in sorted([*_CITIES, *_CITY_ALIASES], key=len, reverse=True)) + r")(?![a-z])"
)


def _plain(text: str | None) -> str:
    return strip_diacritics(text or "").lower()


def normalize_title(title: str | None) -> str:
    text = _GENDER_TAG.sub(" ", _plain(title))
    return " ".join(_NOT_WORD.sub(" ", text).split())


def normalize_company(company: str | None) -> str:
    text = _plain(company).replace(".", "")  # "S.R.L." -> "srl"
    return " ".join(w for w in _NOT_WORD.sub(" ", text).split() if w not in _LEGAL_WORDS)


def normalize_city(city: str | None) -> str:
    """First known city anywhere in the text, else the first segment; "" for remote or unknown."""
    text = _plain(city).replace("-", " ")
    match = _CITY_FINDER.search(text)
    if match:
        return _CITY_ALIASES.get(match.group(1), match.group(1))
    first = re.split(r"[;,(]", text)[0]
    words = [w for w in _NOT_WORD.sub(" ", first).split() if w not in _PLACE_WORDS]
    name = " ".join(words)
    return "" if name in _NO_CITY else name


def dedup_key(job: JobPosting) -> str | None:
    title, company = normalize_title(job.title), normalize_company(job.company)
    if not title or not company:
        return None
    return f"{title}|{company}|{normalize_city(job.city)}"


@dataclass(frozen=True)
class DuplicateGroup:
    primary: JobPosting  # the copy shown in the list
    others: list[JobPosting] = field(default_factory=list)

    @property
    def members(self) -> list[JobPosting]:
        return [self.primary, *self.others]


def _richness(job: JobPosting) -> tuple[bool, bool, bool, bool]:
    return (bool(job.description), job.details_fetched_at is not None, job.salary_max is not None, job.posted_at is not None)


def group_duplicates(jobs: list[JobPosting]) -> list[DuplicateGroup]:
    """Groups in order of first appearance; the primary is the most complete copy
    (description, loaded details, salary, posting date), the first one on ties."""
    groups: dict[str, list[JobPosting]] = {}
    for job in jobs:
        groups.setdefault(dedup_key(job) or f"id:{job.id}", []).append(job)
    result = []
    for members in groups.values():
        primary = max(members, key=_richness)  # max keeps the first of equal items
        result.append(DuplicateGroup(primary=primary, others=[j for j in members if j is not primary]))
    return result
