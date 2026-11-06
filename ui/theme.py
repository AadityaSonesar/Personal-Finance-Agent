"""Dark fintech theme: typography, surfaces, cards and control styling."""

from __future__ import annotations

import streamlit as st

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
  --au-bg: #060a12;
  --au-surface: #0d1524;
  --au-surface-2: #111c2e;
  --au-border: rgba(148, 163, 184, 0.14);
  --au-border-strong: rgba(148, 163, 184, 0.24);
  --au-text: #e6ebf4;
  --au-muted: #93a3b8;
  --au-emerald: #10b981;
  --au-emerald-soft: rgba(16, 185, 129, 0.12);
  --au-red: #ef4444;
  --au-red-soft: rgba(239, 68, 68, 0.12);
  --au-amber: #f59e0b;
  --au-amber-soft: rgba(245, 158, 11, 0.12);
  --au-sky: #38bdf8;
  --au-sky-soft: rgba(56, 189, 248, 0.12);
  --au-violet: #a78bfa;
}

html, body, [class*="st-"], .stApp, button, input, textarea, select {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.stApp {
  background:
    radial-gradient(1100px 520px at 12% -8%, rgba(16, 185, 129, 0.10), transparent 60%),
    radial-gradient(900px 480px at 100% 0%, rgba(56, 189, 248, 0.07), transparent 55%),
    var(--au-bg);
  color: var(--au-text);
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { right: 1rem; }
#MainMenu, footer { visibility: hidden; }

.block-container {
  padding-top: 1.8rem;
  padding-bottom: 4rem;
  max-width: 1440px;
}

h1, h2, h3, h4 { letter-spacing: -0.02em; color: var(--au-text); }
p, span, label, li { color: var(--au-text); }
a { color: var(--au-sky); }

/* ---------------- sidebar ---------------- */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #080e19 0%, #070c15 100%);
  border-right: 1px solid var(--au-border);
}
[data-testid="stSidebar"] > div { padding-top: 1.2rem; }
[data-testid="stSidebar"] .block-container { padding-top: 0; }

.au-brand { display: flex; align-items: center; gap: 10px; padding: 0 4px 14px; }
.au-brand-mark {
  width: 34px; height: 34px; border-radius: 10px;
  background: linear-gradient(135deg, #10b981, #059669);
  display: flex; align-items: center; justify-content: center;
  color: #04110c; font-weight: 800; font-size: 16px;
  box-shadow: 0 6px 18px rgba(16, 185, 129, 0.35);
}
.au-brand-text { line-height: 1.1; }
.au-brand-name { font-weight: 700; letter-spacing: 0.16em; font-size: 0.82rem; color: var(--au-text); }
.au-brand-sub { font-size: 0.68rem; color: var(--au-muted); letter-spacing: 0.04em; }

[data-testid="stSidebar"] div[role="radiogroup"] { gap: 2px; }
[data-testid="stSidebar"] div[role="radiogroup"] > label {
  padding: 8px 11px;
  border-radius: 9px;
  width: 100%;
  transition: background 120ms ease;
  border: 1px solid transparent;
}
[data-testid="stSidebar"] div[role="radiogroup"] > label:hover { background: rgba(148, 163, 184, 0.08); }
[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child { display: none; }
[data-testid="stSidebar"] div[role="radiogroup"] > label p { font-size: 0.87rem; font-weight: 500; color: #c6d2e2; }
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
  background: linear-gradient(90deg, rgba(16, 185, 129, 0.16), rgba(16, 185, 129, 0.02));
  border: 1px solid rgba(16, 185, 129, 0.28);
}
[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p { color: #ecfdf5; font-weight: 600; }

.au-side-sep { height: 1px; background: var(--au-border); margin: 14px 0 12px; }
.au-side-label {
  font-size: 0.66rem; letter-spacing: 0.14em; text-transform: uppercase;
  color: #64748b; margin: 4px 0 6px;
}

/* ---------------- typography blocks ---------------- */
.au-page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.au-page-title { font-size: 1.65rem; font-weight: 700; letter-spacing: -0.03em; margin: 0; }
.au-page-sub { color: var(--au-muted); font-size: 0.88rem; margin-top: 4px; max-width: 70ch; }
.au-page-badges { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }

.au-section { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 4px 0 10px; }
.au-section-title { font-size: 0.98rem; font-weight: 600; letter-spacing: -0.01em; }
.au-section-caption { color: var(--au-muted); font-size: 0.78rem; }

/* ---------------- cards ---------------- */
.au-card {
  background: linear-gradient(180deg, rgba(18, 28, 46, 0.96), rgba(11, 18, 31, 0.96));
  border: 1px solid var(--au-border);
  border-radius: 14px;
  padding: 16px 18px;
  box-shadow: 0 14px 36px rgba(2, 6, 16, 0.45);
  height: 100%;
}
.au-card.tight { padding: 14px 16px; }
.au-card.hero {
  background:
    radial-gradient(400px 160px at 100% 0%, rgba(16, 185, 129, 0.18), transparent 70%),
    linear-gradient(180deg, rgba(20, 32, 51, 0.98), rgba(10, 17, 29, 0.98));
}

.au-kpi { display: flex; flex-direction: column; gap: 6px; min-height: 96px; }
.au-kpi-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.au-kpi-label { font-size: 0.7rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--au-muted); font-weight: 600; }
.au-kpi-value { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1; }
.au-kpi-sub { font-size: 0.76rem; color: var(--au-muted); }
.au-kpi-value.positive { color: #34d399; }
.au-kpi-value.negative { color: #f87171; }
.au-kpi-value.warning { color: #fbbf24; }
.au-kpi-value.neutral { color: var(--au-text); }

/* ---------------- chips ---------------- */
.au-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 9px; border-radius: 999px;
  font-size: 0.68rem; font-weight: 600; letter-spacing: 0.03em;
  border: 1px solid transparent; white-space: nowrap;
}
.au-chip.neutral { background: rgba(148, 163, 184, 0.12); color: #cbd5e1; border-color: rgba(148, 163, 184, 0.2); }
.au-chip.positive { background: rgba(16, 185, 129, 0.14); color: #34d399; border-color: rgba(16, 185, 129, 0.3); }
.au-chip.warning { background: rgba(245, 158, 11, 0.14); color: #fbbf24; border-color: rgba(245, 158, 11, 0.3); }
.au-chip.critical { background: rgba(239, 68, 68, 0.15); color: #f87171; border-color: rgba(239, 68, 68, 0.32); }
.au-chip.info { background: rgba(56, 189, 248, 0.14); color: #7dd3fc; border-color: rgba(56, 189, 248, 0.3); }
.au-chip.violet { background: rgba(167, 139, 250, 0.14); color: #c4b5fd; border-color: rgba(167, 139, 250, 0.3); }

.au-chip.p1 { background: rgba(239, 68, 68, 0.16); color: #fca5a5; border-color: rgba(239, 68, 68, 0.36); }
.au-chip.p2 { background: rgba(245, 158, 11, 0.16); color: #fcd34d; border-color: rgba(245, 158, 11, 0.34); }
.au-chip.p3 { background: rgba(56, 189, 248, 0.14); color: #7dd3fc; border-color: rgba(56, 189, 248, 0.3); }
.au-chip.p4 { background: rgba(148, 163, 184, 0.14); color: #cbd5e1; border-color: rgba(148, 163, 184, 0.26); }

/* ---------------- lists ---------------- */
.au-list { list-style: none; padding: 0; margin: 0; }
.au-list li {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 9px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  font-size: 0.86rem; line-height: 1.45;
}
.au-list li:last-child { border-bottom: none; }
.au-list .au-dot { width: 6px; height: 6px; border-radius: 50%; margin-top: 7px; flex: 0 0 6px; background: var(--au-emerald); }

.au-facts { margin: 8px 0 0; padding-left: 18px; }
.au-facts li { font-size: 0.85rem; color: #cbd5e1; margin-bottom: 5px; line-height: 1.5; }

.au-note { font-size: 0.78rem; color: var(--au-muted); line-height: 1.5; }
.au-quote {
  border-left: 2px solid rgba(56, 189, 248, 0.45);
  padding: 2px 0 2px 12px; margin: 10px 0;
  color: #cbd5e1; font-size: 0.86rem; line-height: 1.55;
}
.au-divider { height: 1px; background: var(--au-border); margin: 18px 0; }

.au-meter { height: 6px; border-radius: 999px; background: rgba(148, 163, 184, 0.14); overflow: hidden; margin-top: 10px; }
.au-meter > span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #10b981, #34d399); }
.au-meter.warning > span { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
.au-meter.critical > span { background: linear-gradient(90deg, #ef4444, #f87171); }

/* ---------------- streamlit widgets ---------------- */
[data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: 14px;
  border-color: var(--au-border);
  background: linear-gradient(180deg, rgba(16, 25, 41, 0.7), rgba(9, 15, 26, 0.7));
}

.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--au-border); }
.stTabs [data-baseweb="tab"] {
  height: 38px; padding: 0 14px; border-radius: 8px 8px 0 0;
  color: var(--au-muted); font-size: 0.84rem; font-weight: 500;
}
.stTabs [aria-selected="true"] {
  color: #ecfdf5 !important;
  background: rgba(16, 185, 129, 0.08);
  border-bottom: 2px solid var(--au-emerald);
}

.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  border-radius: 9px;
  border: 1px solid var(--au-border-strong);
  background: rgba(17, 28, 46, 0.9);
  color: var(--au-text);
  font-weight: 550;
  font-size: 0.84rem;
  padding: 0.42rem 0.9rem;
  transition: all 120ms ease;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: rgba(16, 185, 129, 0.45);
  color: #ecfdf5;
  background: rgba(16, 185, 129, 0.10);
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: linear-gradient(135deg, #10b981, #059669);
  border: 1px solid rgba(16, 185, 129, 0.7);
  color: #03140d;
  font-weight: 700;
}
.stButton > button[kind="primary"]:hover { background: linear-gradient(135deg, #34d399, #10b981); color: #03140d; }

div[data-baseweb="input"] > div, div[data-baseweb="select"] > div, .stTextArea textarea, .stNumberInput input {
  background: rgba(9, 15, 26, 0.9) !important;
  border-color: var(--au-border-strong) !important;
  border-radius: 9px !important;
}
.stTextInput input, .stNumberInput input, .stTextArea textarea { color: var(--au-text) !important; }
div[data-baseweb="select"] span { color: var(--au-text); }

[data-testid="stMetricValue"] { font-size: 1.4rem; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid var(--au-border); }
[data-testid="stDataFrame"] * { font-size: 0.8rem; }
[data-testid="stExpander"] details {
  border: 1px solid var(--au-border) !important;
  border-radius: 12px !important;
  background: rgba(12, 19, 32, 0.7) !important;
}
[data-testid="stExpander"] summary { font-size: 0.85rem; color: #cbd5e1; }

.stAlert { border-radius: 12px; border: 1px solid var(--au-border); }
[data-testid="stSidebar"] .stAlert p { font-size: 0.78rem; }

hr { border-color: var(--au-border); }

.au-onboard { max-width: 900px; margin: 6vh auto 0; }
.au-onboard-title { font-size: 2.1rem; font-weight: 700; letter-spacing: -0.04em; margin-bottom: 8px; }
.au-onboard-sub { color: var(--au-muted); font-size: 0.95rem; line-height: 1.6; max-width: 62ch; }
.au-feature-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-top: 20px; }
.au-feature { border: 1px solid var(--au-border); border-radius: 12px; padding: 14px 16px; background: rgba(13, 21, 36, 0.7); }
.au-feature-title { font-size: 0.84rem; font-weight: 600; margin-bottom: 4px; }
.au-feature-body { font-size: 0.76rem; color: var(--au-muted); line-height: 1.5; }

@media (max-width: 900px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; }
  .au-page-head { flex-direction: column; }
  .au-page-badges { justify-content: flex-start; }
}
"""


def inject_theme() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
