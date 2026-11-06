"""Financial health: transparent score, components and profile inputs."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.formatting import money, percent
from core.health import calculate_health
from core.models import UpcomingBill
from core.storage import FinancialProfile
from ui import data
from ui.actions import run_analysis_flow
from ui.components import (
    KPI,
    badge,
    esc,
    health_gauge,
    health_history_line,
    health_tone,
    meter,
    note,
    page_header,
    render_chart,
    render_kpis,
    section,
)
from ui.context import UIContext, flash, source_badges


def _parse_optional(raw: str, label: str) -> float | None:
    text = (raw or "").strip().replace(",", "").replace("₹", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number (or left blank).") from exc
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


def _profile_editor(store, ctx: UIContext, profile: FinancialProfile) -> None:
    with st.container(border=True):
        section(
            "Your inputs",
            "Missing values stay missing — they are never assumed to be zero",
        )
        st.caption(
            "Income, emergency fund and current balance cannot be derived from a bank statement alone. "
            "Leave a field blank and the dependent score component is reported as unavailable."
        )
        with st.form("profile_form"):
            col1, col2, col3 = st.columns(3)
            name = col1.text_input("Display name", value=profile.display_name, key="pf_name")
            income = col2.text_input(
                "Monthly income",
                value=f"{profile.monthly_income:.0f}" if profile.monthly_income is not None else "",
                placeholder="Not provided",
                key="pf_income",
                help="Used for savings rate and inflow projections. Observed income transactions are used only when this is blank.",
            )
            emergency = col3.text_input(
                "Emergency fund",
                value=f"{profile.emergency_fund:.0f}" if profile.emergency_fund is not None else "",
                placeholder="Not provided",
                key="pf_emergency",
            )
            balance = col1.text_input(
                "Current account balance",
                value=f"{profile.current_balance:.0f}" if profile.current_balance is not None else "",
                placeholder="Not provided",
                key="pf_balance",
                help="Required for the month-end balance prediction.",
            )

            bills_frame = pd.DataFrame(
                [{"Label": bill.label, "Amount": bill.amount} for bill in profile.upcoming_bills]
                or [],
                columns=["Label", "Amount"],
            )
            st.markdown('<div class="au-kpi-label" style="margin-top:8px">Upcoming bills</div>', unsafe_allow_html=True)
            edited_bills = st.data_editor(
                bills_frame,
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                key="pf_bills",
                column_config={
                    "Label": st.column_config.TextColumn(width="medium"),
                    "Amount": st.column_config.NumberColumn(min_value=0, step=500, format="%.0f"),
                },
            )
            submitted = st.form_submit_button("Save & recompute", type="primary")

        if submitted:
            try:
                bills = []
                for _, row in edited_bills.iterrows():
                    label = str(row.get("Label") or "").strip()
                    amount = row.get("Amount")
                    if not label and (amount is None or pd.isna(amount)):
                        continue
                    if not label:
                        raise ValueError("Every upcoming bill needs a label.")
                    value = float(amount or 0)
                    if value <= 0:
                        raise ValueError(f"Upcoming bill “{label}” needs an amount above zero.")
                    bills.append(UpcomingBill(label=label[:80], amount=value))
                updated = FinancialProfile(
                    display_name=(name or "").strip()[:80],
                    monthly_income=_parse_optional(income, "Monthly income"),
                    emergency_fund=_parse_optional(emergency, "Emergency fund"),
                    current_balance=_parse_optional(balance, "Current account balance"),
                    upcoming_bills=bills,
                )
            except ValueError as exc:
                st.error(str(exc))
                return

            store.save_profile(updated)
            flash("success", "Financial inputs saved. Recomputing the health score and forecast…")
            run_analysis_flow(store, config=ctx.config, caption="Recomputing with your updated inputs…")


def _component_card(component, config) -> str:
    if component.available:
        ratio = component.normalized or 0.0
        tone = "positive" if ratio >= 0.7 else ("warning" if ratio >= 0.4 else "critical")
        chip = badge(f"{component.points:.1f} / {component.max_points:.0f} pts", tone)
        body = f'<div class="au-note" style="margin-top:8px">{esc(component.detail)}</div>'
        improvement = (
            f'<div class="au-note" style="margin-top:6px;color:#a7f3d0">{esc(component.improvement)}</div>'
            if component.improvement
            else ""
        )
        return f"""
        <div class="au-card">
          <div class="au-kpi-top"><span class="au-kpi-label">{esc(component.label)}</span>{chip}</div>
          {meter(ratio, tone)}
          {body}{improvement}
        </div>
        """
    return f"""
    <div class="au-card">
      <div class="au-kpi-top"><span class="au-kpi-label">{esc(component.label)}</span>{badge("Unavailable", "warning")}</div>
      <div class="au-note" style="margin-top:8px">{esc(component.missing_reason)}</div>
      <div class="au-note" style="margin-top:6px">{esc(component.detail)}</div>
      <div class="au-note" style="margin-top:6px;color:#fcd34d">{esc(component.improvement)}</div>
    </div>
    """


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    transactions = data.transactions(store)
    profile = data.profile(store)
    health = data.health(store)
    history = data.health_history(store)
    scoring = ctx.config.financial.scoring

    page_header(
        "Financial Health",
        "One explainable score built from savings, budget adherence and emergency coverage.",
        source_badges(ctx),
    )

    if not transactions:
        note("Upload and validate a statement first — the health score is computed from validated transactions.")
        _profile_editor(store, ctx, profile)
        return

    if health is None:
        # Compute live so the page is still truthful before the first workflow run.
        health = calculate_health(
            transactions,
            profile=profile,
            budgets=store.effective_budgets(),
            config=ctx.config,
            month=None,
        )
        st.caption("Showing a live calculation. Run the full analysis to persist the score and refresh the advisory.")

    score = health.score
    render_kpis(
        [
            KPI(
                "Financial health",
                f"{score:.1f}/100" if score is not None else "—",
                health.band,
                health_tone(score),
                chip=("Complete" if health.complete else "Provisional", "positive" if health.complete else "warning") if score is not None else None,
            ),
            KPI(
                "Savings",
                f"{(next((c.points for c in health.components if c.key == 'savings'), None) or 0):.1f} / {scoring.max_points['savings']:.0f}",
                percent(health.inputs.get("savings_rate")) + " savings rate" if health.inputs.get("savings_rate") is not None else "Income required",
                "positive" if (health.inputs.get("savings_rate") or 0) >= scoring.target_savings_rate else "warning",
            ),
            KPI(
                "Budget adherence",
                f"{(next((c.points for c in health.components if c.key == 'budget'), None) or 0):.1f} / {scoring.max_points['budget']:.0f}",
                "Weighted across category limits",
                "positive" if (next((c.normalized for c in health.components if c.key == 'budget'), None) or 0) >= 0.7 else "warning",
            ),
            KPI(
                "Emergency coverage",
                f"{(next((c.points for c in health.components if c.key == 'emergency'), None) or 0):.1f} / {scoring.max_points['emergency']:.0f}",
                f"{next((c.metric for c in health.components if c.key == 'emergency'), None):.1f} of {scoring.emergency_months_target} months"
                if next((c.metric for c in health.components if c.key == "emergency"), None) is not None
                else "Emergency fund required",
                "positive" if (next((c.metric for c in health.components if c.key == 'emergency'), None) or 0) >= scoring.emergency_months_target else "warning",
            ),
        ]
    )

    st.write("")
    left, right = st.columns([1, 1.35], gap="large")
    with left:
        with st.container(border=True):
            section("Health score", f"Rule version {ctx.config.rule_version}")
            render_chart(health_gauge(score, height=240), height=240, key="health_gauge")
            st.markdown(
                f'<div class="au-note" style="text-align:center">{esc(health.interpretation)}</div>',
                unsafe_allow_html=True,
            )
    with right:
        with st.container(border=True):
            section("Scoring model", "Transparent and configuration-driven")
            st.markdown(
                f"""
                <div class="au-quote">
                  S = {scoring.weights['savings']:.2f} × Savings + {scoring.weights['budget']:.2f} × Budget adherence +
                  {scoring.weights['emergency']:.2f} × Emergency coverage
                </div>
                <ul class="au-facts">
                  <li>Savings: rate ÷ {percent(scoring.target_savings_rate)} target, capped at {scoring.max_points['savings']:.0f} points.</li>
                  <li>Budget adherence: weighted min(1, limit ÷ actual) across categories, capped at {scoring.max_points['budget']:.0f} points.</li>
                  <li>Emergency coverage: fund ÷ monthly expenses, target {scoring.emergency_months_target} months, capped at {scoring.max_points['emergency']:.0f} points.</li>
                  <li>Monthly expense baseline: {esc(", ".join(health.inputs.get("expense_basis_months", []) or ["—"]))}.</li>
                  <li>Income source: {esc((health.inputs.get("income_source") or "unavailable").replace("_", " "))}.</li>
                </ul>
                """,
                unsafe_allow_html=True,
            )

    st.write("")
    hand = health
    columns = st.columns(3, gap="medium")
    for column, component in zip(columns, hand.components):
        with column:
            st.markdown(_component_card(component, ctx.config), unsafe_allow_html=True)

    if health.improvements:
        st.write("")
        with st.container(border=True):
            section("Areas to improve", "Largest scoring gains first")
            st.markdown(
                '<ul class="au-facts">' + "".join(f"<li>{esc(item)}</li>" for item in health.improvements) + "</ul>",
                unsafe_allow_html=True,
            )

    if history:
        st.write("")
        with st.container(border=True):
            section("Score history", "Persisted across analyses")
            figure = health_history_line(history)
            if figure:
                render_chart(figure, height=240, key="health_history")
            else:
                note("At least two scored runs are needed to plot a trend.")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Computed": row.get("computed_at", "")[:19].replace("T", " "),
                            "Score": row.get("score"),
                            "Complete": "Yes" if row.get("complete") else "No",
                            "Band": row.get("band", ""),
                            "Available weight": row.get("available_weight"),
                        }
                        for row in history[::-1][:20]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    st.write("")
    _profile_editor(store, ctx, profile)
