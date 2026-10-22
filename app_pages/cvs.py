"""CV-urile mele: every generated CV, filtered by type of job (QA, development...), to open or delete."""

from collections import Counter
from datetime import datetime

import streamlit as st

from jobscraper import db
from jobscraper.cv.library import LibraryEntry, LibraryFilter, filter_entries, library_entries, library_types
from jobscraper.ui import app_context, card, card_title, page_header, pills, plain_text, stat_tiles

LANGUAGE_LABELS = {"ro": "Română", "en": "Engleză"}
TARGETS = {"all": "Toate", "job": "Pentru un anunț", "focus": "Pe tip de job"}
SUMMARY_CHARS = 240

config, _ = app_context()


def short_date(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")


def open_cv(entry: LibraryEntry) -> None:
    """The CV page shows this exact version: its ad (or type of job) is selected and the version kept."""
    st.session_state.cv_open_id = entry.stored.id
    if entry.for_job:
        st.session_state.cv_mode = "job"
        st.session_state.cv_job_id = entry.stored.job_id
    else:
        st.session_state.cv_mode = "focus"
        st.session_state.cv_focus_choice = entry.stored.focus
    st.switch_page("app_pages/cv.py")


def delete(entry: LibraryEntry) -> None:
    with db.open_db(config.paths.db) as conn:
        db.delete_cv(conn, entry.stored.id)
    if st.session_state.get("cv_open_id") == entry.stored.id:
        st.session_state.pop("cv_open_id")
    st.session_state.library_notice = f"Am șters CV-ul „{entry.stored.cv.headline}”."


with db.open_db(config.paths.db) as conn:
    cvs = db.all_cvs(conn)
    jobs = {job_id: job for job_id in {c.job_id for c in cvs if c.job_id} if (job := db.get_job(conn, job_id))}
entries = library_entries(cvs, jobs)

page_header(
    "CV-urile",
    accent="mele",
    eyebrow=f"Bibliotecă · {len(entries)} CV-uri",
    lead="Toate CV-urile generate, pentru anunțuri și pe tipuri de job. Deschide unul ca să-l editezi sau să-l exporți.",
)
if notice := st.session_state.pop("library_notice", None):
    st.success(notice)

if not entries:
    st.info("Nu ai generat încă niciun CV.")
    st.page_link("app_pages/cv.py", label="Generează un CV", icon=":material/description:")
    st.stop()

types = library_types(entries)
per_type = Counter(entry.cv_type for entry in entries)
stat_tiles([("Total", len(entries)), *((cv_type, per_type[cv_type]) for cv_type in types[:4])])

with st.sidebar:
    st.header("Filtre")
    text = st.text_input("Caută", placeholder="ex. python, Proxify", key="l_text")
    chosen_types = st.multiselect("Tip de job", types, key="l_types", placeholder="Toate")
    target = st.radio("Pentru", list(TARGETS), format_func=TARGETS.get, key="l_target")
    language = st.selectbox(
        "Limba", [None, *LANGUAGE_LABELS], format_func=lambda code: "Oricare" if code is None else LANGUAGE_LABELS[code],
        key="l_language",
    )

shown = filter_entries(
    entries, LibraryFilter(types=frozenset(chosen_types), target=target, language=language, text=text)
)
st.caption(f"{len(shown)} din {len(entries)} CV-uri" if len(shown) != len(entries) else f"{len(entries)} CV-uri")
if not shown:
    st.info("Niciun CV nu se potrivește filtrelor.")

for entry in shown:
    stored, cv = entry.stored, entry.stored.cv
    with card(f"cv-{stored.id}"):
        info, actions = st.columns([5, 2], gap="medium")
        with info:
            card_title(entry.title, entry.job.company if entry.job and entry.job.company else None)
            pills([entry.cv_type, LANGUAGE_LABELS[cv.language], TARGETS["job" if entry.for_job else "focus"]])
            if cv.headline:
                plain_text(cv.headline, strong=True)
            summary = cv.summary if len(cv.summary) <= SUMMARY_CHARS else cv.summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
            st.caption(summary)
            meta = [f"generat {short_date(stored.created_at)}", stored.model or ""]
            if stored.updated_at != stored.created_at:
                meta.append(f"editat {short_date(stored.updated_at)}")
            if cv.gaps:
                meta.append(f"{len(cv.gaps)} cerințe neacoperite")
            if stored.focus and entry.for_job:
                meta.append(f"accent pe {stored.focus}")
            st.caption(" · ".join(filter(None, meta)))
        with actions:
            if st.button("Deschide", key=f"open:{stored.id}", type="primary", icon=":material/edit:", width="stretch"):
                open_cv(entry)
            with st.popover("Șterge", icon=":material/delete:", width="stretch"):
                st.markdown("Șterge definitiv acest CV. Fișierele PDF/DOCX exportate deja rămân în `data/output`.")
                st.button(
                    "Da, șterge", key=f"delete:{stored.id}", type="primary", icon=":material/delete_forever:",
                    on_click=delete, args=(entry,), width="stretch",
                )
