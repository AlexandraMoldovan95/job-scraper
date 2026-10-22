"""Generează CV: generate a CV with Claude for one job ad or for a type of job, check it, edit it and export it."""

from datetime import datetime
from pathlib import Path

import streamlit as st
import yaml
from pydantic import ValidationError

from jobscraper import db
from jobscraper.cv.document import build_document
from jobscraper.cv.generator import CVGenerationError, generate_cv, make_client
from jobscraper.cv.prompts import FOCUS_PRESETS
from jobscraper.cv.render import CVExportError, file_stem, focus_file_stem, render_html, write_docx, write_pdf
from jobscraper.cv.validate import check_cv
from jobscraper.models import JobPosting, JobStatus, Profile, TailoredCV, TailoredExperience, TailoredProject, now_local
from jobscraper.profile import load_profile
from jobscraper.scrape import load_details
from jobscraper.sources import PAID_DETAILS, SourceError, SourceUnavailable, create_details_source, details_source_name
from jobscraper.ui import app_context, card, page_header, plain_text, source_label

LANGUAGES = {"auto": "Limba anunțului", "ro": "Română", "en": "Engleză"}
FOCUS_LANGUAGES = {"ro": "Română", "en": "Engleză"}
MODES = {"job": "Pentru un anunț", "focus": "Pentru un tip de job"}
OTHER_FOCUS = "__other__"
MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

config, keys = app_context()


def lines(text: str) -> list[str]:
    return [line.strip(" •-\t") for line in text.splitlines() if line.strip(" •-\t")]


def render_editor(stored: db.StoredCV, profile: Profile) -> TailoredCV:
    """Widgets for every part of the CV; returns the CV as currently edited."""
    cv = stored.cv
    prefix = f"cv{stored.id}.{stored.updated_at}."  # new keys after a save, so widgets show what was saved

    headline = st.text_input("Titlu", value=cv.headline, key=prefix + "headline")
    summary = st.text_area("Rezumat", value=cv.summary, key=prefix + "summary", height=110)
    skills = st.text_area(
        "Competențe (separate prin virgulă)", value=", ".join(cv.skills), key=prefix + "skills", height=80
    )

    st.markdown("**Experiență**")
    chosen = {item.profile_index: item for item in cv.experience if 0 <= item.profile_index < len(profile.experience)}
    order = [*chosen, *(i for i in range(len(profile.experience)) if i not in chosen)]
    experience = []
    for i in order:
        job = profile.experience[i]
        include = st.checkbox(f"{job.role} — {job.company}", value=i in chosen, key=f"{prefix}exp{i}.on")
        default = chosen[i].bullets if i in chosen else job.achievements
        bullets = st.text_area(
            "Realizări (câte una pe rând)", value="\n".join(default), key=f"{prefix}exp{i}.bullets",
            disabled=not include, label_visibility="collapsed", height=110,
        )
        if include:
            experience.append(TailoredExperience(profile_index=i, bullets=lines(bullets)))

    projects = []
    if profile.projects:
        st.markdown("**Proiecte**")
        chosen_projects = {p.profile_index: p for p in cv.projects if 0 <= p.profile_index < len(profile.projects)}
        for i in [*chosen_projects, *(i for i in range(len(profile.projects)) if i not in chosen_projects)]:
            project = profile.projects[i]
            include = st.checkbox(project.name, value=i in chosen_projects, key=f"{prefix}proj{i}.on")
            description = st.text_area(
                "Descriere", value=chosen_projects[i].description if i in chosen_projects else project.description,
                key=f"{prefix}proj{i}.text", disabled=not include, label_visibility="collapsed", height=80,
            )
            if include:
                projects.append(TailoredProject(profile_index=i, description=description.strip()))

    education = st.multiselect(
        "Educație", list(range(len(profile.education))), default=[i for i in cv.education_indices if i < len(profile.education)],
        format_func=lambda i: f"{profile.education[i].degree} — {profile.education[i].institution}", key=prefix + "edu",
    )
    courses = st.multiselect(
        "Cursuri", list(range(len(profile.courses))), default=[i for i in cv.course_indices if i < len(profile.courses)],
        format_func=lambda i: f"{profile.courses[i].name} — {profile.courses[i].provider}", key=prefix + "courses",
    )
    return cv.model_copy(
        update={
            "headline": headline.strip(),
            "summary": summary.strip(),
            "skills": [s.strip() for s in skills.split(",") if s.strip()],
            "experience": experience,
            "projects": projects,
            "education_indices": list(education),
            "course_indices": list(courses),
        }
    )


def export(stem: str, export_key: str, edited: TailoredCV, profile: Profile, writer, suffix: str) -> None:
    path = Path(config.paths.output_dir) / f"{stem}{suffix}"
    try:
        with st.spinner("Generez fișierul..."):
            writer(build_document(profile, edited), path)
    except CVExportError as exc:
        st.error(str(exc))
    else:
        st.session_state.setdefault("cv_exports", {})[export_key + suffix] = str(path)


def generate(profile: Profile, job: JobPosting | None, language: str, focus: str | None) -> None:
    """Call Claude and store the CV as a new row (each generation cost money)."""
    try:
        with st.spinner("Claude scrie CV-ul... (poate dura un minut)"):
            result = generate_cv(make_client(keys), profile, job, llm=config.llm, language=language, focus=focus)
    except CVGenerationError as exc:
        st.error(str(exc))
        return
    with db.open_db(config.paths.db) as conn:
        st.session_state.cv_open_id = db.add_cv(conn, job.id if job else None, result.cv, result.model, focus=focus)
    st.session_state.cv_notice = (
        f"CV generat de {result.model} ({result.input_tokens} tokeni la intrare, {result.output_tokens} la ieșire)."
    )
    st.rerun()


GENERATE_HELP = f"Consumă tokeni din contul tău Anthropic ({config.llm.model}, efort {config.llm.effort})."


def show_ad(job: JobPosting, *, expanded: bool) -> None:
    """The ad the CV is written for, so a description loaded here is visible here too."""
    label = "Anunțul" if job.description else "Anunțul (fără descriere)"
    with st.expander(label, expanded=expanded and bool(job.description), icon=":material/article:"):
        if job.requirements:
            st.markdown("**Cerințe**\n" + "\n".join(f"- {item}" for item in job.requirements))
        if job.description:
            with st.container(height=360, border=False):
                plain_text(job.description)
        with st.container(horizontal=True):
            st.link_button("Deschide anunțul", job.url, icon=":material/open_in_new:")
            if st.button("Vezi în Joburi", icon=":material/work:", help="Deschide jobul în panoul din Joburi, chiar dacă filtrele îl ascund"):
                st.session_state.selected_job_id = job.id
                st.switch_page("app_pages/jobs.py")


def version_label(stored: db.StoredCV, newest: bool) -> str:
    created = datetime.fromisoformat(stored.created_at).strftime("%d.%m.%Y %H:%M")
    extras = [stored.model or "", "editat" if stored.updated_at != stored.created_at else "", "cea mai nouă" if newest else ""]
    return " · ".join([created, *filter(None, extras)])


def pick_version(versions: list[db.StoredCV]) -> db.StoredCV | None:
    """The version opened from "CV-urile mele" (or just generated) if it belongs here, else the newest."""
    if not versions:
        return None
    ids = [v.id for v in versions]
    wanted = st.session_state.get("cv_open_id")
    chosen = ids[ids.index(wanted) if wanted in ids else 0]
    if len(versions) > 1:
        chosen = st.selectbox(
            "Versiune", ids, index=ids.index(chosen), format_func=lambda i: version_label(versions[ids.index(i)], i == ids[0]),
            help="Fiecare generare se păstrează separat.",
        )
    st.session_state.cv_open_id = chosen
    return versions[ids.index(chosen)]


def choose_job_cv(profile: Profile, focus_options: list[str]) -> tuple[list[db.StoredCV], str, str]:
    """The "Pentru un anunț" flow: pick the job, load its ad if needed, generate."""
    with db.open_db(config.paths.db) as conn:
        candidates = db.list_jobs(
            conn, statuses=[JobStatus.SAVED, JobStatus.APPLIED, JobStatus.SEEN, JobStatus.NEW], limit=500
        )
    if not candidates:
        st.info("Nu ai încă joburi salvate. Poți face un CV pentru un tip de job: alege „Pentru un tip de job” mai sus.")
        st.page_link("app_pages/search.py", label="Fă o căutare", icon=":material/search:")
        st.stop()

    # Saved jobs first: those are the ones you are most likely applying to
    candidates.sort(key=lambda j: j.status is not JobStatus.SAVED)
    ids = [job.id for job in candidates]
    wanted = st.session_state.get("cv_job_id") or st.session_state.get("selected_job_id")
    job_id = st.selectbox(
        "Job",
        ids,
        index=ids.index(wanted) if wanted in ids else 0,
        format_func=lambda i: next(f"{j.title} — {j.company or source_label(j.source)}" for j in candidates if j.id == i),
    )
    st.session_state.cv_job_id = job_id
    with db.open_db(config.paths.db) as conn:
        job = db.get_job(conn, job_id)
        versions = db.cv_versions(conn, job_id=job_id)

    st.caption(" · ".join(filter(None, (job.company, job.city, f"prin {job.via or source_label(job.source)}"))))
    show_ad(job, expanded=not versions)

    if not job.description:
        st.warning("Jobul nu are încă descriere, iar fără ea CV-ul nu poate fi adaptat.")
        details_from = details_source_name(job)
        if details_from is not None and st.button(
            f"Încarcă detaliile ({source_label(details_from)})",
            help="Consumă o căutare din contul SearchApi.io" if details_from in PAID_DETAILS else "O cerere către site, fără cost",
        ):
            try:
                with st.spinner("Încarc detaliile..."), db.open_db(config.paths.db) as conn:
                    load_details(conn, create_details_source(job, config, keys), job)
            except (SourceUnavailable, SourceError) as exc:
                st.error(str(exc))
            else:
                st.rerun()

    with card("generate"):
        left, middle, right = st.columns([2, 2, 2], vertical_alignment="bottom")
        language = left.radio("Limba CV-ului", list(LANGUAGES), format_func=LANGUAGES.get, horizontal=True, key="cv_language")
        focus = middle.selectbox(
            "Accent pe", [None, *focus_options], format_func=lambda f: "Doar anunțul" if f is None else f,
            key="cv_job_focus", help="Opțional: un tip de job pe care CV-ul să-l pună în față, acolo unde anunțul permite.",
        )
        if right.button(
            "Generează din nou" if versions else "Generează CV",
            type="primary",
            icon=":material/auto_awesome:",
            disabled=not job.description,
            width="stretch",
            help=GENERATE_HELP,
        ):
            generate(profile, job, language, focus)
    return versions, file_stem(job, now_local().date()), job.id


def choose_focus_cv(profile: Profile, focus_options: list[str], saved_focuses: list[str]) -> tuple[list[db.StoredCV], str, str]:
    """The "Pentru un tip de job" flow: one reusable CV per type of job (QA, development...)."""
    st.caption(
        "Un CV pe care îl poți trimite la orice job de acest tip. Experiența și studiile rămân aceleași; "
        "se schimbă felul în care te prezinți și ce pui în față din fiecare loc prin care ai trecut."
    )
    choice = st.selectbox(
        "Tipul de job",
        [*focus_options, OTHER_FOCUS],
        format_func=lambda f: "Altul (îl descrii tu)..." if f == OTHER_FOCUS else f + (" · are CV" if f in saved_focuses else ""),
        key="cv_focus_choice",
    )
    if choice == OTHER_FOCUS:
        focus = st.text_input("Descrie tipul de job", placeholder="ex. Automation tester cu Python", key="cv_focus_custom")
    else:
        focus = choice
    focus = (focus or "").strip()
    with db.open_db(config.paths.db) as conn:
        versions = db.cv_versions(conn, focus=focus) if focus else []

    with card("generate"):
        left, right = st.columns([3, 2], vertical_alignment="bottom")
        language = left.radio(
            "Limba CV-ului", list(FOCUS_LANGUAGES), format_func=FOCUS_LANGUAGES.get, horizontal=True, key="cv_focus_language"
        )
        if right.button(
            "Generează din nou" if versions else "Generează CV",
            type="primary",
            icon=":material/auto_awesome:",
            disabled=not focus,
            width="stretch",
            help=GENERATE_HELP,
        ):
            generate(profile, None, language, focus)
    return versions, focus_file_stem(focus or "cv", now_local().date()), f"focus:{focus}"


page_header(
    "Generează",
    accent="CV-ul",
    eyebrow="Generator · Claude",
    lead="Construit doar din profilul tău: Claude alege, reordonează și reformulează, dar nu inventează.",
)

try:
    profile = load_profile(config.paths.profile)
except (yaml.YAMLError, ValidationError):
    profile = None
if profile is None:
    st.info("Completează întâi profilul: CV-ul se construiește doar din el.")
    st.page_link("app_pages/profile.py", label="Mergi la Profil", icon=":material/person:")
    st.stop()

with db.open_db(config.paths.db) as conn:
    saved_focuses = db.cv_focuses(conn)
focus_options = list(dict.fromkeys([*FOCUS_PRESETS, *saved_focuses]))

st.session_state.setdefault("cv_mode", "job")  # "CV-urile mele" may have set it already
mode = st.segmented_control("Pentru", list(MODES), format_func=MODES.get, key="cv_mode") or "job"
if mode == "focus":
    versions, stem, export_key = choose_focus_cv(profile, focus_options, saved_focuses)
else:
    versions, stem, export_key = choose_job_cv(profile, focus_options)

if notice := st.session_state.pop("cv_notice", None):
    st.success(notice)

stored = pick_version(versions)
if stored is None:
    st.stop()

if stored.focus and stored.job_id:
    st.caption(f"Ultimul CV pentru acest anunț pune accent pe: {stored.focus}.")

edit_tab, preview_tab, export_tab = st.tabs(["Editează", "Previzualizare", "Export"])
with edit_tab:
    edited = render_editor(stored, profile)
    if edited != stored.cv and st.button("Salvează modificările", icon=":material/save:"):
        with db.open_db(config.paths.db) as conn:
            db.update_cv(conn, stored.id, edited)
        st.rerun()

check = check_cv(edited, profile)
with preview_tab:
    # Isolated in an iframe so the CV's print styles do not leak into the app; safe to embed
    # because the template autoescapes everything Claude wrote
    st.iframe(render_html(build_document(profile, edited)), height=1000)

with export_tab:
    for message in check.errors:
        st.error(message)
    for message in check.warnings:
        st.warning(message)
    if edited.gaps:
        heading = "Cerințe din anunț" if stored.job_id else "Ce se cere de obicei la acest tip de job"
        st.info(f"**{heading} pe care profilul nu le acoperă** (nu apar în CV):\n" + "\n".join(f"- {g}" for g in edited.gaps))
    if not check.ok:
        st.caption("Exportul e blocat până corectezi erorile de mai sus în tab-ul „Editează”.")
    pdf_col, docx_col = st.columns(2)
    if pdf_col.button("Exportă PDF", icon=":material/picture_as_pdf:", disabled=not check.ok, width="stretch"):
        export(stem, export_key, edited, profile, write_pdf, ".pdf")
    if docx_col.button("Exportă DOCX", icon=":material/description:", disabled=not check.ok, width="stretch"):
        export(stem, export_key, edited, profile, write_docx, ".docx")
    for suffix, column in ((".pdf", pdf_col), (".docx", docx_col)):
        exported = st.session_state.get("cv_exports", {}).get(export_key + suffix)
        if exported and Path(exported).exists():
            column.download_button(
                f"Descarcă {Path(exported).name}", Path(exported).read_bytes(), file_name=Path(exported).name,
                mime=MIME[suffix], width="stretch", key=f"download{suffix}",
            )
            column.caption(f"Salvat și în `{exported}`")
