"""Sidebar: brand, dataset switch, navigation and analysis controls."""

from __future__ import annotations

import streamlit as st

from core.storage import dataset_choice_labels, set_active_dataset
from ui.actions import load_demo_flow, run_analysis_flow
from ui.components import esc
from ui.context import UIContext

NAV_ITEMS: list[tuple[str, str]] = [
    ("Overview", "Portfolio pulse"),
    ("Transactions", "Ingest & validate"),
    ("Budget Management", "Plans vs actuals"),
    ("Financial Intelligence", "Patterns & anomalies"),
    ("Financial Health", "Score & inputs"),
    ("Cash Flow Forecast", "Month-end outlook"),
    ("AI Financial Advisor", "Recommendations"),
    ("Alerts & Approvals", "Triage & review"),
    ("Audit & Monitoring", "Traceability"),
]


def _brand() -> None:
    st.markdown(
        """
        <div class="au-brand">
          <div class="au-brand-mark">A</div>
          <div class="au-brand-text">
            <div class="au-brand-name">AURELIA</div>
            <div class="au-brand-sub">Personal financial wellness</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _on_dataset_change(config) -> None:
    selected = st.session_state.get("au_dataset_select")
    if selected:
        set_active_dataset(str(selected), config=config)


def render_sidebar(ctx: UIContext) -> str:
    with st.sidebar:
        _brand()

        if ctx.datasets:
            labels = dataset_choice_labels(ctx.datasets)
            ids = [str(meta["id"]) for meta in ctx.datasets if str(meta["id"]) in labels]
            current = ctx.store.dataset_id if ctx.store else ids[0]
            index = ids.index(current) if current in ids else 0
            st.markdown('<div class="au-side-label">Dataset</div>', unsafe_allow_html=True)
            st.selectbox(
                "Dataset",
                ids,
                index=index,
                format_func=lambda value: labels.get(value, value),
                key="au_dataset_select",
                on_change=_on_dataset_change,
                args=(ctx.config,),
                label_visibility="collapsed",
            )
            if ctx.store is not None:
                tone = "warning" if ctx.store.is_demo else "info"
                label = "DEMONSTRATION DATA" if ctx.store.is_demo else "Uploaded statements"
                st.markdown(
                    f'<div style="margin:2px 0 10px"><span class="au-chip {tone}">{esc(label)}</span></div>',
                    unsafe_allow_html=True,
                )

        st.markdown('<div class="au-side-sep"></div>', unsafe_allow_html=True)
        page = st.radio(
            "Navigation",
            [label for label, _ in NAV_ITEMS],
            label_visibility="collapsed",
            key="au_nav",
        )

        st.markdown('<div class="au-side-sep"></div>', unsafe_allow_html=True)
        with st.expander("Analysis controls", expanded=False):
            st.checkbox(
                "AI interpretations",
                value=ctx.config.ai.enabled,
                key="au_use_ai",
                help="When enabled and a key is configured, the model explains verified figures. It never changes them.",
            )
            st.checkbox(
                "Route tools through MCP",
                value=ctx.config.mcp.prefer_for_workflow and ctx.config.mcp.enabled,
                key="au_use_mcp",
                help="Uses the STDIO MCP server. If it is unavailable the approved in-process tool layer is used.",
            )
            disabled = ctx.store is None
            if st.button("Run full analysis", type="primary", use_container_width=True, disabled=disabled):
                if ctx.store is not None:
                    run_analysis_flow(ctx.store, config=ctx.config)
            if ctx.store is not None and not ctx.store.is_demo:
                if st.button("Load demonstration data", use_container_width=True):
                    load_demo_flow(ctx.config)

        st.markdown('<div class="au-side-sep"></div>', unsafe_allow_html=True)
        ai_tone = "violet" if ctx.ai.get("available") else "neutral"
        ai_label = f"AI · {ctx.ai.get('model', 'disabled')}" if ctx.ai.get("available") else "AI · deterministic"
        layer_tone = "positive" if ctx.config.mcp.enabled else "neutral"
        layer_label = "Tools · MCP + fallback" if ctx.config.mcp.enabled else "Tools · in-process"
        st.markdown(
            f"""
            <div style="display:flex;flex-direction:column;gap:6px;">
              <span class="au-chip {ai_tone}">{esc(ai_label)}</span>
              <span class="au-chip {layer_tone}">{esc(layer_label)}</span>
              <span class="au-chip neutral">Rule v{esc(ctx.config.rule_version)}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    return page
