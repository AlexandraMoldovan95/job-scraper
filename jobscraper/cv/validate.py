"""Checks a TailoredCV against the profile before export: nothing may be invented.

Errors block the export (the user fixes them in the editor); warnings are shown only.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from jobscraper.models import Profile, TailoredCV
from jobscraper.normalize import strip_diacritics

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class CVCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def allowed_skills(profile: Profile) -> list[str]:
    """Skills a CV may list: the profile's skills plus technologies used in experience/projects."""
    names = [skill.name for skill in profile.skills]
    for entry in [*profile.experience, *profile.projects]:
        names.extend(entry.technologies)
    return list(dict.fromkeys(names))


def check_cv(cv: TailoredCV, profile: Profile) -> CVCheck:
    errors: list[str] = []
    warnings: list[str] = []

    _check_indices("Experiență", [e.profile_index for e in cv.experience], len(profile.experience), errors)
    _check_indices("Proiecte", [p.profile_index for p in cv.projects], len(profile.projects), errors)
    _check_indices("Educație", cv.education_indices, len(profile.education), errors)
    _check_indices("Cursuri", cv.course_indices, len(profile.courses), errors)

    known = {_plain(name) for name in allowed_skills(profile)}
    for skill in cv.skills:
        if _plain(skill) not in known:
            errors.append(f"Skill-ul „{skill}” nu apare în profil. Șterge-l din CV sau adaugă-l în profil.")

    if profile.experience and not cv.experience:
        warnings.append("CV-ul nu include nicio experiență din profil.")
    for item in cv.experience:
        if not any(bullet.strip() for bullet in item.bullets):
            warnings.append(f"Experiența #{item.profile_index + 1} nu are nicio realizare în CV.")

    # Contact details (+40 phone prefix) and preferences (min salary) are not claims a CV can quote
    facts = profile.model_dump_json(exclude={"contact", "preferences"})
    profile_numbers = {_number_key(n) for n in _NUMBER.findall(facts)}
    texts = [cv.headline, cv.summary, *(b for e in cv.experience for b in e.bullets), *(p.description for p in cv.projects)]
    for number in dict.fromkeys(n for text in texts for n in _NUMBER.findall(text)):
        if _number_key(number) not in profile_numbers:
            warnings.append(f"Cifra „{number}” nu apare în profil; verifică să nu fie inventată.")

    return CVCheck(errors=errors, warnings=warnings)


def _check_indices(section: str, indices: list[int], size: int, errors: list[str]) -> None:
    for index in indices:
        if not 0 <= index < size:
            errors.append(f"{section}: intrarea #{index + 1} nu există în profil.")
    for index, count in Counter(indices).items():
        if count > 1:
            errors.append(f"{section}: intrarea #{index + 1} apare de {count} ori.")


def _plain(text: str) -> str:
    return " ".join(strip_diacritics(text).lower().split())


def _number_key(number: str) -> str:
    return number.replace(",", ".")
