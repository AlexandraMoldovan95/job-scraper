"""Chei API: bring your own keys (BYOK)."""

import streamlit as st

from jobscraper.ui import app_context, page_header, render_key_settings

_, keys = app_context()

page_header("Chei", accent="API", eyebrow="BYOK · cheile tale")
render_key_settings(keys)
