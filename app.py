"""Streamlit entry point: .venv\\Scripts\\python.exe -m streamlit run app.py"""

import streamlit as st

from jobscraper import theme

st.set_page_config(page_title="Job Scraper", page_icon=":material/radar:", layout="wide")
theme.apply()
st.logo(theme.LOGO, size="large", icon_image=theme.LOGO_ICON)

st.navigation(
    [
        st.Page("app_pages/jobs.py", title="Joburi", icon=":material/work:", default=True),
        st.Page("app_pages/search.py", title="Căutare", icon=":material/search:"),
        st.Page("app_pages/cv.py", title="Generează CV", icon=":material/description:"),
        st.Page("app_pages/cvs.py", title="CV-urile mele", icon=":material/folder_copy:"),
        st.Page("app_pages/profile.py", title="Profil", icon=":material/person:"),
        st.Page("app_pages/keys.py", title="Chei API", icon=":material/key:"),
    ]
).run()
