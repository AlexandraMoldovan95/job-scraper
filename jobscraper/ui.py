"""Streamlit helpers shared by every page."""

from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from jobscraper.config import AppConfig, load_config
from jobscraper.keys import KNOWN_KEYS, KeyStore, mask
from jobscraper.models import BUCHAREST_TZ, ExperienceLevel, JobStatus, WorkMode
from jobscraper.sources import SOURCE_LABELS

STATUS_LABELS = {
    JobStatus.NEW: "Nou",
    JobStatus.SEEN: "Văzut",
    JobStatus.SAVED: "Salvat",
    JobStatus.APPLIED: "Am aplicat",
    JobStatus.REJECTED: "Respins",
}

WORK_MODE_LABELS = {
    WorkMode.REMOTE: "Remote",
    WorkMode.HYBRID: "Hibrid",
    WorkMode.ON_SITE: "La birou",
}

LEVEL_LABELS = {
    ExperienceLevel.JUNIOR: "Junior (sub 2 ani)",
    ExperienceLevel.MID: "Mid (2–5 ani)",
    ExperienceLevel.SENIOR: "Senior (5+ ani)",
}

_ORIGIN_LABELS = {"session": "din sesiune", "env": "din .env"}


def app_context() -> tuple[AppConfig, KeyStore]:
    """Config and keys for the current run; cheap enough to rebuild on every rerun."""
    return load_config(), KeyStore(st.session_state)


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def format_date(value: datetime | None) -> str:
    return value.astimezone(BUCHAREST_TZ).strftime("%d.%m.%Y") if value else ""


# --- design system building blocks (styles in theme.py) -------------------------------------


def page_header(title: str, *, eyebrow: str, accent: str | None = None, lead: str | None = None) -> None:
    """Mono eyebrow pill, gradient headline (optional shimmering accent words), muted lead."""
    accent_html = f' <span class="jl-accent-text">{escape(accent)}</span>' if accent else ""
    lead_html = f'<p class="jl-lead">{escape(lead)}</p>' if lead else ""
    st.html(
        f'<div class="jl-hero"><span class="jl-eyebrow">{escape(eyebrow)}</span>'
        f'<h1 class="jl-title">{escape(title)}{accent_html}</h1>{lead_html}</div>'
    )


def card(name: str, **kwargs) -> DeltaGenerator:
    """Glass card: a bordered container whose `st-key-card-*` class the theme styles."""
    return st.container(border=True, key=f"card-{name}", **kwargs)


def stat_tiles(items: list[tuple[str, int | str]]) -> None:
    """A row of small lifted tiles with a mono label and a gradient number, entering staggered."""
    for i, (column, (label, value)) in enumerate(zip(st.columns(len(items)), items), start=1):
        with column.container(border=True, key=f"tile-{i}"):
            st.metric(label, value)


def pills(values: list[str], *, muted: bool = False) -> None:
    css = "jl-pill jl-pill--muted" if muted else "jl-pill"
    st.html('<div class="jl-pills">' + "".join(f'<span class="{css}">{escape(v)}</span>' for v in values if v) + "</div>")


def divider() -> None:
    st.html('<hr class="jl-divider">')


def card_title(title: str, subtitle: str | None = None) -> None:
    """Title line of a card; HTML-escaped, so "C# / .NET" or "*" in a job title stay literal."""
    sub = f' <span class="jl-card-sub">{escape(subtitle)}</span>' if subtitle else ""
    st.html(f'<div class="jl-card-title">{escape(title)}{sub}</div>')


def plain_text(text: str, *, strong: bool = False) -> None:
    """Text as written (a job ad, a CV headline) in the body font: st.text would set it in
    monospace, and Markdown would turn its "-", "*" and "#" into formatting."""
    css = "jl-plain jl-plain--strong" if strong else "jl-plain"
    st.html(f'<div class="{css}">{escape(text)}</div>')


def render_key_settings(keys: KeyStore) -> None:
    """Where the user brings their own API keys (the "Chei API" page)."""
    st.caption(
        "Folosește cheile tale. Cele puse doar în sesiune dispar când închizi tab-ul; "
        "cele salvate în .env rămân doar pe calculatorul tău."
    )
    for name, label in KNOWN_KEYS.items():
        found = keys.lookup(name)
        status = f"{mask(found[0])} ({_ORIGIN_LABELS[found[1]]})" if found else "lipsește"
        with st.container(border=True):
            st.markdown(f"**{label}:** {status}")
            input_key = f"key_input:{name}"
            st.text_input(name, type="password", key=input_key)
            buttons = st.columns([1, 1, 2])
            buttons[0].button(
                "Folosește",
                key=f"use:{name}",
                help="Doar pentru sesiunea curentă",
                on_click=_use_for_session,
                args=(keys, name, input_key),
            )
            buttons[1].button(
                "Salvează",
                key=f"save:{name}",
                help="Salvează în fișierul .env",
                on_click=_save_to_env,
                args=(keys, name, input_key),
            )
            if found and found[1] == "session":
                buttons[2].button("Uită din sesiune", key=f"forget:{name}", on_click=keys.forget_session, args=(name,))
            elif found and found[1] == "env":
                buttons[2].button("Șterge din .env", key=f"remove:{name}", on_click=keys.remove_from_env, args=(name,))


# Callbacks run before the next rerun, so they may clear the input widget's value.


def _use_for_session(keys: KeyStore, name: str, input_key: str) -> None:
    keys.set_for_session(name, st.session_state[input_key])
    st.session_state[input_key] = ""


def _save_to_env(keys: KeyStore, name: str, input_key: str) -> None:
    value = st.session_state[input_key]
    if value.strip():
        keys.save_to_env(name, value)
    st.session_state[input_key] = ""
