"""Text, date, work mode and URL normalization for job ads."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from jobscraper.models import WorkMode

# Old cedilla forms (ş ţ) still common on Romanian sites -> correct comma-below forms (ș ț);
# zero-width characters pasted from other tools are dropped
_CEDILLA_TO_COMMA = str.maketrans(
    {"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț", "​": None, "‌": None, "‍": None, "﻿": None}
)


def fix_romanian_diacritics(text: str) -> str:
    return text.translate(_CEDILLA_TO_COMMA)


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_text(text: str | None) -> str | None:
    """Fix diacritics and collapse whitespace; None for empty input."""
    if not text:
        return None
    cleaned = re.sub(r"[ \t]+", " ", fix_romanian_diacritics(text)).strip()
    return cleaned or None


_BLOCK_TAGS = ["p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]
# Ads pasted by employers from their own careers site sometimes bring a loader along
_PLACEHOLDER_LINES = {"please wait...", "loading...", "se incarca..."}


def html_to_text(html: str | None) -> str | None:
    """Job description HTML -> plain text: one line per block, "- " for list items."""
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for item in soup.find_all("li"):
        item.insert(0, "- ")
    text = soup.get_text()
    lines = (clean_text(line) for line in text.replace("\xa0", " ").splitlines())
    kept = (line for line in lines if line and line != "-" and strip_diacritics(line.lstrip("- ")).lower() not in _PLACEHOLDER_LINES)
    return clean_text("\n".join(kept))


# --- Work mode: "Bucharest (Hybrid)", "Oriunde", "Remote" ------------------------

_WORK_MODE_WORDS = (
    (WorkMode.REMOTE, ("remote", "anywhere", "oriunde", "de acasa", "work from home")),
    (WorkMode.HYBRID, ("hybrid", "hibrid")),
    (WorkMode.ON_SITE, ("on-site", "onsite", "la birou")),
)


def work_mode_from_text(text: str | None) -> WorkMode | None:
    plain = strip_diacritics(text or "").lower()
    for mode, words in _WORK_MODE_WORDS:
        if any(word in plain for word in words):
            return mode
    return None


# --- LinkedIn job ids --------------------------------------------------------------

_LINKEDIN_VIEW = re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*?-)?(\d{6,})(?:[/?#]|$)")
_LINKEDIN_CURRENT_JOB = re.compile(r"linkedin\.com/.*[?&]currentJobId=(\d{6,})")


def linkedin_job_id_from_url(url: str | None) -> str | None:
    """'https://ro.linkedin.com/jobs/view/python-dev-at-x-3812345678?utm=..' -> '3812345678'"""
    if not url:
        return None
    match = _LINKEDIN_VIEW.search(url) or _LINKEDIN_CURRENT_JOB.search(url)
    return match.group(1) if match else None


# --- Relative dates: "acum 2 zile", "1 day ago", "ieri", "30+ days ago" ---------

_UNITS = (
    (re.compile(r"^(minut|min)"), timedelta(minutes=1)),
    (re.compile(r"^(or[ae]|hour|hr)"), timedelta(hours=1)),
    (re.compile(r"^(zi|day)"), timedelta(days=1)),
    (re.compile(r"^(saptaman|week)"), timedelta(weeks=1)),
    (re.compile(r"^(lun[ai]|month)"), timedelta(days=30)),
)
_ONE_WORDS = {"o", "un", "una", "a", "an", "one"}
_AMOUNT_AND_UNIT = re.compile(r"(\d+|o|un|una|a|an|one)\s*\+?\s+([a-z]+)")


def parse_relative_date(text: str | None, now: datetime) -> datetime | None:
    """Return the moment the text refers to, or None if it is not a relative date."""
    if not text:
        return None
    value = strip_diacritics(text).lower().strip()
    if value in {"azi", "astazi", "today", "acum", "just now"} or value.startswith("acum cateva"):
        return now
    if value in {"ieri", "yesterday"}:
        return now - timedelta(days=1)

    for match in _AMOUNT_AND_UNIT.finditer(value):
        amount_text, unit_text = match.groups()
        amount = 1 if amount_text in _ONE_WORDS else int(amount_text)
        for pattern, step in _UNITS:
            if pattern.match(unit_text):
                return now - amount * step
    return None
