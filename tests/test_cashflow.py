"""Cash-flow projection arithmetic and transparency."""

from __future__ import annotations

from datetime import date

from core.cashflow import forecast_cashflow
from core.models import UpcomingBill
from core.storage import FinancialProfile
from tests.helpers import tx


def _transactions():
    return [
        tx(1, date(2026, 1, 5), "Employer", 100000, tx_type="income", category="Income"),
        tx(2, date(2026, 1, 12), "Grocer", 30000, category="Food and Dining"),
        tx(3, date(2026, 2, 4), "Grocer", 10000, category="Food and Dining"),
    ]


def _forecast(**profile_kwargs):
    profile = FinancialProfile(**profile_kwargs)
    return forecast_cashflow(
        _transactions(),
        profile=profile,
        config=None,
        month="2026-02",
        today=date(2026, 2, 10),
    )


def test_actual_and_projected_values_are_separate(config):
    forecast = forecast_cashflow(
        _transactions(),
        profile=FinancialProfile(monthly_income=100000, current_balance=20000),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    assert forecast.actual_expense_mtd == 10000
    assert forecast.projected_expense is not None
    assert forecast.projected_expense >= forecast.actual_expense_mtd
    assert forecast.days_elapsed == 10
    assert forecast.days_in_month == 28
    assert "blend" in forecast.projection_method.lower()
    assert forecast.assumptions


def test_predicted_balance_follows_the_stated_arithmetic(config):
    forecast = forecast_cashflow(
        _transactions(),
        profile=FinancialProfile(monthly_income=100000, current_balance=20000),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    expected = round(
        forecast.current_balance
        + forecast.expected_inflow_remaining
        - forecast.expected_outflow_remaining,
        2,
    )
    assert forecast.predicted_end_balance == expected
    assert forecast.expected_outflow_remaining == round(
        max(0.0, forecast.projected_expense - forecast.actual_expense_mtd), 2
    )


def test_upcoming_bills_reduce_the_projection(config):
    without_bills = forecast_cashflow(
        _transactions(),
        profile=FinancialProfile(monthly_income=100000, current_balance=50000),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    with_bills = forecast_cashflow(
        _transactions(),
        profile=FinancialProfile(
            monthly_income=100000,
            current_balance=50000,
            upcoming_bills=[UpcomingBill(label="Rent", amount=25000)],
        ),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    assert with_bills.upcoming_bills_total == 25000
    assert with_bills.predicted_end_balance == round(without_bills.predicted_end_balance - 25000, 2)
    assert any("Upcoming bills" in line for line in with_bills.assumptions)


def test_missing_balance_is_reported_not_assumed(config):
    forecast = forecast_cashflow(
        _transactions(),
        profile=FinancialProfile(monthly_income=100000),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    assert forecast.current_balance is None
    assert forecast.predicted_end_balance is None
    assert any("balance" in line.lower() for line in forecast.uncertainty)


def test_missing_income_makes_inflow_figures_unavailable(config):
    transactions = [tx(1, date(2026, 2, 4), "Grocer", 10000, category="Food and Dining")]
    forecast = forecast_cashflow(
        transactions,
        profile=FinancialProfile(current_balance=40000),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    assert forecast.monthly_income is None
    assert forecast.expected_inflow_remaining is None
    assert forecast.net_savings is None
    assert forecast.predicted_end_balance is None
    assert forecast.income_source == "unavailable"


def test_deficit_and_shortfall_are_flagged(config):
    forecast = forecast_cashflow(
        _transactions(),
        profile=FinancialProfile(
            monthly_income=15000,
            current_balance=5000,
            upcoming_bills=[UpcomingBill(label="Rent", amount=30000)],
        ),
        config=config,
        month="2026-02",
        today=date(2026, 2, 10),
    )
    assert forecast.deficit is True
    assert forecast.predicted_end_balance is not None and forecast.predicted_end_balance < 0
    assert forecast.shortfall == round(-forecast.predicted_end_balance, 2)


def test_forecast_is_deterministic(config):
    def run():
        return forecast_cashflow(
            _transactions(),
            profile=FinancialProfile(monthly_income=100000, current_balance=20000),
            config=config,
            month="2026-02",
            today=date(2026, 2, 10),
        )

    first, second = run(), run()
    assert first.projected_expense == second.projected_expense
    assert first.predicted_end_balance == second.predicted_end_balance
    assert first.projection_method == second.projection_method
