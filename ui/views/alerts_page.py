"""Alerts and approvals: triage queue, SLA tracking and human review."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from core.alerts import sla_state
from core.constants import PRIORITIES, PRIORITY_LABELS, REVIEW_DECISIONS
from core.governance import (
    EXECUTION_POLICY,
    apply_decisions,
    decision_requirements,
    review_summary,
)
from core.services import acknowledge_alert, resolve_alert, review_recommendation
from ui import data
from ui.components import (
    KPI,
    alert_status_chip,
    badge,
    esc,
    facts_list,
    note,
    page_header,
    priority_chip,
    render_kpis,
    section,
    sla_chip,
    status_chip,
)
from ui.context import UIContext, flash, source_badges


def _sla_text(alert) -> str:
    state = sla_state(alert)
    remaining = state.get("remaining_minutes")
    if state["state"] == "met":
        return "Resolved within SLA"
    if remaining is None:
        return "Deadline unavailable"
    if remaining < 0:
        return f"Overdue by {abs(remaining):.0f} min"
    if remaining < 120:
        return f"{remaining:.0f} min remaining"
    return f"{remaining / 60:.1f} h remaining"


def _queue_tab(store, reviewer: str) -> None:
    alerts = data.alerts(store)
    recommendations = {r.recommendation_id: r for r in data.recommendations(store)}

    if not alerts:
        note("No alerts yet. Alerts are created from recommendations and anomaly signals during analysis.")
        return

    show_resolved = st.checkbox("Include resolved alerts", value=False, key="alerts_show_resolved")
    priorities = st.multiselect("Priority", list(PRIORITIES), default=list(PRIORITIES), key="alerts_priority_filter")

    visible = [
        alert
        for alert in alerts
        if alert.priority in priorities and (show_resolved or not alert.resolved_at)
    ]
    visible.sort(key=lambda a: (a.resolved_at is not None, PRIORITIES.index(a.priority), a.deadline))
    st.caption(f"{len(visible)} alert(s) in view · newest deadline first within each priority.")

    for alert in visible:
        state = sla_state(alert)
        with st.container(border=True):
            st.markdown(
                f"""
                <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
                  <div style="max-width:68ch">
                    <div style="font-weight:650;font-size:0.97rem">{esc(alert.subject)}</div>
                    <div class="au-note" style="margin-top:3px">
                      {esc(alert.intent.replace('_', ' '))} · {esc(alert.source)} ·
                      created {esc(alert.created_at.strftime('%d %b %Y %H:%M'))} ·
                      deadline {esc(alert.deadline.strftime('%d %b %Y %H:%M'))}
                    </div>
                  </div>
                  <div style="display:flex;gap:6px;flex-wrap:wrap">
                    {priority_chip(alert.priority)}
                    {alert_status_chip(alert)}
                    {sla_chip(alert)}
                  </div>
                </div>
                <div class="au-note" style="margin-top:10px">
                  <b>{esc(_sla_text(alert))}</b> · SLA {alert.sla_minutes} min ·
                  complexity {esc(alert.complexity)} · confidence {alert.confidence:.0%} ·
                  human review {'required' if alert.requires_human else 'optional'}
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(facts_list(alert.evidence), unsafe_allow_html=True)

            if alert.recommendation_id:
                with st.expander("Linked recommendation"):
                    recommendation = recommendations.get(alert.recommendation_id)
                    if recommendation:
                        st.markdown(
                            f"{priority_chip(recommendation.priority)} {status_chip(recommendation.status)}",
                            unsafe_allow_html=True,
                        )
                        st.markdown(facts_list(recommendation.facts), unsafe_allow_html=True)
                        st.caption(recommendation.explanation)
                        st.markdown(
                            f'<div class="au-note"><b>Next action:</b> {esc(recommendation.next_action)}</div>',
                            unsafe_allow_html=True,
                        )
                        st.caption("Record the decision in the Recommendation review tab.")
                    else:
                        note("The linked recommendation is no longer present in this dataset.")

            with st.expander("Notification delivery"):
                if alert.notifications:
                    for outcome in alert.notifications:
                        st.markdown(
                            f'<div class="au-note">Channel <b>{esc(outcome.get("channel"))}</b> · '
                            f'{esc("confirmed" if outcome.get("delivered") else "not confirmed")} · '
                            f'{esc(outcome.get("attempt_count", len(outcome.get("attempts", []))))} attempt(s) · '
                            f'{esc(outcome.get("detail", ""))}</div>',
                            unsafe_allow_html=True,
                        )
                        for attempt in outcome.get("attempts", []):
                            st.caption(
                                f"attempt {attempt.get('attempt')}: {'ok' if attempt.get('ok') else 'failed'} · "
                                f"{attempt.get('latency_ms')} ms · {attempt.get('detail')}"
                            )
                else:
                    note("No delivery attempt recorded for this alert.")
                if not alert.notification_delivered:
                    st.warning("Delivery is unconfirmed. The product never claims an unconfirmed notification was sent.")

            if not alert.resolved_at:
                col1, col2 = st.columns([1, 2])
                if col1.button("Acknowledge", key=f"ack_{alert.alert_id}", use_container_width=True):
                    acknowledge_alert(store, alert.alert_id, reviewer=reviewer or "Reviewer")
                    flash("info", "Alert acknowledged. The SLA clock continues until it is resolved.")
                    st.rerun()
                with col2.form(f"resolve_form_{alert.alert_id}"):
                    resolution = st.text_input(
                        "Resolution note",
                        placeholder="What did you check, and what was the outcome?",
                        key=f"res_note_{alert.alert_id}",
                    )
                    if st.form_submit_button("Resolve alert"):
                        if not (resolution or "").strip():
                            st.error("A resolution note is required to close an alert.")
                        else:
                            resolve_alert(
                                store,
                                alert.alert_id,
                                resolution=resolution.strip(),
                                reviewer=reviewer or "Reviewer",
                            )
                            flash("success", "Alert resolved and recorded in the audit trail.")
                            st.rerun()


def _review_tab(store, reviewer: str) -> None:
    recommendations = apply_decisions(data.recommendations(store), data.decisions(store))
    if not recommendations:
        note("No recommendations to review. Run the analysis first.")
        return

    pending = [r for r in recommendations if r.status == "pending_review"]
    decided = [r for r in recommendations if r.status != "pending_review"]
    st.caption(
        f"{len(pending)} awaiting a decision · {len(decided)} with a recorded decision. "
        "Every decision is appended to the audit trail."
    )

    for recommendation in pending:
        with st.expander(f"{recommendation.priority} · {recommendation.title}", expanded=False):
            st.markdown(facts_list(recommendation.facts), unsafe_allow_html=True)
            st.markdown(
                f'<div class="au-quote"><b>Proposed action:</b> {esc(recommendation.next_action)}<br>'
                f'<b>Expected benefit:</b> {esc(recommendation.expected_benefit)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 12px">'
                f'{priority_chip(recommendation.priority)}'
                f'{badge("risk " + recommendation.risk_level, "critical" if recommendation.risk_level == "high" else "warning")}'
                f'{badge("AI explanation" if recommendation.ai_generated else "Rule-based explanation", "violet" if recommendation.ai_generated else "neutral")}'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.caption(recommendation.explanation)
            if recommendation.ai_explanation:
                st.markdown(
                    f'<div class="au-quote" style="border-left-color:rgba(167,139,250,0.6)">'
                    f'<b style="font-size:0.72rem;color:#c4b5fd">AI INTERPRETATION</b><br>'
                    f'{esc(recommendation.ai_explanation)}</div>',
                    unsafe_allow_html=True,
                )

            with st.form(f"decision_form_{recommendation.recommendation_id}"):
                decision = st.selectbox(
                    "Decision",
                    list(REVIEW_DECISIONS),
                    key=f"dec_{recommendation.recommendation_id}",
                    help="Approve, edit and approve, hold, or request more information.",
                )
                requirements = decision_requirements(decision)
                notes = st.text_area(
                    "Reviewer notes",
                    key=f"notes_{recommendation.recommendation_id}",
                    placeholder="Required for edit, hold and information requests.",
                )
                edited_action = st.text_area(
                    "Revised action (required for EDIT)",
                    key=f"edit_{recommendation.recommendation_id}",
                    placeholder="Describe the modified action you are approving.",
                )
                st.caption(
                    f"Selected: {requirements['label']} — {requirements['description']}."
                    + (" Notes required." if requirements["requires_notes"] else "")
                )
                submitted = st.form_submit_button("Record decision", type="primary")
            if submitted:
                try:
                    review_recommendation(
                        store,
                        recommendation,
                        decision=decision,
                        reviewer=reviewer or "Reviewer",
                        notes=notes,
                        edited_action=edited_action,
                        config=None,
                    )
                except Exception as exc:
                    st.error(f"Decision rejected: {exc}")
                else:
                    flash(
                        "success",
                        f"{requirements['label']} recorded for “{recommendation.title}”. Advisory only — no funds moved.",
                    )
                    st.rerun()

    if decided:
        with st.container(border=True):
            section("Recorded decisions", "Latest decision per recommendation")
            for recommendation in decided:
                decision = data.decisions(store).get(recommendation.recommendation_id, {})
                st.markdown(
                    f"""
                    <div style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.08)">
                      <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
                        <div style="font-weight:600;font-size:0.87rem">{esc(recommendation.title)}</div>
                        <div style="display:flex;gap:6px">{priority_chip(recommendation.priority)}{status_chip(recommendation.status)}</div>
                      </div>
                      <div class="au-note" style="margin-top:4px">
                        {esc(decision.get('reviewer', '—'))} · {esc(str(decision.get('decided_at', ''))[:19].replace('T', ' '))}
                        {("— " + esc(decision.get('notes', ''))) if decision.get('notes') else ""}
                      </div>
                      {f"<div class='au-note' style='margin-top:4px'>Revised action: {esc(decision.get('edited_action', ''))}</div>" if decision.get('edited_action') else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


def _history_tab(store) -> None:
    approvals = store.load_approvals()
    if not approvals:
        note("No reviewer decisions recorded yet.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Recommendation": item.get("recommendation_id"),
                    "Decision": item.get("decision"),
                    "Reviewer": item.get("reviewer"),
                    "Notes": item.get("notes", ""),
                    "Revised action": item.get("edited_action", ""),
                    "Decided": str(item.get("decided_at", ""))[:19].replace("T", " "),
                    "Execution scope": item.get("execution_scope", "advisory_only"),
                    "External authorization": "yes" if item.get("external_authorization") else "no",
                }
                for item in approvals[::-1]
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    alerts = data.alerts(store)
    recommendations = apply_decisions(data.recommendations(store), data.decisions(store))
    summary = review_summary(recommendations, data.decisions(store))
    open_alerts = [alert for alert in alerts if not alert.resolved_at]
    breached = sum(1 for alert in open_alerts if sla_state(alert)["state"] == "breached")

    page_header(
        "Alerts & Approvals",
        "Priority triage with SLA deadlines, plus the human review gate for every consequential recommendation.",
        source_badges(ctx),
    )

    render_kpis(
        [
            KPI("Open alerts", f"{len(open_alerts)}", f"{sum(1 for a in open_alerts if a.priority == 'P1')} critical", "negative" if any(a.priority == "P1" for a in open_alerts) else "neutral"),
            KPI("SLA breached", f"{breached}", "Open past their deadline", "negative" if breached else "positive"),
            KPI("Awaiting review", f"{summary['pending']}", "Recommendations without a decision", "warning" if summary["pending"] else "positive"),
            KPI("P1 awaiting", f"{summary['p1_pending']}", "Critical items needing a human", "negative" if summary["p1_pending"] else "neutral"),
            KPI(
                "Review completion",
                f"{summary['completion_rate'] * 100:.0f}%" if summary["completion_rate"] is not None else "—",
                f"{summary['decided']} of {summary['total']} decided",
            ),
        ]
    )

    st.write("")
    st.markdown(
        f'<div class="au-card tight"><div class="au-kpi-label">Governance policy</div>'
        f'<div class="au-note" style="margin-top:6px">{esc(EXECUTION_POLICY)}</div></div>',
        unsafe_allow_html=True,
    )

    st.write("")
    reviewer = st.text_input(
        "Reviewer name",
        value=st.session_state.get("au_reviewer", ""),
        key="au_reviewer",
        placeholder="Your name, recorded with each decision",
        help="Recorded in the approval history and audit trail.",
    )

    tabs = st.tabs(["Priority queue", "Recommendation review", "Decision history"])
    with tabs[0]:
        _queue_tab(store, reviewer)
    with tabs[1]:
        _review_tab(store, reviewer)
    with tabs[2]:
        _history_tab(store)
