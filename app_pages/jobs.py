"""Joburi: stored jobs with filters, a detail panel and status changes."""

import streamlit as st
import yaml
from pydantic import ValidationError

from jobscraper import db
from jobscraper.dedupe import group_duplicates
from jobscraper.filters import JobFilter, SortOrder, filter_jobs, sort_jobs
from jobscraper.matching import MatchResult, MatchSettings, score_job, score_jobs
from jobscraper.models import JobPosting, JobStatus, WorkMode, now_local
from jobscraper.profile import load_profile
from jobscraper.scrape import load_details
from jobscraper.sources import PAID_DETAILS, SourceError, SourceUnavailable, create_details_source, details_source_name
from jobscraper.ui import (
    STATUS_LABELS,
    WORK_MODE_LABELS,
    app_context,
    card,
    format_date,
    page_header,
    pills,
    plain_text,
    source_label,
    stat_tiles,
)

SORT_LABELS = {
    SortOrder.MATCH: "Potrivire (mare → mic)",
    SortOrder.NEWEST: "Cele mai noi",
    SortOrder.SALARY: "Salariu (mare → mic)",
    SortOrder.COMPANY: "Firmă (A → Z)",
}
AGE_OPTIONS = [None, 1, 3, 7, 30]
OPEN_STATUSES = [JobStatus.NEW, JobStatus.SEEN, JobStatus.SAVED]
MAX_JOBS = 2000

config, keys = app_context()
try:
    profile = load_profile(config.paths.profile)
except (yaml.YAMLError, ValidationError):
    profile = None  # the Profil page reports the problem; here we just skip the score
match_settings = MatchSettings.from_sources(profile, config.search) if profile else None


def refresh() -> None:
    """Rerun with a fresh table. Its row selection is by position, so after any data
    change it would point at another job; the open job is kept by id instead."""
    st.session_state.jobs_table_version = st.session_state.get("jobs_table_version", 0) + 1
    st.rerun()


# Filter callbacks: they run before the widgets are drawn, so they may change their values.
# The table gets a new key too, since its row selection is by position.


def clear_search() -> None:
    st.session_state.pop("f_search", None)
    st.session_state.jobs_table_version = st.session_state.get("jobs_table_version", 0) + 1


def show_excluded() -> None:
    st.session_state.f_hide_excluded = False
    st.session_state.jobs_table_version = st.session_state.get("jobs_table_version", 0) + 1


REASON_MARKS = {"+": ":green[✓]", "−": ":red[✗]", "·": ":gray[•]"}


def reason_line(reason: str) -> str:
    """"+ Remote" -> green check; a leading +/- in Markdown would start a nested list."""
    mark, _, text = reason.partition(" ")
    return f"{REASON_MARKS.get(mark, mark)} {text}"


def markdown_url(url: str) -> str:
    """Hipo URLs contain "(AWS)"; unescaped parentheses would end the Markdown link early."""
    return url.replace("(", "%28").replace(")", "%29")


def change_status(job: JobPosting, status: JobStatus) -> None:
    with db.open_db(config.paths.db) as conn:
        db.set_status(conn, job.id, status)


def render_detail(job: JobPosting) -> None:
    with card("detail"):
        top, close = st.columns([6, 1])
        top.subheader(job.title)
        if close.button("", icon=":material/close:", help="Închide", key="close_detail", type="tertiary"):
            st.session_state.pop("selected_job_id", None)
            refresh()

        pills([STATUS_LABELS[job.status], WORK_MODE_LABELS.get(job.work_mode), job.employment_type, job.seniority])
        st.write(" · ".join(fact for fact in (job.company, job.city) if fact))
        if job.salary_text:
            st.write(f"**Salariu:** {job.salary_text}")
        dates = f"postat {format_date(job.posted_at)}" if job.posted_at else f"văzut prima dată {format_date(job.first_seen_at)}"
        st.caption(f"Prin {job.via or source_label(job.source)} · {dates}")
        with db.open_db(config.paths.db) as conn:
            copies = db.duplicates_of(conn, job.id)
        if copies:
            links = [f"[{copy.via or source_label(copy.source)}]({markdown_url(copy.url)})" for copy in copies]
            st.markdown("**Același anunț și pe:** " + " · ".join(links))

        if profile is not None:
            match: MatchResult = score_job(job, profile, match_settings, today=now_local().date())
            if match.excluded_by:
                st.warning(f"Nu se potrivește preferințelor: {match.excluded_by}.")
            with st.expander(f"Potrivire {match.score}% · de ce?"):
                st.markdown("  \n".join(reason_line(reason) for reason in match.reasons))
                if not job.description:
                    st.caption("Fără descriere, scorul se bazează doar pe titlu și locație; încarcă detaliile pentru un scor mai bun.")

        choice = st.segmented_control(
            "Status",
            list(JobStatus),
            format_func=STATUS_LABELS.get,
            default=job.status,
            key=f"status_{job.id}_{st.session_state.get('jobs_table_version', 0)}",
        )
        if choice is not None and choice != job.status:
            change_status(job, choice)
            refresh()

        st.link_button("Deschide anunțul", job.url, icon=":material/open_in_new:", width="stretch")
        if st.button("CV pentru acest job", icon=":material/description:", width="stretch"):
            st.session_state.cv_job_id = job.id
            st.switch_page("app_pages/cv.py")
        details_from = details_source_name(job)
        if details_from is not None and st.button(
            f"Încarcă detaliile ({source_label(details_from)})",
            help="Consumă o căutare din contul SearchApi.io" if details_from in PAID_DETAILS else "O cerere către site, fără cost",
            icon=":material/download:",
            width="stretch",
        ):
            try:
                with st.spinner("Încarc detaliile..."), db.open_db(config.paths.db) as conn:
                    load_details(conn, create_details_source(job, config, keys), job)
            except (SourceUnavailable, SourceError) as exc:
                st.error(str(exc))
            else:
                refresh()

        if job.requirements:
            st.markdown("**Cerințe**\n" + "\n".join(f"- {item}" for item in job.requirements))
        if job.description:
            with st.container(height=420, border=False):
                plain_text(job.description)
        elif details_from is None:
            st.caption("Sursa nu a oferit o descriere pentru acest job.")
        else:
            st.caption("Descrierea nu e încă încărcată.")


with db.open_db(config.paths.db) as conn:
    counts = db.count_by_status(conn)
    sources_in_db = db.stored_sources(conn)

page_header(
    "Joburi",
    accent="potrivite",
    eyebrow=f"Panou · {sum(counts.values())} anunțuri salvate",
    lead="Ordonate după cât de bine se potrivesc cu profilul tău. Deschide un job ca să vezi descrierea și de ce are scorul acela.",
)

if not counts:
    st.info("Nu ai încă niciun job salvat.")
    st.page_link("app_pages/search.py", label="Fă o căutare", icon=":material/search:")
    st.stop()

stat_tiles([(STATUS_LABELS[status], counts.get(status, 0)) for status in JobStatus])

with st.sidebar:
    st.header("Filtre")
    text = st.text_input("Caută", placeholder="ex. python cluj", key="f_text")
    statuses = st.multiselect(
        "Status", list(JobStatus), default=OPEN_STATUSES, format_func=STATUS_LABELS.get, key="f_status", placeholder="Oricare"
    )
    sources = st.multiselect("Surse", sources_in_db, format_func=source_label, key="f_sources", placeholder="Toate")
    work_modes = st.multiselect(
        "Mod de lucru", list(WorkMode), format_func=WORK_MODE_LABELS.get, key="f_modes", placeholder="Oricare"
    )
    max_age = st.selectbox(
        "Publicat", AGE_OPTIONS, format_func=lambda d: "oricând" if d is None else f"în ultimele {d} zile", key="f_age"
    )
    orders = list(SortOrder) if profile else [o for o in SortOrder if o is not SortOrder.MATCH]
    order = st.selectbox("Ordine", orders, format_func=SORT_LABELS.get, key="f_order")
    with_salary = st.toggle("Doar cu salariu", key="f_salary", help="Doar joburile cu salariul angajatorului, fără estimări")
    st.session_state.setdefault("f_hide_excluded", True)  # the Căutare page may have set it already
    hide_excluded = profile is not None and st.toggle(
        "Aplică preferințele din profil",
        key="f_hide_excluded",
        help="Ascunde joburile care nu se potrivesc cu Profil → Preferințe (rol, nivel, oraș, mod de lucru, "
        "salariu minim, cuvinte excluse) și cu filtrele din config.yaml. Motivul apare în panoul jobului.",
    )

with db.open_db(config.paths.db) as conn:
    stored = db.list_jobs(conn, statuses=statuses or None, sources=sources or None, limit=MAX_JOBS)
job_filter = JobFilter(text=text, work_modes=frozenset(work_modes), with_salary=with_salary, max_age_days=max_age)
jobs = filter_jobs(stored, job_filter, now=now_local())
search = st.session_state.get("f_search")  # {"keywords", "ids"}: "Vezi rezultatele" on the Căutare page
if search:
    found = set(search["ids"])
    jobs = [job for job in jobs if job.id in found]
# One row per ad: copies of the same ad from other boards are folded into the most complete one
groups = group_duplicates(jobs)
merged = len(jobs) - len(groups)
boards = {g.primary.id: list(dict.fromkeys(j.via or source_label(j.source) for j in g.members)) for g in groups}
jobs = [g.primary for g in groups]
matches = score_jobs(jobs, profile, match_settings, today=now_local().date()) if profile else {}
excluded = [job for job in jobs if job.id in matches and matches[job.id].excluded_by]
if hide_excluded:
    jobs = [job for job in jobs if not matches[job.id].excluded_by]
jobs = sort_jobs(jobs, order, {job_id: m.score for job_id, m in matches.items()})

list_column, detail_column = st.columns([3, 2], gap="medium")

with list_column:
    if search:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption(f"Doar rezultatele căutării „{search['keywords']}”")
            st.button("Arată toate joburile", icon=":material/close:", type="tertiary", on_click=clear_search)
    summary = f"{len(jobs)} joburi" + (f" (din primele {MAX_JOBS})" if len(stored) == MAX_JOBS else "")
    if merged:
        summary += f" · {merged} duplicate unite"
    with st.container(horizontal=True, vertical_alignment="center"):
        if hide_excluded and excluded:
            summary += f" · {len(excluded)} ascunse de preferințe"
        st.caption(summary)
        if hide_excluded and excluded:
            st.button("Arată-le", icon=":material/visibility:", type="tertiary", on_click=show_excluded,
                      help="Oprește „Aplică preferințele din profil”; motivul apare în panoul fiecărui job.")
    if profile is None:
        st.page_link("app_pages/profile.py", label="Completează profilul ca să vezi scorul de potrivire", icon=":material/person:")
    if not jobs:
        st.info("Niciun job nu se potrivește filtrelor.")
    else:
        event = st.dataframe(
            [
                {
                    **({"Potrivire": matches[job.id].score} if profile else {}),
                    "Titlu": job.title,
                    "Firmă": job.company,
                    "Locație": job.city,
                    "Salariu": job.salary_text,
                    "Data": format_date(job.listed_at),
                    "Status": STATUS_LABELS[job.status],
                    "Prin": " + ".join(boards[job.id]),
                }
                for job in jobs
            ],
            column_config={
                "Potrivire": st.column_config.ProgressColumn("Potrivire", min_value=0, max_value=100, format="%d%%", width="small"),
            },
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"jobs_table_{st.session_state.get('jobs_table_version', 0)}",
            height=640,
            width="stretch",
        )
        if event.selection.rows:
            st.session_state.selected_job_id = jobs[event.selection.rows[0]].id

with detail_column:
    selected_id = st.session_state.get("selected_job_id")
    with db.open_db(config.paths.db) as conn:
        selected = db.get_job(conn, selected_id) if selected_id else None
    if selected is None:
        st.info("Selectează un job din listă ca să-i vezi detaliile.")
    elif selected.status is JobStatus.NEW:
        change_status(selected, JobStatus.SEEN)  # opening a job marks it as seen
        refresh()
    else:
        render_detail(selected)
