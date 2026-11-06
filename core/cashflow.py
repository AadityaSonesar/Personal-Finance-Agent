"""Transparent cash-flow forecasting.

Actual values and projections are kept in separate fields so the dashboard never
presents an estimate as a realized figure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from core.config import AppConfig, get_config
from core.models import CashFlowForecast, Subscription, Transaction
from core.spending import (
    available_months,
    days_in_month as month_length,
    expense_basis,
    month_expense,
    month_income,
)
from core.storage import FinancialProfile
from core.subscriptions import monthly_recurring_cost


def forecast_cashflow(
    transactions: list[Transaction],
    *,
    profile: FinancialProfile,
    subscriptions: list[Subscription] | None = None,
    budgets: dict[str, float] | None = None,
    config: AppConfig | None = None,
    month: str | None = None,
    today: date | None = None,
) -> CashFlowForecast:
    """Project the remainder of a month from actual data plus stated inputs."""
    config = config or get_config()
    cfg = config.financial.forecast
    today = today or date.today()
    subscriptions = subscriptions or []
    budgets = budgets or {}

    months = available_months(transactions)
    if month is None:
        month = months[-1] if months else today.strftime("%Y-%m")

    length = month_length(month)
    is_current_month = month == today.strftime("%Y-%m")
    days_elapsed = today.day if is_current_month else length
    days_elapsed = max(1, min(days_elapsed, length))

    actual_expense = month_expense(transactions, month)
    income_received = month_income(transactions, month)

    historical_avg, basis_months = expense_basis(transactions, months=cfg.history_months)
    run_rate = actual_expense / days_elapsed * length if days_elapsed > 0 else None

    assumptions: list[str] = []
    uncertainty: list[str] = []

    elapsed_ratio = (days_elapsed / length) if length else 1.0
    if run_rate is not None and historical_avg is not None:
        # Confidence weighting: early in the month a few days of data are a weak
        # signal, so the configured run-rate weight is scaled by elapsed time.
        run_weight = round(cfg.run_rate_weight * elapsed_ratio, 4)
        hist_weight = round(1.0 - run_weight, 4)
        projected = run_weight * run_rate + hist_weight * historical_avg
        method = (
            f"Recency-weighted blend: {run_weight:.0%} current run rate "
            f"({days_elapsed} of {length} days elapsed) + {hist_weight:.0%} "
            f"{len(basis_months)}-month historical average"
        )
        uncertainty.append(
            "The blend depends on spending continuing at the observed pace; a single large purchase can shift it."
        )
        if days_elapsed < 7:
            uncertainty.append(
                "Fewer than seven days of the month have elapsed, so the projection leans on historical averages."
            )
    elif run_rate is not None:
        projected = run_rate
        method = "Current-month run rate extrapolated to the full month"
        uncertainty.append("No complete prior month exists, so the projection relies on a partial month only.")
    elif historical_avg is not None:
        projected = historical_avg
        method = f"Historical average across {len(basis_months)} month(s)"
        uncertainty.append("No expenditure was recorded for the selected month; a historical average is used.")
    else:
        projected = None
        method = "unavailable"
        uncertainty.append("Insufficient history to project expenditure.")

    if projected is not None:
        projected = max(projected, actual_expense)
        projected = round(projected, 2)
    assumptions.append(
        f"Projected expenditure covers the full {length}-day month; {days_elapsed} day(s) have elapsed."
    )

    monthly_income = profile.monthly_income
    income_source = "profile"
    if monthly_income is None:
        if income_received > 0:
            monthly_income = income_received
            income_source = "observed_transactions"
            assumptions.append("Income is taken from income transactions recorded in the selected month.")
        else:
            income_source = "unavailable"
            uncertainty.append("Monthly income was not provided, so inflow-dependent figures are unavailable.")

    expected_inflow_remaining = None
    if monthly_income is not None:
        expected_inflow_remaining = round(max(0.0, monthly_income - income_received), 2)

    upcoming_total = round(sum(bill.amount for bill in profile.upcoming_bills), 2)
    if upcoming_total > 0:
        assumptions.append(
            "Upcoming bills you supplied are assumed not to be present in the uploaded statements."
        )

    expected_outflow_remaining = None
    if projected is not None:
        expected_outflow_remaining = round(max(0.0, projected - actual_expense) + upcoming_total, 2)

    net_savings = None
    if monthly_income is not None and projected is not None:
        net_savings = round(monthly_income - projected - upcoming_total, 2)

    current_balance = profile.current_balance
    if current_balance is None:
        uncertainty.append("Current account balance is not provided, so the predicted end balance is unavailable.")

    predicted_end_balance = None
    if current_balance is not None and expected_inflow_remaining is not None and expected_outflow_remaining is not None:
        predicted_end_balance = round(current_balance + expected_inflow_remaining - expected_outflow_remaining, 2)

    shortfall = round(-predicted_end_balance, 2) if predicted_end_balance is not None and predicted_end_balance < 0 else 0.0
    deficit = bool(
        (predicted_end_balance is not None and predicted_end_balance < 0)
        or (net_savings is not None and net_savings < 0)
    )
    if deficit:
        uncertainty.append("A deficit is projected; timing of income and bills within the month can change the outcome.")

    budget_total = round(float(sum(budgets.values())), 2)
    budget_utilization = None
    if budget_total > 0 and projected is not None:
        budget_utilization = round((projected + upcoming_total) / budget_total, 4)

    recurring_monthly = monthly_recurring_cost(subscriptions)
    if recurring_monthly > 0:
        assumptions.append(
            f"Recurring charges of about {recurring_monthly:,.0f}/month are already included in historical expenditure."
        )

    return CashFlowForecast(
        month=month,
        monthly_income=round(monthly_income, 2) if monthly_income is not None else None,
        income_source=income_source,
        income_received_mtd=round(income_received, 2),
        expected_inflow_remaining=expected_inflow_remaining,
        actual_expense_mtd=round(actual_expense, 2),
        projected_expense=projected,
        projection_method=method,
        historical_average_expense=historical_avg,
        run_rate_expense=round(run_rate, 2) if run_rate is not None else None,
        recurring_monthly_cost=recurring_monthly,
        upcoming_bills_total=upcoming_total,
        upcoming_bills=profile.upcoming_bills,
        net_savings=net_savings,
        current_balance=round(current_balance, 2) if current_balance is not None else None,
        predicted_end_balance=predicted_end_balance,
        expected_outflow_remaining=expected_outflow_remaining,
        shortfall=shortfall,
        deficit=deficit,
        budget_total=budget_total,
        budget_utilization=budget_utilization,
        days_elapsed=days_elapsed,
        days_in_month=length,
        assumptions=assumptions,
        uncertainty=uncertainty,
    )


def forecast_summary(forecast: CashFlowForecast) -> dict[str, Any]:
    return {
        "month": forecast.month,
        "income": forecast.monthly_income,
        "projected_expense": forecast.projected_expense,
        "predicted_end_balance": forecast.predicted_end_balance,
        "deficit": forecast.deficit,
        "shortfall": forecast.shortfall,
        "budget_utilization": forecast.budget_utilization,
        "method": forecast.projection_method,
    }
