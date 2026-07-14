"""theme.py — CSS overrides on top of `.streamlit/config.toml`.

Apply via `theme.inject()` in app.py and each page. The injection is idempotent
within a Streamlit run and very cheap. Defines a small, consistent type scale.
"""
from __future__ import annotations

import streamlit as st

_CSS = """
<style>
:root {
    --so-accent: #4fd1c5;
    --so-warn:   #fbbf24;
    --so-error:  #f87171;
    --so-bg:     #0a0e17;
    --so-panel:  #0d1320;
    --so-text:   #e6eaf2;

    /* Type scale — single source of truth, used by every page.
       Tuned to read comfortably at 100% browser zoom. Steps:
       12 · 13 · 15 · 16 · 18 · 20 · 26. */
    --so-fs-h1:      26px;
    --so-fs-h2:      20px;
    --so-fs-h3:      16px;
    --so-fs-body:    15px;
    --so-fs-small:   13px;
    --so-fs-caption: 12px;
    --so-fs-metric-value: 18px;
}

/* Layout */
/* Main content wrapper — fill the viewport. Streamlit's default caps
   at ~1200 px which truncates long labels in multiselect chips. */
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    max-width: 100% !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
}
/* Some Streamlit versions wrap content in [data-testid="stMainBlockContainer"] */
[data-testid="stMainBlockContainer"],
[data-testid="block-container"] {
    max-width: 100% !important;
}
section[data-testid="stSidebar"] { background: #060912; }

/* Headings — collapse the gap between h1/h2/h3 sizes */
h1, .stMarkdown h1 { font-size: var(--so-fs-h1) !important; color: var(--so-accent); letter-spacing: 0.01em; margin: 0 0 6px; }
h2, .stMarkdown h2 { font-size: var(--so-fs-h2) !important; color: var(--so-accent); margin: 14px 0 6px; }
h3, .stMarkdown h3 { font-size: var(--so-fs-h3) !important; color: var(--so-accent); margin: 12px 0 4px; }

/* Expander section titles — match the h3 subheaders (size/weight/accent) so
   retractable sections and plain subheaders read as one uniform hierarchy. */
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span,
.streamlit-expanderHeader,
.streamlit-expanderHeader p {
    font-size: var(--so-fs-h3) !important;
    font-weight: 600 !important;
    color: var(--so-accent) !important;
}

/* Body text + Markdown — DO NOT match every <div> (would override the log box) */
.stMarkdown p, .stMarkdown li, p, li { font-size: var(--so-fs-body); line-height: 1.45; }
.stMarkdown small, small { font-size: var(--so-fs-small); }

/* Caption / help text — slightly larger for readability */
.stCaption,
div[data-testid="stCaptionContainer"],
div[data-testid="stCaptionContainer"] * ,
.stMarkdown small,
small {
    font-size: var(--so-fs-small) !important;
    color: #b8c5d6 !important;
    line-height: 1.5;
}

/* Help-icon tooltip text (the small "?" next to widgets) */
[data-testid="stTooltipHoverTarget"] + div,
[data-baseweb="tooltip"] {
    font-size: var(--so-fs-small) !important;
    line-height: 1.45;
}

/* Form widgets — uniform size */
.stTextInput input, .stNumberInput input, .stTextArea textarea,
.stSelectbox div[data-baseweb="select"], .stMultiSelect div[data-baseweb="select"] {
    font-size: var(--so-fs-body) !important;
}

/* Multiselect / select chips ("tag" pills) — single-line chips,
   container wraps them onto multiple rows and grows tall as needed. */
.stMultiSelect [data-baseweb="tag"],
.stSelectbox [data-baseweb="tag"],
[data-baseweb="select"] [data-baseweb="tag"] {
    background: #11364e !important;
    border: 1px solid var(--so-accent) !important;
    border-radius: 6px !important;
    max-width: none !important;
    margin: 3px 4px 3px 0 !important;
    padding: 2px 6px !important;
    flex-shrink: 0 !important;
}
.stMultiSelect [data-baseweb="tag"] *,
.stSelectbox [data-baseweb="tag"] *,
[data-baseweb="select"] [data-baseweb="tag"] * {
    color: var(--so-accent) !important;
    font-weight: 600 !important;
    font-size: var(--so-fs-body) !important;
    white-space: nowrap !important;
    overflow: visible !important;
    text-overflow: clip !important;
    max-width: none !important;
}

/* Multiselect value container — let chips wrap to multiple rows and let
   the input grow tall instead of clipping. Scoped to .stMultiSelect so
   single-pick selectboxes keep their default single-line layout (chevron
   stays vertically centered). */
.stMultiSelect [data-baseweb="select"] > div,
.stMultiSelect [data-baseweb="select"] > div > div {
    flex-wrap: wrap !important;
    overflow: visible !important;
    height: auto !important;
    min-height: 38px !important;
    max-height: none !important;
    align-items: center !important;
}
.stMultiSelect [data-baseweb="select"] [role="combobox"] {
    flex-wrap: wrap !important;
    height: auto !important;
    min-height: 38px !important;
    overflow: visible !important;
    padding-top: 3px !important;
    padding-bottom: 3px !important;
    align-items: center !important;
}
/* The "x" remove button on each tag */
.stMultiSelect [data-baseweb="tag"] [role="button"],
[data-baseweb="select"] [data-baseweb="tag"] [role="button"] {
    color: var(--so-accent) !important;
    opacity: 0.85;
}
.stMultiSelect [data-baseweb="tag"] [role="button"]:hover,
[data-baseweb="select"] [data-baseweb="tag"] [role="button"]:hover {
    color: #ffffff !important;
    opacity: 1;
}

/* Dropdown popover — keep options on one line each, but let the popover
   itself grow wider than the trigger so long labels fit. */
[data-baseweb="popover"] {
    max-width: 95vw !important;
}
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="menu"] [role="listbox"] {
    max-width: 95vw !important;
    min-width: 100% !important;
}
[data-baseweb="popover"] [role="option"],
[data-baseweb="menu"] [role="option"] {
    white-space: nowrap !important;
    overflow: visible !important;
    text-overflow: clip !important;
    font-size: var(--so-fs-body) !important;
    padding: 6px 10px !important;
}

/* Multiselect / select container — give chips room to wrap on multiple lines */
.stMultiSelect [data-baseweb="select"] > div,
.stSelectbox  [data-baseweb="select"] > div {
    background: #060912 !important;
    min-height: 36px;
    flex-wrap: wrap !important;
}

/* Dropdown menu (when typing/searching) — dark bg */
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="menu"] {
    background: #0d1320 !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
}
[data-baseweb="popover"] [role="option"],
[data-baseweb="menu"] [role="option"] {
    background: transparent !important;
    color: var(--so-text) !important;
    font-size: var(--so-fs-body) !important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="popover"] [role="option"][aria-selected="true"],
[data-baseweb="menu"] [role="option"][aria-selected="true"] {
    background: #11364e !important;
    color: var(--so-accent) !important;
}
.stSelectbox label, .stMultiSelect label, .stTextInput label, .stNumberInput label,
.stCheckbox label, .stRadio label, .stSlider label, .stTextArea label, .stFileUploader label {
    font-size: var(--so-fs-small) !important;
    color: var(--so-text);
    font-weight: 600;
}
/* Help/description text under radio captions and similar */
.stRadio label + div, .stRadio [data-baseweb="radio"] + div,
div[data-testid="stRadio"] p,
div[data-testid="stCheckbox"] p {
    font-size: var(--so-fs-small) !important;
    color: #b8c5d6;
    line-height: 1.45;
}

/* Buttons — every button uses the launch (primary) look: accent border,
   accent text on dark bg, bold. Hover brightens.
   Selectors cover every Streamlit button variant:
     - data-testid="stBaseButton-*" (Streamlit ≥1.32 unified): primary,
       secondary, primaryFormSubmit, secondaryFormSubmit
     - legacy kind="..." attribute fallback
     - .stButton, .stDownloadButton, .stFormSubmitButton, .stLinkButton wrappers
*/
button[data-testid^="stBaseButton"],
button[data-testid^="baseButton"],
.stButton button,
.stDownloadButton button,
.stFormSubmitButton button,
.stForm button[type="submit"],
button[kind^="primary"],
button[kind^="secondary"],
button[kind="primaryFormSubmit"],
button[kind="secondaryFormSubmit"],
.stLinkButton a,
.stPageLink a {
    font-size: var(--so-fs-body) !important;
    background: #11364e !important;
    color: var(--so-accent) !important;
    border: 1px solid var(--so-accent) !important;
    border-radius: 6px !important;
    padding: 3px 10px !important;
    font-weight: 700 !important;
    box-shadow: none !important;
    transition: background .15s, border-color .15s, color .15s;
    line-height: 1.2 !important;
}

/* Icon-only buttons (no text label) — shrink to icon size.
   :has() lets us detect buttons where the inner content holder is empty. */
.stButton button:has(p:empty),
.stButton button:has(div:empty):not(:has(svg + *)),
button[data-testid^="stBaseButton"]:has(p:empty),
button[data-testid^="baseButton"]:has(p:empty) {
    padding: 2px 6px !important;
    min-width: 30px !important;
    max-width: 30px !important;
    min-height: 26px !important;
    max-height: 26px !important;
    justify-content: center !important;
    box-sizing: border-box !important;
}
/* Icons inside icon-only buttons — fixed square, no trailing margin */
.stButton button:has(p:empty) [data-testid="stIconMaterial"],
button[data-testid^="stBaseButton"]:has(p:empty) [data-testid="stIconMaterial"],
button[data-testid^="baseButton"]:has(p:empty) [data-testid="stIconMaterial"] {
    margin: 0 !important;
    font-size: 18px !important;
    width: 18px !important;
    height: 18px !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}

/* Force every child (text spans, icons) to inherit accent color */
button[data-testid^="stBaseButton"] *,
button[data-testid^="baseButton"] *,
.stButton button *,
.stDownloadButton button *,
.stFormSubmitButton button *,
.stForm button[type="submit"] *,
button[kind^="primary"] *,
button[kind^="secondary"] *,
.stLinkButton a *,
.stPageLink a * {
    color: inherit !important;
}
button[data-testid^="stBaseButton"]:hover,
button[data-testid^="baseButton"]:hover,
.stButton button:hover,
.stDownloadButton button:hover,
.stFormSubmitButton button:hover,
.stForm button[type="submit"]:hover,
button[kind^="primary"]:hover,
button[kind^="secondary"]:hover,
button[kind="primaryFormSubmit"]:hover,
button[kind="secondaryFormSubmit"]:hover,
.stLinkButton a:hover,
.stPageLink a:hover {
    background: #163f5c !important;
    color: #ffffff !important;
    border-color: var(--so-accent) !important;
}
/* Active / focus — keep accent ring */
button[data-testid^="stBaseButton"]:focus,
button[data-testid^="stBaseButton"]:active,
.stButton button:focus,
.stButton button:active,
.stDownloadButton button:focus,
.stFormSubmitButton button:focus,
.stForm button[type="submit"]:focus,
button[kind^="primary"]:focus,
button[kind^="secondary"]:focus {
    background: #163f5c !important;
    color: #ffffff !important;
    border-color: var(--so-accent) !important;
    box-shadow: 0 0 0 2px rgba(79,209,197,0.30) !important;
    outline: none !important;
}
/* Disabled — muted */
button[data-testid^="stBaseButton"]:disabled,
button[data-testid^="baseButton"]:disabled,
.stButton button:disabled,
.stDownloadButton button:disabled,
.stFormSubmitButton button:disabled,
.stForm button[type="submit"]:disabled,
button[kind^="primary"]:disabled,
button[kind^="secondary"]:disabled,
button[kind="primaryFormSubmit"]:disabled,
button[kind="secondaryFormSubmit"]:disabled {
    background: #0d1320 !important;
    color: #475569 !important;
    border-color: rgba(255,255,255,0.10) !important;
    cursor: not-allowed;
}
/* Page link — same launch style */
.stPageLink a {
    background: #11364e !important;
    border: 1px solid var(--so-accent) !important;
    border-radius: 6px !important;
    padding: 4px 10px !important;
    color: var(--so-accent) !important;
    font-weight: 700 !important;
}
.stPageLink a:hover {
    background: #163f5c !important;
    color: #ffffff !important;
}

/* Material Symbol icons — inherit color from parent button so they match the
   accent text and turn white on hover. */
button [data-testid="stIconMaterial"],
.stPageLink a [data-testid="stIconMaterial"],
.stLinkButton a [data-testid="stIconMaterial"] {
    color: inherit !important;
    font-size: 18px !important;
    line-height: 1 !important;
    vertical-align: middle !important;
    margin-right: 4px !important;
    font-variation-settings: 'FILL' 0, 'wght' 600, 'GRAD' 0, 'opsz' 24 !important;
}

/* Disabled state — icon dims with the button */
.stButton button:disabled [data-testid="stIconMaterial"],
.stForm button[type="submit"]:disabled [data-testid="stIconMaterial"],
.stDownloadButton button:disabled [data-testid="stIconMaterial"],
button:disabled [data-testid="stIconMaterial"] {
    color: #475569 !important;
    opacity: 0.7;
}

/* Sidebar nav icons (st.navigation pages list) — slightly larger, accent */
section[data-testid="stSidebarNav"] [data-testid="stIconMaterial"],
section[data-testid="stSidebar"] [data-testid="stIconMaterial"] {
    font-size: 18px !important;
    vertical-align: middle !important;
    margin-right: 6px !important;
}

/* Metrics: aggressive override + compact box */
div[data-testid="stMetric"] {
    background: #0e1422;
    padding: 3px 6px !important;
    border-radius: 5px;
    min-height: 0 !important;
    line-height: 1.1 !important;
    width: 100%;
    overflow: hidden;
    white-space: nowrap;
}
div[data-testid="stMetric"] > div { padding: 0 !important; gap: 0 !important; }
div[data-testid="stMetric"] * { line-height: 1.1 !important; }
div[data-testid="stMetricLabel"],
div[data-testid="stMetricLabel"] * ,
div[data-testid="stMetricLabel"] p {
    font-size: var(--so-fs-caption) !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #94a3b8 !important;
    font-weight: 600;
    margin: 0 0 1px !important;
}
div[data-testid="stMetricValue"],
div[data-testid="stMetricValue"] *,
div[data-testid="stMetricValue"] > div {
    font-size: var(--so-fs-metric-value) !important;
    font-weight: 700 !important;
    color: var(--so-text) !important;
    margin: 0 !important;
    padding: 0 !important;
}
div[data-testid="stMetricDelta"],
div[data-testid="stMetricDelta"] * {
    font-size: var(--so-fs-small) !important;
}

/* Columns gap (reduces visual whitespace between metric boxes) */
div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    padding-left: 3px !important;
    padding-right: 3px !important;
    min-width: 0 !important;
}

/* Form inputs: cap width to avoid stretching across the page */
.stTextInput, .stNumberInput, .stSelectbox, .stMultiSelect, .stTextArea {
    max-width: 320px;
}
.stTextInput > div, .stNumberInput > div, .stSelectbox > div, .stMultiSelect > div {
    max-width: 320px;
}

/* Container bordered: tighter padding */
div[data-testid="stVerticalBlockBorderWrapper"] > div { padding: 8px 10px !important; }

/* Tables / DataFrames */
.stDataFrame, .stDataFrame * { font-size: var(--so-fs-small) !important; }

/* Code & monospace — generic. Do NOT force size on every <pre> (the log box
   uses its own slider-controlled size via the .so-log class). */
.stCode, code { font-size: var(--so-fs-small) !important; }

/* Log box — one <div> per line; CSS forces each entry to its own row. */
.so-log {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #060912;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 6px;
    padding: 10px 12px;
    overflow: auto;
    max-height: 540px;
    line-height: 1.35;
    color: #d7dde8;
    white-space: pre;
    word-break: normal;
}
/* Children inherit font-size from .so-log (don't hard-set it). */
.so-log > div {
    display: block;
    white-space: pre;
    min-height: 1em;
    font-size: inherit !important;
    line-height: inherit !important;
}

/* Tabs */
.stTabs [data-baseweb="tab"] { font-size: var(--so-fs-body) !important; padding: 8px 14px; }

/* Custom pills */
.so-pill { display:inline-block; padding:2px 10px; border-radius:999px;
           font-size: var(--so-fs-caption); font-weight:700;
           text-transform:uppercase; letter-spacing:.04em; }
.so-pill-ok    { background:#14532d; color:#86efac; border:1px solid #166534; }
.so-pill-warn  { background:#422006; color:#fcd34d; border:1px solid #92400e; }
.so-pill-error { background:#7f1d1d; color:#fca5a5; border:1px solid #b91c1c; }

.so-card { background: var(--so-panel); border: 1px solid rgba(255,255,255,0.08);
           border-radius: 8px; padding: 14px 18px; margin: 10px 0; }
.so-mono { font-family: ui-monospace, monospace; font-size: var(--so-fs-small); color: #94a3b8; }
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def pill(text: str, kind: str = "ok") -> str:
    return f'<span class="so-pill so-pill-{kind}">{text}</span>'
