"""Aurelia — Autonomous Personal Financial Wellness.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import traceback

import streamlit as st

from core.config import get_config
from core.observability import get_logger
from ui.actions import create_dataset_flow, load_demo_flow
from ui.context import UIContext, build_context, render_flash
from ui.sidebar import render_sidebar
from ui.theme import inject_theme
from ui.views import PAGES

logger = get_logger("aurelia.ui")


def _onboarding(ctx: UIContext) -> None:
    st.markdown(
        """
        <div class="au-onboard">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:18px">
            <div class="au-brand-mark" style="width:40px;height:40px;font-size:18px">A</div>
            <div>
              <div class="au-brand-name" style="font-size:0.9rem">AURELIA</div>
              <div class="au-brand-sub">Autonomous personal financial wellness</div>
            </div>
          </div>
          <div class="au-onboard-title">Your finances, explained with evidence.</div>
          <div class="au-onboard-sub">
            Upload a statement and Aurelia validates every row, categorizes spending, detects anomalies and
            recurring payments, scores financial health, forecasts the month, and prepares recommendations for your
            review. Nothing is ever executed automatically.
          </div>
          <div class="au-feature-grid">
            <div class="au-feature"><div class="au-feature-title">Validated ingestion</div><div class="au-feature-body">CSV, Excel and PDF statements parsed with quarantine for malformed rows.</div></div>
            <div class="au-feature"><div class="au-feature-title">Explainable scoring</div><div class="au-feature-body">A 0–100 health score from savings, budget adherence and emergency coverage.</div></div>
            <div class="au-feature"><div class="au-feature-title">Transparent forecasts</div><div class="au-feature-body">Month-end projections that separate actual values from estimates.</div></div>
            <div class="au-feature"><div class="au-feature-title">Human review gate</div><div class="au-feature-body">Priorities, SLA deadlines and recorded decisions for every recommendation.</div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    st.write("")
    left, right = st.columns(2, gap="large")

    with left:
        with st.container(border=True):
            st.markdown(
                '<div class="au-section-title">Start with your own statements</div>'
                '<div class="au-note" style="margin:6px 0 14px">Create a dataset, then upload a bank or card '
                "statement in Transactions. Uploaded data stays isolated from demonstration data.</div>",
                unsafe_allow_html=True,
            )
            with st.form("onboarding_create"):
                name = st.text_input("Dataset name", value="My finances", key="onb_name")
                submitted = st.form_submit_button("Create dataset", type="primary")
            if submitted:
                create_dataset_flow(name, ctx.config)

    with right:
        with st.container(border=True):
            st.markdown(
                '<div class="au-section-title">Explore with demonstration data</div>'
                '<div class="au-note" style="margin:6px 0 14px">A deterministic synthetic dataset with recurring '
                "payments, a large purchase, duplicate charges and malformed rows — clearly labelled as "
                "DEMONSTRATION DATA.</div>",
                unsafe_allow_html=True,
            )
            if st.button("Load demonstration data", type="primary", use_container_width=True, key="onboard_demo"):
                load_demo_flow(ctx.config)

    st.write("")
    st.caption(
        "Aurelia provides analysis and education, not regulated financial advice. All figures are computed from the "
        "data you provide; no transaction, subscription or account is modified by the application."
    )


def main() -> None:
    config = get_config()
    st.set_page_config(
        page_title="Aurelia · Financial Wellness",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_theme()

    ctx = build_context()
    page = render_sidebar(ctx)
    render_flash()

    if ctx.store is None:
        _onboarding(ctx)
        return

    renderer = PAGES.get(page)
    if renderer is None:
        st.error("That page is not available.")
        return

    try:
        renderer(ctx)
    except Exception as exc:  # keep the shell usable, surface the failure
        logger.exception("Page %s failed", page)
        st.error(f"This page hit an unexpected error: {type(exc).__name__}: {exc}")
        with st.expander("Technical detail"):
            st.code(traceback.format_exc())


main()
