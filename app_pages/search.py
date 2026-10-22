"""Căutare: run a search on the chosen sources and store the results."""

from collections import Counter

import streamlit as st
import yaml
from pydantic import ValidationError

from jobscraper import db
from jobscraper.matching import MatchSettings, city_matches, exclusion_kind, score_jobs
from jobscraper.models import JobPreferences, SearchQuery, WorkMode, now_local
from jobscraper.profile import load_profile
from jobscraper.scrape import run_source
from jobscraper.sources import IMPLEMENTED_SOURCES, PAID_SEARCH, SCRAPERS, SourceUnavailable, create_source
from jobscraper.ui import app_context, card, card_title, page_header, source_label

config, keys = app_context()
available = [name for name in config.enabled_sources() if name in IMPLEMENTED_SOURCES]
try:
    profile = load_profile(config.paths.profile)
except (yaml.YAMLError, ValidationError):
    profile = None  # the Profil page reports the problem
prefs = profile.preferences if profile else JobPreferences()

page_header(
    "Caută",
    accent="joburi noi",
    eyebrow=f"Căutare · {len(available)} surse active",
    lead="Rezultatele se salvează local și apar în Joburi, ordonate după potrivire și filtrate după preferințele din profil.",
)

with st.form("search"):
    left, middle, right = st.columns([3, 2, 1])
    keywords = left.text_input("Ce cauți", value=next(iter(prefs.roles or config.search.queries), ""))
    city = middle.text_input("Oraș (opțional)", value=next(iter(prefs.cities or config.search.cities), ""))
    pages = right.number_input("Pagini", min_value=1, max_value=10, value=1)
    sources = st.multiselect("Surse", available, default=available, format_func=source_label)
    remote_only = st.checkbox("Doar remote", value=prefs.work_modes == [WorkMode.REMOTE])
    scrapers = [source_label(name) for name in available if name in SCRAPERS]
    with_details = bool(scrapers) and st.checkbox(
        f"Încarcă și descrierile complete ({', '.join(scrapers)})",
        value=config.scraping.auto_details,
        help=f"Câte o cerere pentru fiecare anunț nou, la ~{config.scraping.min_delay_seconds:g} s distanță "
        f"(maximum {config.scraping.max_auto_details} per sursă). Fără cost. "
        "Descrierile de pe LinkedIn se încarcă din panoul jobului, pentru că fiecare costă un credit.",
    )
    submitted = st.form_submit_button("Caută", type="primary", icon=":material/search:")

paid = [source_label(name) for name in available if name in PAID_SEARCH]
st.caption(
    (f"{', '.join(paid)} (prin SearchApi.io) consumă o căutare din contul tău pe pagină de rezultate. " if paid else "")
    + "Scraperele directe sunt gratuite, dar mai lente: o cerere la câteva secunde pe site."
)
if profile is not None:
    st.caption("Căutarea pornește cu primul rol și primul oraș din Profil → Preferințe.")


def run_one(conn, name: str, query: SearchQuery) -> tuple[str, ...]:
    """Search one source and report the outcome; returns the ids of the jobs it found."""
    try:
        source = create_source(name, config, keys)
    except SourceUnavailable as exc:
        st.error(f"{source_label(name)}: {exc}")
        return ()
    if with_details and name in SCRAPERS:
        with st.status(f"Caut pe {source_label(name)}...", expanded=True) as status:
            bar = st.progress(0.0, text="Caut anunțuri...")

            def progress(done: int, total: int) -> None:
                if total:
                    bar.progress(done / total, text=f"Descrieri încărcate: {done} din {total}")
                else:
                    bar.progress(1.0, text="Toate anunțurile găsite aveau deja descrierea completă.")

            outcome = run_source(
                conn, source, query, details=source, max_details=config.scraping.max_auto_details, on_progress=progress
            )
            status.update(
                label=f"{source_label(name)}: {'eroare' if outcome.error else 'gata'}",
                state="error" if outcome.error else "complete",
                expanded=False,
            )
    else:
        with st.spinner(f"Caut pe {source_label(name)}..."):
            outcome = run_source(conn, source, query)
    if outcome.error:
        st.error(f"{source_label(name)}: {outcome.error}")
        return ()
    if not outcome.found:
        message = f"{source_label(name)}: niciun job pentru „{query.keywords}”."
        if outcome.unrelated:
            message += (
                f" Sursa a întors în schimb {outcome.unrelated} anunțuri generale, fără cuvintele căutate în titlu; "
                "nu le-am salvat."
            )
        st.info(message)
        return ()
    if not outcome.new:
        message = f"{source_label(name)}: {outcome.found} joburi găsite, toate erau deja salvate."
    else:
        message = f"{source_label(name)}: {outcome.found} joburi găsite, dintre care {outcome.new} noi."
    if outcome.described:
        message += f" Descrieri complete încărcate: {outcome.described}."
    st.success(message)
    if outcome.details_failed:
        st.warning(f"{source_label(name)}: {outcome.details_failed} descrieri nu s-au putut încărca; încearcă din panoul jobului.")
    return outcome.job_ids


def open_results(*, with_preferences: bool) -> None:
    """Joburi with only this search's jobs; optionally without the profile preferences hiding any."""
    st.session_state.f_search = st.session_state.last_search
    st.session_state.f_hide_excluded = with_preferences
    st.switch_page("app_pages/jobs.py")


def render_results(last: dict) -> None:
    """How many of the jobs found the Joburi page shows, and why the others are hidden."""
    with db.open_db(config.paths.db) as conn:
        jobs = [job for job_id in last["ids"] if (job := db.get_job(conn, job_id))]
    hidden: Counter[str] = Counter()
    if profile is not None and jobs:
        matches = score_jobs(jobs, profile, MatchSettings.from_sources(profile, config.search), today=now_local().date())
        hidden = Counter(exclusion_kind(m.excluded_by) for m in matches.values() if m.excluded_by)
    shown = len(jobs) - sum(hidden.values())
    with card("results"):
        card_title(f"Rezultate pentru „{last['keywords']}”", f"{len(jobs)} joburi")
        if not jobs:
            st.caption("Sursele nu au găsit niciun job.")
            return
        if hidden:
            reasons = ", ".join(f"{kind} ({count})" for kind, count in hidden.most_common())
            st.markdown(
                f"**{shown}** se potrivesc preferințelor din profil; **{sum(hidden.values())}** sunt ascunse în Joburi "
                f"din cauza lor: {reasons}."
            )
        else:
            st.markdown(f"Toate cele **{len(jobs)}** apar în Joburi.")
        with st.container(horizontal=True):
            if st.button("Vezi rezultatele în Joburi", type="primary", icon=":material/arrow_forward:", disabled=not shown):
                open_results(with_preferences=True)
            if hidden and st.button(f"Vezi toate cele {len(jobs)}, fără preferințe", icon=":material/filter_alt_off:"):
                open_results(with_preferences=False)


if submitted:
    if not keywords.strip():
        st.warning("Scrie ce job cauți.")
    elif not sources:
        st.warning("Alege cel puțin o sursă.")
    else:
        query = SearchQuery(
            keywords=keywords.strip(),
            city=city.strip() or None,
            work_mode=WorkMode.REMOTE if remote_only else None,
            max_pages=int(pages),
        )
        if query.city and prefs.cities and city_matches(query.city, prefs.cities) is False:
            st.info(
                f"{query.city} nu e printre orașele din preferințele tale: joburile de acolo (în afară de cele remote) "
                "vor fi ascunse în Joburi cât timp e pornit „Aplică preferințele din profil”."
            )
        with db.open_db(config.paths.db) as conn:
            found = [job_id for name in sources for job_id in run_one(conn, name, query)]
        # Kept in the session: the buttons below must still work on the rerun their click causes
        st.session_state.last_search = {"keywords": query.keywords, "ids": list(dict.fromkeys(found))}

if last := st.session_state.get("last_search"):
    render_results(last)

with db.open_db(config.paths.db) as conn:
    runs = db.recent_runs(conn, limit=20)

if runs:
    st.subheader("Ultimele căutări")
    st.dataframe(
        [
            {
                "Când": run.started_at[:16].replace("T", " "),
                "Sursă": source_label(run.source),
                "Căutare": run.query,
                "Găsite": run.jobs_found,
                "Noi": run.jobs_new,
                "Eroare": run.error or "",
            }
            for run in runs
        ],
        hide_index=True,
        width="stretch",
    )
