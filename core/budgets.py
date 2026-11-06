"""Budget analysis: actual versus configured limits."""

from __future__ import annotations

from typing import Any

from core.config import AppConfig, get_config
from core.constants import EXPENSE_CATEGORIES
from core.models import BudgetStatus, Transaction
from core.spending import available_months, month_expense


def analyze_budgets(
    transactions: list[Transaction],
    budgets: dict[str, float],
    *,
    month: str | None = None,
    config: AppConfig | None = None,
) -> list[BudgetStatus]:
    """Compare the selected month's expenditure against category limits."""
    config = config or get_config()
    warning_ratio = config.financial.scoring.budget_warning_ratio
    months = available_months(transactions)
    if month is None:
        month = months[-1] if months else None

    actual_by_category: dict[str, float] = {}
    if month:
        for transaction in transactions:
            if transaction.transaction_type == "expense" and transaction.month_key == month:
                actual_by_category[transaction.category] = round(
                    actual_by_category.get(transaction.category, 0.0) + transaction.amount, 2
                )

    categories = set(budgets.keys()) | set(actual_by_category.keys())
    categories = {c for c in categories if c in EXPENSE_CATEGORIES}
    total_actual = sum(actual_by_category.values()) or 1.0

    statuses: list[BudgetStatus] = []
    for category in sorted(categories, key=lambda c: actual_by_category.get(c, 0.0), reverse=True):
        limit = float(budgets.get(category, 0.0) or 0.0)
        actual = round(actual_by_category.get(category, 0.0), 2)
        utilization = (actual / limit) if limit > 0 else (1.0 if actual > 0 else 0.0)
        overrun = round(max(0.0, actual - limit), 2) if limit > 0 else actual

        if limit <= 0 and actual > 0:
            state = "unbudgeted"
        elif limit > 0 and actual > limit:
            state = "over"
        elif limit > 0 and utilization >= warning_ratio:
            state = "warning"
        else:
            state = "ok"

        statuses.append(
            BudgetStatus(
                category=category,
                limit=round(limit, 2),
                actual=actual,
                utilization=round(utilization, 4),
                remaining=round(limit - actual, 2),
                status=state,  # type: ignore[arg-type]
                overrun=overrun,
                share_of_total=round(actual / total_actual, 4),
            )
        )

    statuses.sort(key=lambda s: (s.status == "over", s.utilization), reverse=True)
    return statuses


def budget_summary(statuses: list[BudgetStatus], month: str | None = None) -> dict[str, Any]:
    total_limit = round(sum(s.limit for s in statuses), 2)
    total_actual = round(sum(s.actual for s in statuses), 2)
    over = [s for s in statuses if s.status == "over"]
    warning = [s for s in statuses if s.status == "warning"]
    unbudgeted = [s for s in statuses if s.status == "unbudgeted"]
    return {
        "month": month,
        "total_limit": total_limit,
        "total_actual": total_actual,
        "utilization": round(total_actual / total_limit, 4) if total_limit > 0 else None,
        "remaining": round(total_limit - total_actual, 2),
        "over_budget_count": len(over),
        "warning_count": len(warning),
        "unbudgeted_count": len(unbudgeted),
        "overrun_total": round(sum(s.overrun for s in statuses), 2),
        "over_budget": [
            {
                "category": s.category,
                "limit": s.limit,
                "actual": s.actual,
                "overrun": s.overrun,
                "overrun_ratio": round(s.overrun / s.limit, 4) if s.limit > 0 else None,
            }
            for s in over
        ],
    }


def adherence_score(statuses: list[BudgetStatus]) -> tuple[float | None, dict[str, Any]]:
    """Weighted budget adherence normalized to 0..1.

    Each category contributes ``min(1, limit / actual)`` weighted by the larger
    of its limit and its actual spend, so hiding spend in an unbudgeted category
    cannot inflate the score.
    """
    usable = [s for s in statuses if s.limit > 0 or s.actual > 0]
    if not usable:
        return None, {"reason": "No budgets or expenditure available."}

    weighted_sum = 0.0
    weight_total = 0.0
    for status in usable:
        weight = max(status.limit, status.actual)
        if weight <= 0:
            continue
        ratio = 1.0 if status.actual <= status.limit else (status.limit / status.actual if status.actual else 1.0)
        weighted_sum += weight * ratio
        weight_total += weight

    if weight_total <= 0:
        return None, {"reason": "No positive budget weights available."}
    score = weighted_sum / weight_total
    return round(max(0.0, min(1.0, score)), 4), {
        "weighted_ratio": round(score, 4),
        "categories_scored": len(usable),
        "over_budget": sum(1 for s in usable if s.actual > s.limit),
    }


def latest_month_expense(transactions: list[Transaction]) -> float:
    months = available_months(transactions)
    if not months:
        return 0.0
    return month_expense(transactions, months[-1])
