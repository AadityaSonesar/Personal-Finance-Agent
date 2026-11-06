"""Overview dashboard."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.constants import PRIORITIES
from core.formatting import money, month_label, percent
from core.spending import build_spending_intelligence
from ui import data
from ui.components import (
    KPI,
    alert_status_chip,
    badge,
    bullet_list,
    category_donut,
    empty_state,
    esc,
    health_tone,
    money_or_dash,
    monthly_bars,
    note,
    page_header,
    priority_chip,
    ratio_tone,
    render_chart,
    render_kpis,
    section,
    sla_chip,
    status_chip,
)
from ui.context import UIContext, source_badges

PRIORITY_COLOR = {"P1": "#ef4444", "P2": "#f59e0b", "P3": "#38bdf8", "P4": "#94a3b8"}


def _open_alerts(alerts):
    open_alerts = [a for a in alerts if not a.resolved_at]
    return sorted(open_alerts, key=lambda a: (PRIORITIES.index(a.priority), a.deadline))


def _alert_rows(alerts) -> str:
    if not alerts:
        return '<div class="au-note">No open alerts. Priorities and SLA deadlines appear here after an analysis.</div>'
    rows = []
    for alert in alerts[:4]:
        color = PRIORITY_COLOR.get(alert.priority, "#94a3b8")
        intent = esc(alert.intent.replace("_", " "))
        due = esc(alert.deadline.strftime("%d %b %H:%M"))
        # Single-line markup: indented multi-line HTML is parsed as a code block.
        rows.append(
            f'<li><span class="au-dot" style="background:{color}"></span>'
            f'<div style="flex:1"><div style="font-weight:600;font-size:0.85rem">{esc(alert.subject)}</div>'
            f'<div class="au-note">{intent} · due {due}</div></div>'
            f'<div style="display:flex;gap:5px;flex-wrap:wrap">{priority_chip(alert.priority)}{sla_chip(alert)}</div></li>'
        )
    return f'<ul class="au-list">{"".join(rows)}</ul>'


def _transaction_table(transactions) -> None:
    if not transactions:
        note("No transactions to show.")
        return
    recent = sorted(transactions, key=lambda t: (t.transaction_date, t.transaction_id), reverse=True)[:8]
    frame = pd.DataFrame(
        [
            {
                "Date": t.transaction_date.strftime("%d %b"),
                "Merchant": t.merchant[:38],
                "Category": t.category,
                "Type": t.transaction_type.title(),
                "Amount": float(t.amount),
                "Flags": "duplicate" if t.is_duplicate_suspect else ("sanitised" if t.sanitization_flags else ""),
            }
            for t in recent
        ]
    )
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Amount": st.column_config.NumberColumn(format="%.0f"),
            "Date": st.column_config.TextColumn(width="small"),
        },
    )


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    transactions = data.transactions(store)
    profile = data.profile(store)
    spending = data.spending(store)
    health = data.health(store)
    forecast = data.forecast(store)
    alerts = data.alerts(store)
    recommendations = data.recommendations(store)
    decisions = data.decisions(store)
    anomalies = data.anomalies(store)
    subscriptions = data.subscriptions(store)

    if not spending.get("available") and transactions:
        spending = build_spending_intelligence(transactions)

    month = spending.get("reference_month") or "—"
    subtitle = (
        f"Position for {month_label(month)} · {spending.get('months_covered', 0)} month(s) of validated data"
        if spending.get("available")
        else "No analysed month yet. Upload a statement and run the analysis."
    )
    page_header("Overview", subtitle, source_badges(ctx))

    if not transactions:
        empty_state(
            "No validated transactions yet",
            "Upload a CSV, Excel or PDF statement in Transactions, then run the full analysis from the sidebar. "
            "Each file is parsed, validated and quarantined where necessary before any figure is calculated.",
            hint="Demonstration data can be loaded from the sidebar if you want to explore the product first.",
        )
        return

    income = float(spending.get("month_income") or 0.0)
    expense = float(spending.get("month_expense") or 0.0)
    net = income - expense
    score = health.score if health else None

    render_kpis(
        [
            KPI(
                "Total balance",
                money_or_dash(profile.current_balance),
                "Current account balance you provided" if profile.current_balance is not None else "Not provided · add it in Financial Health",
                "neutral" if profile.current_balance is not None else "warning",
            ),
            KPI("Monthly income", money(income) if income else "—", month_label(month), "positive" if income else "neutral"),
            KPI("Monthly expenditure", money(expense), f"{spending.get('month_transactions', 0)} transactions", "neutral"),
            KPI(
                "Net savings",
                money(net),
                f"Savings rate {percent(spending.get('savings_rate'))}" if spending.get("savings_rate") is not None else "Income transactions needed",
                "positive" if net >= 0 else "negative",
            ),
            KPI(
                "Financial health",
                f"{score:.0f}/100" if score is not None else "—",
                health.band if health else "Run the analysis to score",
                health_tone(score),
                chip=(health.band, "positive" if (score or 0) >= 70 else ("warning" if (score or 0) >= 50 else "critical")) if health and score is not None else None,
            ),
        ]
    )

    st.write("")
    left, right = st.columns([1.08, 1], gap="large")

    with left:
        with st.container(border=True):
            section("Spending by category", f"{month_label(month)} · validated transactions")
            figure = category_donut(spending.get("category_breakdown", []))
            if figure:
                render_chart(figure, height=300, key="overview_category_donut")
                top = (spending.get("category_breakdown") or [{}])[0]
                note(
                    f"Largest category: {top.get('category', '—')} at {money(top.get('amount'))} "
                    f"({percent(top.get('share'))} of the month)."
                )
            else:
                note("No expenditure recorded for the selected month.")

    with right:
        with st.container(border=True):
            section("Income vs expenditure", "Validated transactions, most recent months")
            figure = monthly_bars(spending.get("monthly", []))
            if figure:
                render_chart(figure, height=300, key="overview_monthly_bars")
            else:
                note("Monthly history appears after the first analysis.")

    st.write("")
    left, right = st.columns([1.08, 1], gap="large")

    with left:
        with st.container(border=True):
            section("Cash flow outlook", "Projection, not an actual balance")
            if forecast and forecast.predicted_end_balance is not None:
                insolvent = forecast.predicted_end_balance < 0
                tone = "negative" if insolvent else ("warning" if forecast.deficit else "positive")
                if insolvent:
                    verdict = badge("Projected deficit", "critical")
                elif forecast.deficit:
                    verdict = badge("Spending exceeds income", "warning")
                else:
                    verdict = badge("No deficit projected", "positive")
                st.markdown(
                    f"""
                    <div style="display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap">
                      <div class="au-kpi-value {tone}" style="font-size:1.9rem">{money(forecast.predicted_end_balance)}</div>
                      <div style="padding-bottom:6px">
                        {verdict}
                      </div>
                    </div>
                    <div class="au-note" style="margin-top:6px">Predicted balance at the end of {month_label(forecast.month)}</div>
                    <div style="display:flex;gap:26px;flex-wrap:wrap;margin-top:14px">
                      <div><div class="au-kpi-label">Projected spend</div><div style="font-weight:600">{money(forecast.projected_expense)}</div></div>
                      <div><div class="au-kpi-label">Recurring / month</div><div style="font-weight:600">{money(forecast.recurring_monthly_cost)}</div></div>
                      <div><div class="au-kpi-label">Upcoming bills</div><div style="font-weight:600">{money(forecast.upcoming_bills_total)}</div></div>
                    </div>
                    <div class="au-note" style="margin-top:12px">{esc(forecast.projection_method)}.</div>
                    """,
                    unsafe_allow_html=True,
                )
            elif forecast:
                st.markdown(
                    f'<div class="au-kpi-value neutral" style="font-size:1.6rem">—</div>'
                    f'<div class="au-note" style="margin-top:6px">Add your current balance in Financial Health to '
                    f'unlock the month-end prediction. Projected spend is {money(forecast.projected_expense)}.</div>',
                    unsafe_allow_html=True,
                )
            else:
                note("Run the analysis to build a cash flow forecast from validated data.")

    with right:
        with st.container(border=True):
            section("Priority alerts", "P1 → P4 with SLA deadlines")
            st.markdown(_alert_rows(_open_alerts(alerts)), unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([1.55, 1], gap="large")

    with left:
        with st.container(border=True):
            section("Recent transactions", "Newest validated records")
            _transaction_table(transactions)

    with right:
        with st.container(border=True):
            section("Signals", "From the latest analysis")
            signals = []
            if anomalies:
                high = sum(1 for a in anomalies if a.severity in {"critical", "high"})
                signals.append(f"{len(anomalies)} anomaly signal(s), {high} high or critical.")
            else:
                signals.append("No anomalies detected in the analysed data.")
            if subscriptions:
                pending = sum(1 for s in subscriptions if s.status == "needs_verification")
                signals.append(
                    f"{len(subscriptions)} recurring payment(s) identified, {pending} awaiting verification."
                )
            pending_reviews = [r for r in recommendations if r.recommendation_id not in decisions]
            signals.append(
                f"{len(pending_reviews)} recommendation(s) awaiting human review."
                if recommendations
                else "No recommendations generated yet."
            )
            st.markdown(bullet_list(signals, tone="info"), unsafe_allow_html=True)

            if recommendations:
                top = recommendations[0]
                st.markdown(
                    f'<div class="au-divider"></div>'
                    f'<div class="au-kpi-label">Top recommendation</div>'
                    f'<div style="font-weight:600;margin:6px 0 4px">{esc(top.title)}</div>'
                    f'<div style="display:flex;gap:6px;margin-bottom:8px">{priority_chip(top.priority)}'
                    f'{status_chip(top.status if top.recommendation_id not in decisions else "approved")}</div>'
                    f'<div class="au-note">{esc(top.facts[0] if top.facts else "")}</div>',
                    unsafe_allow_html=True,
                )
