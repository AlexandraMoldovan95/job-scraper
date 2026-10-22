"""Profile + TailoredCV -> CVDocument: the CV as sections ready to render.

Companies, roles, dates and schools come from the profile here, by index; the
TailoredCV only contributes the texts and the selection/order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobscraper.models import Profile, TailoredCV

LABELS = {
    "ro": {
        "summary": "Rezumat",
        "skills": "Competențe",
        "experience": "Experiență profesională",
        "projects": "Proiecte",
        "education": "Educație",
        "courses": "Cursuri și certificări",
        "languages": "Limbi străine",
        "technologies": "Tehnologii",
        "present": "prezent",
    },
    "en": {
        "summary": "Summary",
        "skills": "Skills",
        "experience": "Professional experience",
        "projects": "Projects",
        "education": "Education",
        "courses": "Courses and certifications",
        "languages": "Languages",
        "technologies": "Technologies",
        "present": "present",
    },
}


@dataclass(frozen=True)
class CVEntry:
    title: str
    organization: str | None = None
    location: str | None = None
    period: str | None = None
    bullets: list[str] = field(default_factory=list)
    text: str | None = None
    technologies: list[str] = field(default_factory=list)
    link: str | None = None


@dataclass(frozen=True)
class CVDocument:
    language: str
    labels: dict[str, str]
    full_name: str
    headline: str
    contact: list[str]
    summary: str
    skills: list[str]
    experience: list[CVEntry]
    projects: list[CVEntry]
    education: list[CVEntry]
    courses: list[CVEntry]
    languages: list[str]


def format_month(value: str | None) -> str | None:
    """"2022-09" -> "09.2022"; a year alone stays "2022"."""
    if not value:
        return None
    year, _, month = value.partition("-")
    return f"{month}.{year}" if month else year


def period(start: str | None, end: str | None, present: str | None = None) -> str | None:
    start_text, end_text = format_month(start), format_month(end) or present
    if start_text and end_text:
        return f"{start_text} – {end_text}"
    return start_text or end_text


def build_document(profile: Profile, cv: TailoredCV) -> CVDocument:
    labels = LABELS[cv.language]
    contact = profile.contact
    experience = [
        CVEntry(
            title=job.role,
            organization=job.company,
            location=job.city,
            period=period(job.start, job.end, labels["present"]),
            bullets=[b.strip() for b in item.bullets if b.strip()],
            technologies=job.technologies,
        )
        for item in cv.experience
        if 0 <= item.profile_index < len(profile.experience)
        for job in [profile.experience[item.profile_index]]
    ]
    projects = [
        CVEntry(title=project.name, text=item.description.strip(), technologies=project.technologies, link=project.url)
        for item in cv.projects
        if 0 <= item.profile_index < len(profile.projects)
        for project in [profile.projects[item.profile_index]]
    ]
    education = [
        CVEntry(
            title=", ".join(filter(None, (school.degree, school.field))),
            organization=school.institution,
            period=period(school.start, school.end),
        )
        for i in cv.education_indices
        if 0 <= i < len(profile.education)
        for school in [profile.education[i]]
    ]
    courses = [
        CVEntry(title=course.name, organization=course.provider, period=format_month(course.completed))
        for i in cv.course_indices
        if 0 <= i < len(profile.courses)
        for course in [profile.courses[i]]
    ]
    return CVDocument(
        language=cv.language,
        labels=labels,
        full_name=contact.full_name,
        headline=cv.headline.strip(),
        contact=[v for v in (contact.email, contact.phone, contact.city, contact.linkedin, contact.github, contact.website) if v],
        summary=cv.summary.strip(),
        skills=[s.strip() for s in cv.skills if s.strip()],
        experience=experience,
        projects=projects,
        education=education,
        courses=courses,
        languages=[f"{lang.name} – {lang.level}" for lang in profile.languages],
    )
