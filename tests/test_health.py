"""Financial health scoring, including missing-data semantics."""

from __future__ import annotations

from datetime import date

from core.health import calculate_health
from core.storage import FinancialProfile
from tests.helpers import tx


def _months():
    return [
        tx(1, date(2026, 1, 1), "Employer", 100000, tx_type="income", category="Income"),
        tx(2, date(2026, 1, 10), "Grocer", 50000, category="Food and Dining"),
        tx(3, date(2026, 2, 1), "Employer", 100000, tx_type="income", category="Income"),
        tx(4, date(2026, 2, 10), "Grocer", 50000, category="Food and Dining"),
    ]


def test_weighted_formula_matches_the_specification(config):
    health = calculate_health(
        _months(),
        profile=FinancialProfile(monthly_income=100000, emergency_fund=50000),
        budgets={"Food and Dining": 50000},
        config=config,
    )
    points = {c.key: c.points for c in health.components}
    # 50% savings rate -> full 40; budgets within limit -> full 30;
    # emergency covers 1 of 3 months -> one third of 30 = 10.
    assert points["savings"] == 40.0
    assert points["budget"] == 30.0
    assert points["emergency"] == 10.0
    assert health.score == 80.0
    assert health.complete is True
    assert health.available_weight == 1.0
    assert health.interpretation


def test_missing_emergency_fund_is_unavailable_not_zero(config):
    health = calculate_health(
        _months(),
        profile=FinancialProfile(monthly_income=100000),
        budgets={"Food and Dining": 50000},
        config=config,
    )
    emergency = next(c for c in health.components if c.key == "emergency")
    assert emergency.available is False
    assert emergency.points is None
    assert "not provided" in emergency.missing_reason
    assert health.complete is False
    assert health.available_weight == 0.7
    assert health.score == 100.0
    assert "Emergency coverage" in health.unavailable_components


def test_no_income_and_no_budgets_yields_insufficient_data(config):
    health = calculate_health(
        [],
        profile=FinancialProfile(),
        budgets={},
        config=config,
    )
    assert health.score is None
    assert health.complete is False
    assert health.band == "Insufficient data"
    assert len(health.unavailable_components) == 3


def test_budget_overruns_reduce_the_component(config):
    transactions = _months()
    health = calculate_health(
        transactions,
        profile=FinancialProfile(monthly_income=100000, emergency_fund=300000),
        budgets={"Food and Dining": 25000},
        config=config,
    )
    budget = next(c for c in health.components if c.key == "budget")
    assert budget.available is True
    # 25,000 / 50,000 adherence ratio halves the component.
    assert budget.points == 15.0
    assert "over" in budget.detail or "weighted" in budget.detail


def test_emergency_coverage_uses_monthly_expense_basis(config):
    health = calculate_health(
        _months(),
        profile=FinancialProfile(monthly_income=100000, emergency_fund=150000),
        budgets={"Food and Dining": 50000},
        config=config,
    )
    emergency = next(c for c in health.components if c.key == "emergency")
    assert emergency.metric == 3.0
    assert emergency.points == 30.0


def test_provisional_score_is_labelled(config):
    health = calculate_health(
        _months(),
        profile=FinancialProfile(monthly_income=100000),
        budgets={},
        config=config,
    )
    assert health.complete is False
    assert "Provisional" in health.interpretation


def test_scoring_is_deterministic(config):
    def run():
        return calculate_health(
            _months(),
            profile=FinancialProfile(monthly_income=100000, emergency_fund=50000),
            budgets={"Food and Dining": 50000},
            config=config,
        )

    first, second = run(), run()
    assert first.score == second.score
    assert [c.points for c in first.components] == [c.points for c in second.components]
    assert first.inputs["monthly_expense"] == second.inputs["monthly_expense"]
