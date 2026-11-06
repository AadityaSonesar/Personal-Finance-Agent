"""Financial health scoring.

    S = 0.40 × Savings + 0.30 × Budget adherence + 0.30 × Emergency coverage

Each component is normalized to 0..1 and mapped to its configured maximum
points. Unavailable inputs are reported as unavailable — never as zero — and
the overall score is renormalized across the components that could be measured.
"""

from __future__ import annotations

from typing import Any

from core.budgets import adherence_score, analyze_budgets
from core.config import AppConfig, get_config
from core.models import (
    BudgetStatus,
    HealthComponent,
    HealthScore,
    Transaction,
)
from core.spending import (
    available_months,
    expense_basis,
    month_expense,
    month_income,
)
from core.storage import FinancialProfile


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _band(score: float, config: AppConfig) -> tuple[str, str]:
    bands = config.financial.scoring.score_bands
    for band in sorted(bands, key=lambda b: b.min, reverse=True):
        if score >= band.min:
            return band.label, band.summary
    return "At risk", "Immediate corrective action is required."


def calculate_health(
    transactions: list[Transaction],
    *,
    profile: FinancialProfile,
    budgets: dict[str, float],
    config: AppConfig | None = None,
    month: str | None = None,
    budget_statuses: list[BudgetStatus] | None = None,
    expense_override: float | None = None,
) -> HealthScore:
    """Compute the explainable health score with explicit missing-data handling."""
    config = config or get_config()
    scoring = config.financial.scoring
    months = available_months(transactions)
    month = month or (months[-1] if months else None)

    monthly_expense, basis_months = expense_basis(transactions, months=config.financial.forecast.history_months)
    if monthly_expense is None:
        monthly_expense = month_expense(transactions, month) if month else None
        if monthly_expense:
            basis_months = [month] if month else []
    if monthly_expense is not None and monthly_expense <= 0:
        monthly_expense = None

    income = profile.monthly_income
    income_source = "profile"
    if income is None:
        observed = month_income(transactions, month) if month else 0.0
        if observed > 0:
            income = observed
            income_source = "observed_transactions"
        else:
            income_source = "unavailable"

    components: list[HealthComponent] = []

    # -- savings -----------------------------------------------------------
    savings_target = scoring.target_savings_rate
    if income and income > 0 and monthly_expense is not None:
        savings_rate = (income - monthly_expense) / income
        normalized = _clamp01(savings_rate / savings_target) if savings_target > 0 else 0.0
        components.append(
            HealthComponent(
                key="savings",
                label="Savings score",
                available=True,
                normalized=round(normalized, 4),
                points=round(normalized * scoring.max_points["savings"], 1),
                max_points=scoring.max_points["savings"],
                weighted_contribution=round(normalized * scoring.weights["savings"] * 100, 1),
                metric=round(savings_rate, 4),
                target=savings_target,
                detail=(
                    f"Monthly savings rate is {savings_rate * 100:.1f}% against a target of "
                    f"{savings_target * 100:.0f}% (income {income:,.0f}, expenses {monthly_expense:,.0f})."
                ),
                improvement=(
                    f"Increase monthly savings by about "
                    f"{max(0.0, savings_target * income - (income - monthly_expense)):,.0f} to reach the target."
                    if savings_rate < savings_target
                    else "Savings rate meets the configured target."
                ),
            )
        )
    else:
        reasons = []
        if not income:
            reasons.append("monthly income is not provided and no income transactions exist")
        if monthly_expense is None:
            reasons.append("monthly expenditure could not be determined")
        components.append(
            HealthComponent(
                key="savings",
                label="Savings score",
                available=False,
                max_points=scoring.max_points["savings"],
                missing_reason="; ".join(reasons) or "insufficient data",
                detail="Savings cannot be scored without verified income and expenditure.",
                improvement="Add your monthly income in Financial Health to enable savings scoring.",
            )
        )

    # -- budget adherence --------------------------------------------------
    statuses = budget_statuses if budget_statuses is not None else analyze_budgets(
        transactions, budgets, month=month, config=config
    )
    budget_norm, budget_meta = adherence_score(statuses)
    if budget_norm is None:
        components.append(
            HealthComponent(
                key="budget",
                label="Budget adherence",
                available=False,
                max_points=scoring.max_points["budget"],
                missing_reason="no category budgets are configured",
                detail="Budget adherence requires at least one category budget.",
                improvement="Set category budgets in Budget Management to enable this component.",
            )
        )
    else:
        over_count = int(budget_meta.get("over_budget", 0))
        components.append(
            HealthComponent(
                key="budget",
                label="Budget adherence",
                available=True,
                normalized=round(budget_norm, 4),
                points=round(budget_norm * scoring.max_points["budget"], 1),
                max_points=scoring.max_points["budget"],
                weighted_contribution=round(budget_norm * scoring.weights["budget"] * 100, 1),
                metric=round(budget_norm, 4),
                target=1.0,
                detail=(
                    f"Weighted adherence across {budget_meta.get('categories_scored', 0)} budget categories; "
                    f"{over_count} categor{'y' if over_count == 1 else 'ies'} over limit."
                ),
                improvement=(
                    "Bring over-budget categories back within their limits."
                    if over_count
                    else "All measured categories are within their limits."
                ),
            )
        )

    # -- emergency coverage ------------------------------------------------
    emergency_target = scoring.emergency_months_target
    if profile.emergency_fund is None:
        components.append(
            HealthComponent(
                key="emergency",
                label="Emergency coverage",
                available=False,
                max_points=scoring.max_points["emergency"],
                missing_reason="emergency fund balance not provided",
                detail="Emergency coverage cannot be assumed: a missing balance is not zero.",
                improvement="Add your emergency fund balance in Financial Health to enable this component.",
            )
        )
    elif monthly_expense is None:
        components.append(
            HealthComponent(
                key="emergency",
                label="Emergency coverage",
                available=False,
                max_points=scoring.max_points["emergency"],
                missing_reason="monthly expenditure could not be determined",
                detail="Coverage months need a reliable monthly expense baseline.",
                improvement="Upload at least two months of transactions to establish a baseline.",
            )
        )
    else:
        coverage_months = profile.emergency_fund / monthly_expense if monthly_expense else 0.0
        normalized = _clamp01(coverage_months / emergency_target) if emergency_target > 0 else 0.0
        components.append(
            HealthComponent(
                key="emergency",
                label="Emergency coverage",
                available=True,
                normalized=round(normalized, 4),
                points=round(normalized * scoring.max_points["emergency"], 1),
                max_points=scoring.max_points["emergency"],
                weighted_contribution=round(normalized * scoring.weights["emergency"] * 100, 1),
                metric=round(coverage_months, 2),
                target=float(emergency_target),
                detail=(
                    f"Emergency fund of {profile.emergency_fund:,.0f} covers "
                    f"{coverage_months:.1f} of {emergency_target} months of expenses "
                    f"({monthly_expense:,.0f}/month)."
                ),
                improvement=(
                    f"Add about {max(0.0, emergency_target * monthly_expense - profile.emergency_fund):,.0f} "
                    "to reach the coverage target."
                    if coverage_months < emergency_target
                    else "Emergency coverage meets the target."
                ),
            )
        )

    # -- combine -----------------------------------------------------------
    available = [c for c in components if c.available and c.normalized is not None]
    weight_total = sum(scoring.weights[c.key] for c in available)
    if available and weight_total > 0:
        weighted = sum(scoring.weights[c.key] * (c.normalized or 0.0) for c in available) / weight_total
        score = round(_clamp01(weighted) * 100, 1)
        label, summary = _band(score, config)
    else:
        score = None
        label = "Insufficient data"
        summary = "No component could be scored from the available information."

    complete = len(available) == 3
    unavailable = [c.label for c in components if not c.available]
    improvements = [c.improvement for c in components if c.available and (c.normalized or 0) < 0.999]
    if unavailable:
        improvements.append(
            "Provide the missing information to score: " + ", ".join(c.label for c in components if not c.available) + "."
        )

    interpretation = summary
    if score is not None and not complete:
        interpretation = (
            f"{summary} Provisional score based on {len(available)} of 3 components "
            f"({weight_total:.0%} of the total weight)."
        )

    return HealthScore(
        score=score,
        complete=complete,
        available_weight=round(weight_total, 4),
        components=components,
        band=label,
        interpretation=interpretation,
        improvements=[i for i in improvements if i][:5],
        unavailable_components=unavailable,
        inputs={
            "month": month,
            "income": income,
            "income_source": income_source,
            "monthly_expense": monthly_expense,
            "expense_basis_months": basis_months,
            "emergency_fund": profile.emergency_fund,
            "budgets_total": round(sum(budgets.values()), 2),
            "savings_rate": (
                round((income - monthly_expense) / income, 4)
                if income and monthly_expense is not None and income > 0
                else None
            ),
        },
        rule_version=config.rule_version,
    )


def health_history_entry(health: HealthScore) -> dict[str, Any]:
    return {
        "computed_at": health.computed_at.isoformat(),
        "score": health.score,
        "complete": health.complete,
        "band": health.band,
        "available_weight": health.available_weight,
        "components": {
            c.key: {"points": c.points, "available": c.available, "normalized": c.normalized}
            for c in health.components
        },
        "rule_version": health.rule_version,
    }
