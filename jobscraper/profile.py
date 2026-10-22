"""Reads and writes the user's profile (data/profile.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml

from jobscraper.models import Profile


def load_profile(path: Path) -> Profile | None:
    """Return the profile, or None if the file does not exist yet."""
    if not path.exists():
        return None
    return profile_from_yaml(path.read_text(encoding="utf-8"))


def profile_from_yaml(text: str) -> Profile:
    """Raises yaml.YAMLError for broken YAML and pydantic.ValidationError for wrong fields."""
    return Profile.model_validate(yaml.safe_load(text) or {})


def profile_to_yaml(profile: Profile) -> str:
    data = profile.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def save_profile(profile: Profile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile_to_yaml(profile), encoding="utf-8")


def linkedin_pdf_path(profile_path: Path) -> Path:
    """The LinkedIn PDF the profile was imported from, kept next to it (also personal data)."""
    return profile_path.with_name("profile_linkedin.pdf")


def linkedin_import_path(profile_path: Path) -> Path:
    """What Claude read from that PDF (JSON), so the form can be refilled without paying again."""
    return profile_path.with_name("profile_linkedin.json")


def save_linkedin_pdf(data: bytes, profile_path: Path) -> Path:
    path = linkedin_pdf_path(profile_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def delete_profile(profile_path: Path) -> list[Path]:
    """Delete the profile, its LinkedIn PDF and the import read from it; returns the files that existed."""
    deleted = []
    for path in (profile_path, linkedin_pdf_path(profile_path), linkedin_import_path(profile_path)):
        if path.exists():
            path.unlink()
            deleted.append(path)
    return deleted
