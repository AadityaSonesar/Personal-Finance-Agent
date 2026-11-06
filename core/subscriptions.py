"""Recurring-payment discovery.

The engine reports regular payment patterns only. It never claims a subscription
is unused, because usage data is not part of a bank statement: anything overdue
is surfaced as "needs verification" with the exact dates that triggered it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from core.categorization import normalize_merchant
from core.config import AppConfig, get_config
from core.models import Subscription, Transaction
from core.security import hash_identifier


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _nearest_period(interval: float, periods: list[int]) -> tuple[int, float]:
    best_period = periods[0]
    best_deviation = float("inf")
    for period in periods:
        deviation = abs(interval - period) / period
        if deviation < best_deviation:
            best_period, best_deviation = period, deviation
    return best_period, best_deviation


def detect_subscriptions(
    transactions: list[Transaction],
    *,
    config: AppConfig | None = None,
    today: date | None = None,
) -> list[Subscription]:
    """Identify confirmed, potential and verification-needed recurring charges."""
    config = config or get_config()
    cfg = config.financial.subscriptions
    today = today or date.today()
    cutoff = today - timedelta(days=cfg.lookback_days)

    expenses = [
        t for t in transactions
        if t.transaction_type == "expense" and t.transaction_date >= cutoff
    ]
    if not expenses:
        return []

    groups: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in expenses:
        key = normalize_merchant(transaction.merchant)
        if key:
            groups[key].append(transaction)

    subscriptions: list[Subscription] = []
    for key, group in groups.items():
        if len(group) < cfg.min_occurrences_potential:
            continue
        group.sort(key=lambda t: t.transaction_date)
        dates = [t.transaction_date for t in group]
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        intervals = [i for i in intervals if i > 0]
        if not intervals:
            continue

        median_interval = _median([float(i) for i in intervals])
        if median_interval <= 0:
            continue
        period, period_deviation = _nearest_period(median_interval, cfg.periods_days)

        mean_interval = float(statistics.fmean(intervals))
        interval_cv = (statistics.pstdev([float(i) for i in intervals]) / mean_interval) if mean_interval else 1.0
        amounts = [t.amount for t in group]
        mean_amount = float(statistics.fmean(amounts))
        amount_cv = (statistics.pstdev(amounts) / mean_amount) if mean_amount else 1.0

        interval_score = max(0.0, 1.0 - (period_deviation / cfg.interval_tolerance_ratio))
        regularity_score = max(0.0, 1.0 - (interval_cv / max(cfg.interval_tolerance_ratio, 0.01)))
        amount_score = max(0.0, 1.0 - (amount_cv / max(cfg.amount_tolerance_ratio, 0.01)))
        confidence = round(
            min(
                1.0,
                0.30 * interval_score
                + 0.20 * regularity_score
                + 0.35 * amount_score
                + 0.15 * min(1.0, len(group) / 6.0),
            ),
            3,
        )

        period_ok = period_deviation <= cfg.interval_tolerance_ratio
        amount_ok = amount_cv <= cfg.amount_tolerance_ratio

        if len(group) >= cfg.min_occurrences and period_ok and amount_ok:
            status = "confirmed_recurring"
        elif len(group) >= cfg.min_occurrences_potential and (period_ok or amount_ok) and confidence >= cfg.minimum_confidence:
            status = "potential_subscription"
        else:
            continue

        last_charge = dates[-1]
        days_since = (today - last_charge).days
        next_expected = last_charge + timedelta(days=int(round(median_interval)))
        usage_note = ""
        if days_since > cfg.needs_verification_after_days:
            status = "needs_verification"
            usage_note = (
                f"No charge in {days_since} days; the expected next charge was {next_expected.isoformat()}. "
                "Usage data is not available, so confirm whether the service is still in use before changing anything."
            )

        category = max(
            {t.category for t in group},
            key=lambda c: sum(1 for t in group if t.category == c),
        )
        evidence = [
            f"{len(group)} charges observed, median interval {median_interval:.0f} days "
            f"(classified as a ~{period}-day cycle).",
            f"Average charge {mean_amount:,.2f} with {amount_cv * 100:.1f}% variation.",
            f"Last charge {last_charge.isoformat()}; next expected {next_expected.isoformat()}.",
        ]
        if usage_note:
            evidence.append(usage_note)

        annualized = mean_amount * (365.0 / median_interval) if median_interval > 0 else 0.0
        subscriptions.append(
            Subscription(
                subscription_id=hash_identifier("sub", key, len(group), prefix="SUB"),
                merchant=group[-1].merchant,
                category=category,
                average_amount=round(mean_amount, 2),
                intervals_days=intervals,
                interval_days=round(median_interval, 1),
                occurrences=len(group),
                first_charge=dates[0],
                last_charge=last_charge,
                next_expected_date=next_expected,
                status=status,
                confidence=confidence,
                usage_verified=False,
                days_since_last_charge=days_since,
                annualized_cost=round(annualized, 2),
                evidence=evidence,
                transaction_ids=[t.transaction_id for t in group],
            )
        )

    subscriptions.sort(key=lambda s: s.annualized_cost, reverse=True)
    return subscriptions


def monthly_recurring_cost(subscriptions: list[Subscription]) -> float:
    """Normalize recurring charges to a monthly figure."""
    total = 0.0
    for subscription in subscriptions:
        if subscription.interval_days > 0:
            total += subscription.average_amount * (30.44 / subscription.interval_days)
    return round(total, 2)


def subscription_summary(subscriptions: list[Subscription]) -> dict[str, Any]:
    by_status: dict[str, int] = defaultdict(int)
    for subscription in subscriptions:
        by_status[subscription.status] += 1
    review = [s for s in subscriptions if s.status == "needs_verification"]
    return {
        "total": len(subscriptions),
        "by_status": dict(by_status),
        "confirmed": by_status.get("confirmed_recurring", 0),
        "potential": by_status.get("potential_subscription", 0),
        "needs_verification": by_status.get("needs_verification", 0),
        "monthly_cost": monthly_recurring_cost(subscriptions),
        "annual_cost": round(sum(s.annualized_cost for s in subscriptions), 2),
        "review_candidates": [
            {
                "merchant": s.merchant,
                "average_amount": s.average_amount,
                "days_since_last_charge": s.days_since_last_charge,
                "next_expected_date": s.next_expected_date.isoformat(),
                "annualized_cost": s.annualized_cost,
                "usage_verified": s.usage_verified,
            }
            for s in review
        ],
    }
