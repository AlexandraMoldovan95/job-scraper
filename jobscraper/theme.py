"""The "Linear / Modern" design system (design.md) for Streamlit.

Colors, fonts and radii are in .streamlit/config.toml. This module adds what the
Streamlit theme cannot express, in one place:

- the layered ambient background (base gradient, floating light blobs, grid, noise)
- glass cards with multi-layer shadows and a mouse-tracking spotlight
- accent buttons with glow and shine, inputs with an accent focus ring
- gradient headline type, mono labels, pills
- 200-300 ms expo-out motion, switched off by prefers-reduced-motion

Styles target stable hooks only: Streamlit's data-testid attributes and the
`st-key-<key>` classes of keyed containers (see ui.card / ui.tile).
"""

from __future__ import annotations

from urllib.parse import quote

import streamlit as st

# design.md's palette lifted one step (the user found the literal near-blacks too dark):
# same hues and relationships, more light in the background, surfaces and muted text.
TOKENS = {
    "bg-top": "#1b1b28",
    "bg-deep": "#08080b",
    "bg-base": "#0d0d12",
    "bg-elevated": "#14141a",
    "surface": "rgba(255,255,255,0.06)",
    "surface-hover": "rgba(255,255,255,0.10)",
    "fg": "#EDEDEF",
    "fg-muted": "#9BA0AA",
    "fg-subtle": "rgba(255,255,255,0.60)",
    "accent": "#5E6AD2",
    "accent-bright": "#6872D9",
    "accent-glow": "rgba(94,106,210,0.3)",
    "border": "rgba(255,255,255,0.06)",
    "border-hover": "rgba(255,255,255,0.10)",
    "border-accent": "rgba(94,106,210,0.30)",
    "ease": "cubic-bezier(0.16, 1, 0.3, 1)",
    "mono": "'JetBrains Mono', ui-monospace, SFMono-Regular, monospace",
    "shadow-card": "0 0 0 1px rgba(255,255,255,0.06), 0 2px 20px rgba(0,0,0,0.4), 0 0 40px rgba(0,0,0,0.2)",
    "shadow-card-hover": "0 0 0 1px rgba(255,255,255,0.10), 0 8px 40px rgba(0,0,0,0.5), 0 0 80px rgba(94,106,210,0.10)",
    "shadow-cta": "0 0 0 1px rgba(94,106,210,0.5), 0 4px 12px rgba(94,106,210,0.3), inset 0 1px 0 0 rgba(255,255,255,0.2)",
    "shadow-cta-hover": "0 0 0 1px rgba(104,114,217,0.6), 0 6px 22px rgba(94,106,210,0.45), inset 0 1px 0 0 rgba(255,255,255,0.25)",
    "focus-ring": "0 0 0 2px #0d0d12, 0 0 0 4px rgba(94,106,210,0.5)",
}

# App name for st.logo (top of the sidebar): a radar mark in the accent gradient + "Job Scraper"
LOGO = """<svg xmlns="http://www.w3.org/2000/svg" width="196" height="32" viewBox="0 0 196 32">
<defs><linearGradient id="jl-logo" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#8D95E8"/><stop offset="1" stop-color="#5E6AD2"/></linearGradient></defs>
<rect x="1" y="1" width="30" height="30" rx="8" fill="url(#jl-logo)"/>
<circle cx="16" cy="16" r="8.5" fill="none" stroke="#fff" stroke-width="1.8" opacity="0.9"/>
<circle cx="16" cy="16" r="4" fill="none" stroke="#fff" stroke-width="1.4" opacity="0.6"/>
<path d="M16 16 L22.5 9.5" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
<circle cx="16" cy="16" r="1.8" fill="#fff"/>
<text x="42" y="22" font-family="Inter, 'Segoe UI', system-ui, sans-serif" font-size="17" font-weight="600"
 letter-spacing="-0.3" fill="#EDEDEF">Job <tspan fill="#8D95E8">Scraper</tspan></text>
</svg>"""
LOGO_ICON = ":material/radar:"  # shown instead while the sidebar is collapsed

CARD = '[class*="st-key-card-"]'
TILE = '[class*="st-key-tile-"]'
GLASS = f'{CARD}, {TILE}, [data-testid="stForm"]'

# Fractal noise, 200px tile; very low opacity so it only breaks up gradient banding.
# Fully percent-encoded: st.html sanitizes with DOMPurify, which drops a whole <style>
# whose text contains "<" followed by a letter (its mXSS guard).
_NOISE = 'url("data:image/svg+xml,' + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'>"
    "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/></filter>"
    "<rect width='100%' height='100%' filter='url(#n)' opacity='0.5'/></svg>",
    safe="",
) + '")'

_CSS = """
:root {
%(vars)s
}

/* ---------- Background: base gradient + light blobs + bottom pulse + grid/noise ---------- */
[data-testid="stApp"] {
  background: radial-gradient(ellipse at top, var(--jl-bg-top) 0%%, var(--jl-bg-base) 55%%, var(--jl-bg-deep) 100%%) fixed;
}
/* Light pools: solid blobs softened by a heavy blur (a plain radial gradient only reaches
   its opacity at the very centre and reads as black) */
[data-testid="stApp"]::before {
  content: ""; position: fixed; inset: -15%%; z-index: 0; pointer-events: none;
  background:
    radial-gradient(620px 820px at 50%% 2%%, rgba(94,106,210,0.34) 0%%, rgba(94,106,210,0.26) 45%%, transparent 100%%),
    radial-gradient(420px 520px at 18%% 50%%, rgba(168,85,247,0.22) 0%%, rgba(236,72,153,0.14) 55%%, transparent 100%%),
    radial-gradient(400px 500px at 88%% 60%%, rgba(99,102,241,0.20) 0%%, rgba(59,130,246,0.14) 55%%, transparent 100%%);
  filter: blur(80px); will-change: transform;
  animation: jl-float 10s ease-in-out infinite;
}
[data-testid="stApp"]::after {
  content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background-image:
    %(noise)s,
    linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px);
  background-size: 200px 200px, 64px 64px, 64px 64px;
  -webkit-mask-image: radial-gradient(ellipse at top, #000 30%%, transparent 85%%);
          mask-image: radial-gradient(ellipse at top, #000 30%%, transparent 85%%);
  opacity: 0.6;
}
[data-testid="stMain"]::before {
  content: ""; position: fixed; left: 50%%; bottom: -20vh; width: 90vw; height: 50vh; z-index: 0;
  transform: translateX(-50%%); pointer-events: none;
  background: radial-gradient(closest-side, rgba(94,106,210,0.18) 0%%, rgba(94,106,210,0.12) 50%%, transparent);
  filter: blur(60px);
  animation: jl-pulse 9s ease-in-out infinite;
}
[data-testid="stAppViewContainer"] { position: relative; z-index: 1; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMain"] { background: transparent; }

/* ---------- Sidebar: frosted glass ---------- */
[data-testid="stSidebar"] {
  background: rgba(20,20,28,0.62) !important;
  -webkit-backdrop-filter: blur(20px) saturate(140%%);
          backdrop-filter: blur(20px) saturate(140%%);
  border-right: 1px solid var(--jl-border) !important;
}
[data-testid="stSidebarNavLink"] {
  border-radius: 8px; transition: background-color .2s var(--jl-ease), box-shadow .2s var(--jl-ease);
}
[data-testid="stSidebarNavLink"]:hover { background: var(--jl-surface); }
[data-testid="stSidebarNavLink"][aria-current="page"] {
  background: rgba(94,106,210,0.12);
  box-shadow: inset 0 0 0 1px var(--jl-border-accent), 0 0 24px rgba(94,106,210,0.10);
}
[data-testid="stSidebarNavSeparator"] { border-color: var(--jl-border); }

/* ---------- Typography ---------- */
h1, h2, h3, h4 { letter-spacing: -0.02em; }
/* "backwards", not "both": after the entrance the scroll parallax (script below) sets opacity/transform */
.jl-hero { margin: 0.25rem 0 0.75rem; transform-origin: left top; animation: jl-fade-up .6s var(--jl-ease) backwards; }
.jl-eyebrow, .jl-pill {
  display: inline-flex; align-items: center; gap: .4rem;
  font-family: var(--jl-mono); font-size: .75rem; letter-spacing: .14em; text-transform: uppercase;
  color: var(--jl-fg-muted); border: 1px solid var(--jl-border-accent); border-radius: 999px;
  padding: .28rem .7rem; background: rgba(94,106,210,0.06);
}
.jl-eyebrow::before {
  content: ""; width: 6px; height: 6px; border-radius: 50%%; background: var(--jl-accent);
  box-shadow: 0 0 10px var(--jl-accent);
}
.jl-title {
  margin: .9rem 0 .35rem; font-size: clamp(2.1rem, 4.2vw, 3.1rem); font-weight: 600; line-height: 1.05;
  letter-spacing: -0.03em; color: var(--jl-fg);
  background: linear-gradient(to bottom, #fff 0%%, rgba(255,255,255,0.95) 45%%, rgba(255,255,255,0.7) 100%%);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.jl-accent-text {
  background: linear-gradient(90deg, var(--jl-accent), #a5b4fc, var(--jl-accent));
  background-size: 200%% auto; -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  animation: jl-shimmer 6s linear infinite;
}
.jl-lead { margin: 0; max-width: 46rem; color: var(--jl-fg-muted); font-size: 1.02rem; line-height: 1.65; }
.jl-pill { letter-spacing: .1em; padding: .18rem .6rem; color: var(--jl-fg); }
.jl-pill--muted { border-color: var(--jl-border-hover); background: var(--jl-surface); color: var(--jl-fg-muted); }
.jl-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin: .1rem 0 .4rem; }
.jl-divider {
  height: 1px; border: 0; margin: 1.25rem 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.10), transparent);
}
/* Streamlit fades captions to 60%% on top of their color; design.md wants plain foreground-muted (~6:1) */
[data-testid="stCaptionContainer"] { color: var(--jl-fg-muted); opacity: 1 !important; }
.jl-plain { white-space: pre-wrap; overflow-wrap: anywhere; font-size: .92rem; line-height: 1.65; color: var(--jl-fg); }
.jl-plain--strong { font-weight: 600; }
.jl-card-title { font-size: 1.05rem; font-weight: 600; letter-spacing: -0.01em; color: var(--jl-fg); line-height: 1.35; }
.jl-card-sub { font-weight: 400; color: var(--jl-fg-muted); }

/* ---------- Glass cards, tiles and forms ---------- */
%(glass)s {
  position: relative;
  background: linear-gradient(to bottom, rgba(255,255,255,0.10), rgba(255,255,255,0.04)) !important;
  border: 1px solid var(--jl-border) !important;
  border-radius: 16px !important;
  box-shadow: var(--jl-shadow-card);
  transition: box-shadow .3s var(--jl-ease), border-color .3s var(--jl-ease), transform .3s var(--jl-ease);
  animation: jl-fade-up .6s var(--jl-ease) both;
}
%(glass_hover)s { border-color: var(--jl-border-hover) !important; box-shadow: var(--jl-shadow-card-hover); }
/* 1px highlight along the top edge */
%(glass_before)s {
  content: ""; position: absolute; left: 12px; right: 12px; top: 0; height: 1px; pointer-events: none;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.22), transparent);
}
/* spotlight following the cursor (--jl-mx/--jl-my set by the script below) */
%(glass_after)s {
  content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none; opacity: 0;
  background: radial-gradient(300px circle at var(--jl-mx, 50%%) var(--jl-my, 0%%), rgba(94,106,210,0.15), transparent 70%%);
  transition: opacity .3s var(--jl-ease);
}
%(glass_hover_after)s { opacity: 1; }
%(tile)s:hover { transform: translateY(-4px); }
%(tile)s [data-testid="stMetricValue"] {
  background: linear-gradient(to bottom, #fff, rgba(255,255,255,0.7));
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
%(tile_delays)s
[data-testid="stMetricLabel"] p, [data-testid="stWidgetLabel"] p {
  font-family: var(--jl-mono); font-size: .75rem !important; letter-spacing: .12em; text-transform: uppercase;
  color: var(--jl-fg-muted) !important;
}
/* Checkbox, toggle and radio option labels are sentences, not field names */
[data-testid="stCheckbox"] [data-testid="stWidgetLabel"] p {
  font-family: inherit; font-size: .9rem !important; letter-spacing: normal; text-transform: none;
  color: var(--jl-fg) !important;
}

/* ---------- Buttons ---------- */
[data-testid^="stBaseButton-primary"] {
  position: relative; overflow: hidden; border: 0 !important; color: #fff !important;
  background: var(--jl-accent) !important; box-shadow: var(--jl-shadow-cta);
  transition: background-color .2s var(--jl-ease), box-shadow .2s var(--jl-ease), transform .2s var(--jl-ease);
}
[data-testid^="stBaseButton-primary"]:hover { background: var(--jl-accent-bright) !important; box-shadow: var(--jl-shadow-cta-hover); }
[data-testid^="stBaseButton-primary"]::after {
  content: ""; position: absolute; inset: 0; pointer-events: none; transform: translateX(-120%%);
  background: linear-gradient(110deg, transparent 30%%, rgba(255,255,255,0.25) 50%%, transparent 70%%);
  transition: transform .6s var(--jl-ease);
}
[data-testid^="stBaseButton-primary"]:hover::after { transform: translateX(120%%); }
[data-testid^="stBaseButton-secondary"], [data-testid^="stBaseLinkButton-secondary"], [data-testid="stDownloadButton"] button,
[data-testid^="stBaseButton-segmented_control"], [data-testid^="stBaseButton-pills"] {
  background: var(--jl-surface) !important; border: 0 !important; color: var(--jl-fg) !important;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.08), inset 0 1px 0 0 rgba(255,255,255,0.06);
  transition: background-color .2s var(--jl-ease), box-shadow .2s var(--jl-ease), transform .2s var(--jl-ease);
}
[data-testid^="stBaseButton-secondary"]:hover, [data-testid^="stBaseLinkButton-secondary"]:hover, [data-testid="stDownloadButton"] button:hover,
[data-testid^="stBaseButton-segmented_control"]:hover, [data-testid^="stBaseButton-pills"]:hover {
  background: var(--jl-surface-hover) !important;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.12), 0 0 20px rgba(94,106,210,0.12);
}
[data-testid="stBaseButton-segmented_controlActive"], [data-testid="stBaseButton-pillsActive"] {
  background: rgba(94,106,210,0.16) !important; color: var(--jl-fg) !important;
  box-shadow: inset 0 0 0 1px var(--jl-border-accent), 0 0 18px rgba(94,106,210,0.18) !important;
}
[data-testid^="stBaseButton-tertiary"] { color: var(--jl-fg-muted) !important; }
[data-testid^="stBaseButton-tertiary"]:hover { background: var(--jl-surface) !important; color: var(--jl-fg) !important; }
button:active, a[data-testid^="stBaseLinkButton"]:active { transform: scale(0.98); }
button:focus-visible, a:focus-visible, [role="tab"]:focus-visible { outline: none !important; box-shadow: var(--jl-focus-ring) !important; }

/* ---------- Inputs ---------- */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
  transition: border-color .2s var(--jl-ease), box-shadow .2s var(--jl-ease);
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within {
  border-color: var(--jl-accent) !important; box-shadow: 0 0 0 3px rgba(94,106,210,0.22);
}
[data-baseweb="tag"] { background: rgba(94,106,210,0.18) !important; border: 1px solid var(--jl-border-accent); }

/* ---------- Tabs, expanders, alerts, tables ---------- */
[data-baseweb="tab-list"] { gap: .25rem; border-bottom: 1px solid var(--jl-border); }
[data-baseweb="tab"] { color: var(--jl-fg-muted); transition: color .2s var(--jl-ease); }
[data-baseweb="tab"][aria-selected="true"] { color: var(--jl-fg); }
[data-baseweb="tab-highlight"] { background: var(--jl-accent) !important; box-shadow: 0 0 12px var(--jl-accent-glow); }
[data-baseweb="tab-border"] { display: none; }
[data-testid="stExpander"] details {
  border: 1px solid var(--jl-border) !important; border-radius: 12px !important; background: var(--jl-surface);
}
[data-testid="stAlertContainer"] {
  border-radius: 12px; border: 1px solid var(--jl-border);
  -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
}
[data-testid="stAlertContentInfo"] { color: var(--jl-fg); }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; box-shadow: 0 0 0 1px var(--jl-border), 0 2px 20px rgba(0,0,0,0.35); }
[data-testid="stProgress"] [role="progressbar"] > div > div > div { background: var(--jl-accent) !important; box-shadow: 0 0 12px var(--jl-accent-glow); }

/* hidden carrier of this stylesheet and the spotlight script */
[data-testid="stLayoutWrapper"]:has(> .st-key-jl-theme), .st-key-jl-theme { display: none !important; }

/* ---------- Motion ---------- */
@keyframes jl-float { 0%%, 100%% { transform: translateY(0) rotate(0deg); } 50%% { transform: translateY(-20px) rotate(1deg); } }
@keyframes jl-pulse { 0%%, 100%% { opacity: .55; } 50%% { opacity: 1; } }
@keyframes jl-fade-up { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: none; } }
@keyframes jl-shimmer { to { background-position: 200%% center; } }

@media (max-width: 768px) {
  .jl-title { font-size: 2.1rem; }
  .jl-lead { font-size: .95rem; }
  %(glass)s { border-radius: 14px !important; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  %(tile)s:hover { transform: none; }
}
"""

# Spotlight: sets --jl-mx / --jl-my on the card under the cursor. Parallax: the page hero fades,
# shrinks to 95% and moves down while the page scrolls past it. Installed once per browser tab.
_SPOTLIGHT_JS = """
<script>
(() => {
  if (window.__jlSpotlight) return;
  window.__jlSpotlight = true;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const selector = '%s';
  document.addEventListener("pointermove", (event) => {
    const card = event.target.closest && event.target.closest(selector);
    if (!card) return;
    const box = card.getBoundingClientRect();
    card.style.setProperty("--jl-mx", `${event.clientX - box.left}px`);
    card.style.setProperty("--jl-my", `${event.clientY - box.top}px`);
  }, { passive: true });
  document.addEventListener("scroll", (event) => {
    const scroller = event.target === document ? document.scrollingElement : event.target;
    const hero = document.querySelector(".jl-hero");
    if (!hero || !scroller || !scroller.contains(hero)) return;
    const progress = Math.min(1, scroller.scrollTop / Math.max(1, hero.offsetHeight * 2));
    hero.style.opacity = String(1 - progress);
    hero.style.transform = `translateY(${progress * 100}px) scale(${1 - progress * 0.05})`;
  }, { passive: true, capture: true });
})();
</script>
"""


def _selectors(suffix: str) -> str:
    return ", ".join(f"{part.strip()}{suffix}" for part in GLASS.split(", "))


def stylesheet() -> str:
    css_vars = "\n".join(f"  --jl-{name}: {value};" for name, value in TOKENS.items())
    tile_delays = "\n".join(f'.st-key-tile-{i} {{ animation-delay: {i * 0.08:.2f}s; }}' for i in range(1, 9))
    return _CSS % {
        "vars": css_vars,
        "noise": _NOISE,
        "glass": GLASS,
        "glass_hover": _selectors(":hover"),
        "glass_before": _selectors("::before"),
        "glass_after": _selectors("::after"),
        "glass_hover_after": _selectors(":hover::after"),
        "tile": TILE,
        "tile_delays": tile_delays,
    }


def apply() -> None:
    """Inject the stylesheet and the spotlight script; call once at the top of every run."""
    with st.container(key="jl-theme"):
        # Two calls: st.html drops <style> from mixed content, but applies a style-only body globally
        st.html(f"<style>{stylesheet()}</style>")
        st.html(_SPOTLIGHT_JS % GLASS, unsafe_allow_javascript=True)
