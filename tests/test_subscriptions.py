"""Recurring payment identification and status semantics."""

from __future__ import annotations

from datetime import date

from core.subscriptions import detect_subscriptions, monthly_recurring_cost
from tests.helpers import monthly_series


def test_four_monthly_charges_are_confirmed(config):
    transactions = monthly_series("Netflix", 649, 5, [(2026, 1), (2026, 2), (2026, 3), (2026, 4)])
    subscriptions = detect_subscriptions(transactions, config=config, today=date(2026, 4, 20))
    assert len(subscriptions) == 1
    subscription = subscriptions[0]
    assert subscription.status == "confirmed_recurring"
    assert subscription.occurrences == 4
    assert subscription.interval_days == 31
    assert subscription.usage_verified is False
    assert subscription.annualized_cost > subscription.average_amount


def test_two_charges_are_only_potential(config):
    transactions = monthly_series("Audible", 199, 8, [(2026, 1), (2026, 2)])
    subscriptions = detect_subscriptions(transactions, config=config, today=date(2026, 3, 1))
    assert len(subscriptions) == 1
    assert subscriptions[0].status == "potential_subscription"


def test_overdue_recurring_charge_needs_verification(config):
    transactions = monthly_series("GymPass", 1500, 5, [(2025, 9), (2025, 10), (2025, 11), (2025, 12)])
    subscriptions = detect_subscriptions(transactions, config=config, today=date(2026, 4, 20))
    assert len(subscriptions) == 1
    subscription = subscriptions[0]
    assert subscription.status == "needs_verification"
    assert subscription.usage_verified is False
    assert subscription.days_since_last_charge > 45
    assert any("Usage data is not available" in line for line in subscription.evidence)


def test_irregular_charges_are_not_confirmed(config):
    from tests.helpers import tx

    transactions = [
        tx(1, date(2026, 1, 5), "Random Shop", 500),
        tx(2, date(2026, 1, 8), "Random Shop", 500),
        tx(3, date(2026, 1, 12), "Random Shop", 500),
    ]
    subscriptions = detect_subscriptions(transactions, config=config, today=date(2026, 1, 20))
    assert all(s.status != "confirmed_recurring" for s in subscriptions)


def test_single_charge_is_not_a_subscription(config):
    transactions = monthly_series("One Off", 900, 5, [(2026, 2)])
    assert detect_subscriptions(transactions, config=config, today=date(2026, 2, 20)) == []


def test_monthly_cost_normalizes_annual_cycles(config):
    transactions = monthly_series("Domain Renewal", 1200, 5, [(2024, 4), (2025, 4)])
    subscriptions = detect_subscriptions(transactions, config=config, today=date(2025, 5, 1))
    assert subscriptions, "annual renewal should be detected"
    monthly = monthly_recurring_cost(subscriptions)
    assert monthly < subscriptions[0].average_amount


def test_detection_is_deterministic(config):
    transactions = monthly_series("Netflix", 649, 5, [(2026, 1), (2026, 2), (2026, 3), (2026, 4)])
    first = detect_subscriptions(transactions, config=config, today=date(2026, 4, 20))
    second = detect_subscriptions(transactions, config=config, today=date(2026, 4, 20))
    assert [s.subscription_id for s in first] == [s.subscription_id for s in second]
    assert [s.confidence for s in first] == [s.confidence for s in second]
