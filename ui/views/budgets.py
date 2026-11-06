"""Budget management: configure limits and compare with actuals."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.budgets import analyze_budgets, budget_summary
from core.constants import EXPENSE_CATEGORIES
from core.formatting import money, month_label, percent
from core.services import save_budgets
from core.spending import available_months
from ui import data
from ui.actions import run_analysis_flow
from ui.components import (
    KPI,
    badge,
    budget_bars,
    empty_state,
    esc,
    meter,
    note,
    page_header,
    render_chart,
    render_kpis,
    section,
)
from ui.context import UIContext, flash, source_badges

STATUS_LABEL = {
    "over": ("Over budget", "critical"),
    "warning": ("Near limit", "warning"),
    "ok": ("On track", "positive"),
    "unbudgeted": ("No budget", "info"),
}


def _status_table(statuses) -> str:
    rows = []
    for status in statuses:
        label, tone = STATUS_LABEL.get(status.status, ("Unknown", "neutral"))
        ratio = min(1.0, status.utilization) if status.limit > 0 else 0.0
        meter_tone = "critical" if status.status == "over" else ("warning" if status.status == "warning" else "positive")
        rows.append(
            f"""
            <div style="padding:11px 0;border-bottom:1px solid rgba(148,163,184,0.08)">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
                <div style="font-weight:600;font-size:0.87rem">{esc(status.category)}</div>
                <div style="display:flex;gap:6px;align-items:center">
                  <span class="au-note">{esc(money(status.actual))} / {esc(money(status.limit))}</span>
                  {badge(label, tone)}
                </div>
              </div>
              {meter(ratio, meter_tone)}
              <div class="au-note" style="margin-top:5px">
                {esc(percent(status.utilization))} utilised ·
                {esc(money(status.remaining))} remaining ·
                {esc(percent(status.share_of_total))} of tracked spend
                {f" · over by {esc(money(status.overrun))}" if status.status == "over" else ""}
              </div>
            </div>
            """
        )
    return "".join(rows)


def render(ctx: UIContext) -> None:
    store = ctx.store
    if store is None:
        return

    transactions = data.transactions(store)
    page_header(
        "Budget Management",
        "Set monthly limits per category and see exactly where actual spending stands against them.",
        source_badges(ctx),
    )

    if not transactions:
        empty_state(
            "Budgets need validated transactions",
            "Upload a statement in Transactions first. Budget adherence is measured against real, validated expenditure.",
        )
        return

    months = available_months(transactions)
    month = months[-1] if months else None
    budgets = store.effective_budgets()
    statuses = analyze_budgets(transactions, budgets, month=month, config=ctx.config)
    summary = budget_summary(statuses, month)

    render_kpis(
        [
            KPI("Monthly budget", money(summary["total_limit"]), f"Across {len([s for s in statuses if s.limit > 0])} categories"),
            KPI("Actual spend", money(summary["total_actual"]), month_label(month or "—")),
            KPI(
                "Budget utilisation",
                percent(summary["utilization"]),
                "Actual ÷ total limits",
                "positive" if (summary["utilization"] or 0) <= 1 else "negative",
            ),
            KPI(
                "Over budget",
                f"{summary['over_budget_count']}",
                f"{money(summary['overrun_total'])} above limits" if summary["over_budget_count"] else "All measured categories within limits",
                "negative" if summary["over_budget_count"] else "positive",
            ),
            KPI(
                "Near limit",
                f"{summary['warning_count']}",
                f"At or above {int(ctx.config.financial.scoring.budget_warning_ratio * 100)}% of limit",
                "warning" if summary["warning_count"] else "neutral",
            ),
        ]
    )

    st.write("")
    left, right = st.columns([1.25, 1], gap="large")

    with left:
        with st.container(border=True):
            section("Configured vs actual", "Grey bars are limits; coloured bars are validated spend")
            figure = budget_bars([s.model_dump(mode="json") for s in statuses])
            if figure:
                render_chart(figure, height=350, key="budget_bars")
            else:
                note("No budgeted categories to display.")

    with right:
        with st.container(border=True):
            section("Edit monthly limits", "Save to update adherence scoring")
            frame = pd.DataFrame(
                {
                    "Category": list(EXPENSE_CATEGORIES),
                    "Monthly limit": [float(budgets.get(c, 0.0)) for c in EXPENSE_CATEGORIES],
                }
            )
            edited = st.data_editor(
                frame,
                hide_index=True,
                width="stretch",
                height=340,
                num_rows="fixed",
                key="budget_editor",
                column_config={
                    "Category": st.column_config.TextColumn(disabled=True, width="medium"),
                    "Monthly limit": st.column_config.NumberColumn(min_value=0, step=500, format="%.0f"),
                },
            )
            new_budgets = {str(row["Category"]): float(row["Monthly limit"] or 0) for _, row in edited.iterrows()}
            col1, col2 = st.columns(2)
            if col1.button("Save limits", type="primary", use_container_width=True):
                save_budgets(store, new_budgets)
                flash("success", "Budgets saved. Re-run the analysis to refresh the health score and advisory.")
                st.rerun()
            if col2.button("Save & re-run", use_container_width=True):
                save_budgets(store, new_budgets)
                run_analysis_flow(store, config=ctx.config, caption="Re-scoring with the new budget limits…")
            st.caption(
                "Limits are stored per dataset. Categories with spend but no limit are treated as unbudgeted and "
                "lower the adherence score rather than being ignored."
            )

    st.write("")
    with st.container(border=True):
        section(
            f"Category position — {month_label(month or '—')}",
            "Actual vs configured limit with utilisation",
        )
        st.markdown(_status_table(statuses), unsafe_allow_html=True)
        if summary["over_budget"]:
            over_names = ", ".join(item["category"] for item in summary["over_budget"])
            st.caption(f"Over limit: {over_names}. Adjust the plan deliberately or reduce spend in those categories.")
