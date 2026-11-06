"""Cash flow forecast: actuals, projections and stated assumptions."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.formatting import money, month_label, percent
from ui import data
from ui.components import (
    KPI,
    badge,
    bullet_list,
    esc,
    forecast_path_chart,
    note,
    page_header,
    render_chart,
    render_kpis,
    section,
)
from ui.context import UIContext, source_badges


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    transactions = data.transactions(store)
    forecast = data.forecast(store)
    profile = data.profile(store)
    spending = data.spending(store)
    subscriptions = data.subscriptions(store)

    page_header(
        "Cash Flow Forecast",
        "Actual spending so far, a transparent projection for the rest of the month, and the assumptions behind it.",
        source_badges(ctx),
    )

    if not transactions:
        note("Upload and validate a statement to build a cash flow forecast.")
        return
    if forecast is None:
        note("Run the full analysis from the sidebar to build a forecast.")
        return

    short_of_zero = forecast.predicted_end_balance is not None and forecast.predicted_end_balance < 0
    tone = "negative" if short_of_zero else ("warning" if forecast.deficit else "positive")
    if short_of_zero:
        balance_chip = ("Deficit", "critical")
    elif forecast.deficit:
        balance_chip = ("Spending > income", "warning")
    elif forecast.predicted_end_balance is not None:
        balance_chip = ("On track", "positive")
    else:
        balance_chip = None
    render_kpis(
        [
            KPI(
                "Current balance",
                money(forecast.current_balance),
                "Provided balance" if forecast.current_balance is not None else "Not provided",
                "neutral" if forecast.current_balance is not None else "warning",
            ),
            KPI(
                "Projected spend",
                money(forecast.projected_expense),
                month_label(forecast.month),
                "neutral",
                chip=("PROJECTED", "warning"),
            ),
            KPI(
                "Predicted end balance",
                money(forecast.predicted_end_balance),
                "Projection · not an actual balance" if forecast.predicted_end_balance is not None else "Add current balance to enable",
                tone if forecast.predicted_end_balance is not None else "warning",
                chip=balance_chip,
            ),
            KPI(
                "Projected net savings",
                money(forecast.net_savings),
                "Income − projected spend − upcoming bills",
                "positive" if (forecast.net_savings or 0) >= 0 else "negative",
            ),
            KPI(
                "Budget utilisation",
                percent(forecast.budget_utilization),
                "Projected spend ÷ total budget" if forecast.budget_total else "No budgets configured",
                "warning" if (forecast.budget_utilization or 0) > 1 else "positive",
            ),
        ]
    )

    st.write("")
    left, right = st.columns([1.45, 1], gap="large")
    with left:
        with st.container(border=True):
            section("Cumulative spend path", "Solid = actual · dashed = projection to month end")
            figure = forecast_path_chart(
                spending.get("daily", []),
                projected_expense=forecast.projected_expense,
                days_in_month=forecast.days_in_month,
                days_elapsed=forecast.days_elapsed,
            )
            if figure:
                render_chart(figure, height=320, key="forecast_path")
                st.caption(
                    f"{forecast.days_elapsed} of {forecast.days_in_month} days elapsed · "
                    f"actual spend so far {money(forecast.actual_expense_mtd)}."
                )
            else:
                note("No daily spending recorded for this month yet.")

    with right:
        with st.container(border=True):
            section("Projection basis", "Method and inputs")
            st.markdown(
                f"""
                <div class="au-quote">{esc(forecast.projection_method)}</div>
                <div style="display:flex;gap:22px;flex-wrap:wrap;margin-top:8px">
                  <div><div class="au-kpi-label">Run-rate figure</div><div style="font-weight:600">{money(forecast.run_rate_expense)}</div></div>
                  <div><div class="au-kpi-label">Historical average</div><div style="font-weight:600">{money(forecast.historical_average_expense)}</div></div>
                </div>
                <div class="au-note" style="margin-top:10px">
                  Actual expenditure so far is {esc(money(forecast.actual_expense_mtd))}.
                  Projected remaining outflow is {esc(money(forecast.expected_outflow_remaining))}.
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.write("")
    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            section("Assumptions", "Every projection depends on these")
            st.markdown(bullet_list(forecast.assumptions, tone="info"), unsafe_allow_html=True)
            section("Uncertainty", "What could change the outcome")
            st.markdown(bullet_list(forecast.uncertainty, tone="warning"), unsafe_allow_html=True)

    with right:
        with st.container(border=True):
            section("Upcoming bills", "Provided by you, assumed not yet in statements")
            if forecast.upcoming_bills:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Bill": bill.label,
                                "Amount": bill.amount,
                                "Due": bill.due_date.isoformat() if bill.due_date else "—",
                            }
                            for bill in forecast.upcoming_bills
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                    column_config={"Amount": st.column_config.NumberColumn(format="%.0f")},
                )
            else:
                note("No upcoming bills provided. Add them in Financial Health to improve the forecast.")
            st.markdown(
                f'<div class="au-note" style="margin-top:10px">Recurring charges normalise to '
                f'{esc(money(forecast.recurring_monthly_cost))} per month '
                f'across {len(subscriptions)} detected payment pattern(s).</div>',
                unsafe_allow_html=True,
            )

    st.write("")
    with st.container(border=True):
        section("Recurring commitments in the forecast", "Already included in historical expenditure")
        if subscriptions:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Merchant": s.merchant,
                            "Average charge": s.average_amount,
                            "Cadence (days)": s.interval_days,
                            "Next expected": s.next_expected_date.isoformat(),
                            "Status": s.status.replace("_", " "),
                        }
                        for s in subscriptions
                    ]
                ),
                hide_index=True,
                width="stretch",
                column_config={"Average charge": st.column_config.NumberColumn(format="%.0f")},
            )
        else:
            note("No recurring payments detected.")
        st.caption(
            "Projected values are estimates derived from actual transactions plus your stated inputs. "
            "They are never presented as realized balances."
        )
