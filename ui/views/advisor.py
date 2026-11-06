"""AI financial advisor: verified recommendations with separated interpretation."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from core.constants import PRIORITIES
from core.governance import apply_decisions
from core.services import generate_report, read_report
from ui import data
from ui.components import (
    KPI,
    badge,
    empty_state,
    esc,
    facts_list,
    note,
    page_header,
    priority_chip,
    render_kpis,
    section,
    status_chip,
)
from ui.context import UIContext, source_badges

RISK_TONE = {"high": "critical", "medium": "warning", "low": "positive"}


def _ai_panel(ctx: UIContext) -> None:
    ai = ctx.ai
    available = bool(ai.get("available"))
    with st.container(border=True):
        section("Interpretation layer", "The AI explains verified figures; it never computes them")
        st.markdown(
            f"""
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
              {badge(("AI connected" if available else "Deterministic mode"), "violet" if available else "neutral")}
              {badge(f"provider · {esc(ai.get('provider', 'n/a'))}", "neutral")}
              {badge(f"model · {esc(ai.get('model', 'n/a'))}", "neutral")}
            </div>
            <div class="au-note">{esc(ai.get('reason', ''))}</div>
            <div class="au-note" style="margin-top:8px">
              Recommendations, scores and forecasts are produced by deterministic rules. AI output is validated
              against the supplied facts: explanations that introduce an unsupported figure are discarded, and
              rule-based text is shown instead.
            </div>
            """,
            unsafe_allow_html=True,
        )


def _recommendation_card(recommendation, decision: dict | None) -> None:
    with st.container(border=True):
        st.markdown(
            f"""
            <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap">
              <div style="max-width:70ch">
                <div style="font-weight:650;font-size:1rem;letter-spacing:-0.01em">{esc(recommendation.title)}</div>
                <div class="au-note" style="margin-top:4px">
                  {esc(recommendation.category.replace('_', ' '))} ·
                  created {esc(recommendation.created_at.strftime('%d %b %Y %H:%M'))}
                </div>
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                {priority_chip(recommendation.priority)}
                {badge(f"risk {recommendation.risk_level}", RISK_TONE.get(recommendation.risk_level, "neutral"))}
                {status_chip(recommendation.status)}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<div class="au-kpi-label" style="margin:12px 0 2px">Verified evidence</div>', unsafe_allow_html=True)
        st.markdown(facts_list(recommendation.facts), unsafe_allow_html=True)

        st.markdown(
            f"""
            <div class="au-quote">
              <b style="font-size:0.72rem;letter-spacing:0.08em;color:#94a3b8">RULE-BASED EXPLANATION</b><br>
              {esc(recommendation.explanation)}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if recommendation.ai_explanation:
            st.markdown(
                f"""
                <div class="au-quote" style="border-left-color:rgba(167,139,250,0.6)">
                  <b style="font-size:0.72rem;letter-spacing:0.08em;color:#c4b5fd">AI INTERPRETATION — NOT A FINANCIAL FACT</b><br>
                  {esc(recommendation.ai_explanation)}
                </div>
                """,
                unsafe_allow_html=True,
            )

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                f'<div class="au-kpi-label">Expected benefit</div><div class="au-note" style="margin-top:3px">{esc(recommendation.expected_benefit)}</div>',
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                f'<div class="au-kpi-label">Suggested next action</div><div class="au-note" style="margin-top:3px">{esc(recommendation.next_action)}</div>',
                unsafe_allow_html=True,
            )

        if decision:
            st.markdown(
                f'<div class="au-note" style="margin-top:10px">Decision: <b>{esc(decision.get("decision", ""))}</b> '
                f'by {esc(decision.get("reviewer", ""))} · {esc(str(decision.get("decided_at", ""))[:19].replace("T", " "))}'
                + (f" — {esc(decision.get('notes', ''))}" if decision.get("notes") else "")
                + "</div>",
                unsafe_allow_html=True,
            )

        with st.expander("Evidence payload and provenance"):
            st.json(
                {
                    "recommendation_id": recommendation.recommendation_id,
                    "priority": recommendation.priority,
                    "risk_level": recommendation.risk_level,
                    "requires_approval": recommendation.requires_approval,
                    "ai_status": recommendation.ai_status,
                    "source_agents": recommendation.source_agents,
                    "workflow_id": recommendation.workflow_id,
                    "evidence": recommendation.evidence,
                },
                expanded=False,
            )


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    transactions = data.transactions(store)
    recommendations = data.recommendations(store)
    decisions = data.decisions(store)
    recommendations = apply_decisions(recommendations, decisions)

    page_header(
        "AI Financial Advisor",
        "Personalized recommendations built from your validated data, with evidence separated from interpretation.",
        source_badges(ctx),
    )

    if not transactions:
        empty_state(
            "Recommendations need validated data",
            "Upload a statement and run the analysis. Every recommendation is derived from computed results — "
            "no generic advice is generated without evidence.",
        )
        return

    _ai_panel(ctx)

    if not recommendations:
        st.write("")
        note("No recommendations have been generated yet. Run the full analysis from the sidebar.")
        return

    counts = {p: sum(1 for r in recommendations if r.priority == p) for p in PRIORITIES}
    pending = sum(1 for r in recommendations if r.recommendation_id not in decisions)
    render_kpis(
        [
            KPI("Recommendations", f"{len(recommendations)}", "From the latest analysis"),
            KPI("Critical", f"{counts['P1']}", "Need immediate human review", "negative" if counts["P1"] else "neutral"),
            KPI("High", f"{counts['P2']}", "Budget, savings or cash flow", "warning" if counts["P2"] else "neutral"),
            KPI("Awaiting review", f"{pending}", "No decision recorded yet", "warning" if pending else "positive"),
            KPI(
                "AI interpretations",
                f"{sum(1 for r in recommendations if r.ai_generated)}",
                "Validated against supplied facts",
            ),
        ]
    )

    st.write("")
    filter_col1, filter_col2 = st.columns([1.4, 1])
    selected_priorities = filter_col1.multiselect(
        "Priority", list(PRIORITIES), default=list(PRIORITIES), key="adv_priority_filter"
    )
    status_options = ["pending_review", "approved", "edited", "held", "info_requested"]
    selected_status = filter_col2.multiselect(
        "Review status", status_options, default=status_options, key="adv_status_filter"
    )

    visible = [r for r in recommendations if r.priority in selected_priorities and r.status in selected_status]
    st.caption(f"Showing {len(visible)} of {len(recommendations)} recommendation(s).")

    for recommendation in visible:
        _recommendation_card(recommendation, decisions.get(recommendation.recommendation_id))

    st.write("")
    with st.container(border=True):
        section("Report package", "Exports the complete evidence trail for review")
        st.markdown(
            f'<div class="au-note">The report bundles validated-data statistics, the health score with component '
            f'maths, the forecast and its assumptions, anomalies, recurring payments, recommendations with review '
            f'status, alerts and limitations. Generating it is a high-risk tool action and requires your explicit '
            f'authorization.</div>',
            unsafe_allow_html=True,
        )
        st.write("")
        if st.button("Generate report package", type="primary", key="adv_generate_report"):
            prefer_mcp = bool(st.session_state.get("au_use_mcp", ctx.config.mcp.prefer_for_workflow))
            try:
                with st.spinner("Packaging the report…"):
                    info = generate_report(store, config=ctx.config, prefer_mcp=prefer_mcp)
                    content = read_report(info["report_path"], config=ctx.config)
            except Exception as exc:
                st.error(f"Report generation failed: {type(exc).__name__}: {exc}")
            else:
                st.success(
                    f"Report written to {info['report_path']} "
                    f"({info.get('bytes', 0):,} bytes · {info.get('summary', {}).get('recommendations', 0)} recommendations)."
                )
                st.download_button(
                    "Download Markdown report",
                    data=content,
                    file_name=info["report_path"].split("/")[-1],
                    mime="text/markdown",
                )

    st.write("")
    with st.container(border=True):
        section("Decision summary", "Recorded through Alerts & Approvals")
        if decisions:
            frame = pd.DataFrame(
                [
                    {
                        "Recommendation": str(item.get("recommendation_id")),
                        "Decision": item.get("decision"),
                        "Reviewer": item.get("reviewer"),
                        "Notes": (item.get("notes") or "")[:80],
                        "Decided": str(item.get("decided_at", ""))[:19].replace("T", " "),
                        "Execution scope": item.get("execution_scope", "advisory_only"),
                    }
                    for item in decisions.values()
                ]
            )
            st.dataframe(frame, hide_index=True, width="stretch")
        else:
            note("No review decisions have been recorded yet.")
        st.caption(
            "Approving a recommendation records an in-app decision only. It does not authorize any transfer, "
            "cancellation or external account change."
        )
