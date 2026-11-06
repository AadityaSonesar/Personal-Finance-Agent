"""Financial intelligence: patterns, anomalies, subscriptions and merchants."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.formatting import money, month_label, percent
from core.spending import available_months
from ui import data
from ui.components import (
    KPI,
    badge,
    bullet_list,
    category_donut,
    daily_spend_line,
    esc,
    merchant_bars,
    note,
    page_header,
    priority_chip,
    render_chart,
    render_kpis,
    section,
    severity_chip,
    severity_bars,
)
from ui.context import UIContext, source_badges

def _patterns_tab(store, ctx) -> None:
    spending = data.spending(store)
    transactions = data.transactions(store)
    if not spending.get("available"):
        note("No spending analysis yet. Run the full analysis from the sidebar.")
        return

    month = spending.get("reference_month")
    render_kpis(
        [
            KPI("Month analysed", month_label(month or "—"), f"{spending.get('months_covered', 0)} month(s) of data"),
            KPI("Month expenditure", money(spending.get("month_expense")), f"{spending.get('month_transactions', 0)} transactions", "neutral"),
            KPI("Month income", money(spending.get("month_income")), "Validated income transactions", "positive"),
            KPI(
                "Savings rate",
                percent(spending.get("savings_rate")),
                "Net ÷ income across all analysed data",
                "positive" if (spending.get("savings_rate") or 0) >= ctx.config.financial.scoring.target_savings_rate else "warning",
            ),
            KPI(
                "Largest category",
                (spending.get("highest_category") or {}).get("category", "—"),
                money((spending.get("highest_category") or {}).get("amount")),
            ),
        ]
    )

    st.write("")
    left, right = st.columns([1, 1], gap="large")
    with left:
        with st.container(border=True):
            section("Daily expenditure", f"{month_label(month or '—')} · validated spend per day")
            figure = daily_spend_line(spending.get("daily", []))
            if figure:
                render_chart(figure, height=280, key="intel_daily")
            else:
                note("No daily expenditure for this month.")

    with right:
        with st.container(border=True):
            section("Category mix", "Share of monthly spend")
            figure = category_donut(spending.get("category_breakdown", []), height=280)
            if figure:
                render_chart(figure, height=280, key="intel_donut")
            else:
                note("No categories to display.")

    st.write("")
    with st.container(border=True):
        section("Category breakdown", "Amount, count and share of monthly expenditure")
        rows = spending.get("category_breakdown", [])
        if rows:
            frame = pd.DataFrame(
                [
                    {
                        "Category": row["category"],
                        "Amount": row["amount"],
                        "Transactions": row["count"],
                        "Average": row["average"],
                        "Share": row["share"],
                    }
                    for row in rows
                ]
            )
            st.dataframe(
                frame,
                hide_index=True,
                width="stretch",
                column_config={
                    "Amount": st.column_config.NumberColumn(format="%.0f"),
                    "Average": st.column_config.NumberColumn(format="%.0f"),
                    "Share": st.column_config.NumberColumn(format="percent"),
                },
            )
        else:
            note("No expenditure recorded.")

    st.write("")
    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            section("Month-over-month", "Compared with the previous month")
            mom = spending.get("month_over_month")
            if mom:
                delta = mom["delta"]
                tone = "negative" if delta > 0 else "positive"
                st.markdown(
                    f"""
                    <div class="au-kpi-value {tone}" style="font-size:1.6rem">{money(delta, signed=True)}</div>
                    <div class="au-note">
                      {esc(month_label(mom['current_month']))}: {esc(money(mom['current_total']))} ·
                      {esc(month_label(mom['previous_month']))}: {esc(money(mom['previous_total']))}
                      {f" · {percent(mom['change_ratio'], signed=True)}" if mom.get('change_ratio') is not None else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                note("A second month of data is required for a month-over-month comparison.")

    with right:
        with st.container(border=True):
            section("Income vs expenditure", "Same month")
            ive = spending.get("income_vs_expense", {}) or {}
            ratio = ive.get("ratio")
            st.markdown(
                f"""
                <div style="display:flex;gap:26px;flex-wrap:wrap">
                  <div><div class="au-kpi-label">Income</div><div style="font-weight:600">{money(ive.get('income'))}</div></div>
                  <div><div class="au-kpi-label">Expenditure</div><div style="font-weight:600">{money(ive.get('expense'))}</div></div>
                  <div><div class="au-kpi-label">Expense / income</div><div style="font-weight:600">{percent(ratio)}</div></div>
                </div>
                <div class="au-note" style="margin-top:10px">
                  {"Expenditure exceeds income for this month." if ratio and ratio > 1 else "Expenditure is within recorded income."}
                  {" No income transactions exist for this month." if ratio is None else ""}
                </div>
                """,
                unsafe_allow_html=True,
            )


def _anomalies_tab(store) -> None:
    anomalies = data.anomalies(store)
    if not anomalies:
        note("No anomalies detected, or the analysis has not run yet.")
        return

    by_severity: dict[str, int] = {}
    for anomaly in anomalies:
        by_severity[anomaly.severity] = by_severity.get(anomaly.severity, 0) + 1

    left, right = st.columns([1, 2.2], gap="large")
    with left:
        with st.container(border=True):
            section("Severity mix", f"{len(anomalies)} signals")
            figure = severity_bars(by_severity)
            if figure:
                render_chart(figure, height=220, key="anom_sev")
    with right:
        with st.container(border=True):
            section("How detection works", "Transparent, threshold-driven rules")
            st.markdown(
                '<div class="au-note">Every signal is computed from validated transactions using the configured '
                "thresholds: large-payment ratio vs category median, month-over-month spikes, identical charges, "
                "merchant shifts and category drift. Instruction-like text in merchant fields is detected and "
                "retained as inert data — it never changes a balance, score or approval.</div>",
                unsafe_allow_html=True,
            )

    severity_filter = st.multiselect(
        "Filter by severity",
        ["critical", "high", "medium", "low"],
        default=["critical", "high", "medium", "low"],
        key="anom_filter",
    )
    st.write("")
    for anomaly in [a for a in anomalies if a.severity in severity_filter]:
        with st.container(border=True):
            st.markdown(
                f"""
                <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
                  <div>
                    <div style="font-weight:650;font-size:0.95rem">{esc(anomaly.title)}</div>
                    <div class="au-note" style="margin-top:3px">
                      {esc(anomaly.category)} · {esc(anomaly.date_ref)} · score {anomaly.score:.2f}
                    </div>
                  </div>
                  <div style="display:flex;gap:6px;flex-wrap:wrap">
                    {severity_chip(anomaly.severity)}
                    {badge(anomaly.anomaly_type.replace('_', ' '), 'neutral')}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(
                '<ul class="au-facts">'
                + "".join(f"<li>{esc(item)}</li>" for item in anomaly.evidence)
                + "</ul>",
                unsafe_allow_html=True,
            )
            with st.expander("Metrics and linked transactions"):
                st.json({"metrics": anomaly.metrics, "transaction_ids": anomaly.transaction_ids}, expanded=False)


def _subscriptions_tab(store, ctx) -> None:
    subscriptions = data.subscriptions(store)
    if not subscriptions:
        note("No recurring payment patterns identified yet.")
        return

    needs_review = [s for s in subscriptions if s.status == "needs_verification"]
    confirmed = [s for s in subscriptions if s.status == "confirmed_recurring"]
    potential = [s for s in subscriptions if s.status == "potential_subscription"]
    monthly = sum(s.average_amount * 30.44 / s.interval_days for s in subscriptions if s.interval_days > 0)
    annual = sum(s.annualized_cost for s in subscriptions)

    render_kpis(
        [
            KPI("Recurring payments", f"{len(subscriptions)}", "Detected from interval and amount regularity"),
            KPI("Confirmed", f"{len(confirmed)}", "Regular interval and stable amount", "positive"),
            KPI("Potential", f"{len(potential)}", "Two or more consistent charges", "warning"),
            KPI("Needs verification", f"{len(needs_review)}", "No recent charge observed", "warning" if needs_review else "neutral"),
            KPI("Monthly equivalent", money(monthly), "Normalised from observed intervals"),
            KPI("Annualised", money(annual), "At the observed charge cadence"),
        ]
    )

    st.write("")
    st.info(
        "Usage data is not present in bank statements. A recurring charge is never labelled unused here — "
        "overdue payments are flagged for verification so you can confirm status with the provider."
    )

    frame = pd.DataFrame(
        [
            {
                "Merchant": s.merchant,
                "Category": s.category,
                "Average amount": s.average_amount,
                "Interval (days)": s.interval_days,
                "Occurrences": s.occurrences,
                "Last charge": s.last_charge.isoformat(),
                "Next expected": s.next_expected_date.isoformat(),
                "Status": s.status.replace("_", " "),
                "Confidence": s.confidence,
                "Annualised": s.annualized_cost,
                "Usage verified": "Yes" if s.usage_verified else "No",
            }
            for s in subscriptions
        ]
    )
    with st.container(border=True):
        section("Detected recurring payments", "Review before acting — nothing is cancelled automatically")
        st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            column_config={
                "Average amount": st.column_config.NumberColumn(format="%.0f"),
                "Annualised": st.column_config.NumberColumn(format="%.0f"),
                "Confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
            },
        )

    if needs_review:
        st.write("")
        with st.container(border=True):
            section("Awaiting verification", "Overdue relative to the observed cadence")
            for subscription in needs_review:
                st.markdown(
                    f"""
                    <div style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,0.08)">
                      <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
                        <div style="font-weight:600">{esc(subscription.merchant)}</div>
                        <div>{badge("Needs verification", "warning")}</div>
                      </div>
                      <div class="au-note" style="margin-top:4px">
                        {esc(money(subscription.average_amount))} per charge · last seen
                        {esc(subscription.last_charge.isoformat())} ({subscription.days_since_last_charge} days ago) ·
                        next expected {esc(subscription.next_expected_date.isoformat())}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="au-note" style="margin:6px 0 10px">{esc(subscription.evidence[-1])}</div>',
                    unsafe_allow_html=True,
                )


def _merchants_tab(store) -> None:
    spending = data.spending(store)
    merchants = spending.get("merchants", [])
    if not merchants:
        note("No merchant concentration data yet.")
        return
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        with st.container(border=True):
            section("Merchant concentration", "Highest spend in the analysed month")
            figure = merchant_bars(merchants)
            if figure:
                render_chart(figure, height=320, key="merchant_bars")
    with right:
        with st.container(border=True):
            section("Merchant detail", "Spend, frequency and average ticket")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Merchant": m["merchant"][:28],
                            "Spend": m["amount"],
                            "Charges": m["count"],
                            "Average": m["average"],
                        }
                        for m in merchants
                    ]
                ),
                hide_index=True,
                width="stretch",
                height=320,
                column_config={
                    "Spend": st.column_config.NumberColumn(format="%.0f"),
                    "Average": st.column_config.NumberColumn(format="%.0f"),
                },
            )


def _comparison_tab(store, ctx) -> None:
    spending = data.spending(store)
    monthly = spending.get("monthly", [])
    if not monthly:
        note("Monthly history needs at least one analysed month.")
        return
    frame = pd.DataFrame(
        [
            {
                "Month": month_label(row["month"]),
                "Income": row["income"],
                "Expenditure": row["expense"],
                "Net": row["net"],
                "Savings rate": (row["net"] / row["income"]) if row["income"] else None,
            }
            for row in monthly
        ]
    )
    with st.container(border=True):
        section("Monthly ledger", "Actual values only — no projections")
        st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            column_config={
                "Income": st.column_config.NumberColumn(format="%.0f"),
                "Expenditure": st.column_config.NumberColumn(format="%.0f"),
                "Net": st.column_config.NumberColumn(format="%.0f"),
                "Savings rate": st.column_config.NumberColumn(format="percent"),
            },
        )
    if len(monthly) >= 2:
        latest, previous = monthly[-1], monthly[-2]
        st.caption(
            f"{month_label(latest['month'])} expenditure is {money(latest['expense'])} versus "
            f"{money(previous['expense'])} in {month_label(previous['month'])}."
        )


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return
    transactions = data.transactions(store)
    if not transactions:
        page_header("Financial Intelligence", "Patterns, anomalies and recurring payments.", source_badges(ctx))
        note("Upload and validate a statement first — intelligence is computed only from validated data.")
        return

    page_header(
        "Financial Intelligence",
        "Deterministic spending patterns, anomaly signals, recurring payments and merchant concentration.",
        source_badges(ctx),
    )
    tabs = st.tabs(["Spending patterns", "Anomalies", "Subscriptions", "Merchants", "Monthly comparison"])
    with tabs[0]:
        _patterns_tab(store, ctx)
    with tabs[1]:
        _anomalies_tab(store)
    with tabs[2]:
        _subscriptions_tab(store, ctx)
    with tabs[3]:
        _merchants_tab(store)
    with tabs[4]:
        _comparison_tab(store, ctx)
