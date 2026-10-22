"""Profil: the real experience every generated CV is built from (data/profile.yaml)."""

from datetime import datetime

import pandas as pd
import streamlit as st
import yaml
from pydantic import ValidationError

from jobscraper import db
from jobscraper.config import PROJECT_ROOT
from jobscraper.linkedin_import import ImportedProfile, merge_into_profile, read_linkedin_pdf
from jobscraper.llm import LLMError, make_client
from jobscraper.models import ExperienceLevel, WorkMode
from jobscraper.profile import (
    delete_profile,
    linkedin_import_path,
    linkedin_pdf_path,
    load_profile,
    profile_from_yaml,
    save_linkedin_pdf,
    save_profile,
)
from jobscraper.profile_form import (
    FIELD_LABELS,
    LIST_SECTIONS,
    PREFIX,
    SKILL_LEVEL_LABELS,
    describe_errors,
    entry_state,
    from_form_state,
    key,
    new_uid,
    to_form_state,
)
from jobscraper.ui import LEVEL_LABELS, WORK_MODE_LABELS, app_context, card, page_header

EXAMPLE_PATH = PROJECT_ROOT / "profile.example.yaml"

# Rows of fields per list section; text areas for the long ones
LAYOUTS = {
    "experience": [("role", "company"), ("city", "start", "end"), ("description",), ("achievements",), ("technologies",)],
    "education": [("institution", "degree"), ("field", "start", "end")],
    "courses": [("name", "provider", "completed"), ("description",)],
    "projects": [("name", "url"), ("description",), ("technologies",)],
}
TEXT_AREAS = {"description", "achievements"}
PLACEHOLDERS = {
    "start": "AAAA-LL sau AAAA",
    "end": "AAAA-LL sau AAAA, gol = prezent",
    "completed": "AAAA-LL sau AAAA",
    "achievements": "Câte o realizare pe rând, cu rezultate concrete (ex. am redus timpul raportului de la 3 h la 10 min)",
    "technologies": "separate prin virgulă: Python, Django, SQL",
}
ENTRY_TITLES = {
    "experience": ("role", "company"),
    "education": ("degree", "institution"),
    "courses": ("name", "provider"),
    "projects": ("name",),
}
ADD_LABELS = {
    "experience": "Adaugă experiență",
    "education": "Adaugă studii",
    "courses": "Adaugă curs",
    "projects": "Adaugă proiect",
}

config, keys = app_context()
path = config.paths.profile
pdf_path = linkedin_pdf_path(path)
import_path = linkedin_import_path(path)


def reset_form(profile) -> None:
    for name in [name for name in st.session_state if str(name).startswith(PREFIX)]:
        del st.session_state[name]
    st.session_state.update(to_form_state(profile))


# --- callbacks: they run before the next rerun, so they may rewrite widget values ---------


def on_save() -> None:
    try:
        profile = from_form_state(st.session_state)
    except ValidationError as exc:
        st.session_state.profile_errors = describe_errors(exc)
        return
    save_profile(profile, path)
    reset_form(profile)
    st.session_state.profile_notice = "Profil salvat."


def on_save_yaml() -> None:
    try:
        profile = profile_from_yaml(st.session_state[key("yaml")])
    except yaml.YAMLError as exc:
        st.session_state.profile_errors = [f"YAML invalid: {exc}"]
        return
    except ValidationError as exc:
        st.session_state.profile_errors = describe_errors(exc)
        return
    save_profile(profile, path)
    reset_form(profile)
    st.session_state.profile_notice = "Profil salvat din YAML."


def on_discard() -> None:
    reset_form(load_profile(path))
    st.session_state.profile_notice = "Am revenit la profilul salvat."


def on_load_example() -> None:
    reset_form(load_profile(EXAMPLE_PATH))
    st.session_state.profile_notice = "Am încărcat exemplul. Înlocuiește-l cu datele tale, apoi apasă „Salvează profilul”."


def on_import_pdf() -> None:
    upload = st.session_state.get("linkedin_pdf_upload")
    if upload is None:
        st.session_state.profile_errors = ["Alege întâi PDF-ul exportat de pe LinkedIn."]
        return
    data = upload.getvalue()
    try:
        with st.spinner("Claude citește PDF-ul... (poate dura un minut)"):
            result = read_linkedin_pdf(make_client(keys), data, llm=config.llm)
    except LLMError as exc:
        st.session_state.profile_errors = [str(exc)]
        return
    save_linkedin_pdf(data, path)
    import_path.write_text(result.output.model_dump_json(indent=1), encoding="utf-8")
    fill_from_import(result.output)
    st.session_state.profile_notice = (
        f"Am completat formularul din PDF ({result.model}, {result.input_tokens} tokeni la intrare). "
        "Verifică fiecare tab și apasă „Salvează profilul”: până atunci nu e salvat nimic."
    )


def on_reuse_import() -> None:
    try:
        imported = ImportedProfile.model_validate_json(import_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        st.session_state.profile_errors = [f"Importul salvat nu poate fi citit; încarcă din nou PDF-ul. ({exc})"]
        return
    fill_from_import(imported)
    st.session_state.profile_notice = (
        "Am completat formularul din ultimul import, fără un apel nou la Claude. "
        "Verifică fiecare tab și apasă „Salvează profilul”."
    )


def fill_from_import(imported: ImportedProfile) -> None:
    """Merge into the profile as currently edited (unsaved edits count), else the saved one."""
    try:
        current = from_form_state(st.session_state)
    except ValidationError:
        try:
            current = load_profile(path)
        except (yaml.YAMLError, ValidationError):
            current = None
    reset_form(merge_into_profile(imported, current))


def on_delete() -> None:
    deleted = delete_profile(path)
    removed_cvs = 0
    if st.session_state.get("delete_cvs_too"):
        with db.open_db(config.paths.db) as conn:
            removed_cvs = db.delete_all_cvs(conn)
    reset_form(None)
    what = "Profilul și PDF-ul atașat au fost șterse" if pdf_path in deleted else "Profilul a fost șters"
    cvs = "un CV generat" if removed_cvs == 1 else f"{removed_cvs} CV-uri generate"
    st.session_state.profile_notice = what + (f", împreună cu {cvs}." if removed_cvs else ".")


def on_add(section: str) -> None:
    uid = new_uid()
    st.session_state.update(entry_state(section, uid))
    st.session_state[key(section)] = [*st.session_state[key(section)], uid]


def on_remove(section: str, uid: str) -> None:
    st.session_state[key(section)] = [u for u in st.session_state[key(section)] if u != uid]
    for name in LIST_SECTIONS[section].model_fields:
        st.session_state.pop(key(section, uid, name), None)


# --- widgets ------------------------------------------------------------------------------


def label(section: str, name: str) -> str:
    required = LIST_SECTIONS[section].model_fields[name].is_required()
    return FIELD_LABELS[name] + (" *" if required else "")


def render_entries(section: str) -> None:
    uids = st.session_state[key(section)]
    if not uids:
        st.caption("Nicio intrare încă.")
    for uid in uids:
        title_parts = [st.session_state.get(key(section, uid, name), "") for name in ENTRY_TITLES[section]]
        title = " · ".join(part for part in title_parts if part) or "Intrare nouă"
        with st.expander(title, expanded=title == "Intrare nouă"):
            for row in LAYOUTS[section]:
                for column, name in zip(st.columns(len(row)), row):
                    widget = column.text_area if name in TEXT_AREAS else column.text_input
                    widget(label(section, name), key=key(section, uid, name), placeholder=PLACEHOLDERS.get(name))
            st.button("Șterge", key=f"remove:{section}:{uid}", icon=":material/delete:", on_click=on_remove, args=(section, uid))
    st.button(ADD_LABELS[section], key=f"add:{section}", icon=":material/add:", on_click=on_add, args=(section,))


# --- page ---------------------------------------------------------------------------------

if key("contact", "full_name") not in st.session_state:
    # First visit, or Streamlit dropped the widget values after a page switch: reload from disk
    try:
        reset_form(load_profile(path))
    except (yaml.YAMLError, ValidationError) as exc:
        reset_form(None)
        st.session_state.profile_errors = [f"{path} nu poate fi citit; îl poți repara în tab-ul YAML. ({exc})"]

page_header("Profilul", accent="tău", eyebrow="Profil · sursa unică de adevăr")
st.caption(
    "Tot ce apare în CV-urile generate vine **doar** din acest profil, deci completează-l cu date reale. "
    "Câmpurile cu * sunt obligatorii."
)

# Always the same containers, so one-off messages do not shift the tabs below: Streamlit
# keeps the open tab by position and would jump back to the first one.
with st.container():
    if notice := st.session_state.pop("profile_notice", None):
        st.success(notice)
    for message in st.session_state.pop("profile_errors", []):
        st.error(message)
with st.container():
    if not path.exists():
        st.info("Nu ai salvat încă un profil. Pornește de la PDF-ul tău de pe LinkedIn sau de la exemplu.")
        st.button("Pornește de la exemplu", icon=":material/description:", on_click=on_load_example)

with card("linkedin"):
    st.markdown("**Import din LinkedIn**")
    st.caption(
        "Pe LinkedIn: profilul tău → Mai mult → Salvează ca PDF. Claude citește PDF-ul și completează formularul "
        "fără să inventeze nimic; ce există deja în profil și lipsește din PDF rămâne. "
        f"Consumă tokeni din contul tău Anthropic ({config.llm.model})."
    )
    st.file_uploader("PDF de pe LinkedIn", type="pdf", key="linkedin_pdf_upload", max_upload_size=10)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.button("Completează din PDF", type="primary", icon=":material/auto_awesome:", on_click=on_import_pdf)
        if import_path.exists():
            st.button(
                "Refă din ultimul import", icon=":material/history:", on_click=on_reuse_import,
                help="Completează formularul din ce a citit Claude ultima dată, fără cost.",
            )
        if pdf_path.exists():
            stat = pdf_path.stat()
            attached = datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
            st.download_button(
                "Descarcă PDF-ul atașat", pdf_path.read_bytes(), file_name="profil_linkedin.pdf", mime="application/pdf",
                icon=":material/download:", type="tertiary",
            )
            st.caption(f"PDF atașat profilului: {stat.st_size // 1024 or 1} KB, importat pe {attached}.")

contact_tab, experience_tab, education_tab, projects_tab, skills_tab, prefs_tab, yaml_tab = st.tabs(
    ["Contact și rezumat", "Experiență", "Educație și cursuri", "Proiecte", "Skill-uri și limbi", "Preferințe", "YAML"]
)

with contact_tab:
    rows = [("full_name", "email"), ("phone", "city"), ("linkedin", "github", "website")]
    for row in rows:
        for column, name in zip(st.columns(len(row)), row):
            column.text_input(FIELD_LABELS[name] + (" *" if name == "full_name" else ""), key=key("contact", name))
    st.text_input("Titlu profesional", key=key("headline"), placeholder="ex. Junior Python Developer")
    st.text_area("Rezumat", key=key("summary"), height=120, placeholder="2–4 propoziții despre tine și ce cauți")

with experience_tab:
    st.caption("Pune cel mai recent job primul. Realizările concrete contează cel mai mult pentru CV.")
    render_entries("experience")

with education_tab:
    st.subheader("Studii")
    render_entries("education")
    st.subheader("Cursuri și certificări")
    render_entries("courses")

with projects_tab:
    render_entries("projects")

with skills_tab:
    st.subheader("Skill-uri")
    st.data_editor(
        pd.DataFrame(st.session_state[key("skills")], columns=["name", "level", "years"]),
        key=key("skills", "editor"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "name": st.column_config.TextColumn("Skill", required=True),
            "level": st.column_config.SelectboxColumn("Nivel", options=list(SKILL_LEVEL_LABELS.values())),
            "years": st.column_config.NumberColumn("Ani de experiență", min_value=0.0, step=0.5),
        },
    )
    st.subheader("Limbi străine")
    st.data_editor(
        pd.DataFrame(st.session_state[key("languages")], columns=["name", "level"]),
        key=key("languages", "editor"),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "name": st.column_config.TextColumn("Limbă", required=True),
            "level": st.column_config.TextColumn("Nivel", help="A1–C2 sau nativ", required=True),
        },
    )

with prefs_tab:
    st.caption(
        "Căutarea pornește cu primul rol și primul oraș de aici. Pe pagina Joburi, preferințele sunt și filtre: "
        "joburile care clar nu se potrivesc sunt ascunse (le poți arăta din bara laterală). "
        "Un câmp gol nu filtrează nimic, iar un job fără informația respectivă (ex. fără oraș) rămâne vizibil."
    )
    st.text_input(
        "Roluri căutate", key=key("preferences", "roles"), placeholder="Python Developer, Junior Developer",
        help="Ascunde joburile al căror titlu nu are niciun cuvânt dintr-un rol (developer = engineer = programator).",
    )
    left, right = st.columns(2)
    left.text_input("Orașe", key=key("preferences", "cities"), placeholder="București, Cluj-Napoca",
                    help="Joburile remote acceptate apar oricare ar fi orașul.")
    right.multiselect("Mod de lucru", list(WorkMode), format_func=WORK_MODE_LABELS.get,
                      key=key("preferences", "work_modes"), placeholder="Oricare")
    left.multiselect(
        "Nivel de experiență", list(ExperienceLevel), format_func=LEVEL_LABELS.get, key=key("preferences", "levels"),
        placeholder="Oricare",
        help="Nivelul anunțului vine din titlu (junior, senior...) sau din anii de experiență ceruți.",
    )
    right.number_input("Salariu minim net (RON/lună, 0 = fără)", min_value=0, step=500, key=key("preferences", "min_salary"),
                       help="Se compară doar cu salariile afișate de angajator, cu monedă cunoscută.")
    st.text_input("Cuvinte excluse din titlu", key=key("preferences", "excluded_keywords"),
                  placeholder="call center, vânzări")

with yaml_tab:
    st.caption("Același profil, ca text. Util pentru modificări rapide sau backup. Salvarea de aici înlocuiește formularul.")
    st.text_area("profile.yaml", key=key("yaml"), height=480, label_visibility="collapsed")
    st.button("Salvează YAML", icon=":material/save:", on_click=on_save_yaml)

st.divider()
with st.container(horizontal=True):
    st.button("Salvează profilul", type="primary", icon=":material/save:", on_click=on_save)
    st.button("Renunță la modificări", on_click=on_discard, disabled=not path.exists())
    with st.popover("Șterge profilul", icon=":material/delete:",
                    disabled=not (path.exists() or pdf_path.exists() or import_path.exists())):
        st.markdown("Șterge **profilul** și **PDF-ul de pe LinkedIn** atașat de pe acest calculator. Nu se poate anula.")
        st.checkbox("Șterge și CV-urile generate (conțin date din profil)", key="delete_cvs_too")
        st.button("Da, șterge", type="primary", icon=":material/delete_forever:", on_click=on_delete, width="stretch")
st.caption(f"Fișier: `{path}`. Modificările nesalvate se pierd dacă schimbi pagina.")
