"""Personalized recommendations built from verified results.

Every recommendation is generated from computed facts; the AI layer may only add
an interpretation. No recommendation moves money or changes an account.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from core.budgets import budget_summary
from core.config import AppConfig, get_config
from core.constants import PRIORITIES, PRIORITY_LABELS
from core.formatting import money, month_label, percent
from core.llm import AIClient
from core.models import (
    Anomaly,
    BudgetStatus,
    CashFlowForecast,
    HealthScore,
    Recommendation,
    Subscription,
)
from core.security import hash_identifier

INTENT_BY_CATEGORY = {
    "budget": "budget_control",
    "savings": "savings_improvement",
    "emergency": "emergency_reserve",
    "cashflow": "cashflow_risk",
    "fraud": "fraud_review",
    "subscriptions": "subscription_review",
    "spending": "spending_pattern",
    "budget_setup": "budget_setup",
    "health": "structural_health",
}


def _new(
    *,
    title: str,
    category: str,
    priority: str,
    risk: str,
    facts: list[str],
    evidence: dict[str, Any],
    explanation: str,
    benefit: str,
    next_action: str,
    month: str,
    workflow_id: str,
    source_agents: list[str],
) -> Recommendation:
    recommendation_id = hash_identifier("rec", title, category, month, prefix="RC")
    return Recommendation(
        recommendation_id=recommendation_id,
        title=title,
        category=category,
        priority=priority,
        risk_level=risk,
        facts=facts,
        evidence=evidence,
        explanation=explanation,
        expected_benefit=benefit,
        next_action=next_action,
        requires_approval=True,
        source_agents=source_agents,
        workflow_id=workflow_id,
    )


def build_recommendations(
    *,
    month: str,
    spending: dict[str, Any],
    budgets: list[BudgetStatus],
    health: HealthScore,
    forecast: CashFlowForecast,
    anomalies: list[Anomaly],
    subscriptions: list[Subscription],
    config: AppConfig | None = None,
    workflow_id: str = "",
) -> list[Recommendation]:
    config = config or get_config()
    advisory_cfg = config.financial.advisory
    triage = config.alerts.triage
    recommendations: list[Recommendation] = []
    budget_stats = budget_summary(budgets, month)
    month_name = month_label(month)

    # -- 1. budget overruns ------------------------------------------------
    over = [b for b in budgets if b.status == "over" and b.limit > 0]
    over.sort(key=lambda b: b.overrun, reverse=True)
    category_counts = {c["category"]: c["count"] for c in spending.get("category_breakdown", [])}
    for status in over[:2]:
        ratio = status.overrun / status.limit if status.limit else 1.0
        priority = "P2" if ratio >= triage.p2_budget_overrun_ratio else "P3"
        recommendations.append(
            _new(
                title=f"Bring {status.category} spending back within budget",
                category="budget",
                priority=priority,
                risk="medium" if priority == "P2" else "low",
                facts=[
                    f"{month_name} {status.category} spending is {money(status.actual)} against a {money(status.limit)} budget.",
                    f"The category is over budget by {money(status.overrun)} ({percent(ratio)} of its limit).",
                    f"{status.category} represents {percent(status.share_of_total)} of tracked spending this month across {category_counts.get(status.category, 0)} transactions.",
                ],
                evidence={
                    "category": status.category,
                    "limit": status.limit,
                    "actual": status.actual,
                    "overrun": status.overrun,
                    "month": month,
                },
                explanation=(
                    f"Deterministic budget rules compare validated {month_name} transactions with the configured "
                    f"{status.category} limit. Actual spending exceeded the limit; this is measured data, not a projection."
                ),
                benefit=(
                    f"Returning to the limit would recover {money(status.overrun)} this month and lift budget adherence "
                    f"for {status.category}."
                ),
                next_action=(
                    f"Review the {category_counts.get(status.category, 0)} {status.category} transactions this month, "
                    f"then either reduce spend or raise the budget deliberately."
                ),
                month=month,
                workflow_id=workflow_id,
                source_agents=["budget_agent", "advisor_agent"],
            )
        )

    # -- 2. savings rate ---------------------------------------------------
    savings_rate = health.inputs.get("savings_rate")
    income = health.inputs.get("income")
    monthly_expense = health.inputs.get("monthly_expense")
    target_rate = config.financial.scoring.target_savings_rate
    if savings_rate is not None and income and monthly_expense is not None and savings_rate < target_rate:
        gap = max(0.0, target_rate * income - (income - monthly_expense))
        recommendations.append(
            _new(
                title=f"Raise your monthly savings rate toward {target_rate * 100:.0f}%",
                category="savings",
                priority="P2" if savings_rate <= 0 else "P3",
                risk="medium",
                facts=[
                    f"Monthly income is {money(income)} and the measured monthly expense baseline is {money(monthly_expense)}.",
                    f"The current savings rate is {percent(savings_rate)} against a {target_rate * 100:.0f}% target.",
                    f"Closing the gap requires about {money(gap)} more saved per month.",
                ],
                evidence={"savings_rate": savings_rate, "target": target_rate, "gap": round(gap, 2)},
                explanation=(
                    "Savings scoring uses the configured target of "
                    f"{target_rate * 100:.0f}% of income. The rate shown is computed from validated transactions and "
                    "the income figure you provided."
                ),
                benefit=f"Closing the gap adds about {money(gap)} per month to savings, roughly {money(gap * 12)} a year.",
                next_action=(
                    "Decide which categories can absorb the reduction, or schedule an automatic transfer on payday."
                ),
                month=month,
                workflow_id=workflow_id,
                source_agents=["financial_scoring_agent", "advisor_agent"],
            )
        )

    # -- 3. emergency reserve ---------------------------------------------
    emergency = next((c for c in health.components if c.key == "emergency"), None)
    if emergency and emergency.available and emergency.metric is not None and emergency.metric < config.financial.scoring.emergency_months_target:
        coverage = float(emergency.metric)
        target_months = config.financial.scoring.emergency_months_target
        fund = health.inputs.get("emergency_fund") or 0.0
        target_amount = target_months * (monthly_expense or 0.0)
        gap = max(0.0, target_amount - fund)
        recommendations.append(
            _new(
                title=f"Build emergency reserves to cover {target_months} months of expenses",
                category="emergency",
                priority="P2" if coverage < triage.p2_emergency_months else "P3",
                risk="medium",
                facts=[
                    f"Emergency fund is {money(fund)} against {money(target_amount)} needed for {target_months} months.",
                    f"Current coverage is {coverage:.1f} months at {money(monthly_expense)} of monthly expenses.",
                    f"Reaching the target requires {money(gap)} more.",
                ],
                evidence={"coverage_months": coverage, "target_months": target_months, "gap": round(gap, 2)},
                explanation=(
                    "Emergency coverage is measured as the supplied emergency fund divided by the verified monthly "
                    "expense baseline. It is only scored when both values are known."
                ),
                benefit=f"A funded reserve removes the need to borrow when an unexpected {money(monthly_expense)} month occurs.",
                next_action=f"Plan transfers of roughly {money(gap / 6)} per month over six months to close the gap.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["financial_scoring_agent", "advisor_agent"],
            )
        )

    # -- 4. cash flow ------------------------------------------------------
    if forecast.deficit:
        facts = [
            f"Projected expenditure for {month_name} is {money(forecast.projected_expense)} against income of {money(forecast.monthly_income)}.",
            f"Actual spending so far is {money(forecast.actual_expense_mtd)}; {forecast.days_elapsed} of {forecast.days_in_month} days have elapsed.",
        ]
        if forecast.net_savings is not None:
            facts.append(f"Projected net savings for the month is {money(forecast.net_savings)}.")
        if forecast.predicted_end_balance is not None:
            facts.append(f"Predicted month-end balance is {money(forecast.predicted_end_balance)}.")
        if forecast.shortfall:
            facts.append(f"Projected shortfall is {money(forecast.shortfall)}.")
        recommendations.append(
            _new(
                title=f"Close the projected cash flow gap for {month_name}",
                category="cashflow",
                priority="P2",
                risk="high",
                facts=facts,
                evidence=forecast.model_dump(mode="json"),
                explanation=(
                    "The forecast blends the current run rate with historical monthly expenditure and adds the upcoming "
                    "bills you supplied. All inputs are actual figures; the result is explicitly a projection."
                ),
                benefit="Acting before the month ends avoids a shortfall and the associated late-payment or borrowing costs.",
                next_action=(
                    "Defer discretionary purchases, or move the due date of an upcoming bill, then re-run the forecast."
                ),
                month=month,
                workflow_id=workflow_id,
                source_agents=["cashflow_agent", "advisor_agent"],
            )
        )
    elif (
        forecast.upcoming_bills_total > 0
        and forecast.current_balance
        and forecast.upcoming_bills_total >= 0.25 * forecast.current_balance
    ):
        recommendations.append(
            _new(
                title="Prepare for upcoming bills already committed",
                category="cashflow",
                priority="P3",
                risk="medium",
                facts=[
                    f"Upcoming bills total {money(forecast.upcoming_bills_total)}.",
                    f"That is {percent(forecast.upcoming_bills_total / forecast.current_balance)} of the current balance of {money(forecast.current_balance)}.",
                    f"Predicted month-end balance is {money(forecast.predicted_end_balance)}.",
                ],
                evidence={"upcoming_total": forecast.upcoming_bills_total, "balance": forecast.current_balance},
                explanation=(
                    "Upcoming bills you supplied are treated as additional to recorded transactions; the ratio "
                    "compares them with your stated current balance."
                ),
                benefit="Reserving the committed amount keeps discretionary spending from consuming bill money.",
                next_action="Set aside the committed amount before the next discretionary purchase.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["cashflow_agent", "advisor_agent"],
            )
        )

    # -- 5. anomalies ------------------------------------------------------
    by_type: dict[str, list[Anomaly]] = {}
    for anomaly in anomalies:
        by_type.setdefault(anomaly.anomaly_type, []).append(anomaly)

    injections = by_type.get("suspicious_merchant_instruction", [])
    if injections:
        recommendations.append(
            _new(
                title="Review instruction-like text detected inside transaction data",
                category="fraud",
                priority="P1",
                risk="high",
                facts=[
                    f"{len(injections)} transaction(s) contain text that resembles instructions.",
                    f"Highest-risk merchant field: “{injections[0].merchant[:80]}”.",
                    "The text was quarantined as inert data; no instruction was executed and no financial value changed.",
                ],
                evidence={"anomaly_ids": [a.anomaly_id for a in injections], "flags": injections[0].metrics.get("flags", [])},
                explanation=(
                    "Input sanitization flagged phrases that mimic system instructions. Deterministic rules and "
                    "calculations are isolated from uploaded text, so the content cannot alter balances, scores or approvals."
                ),
                benefit="Confirms the data-trust boundary held and documents the anomaly for review.",
                next_action="Verify the merchant text against the source statement, then close the alert with a note.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["anomaly_agent", "advisor_agent"],
            )
        )

    duplicates = by_type.get("duplicate_payment", [])
    if duplicates:
        top = duplicates[0]
        recommendations.append(
            _new(
                title="Verify possible duplicate payments",
                category="fraud",
                priority="P1",
                risk="high",
                facts=[
                    f"{len(duplicates)} possible duplicate payment group(s) were detected.",
                    f"Largest group: {top.merchant} — {money(top.amount)} across {len(top.transaction_ids)} identical charges.",
                    "Duplicate patterns can be billing errors or genuine repeated purchases; they are not proof of fraud.",
                ],
                evidence={"anomaly_ids": [a.anomaly_id for a in duplicates], "merchant": top.merchant},
                explanation=(
                    "Anomaly rules group charges by normalized merchant, exact date and identical amount. Matching "
                    "charges are surfaced for human verification rather than being cancelled automatically."
                ),
                benefit=f"Resolving a duplicate recovers up to {money(top.amount)} and corrects spending baselines.",
                next_action="Check the statement or contact the merchant, then record the outcome in review notes.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["anomaly_agent", "advisor_agent"],
            )
        )

    large = by_type.get("unusually_large_transaction", [])
    if large:
        top = large[0]
        recommendations.append(
            _new(
                title="Review unusually large transactions",
                category="spending",
                priority="P2" if top.score >= 0.65 else "P3",
                risk="medium",
                facts=[
                    f"{len(large)} transaction(s) exceeded the large-payment rule.",
                    f"Largest: {money(top.amount)} at {top.merchant} on {top.date_ref}.",
                    top.evidence[0] if top.evidence else "",
                ],
                evidence={"anomaly_ids": [a.anomaly_id for a in large]},
                explanation=(
                    "Each flagged payment is measured against the median of its own category and the configured "
                    "absolute threshold, so the ratio shown is reproducible from the data."
                ),
                benefit="Confirms whether a large outflow was intended and protects the month's budget.",
                next_action="Confirm the payment was expected; if not, follow your card issuer's dispute process.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["anomaly_agent", "advisor_agent"],
            )
        )

    pattern_anomalies = [
        a for a in anomalies
        if a.anomaly_type in {"spending_spike", "unexpected_category_spend", "unusual_merchant_activity", "repeated_transaction"}
    ]
    if pattern_anomalies:
        top = pattern_anomalies[0]
        recommendations.append(
            _new(
                title="Investigate unusual spending patterns",
                category="spending",
                priority="P3",
                risk="low",
                facts=[
                    f"{len(pattern_anomalies)} pattern anomaly signal(s) were detected.",
                    top.title + ".",
                    top.evidence[0] if top.evidence else "",
                ],
                evidence={"anomaly_ids": [a.anomaly_id for a in pattern_anomalies]},
                explanation=(
                    "Signed comparisons use trailing monthly averages from validated transactions; each ratio is "
                    "recomputed from the stored data on every run."
                ),
                benefit="Early detection of a pattern shift keeps budgets and forecasts accurate.",
                next_action="Open Financial Intelligence to see the merchants and transactions behind the ratio.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["anomaly_agent", "advisor_agent"],
            )
        )

    # -- 6. subscriptions --------------------------------------------------
    review = [s for s in subscriptions if s.status == "needs_verification"]
    if review:
        top = review[0]
        recommendations.append(
            _new(
                title="Verify recurring payments that have gone quiet",
                category="subscriptions",
                priority="P4",
                risk="low",
                facts=[
                    f"{len(review)} recurring payment(s) have not charged recently.",
                    f"{top.merchant} last charged {money(top.average_amount)} on {top.last_charge.isoformat()} — {top.days_since_last_charge} days ago.",
                    f"Annualized cost when active is about {money(top.annualized_cost)}.",
                    "Usage data is not available from statements, so no claim is made that the service is unused.",
                ],
                evidence={"merchants": [s.merchant for s in review]},
                explanation=(
                    "Recurring detection looks at interval regularity and amount stability. A gap since the last charge "
                    "means the pattern needs human verification before any decision."
                ),
                benefit=f"Confirming status prevents paying for something you no longer need, up to {money(top.annualized_cost)} a year.",
                next_action="Check the provider account for usage, then approve, hold or request more information.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["subscription_agent", "advisor_agent"],
            )
        )

    active_subs = [s for s in subscriptions if s.status in {"confirmed_recurring", "potential_subscription"}]
    expensive = [s for s in active_subs if s.annualized_cost >= advisory_cfg.subscription_review_amount]
    if expensive:
        total_annual = sum(s.annualized_cost for s in expensive)
        recommendations.append(
            _new(
                title="Review the recurring commitments you are carrying",
                category="subscriptions",
                priority="P4",
                risk="low",
                facts=[
                    f"{len(expensive)} recurring payment(s) were confirmed or considered likely.",
                    f"Combined annualized cost is about {money(total_annual)}.",
                    f"Largest: {expensive[0].merchant} at {money(expensive[0].average_amount)} per charge.",
                ],
                evidence={"merchants": [s.merchant for s in expensive], "annual_cost": round(total_annual, 2)},
                explanation=(
                    "Amounts are averaged from observed charges and normalized to a yearly figure using the measured "
                    "charge interval; nothing is cancelled automatically."
                ),
                benefit="A deliberate keep-or-drop review avoids paying for overlapping services.",
                next_action="Mark keep or review for each service, then record the decision in Alerts and Approvals.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["subscription_agent", "advisor_agent"],
            )
        )

    # -- 7. budget coverage ------------------------------------------------
    unbudgeted = [b for b in budgets if b.status == "unbudgeted"]
    if unbudgeted:
        total = sum(b.actual for b in unbudgeted)
        names = ", ".join(b.category for b in unbudgeted[:4])
        recommendations.append(
            _new(
                title="Assign budgets to unbudgeted categories",
                category="budget_setup",
                priority="P3",
                risk="low",
                facts=[
                    f"{len(unbudgeted)} categor{'y has' if len(unbudgeted) == 1 else 'ies have'} spending without a budget: {names}.",
                    f"Unbudgeted spending this month totals {money(total)}.",
                    f"Total tracked spending is {money(budget_stats.get('total_actual'))}.",
                ],
                evidence={"categories": [b.category for b in unbudgeted], "amount": round(total, 2)},
                explanation=(
                    "Budget adherence scores spending against configured limits. Without a limit, spending is treated "
                    "as unbudgeted and lowers the adherence score rather than being ignored."
                ),
                benefit="Complete budget coverage makes the adherence score representative of all spending.",
                next_action="Set a monthly limit for each unbudgeted category in Budget Management.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["budget_agent", "advisor_agent"],
            )
        )

    # -- 8. structural health ---------------------------------------------
    if health.score is not None and health.score < 55 and health.complete:
        lowest = min(
            (c for c in health.components if c.available),
            key=lambda c: (c.normalized or 0.0),
        )
        recommendations.append(
            _new(
                title=f"Strengthen the weakest health component: {lowest.label}",
                category="health",
                priority="P3",
                risk="low",
                facts=[
                    f"Overall financial health score is {health.score:.1f}/100 ({health.band}).",
                    f"{lowest.label} contributes {lowest.points:.1f} of {lowest.max_points:.0f} points.",
                    lowest.detail,
                ],
                evidence={"component": lowest.key, "points": lowest.points},
                explanation=(
                    "The overall score is the weighted sum of savings, budget adherence and emergency coverage. "
                    "Improving the lowest component produces the largest score gain."
                ),
                benefit="Raising the weakest component has the largest effect on the overall score.",
                next_action=lowest.improvement or "Address the component with the lowest normalized score.",
                month=month,
                workflow_id=workflow_id,
                source_agents=["financial_scoring_agent", "advisor_agent"],
            )
        )

    # -- rank and bound ----------------------------------------------------
    recommendations.sort(key=lambda r: (PRIORITIES.index(r.priority), -len(r.facts)))
    unique: dict[str, Recommendation] = {}
    for recommendation in recommendations:
        unique.setdefault(recommendation.recommendation_id, recommendation)
    return list(unique.values())[: advisory_cfg.max_recommendations]


def attach_ai_explanations(
    recommendations: list[Recommendation],
    *,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Add validated AI interpretations; keep deterministic text otherwise."""
    config = config or get_config()
    client = AIClient(config)
    status = client.status()
    meta: dict[str, Any] = status.as_dict()

    if not recommendations:
        return meta

    if not status.available:
        for recommendation in recommendations:
            recommendation.ai_status = "deterministic"
            recommendation.ai_generated = False
        return meta

    payload = [recommendation.model_dump(mode="json") for recommendation in recommendations]
    result = client.explain(payload)
    if not result.ok:
        for recommendation in recommendations:
            recommendation.ai_status = "deterministic_fallback"
        meta.update({"error": result.error, "accepted": 0, "rejected": 0})
        return meta

    explanations: dict[str, str] = result.payload.get("explanations", {}) or {}
    for recommendation in recommendations:
        text = explanations.get(recommendation.recommendation_id, "")
        if text:
            recommendation.ai_explanation = text
            recommendation.ai_generated = True
            recommendation.ai_status = "ai"
        else:
            recommendation.ai_status = "rule_based"
    meta.update({"accepted": result.accepted, "rejected": result.rejected, "latency_ms": round(result.latency_ms, 1)})
    return meta


def priority_label(priority: str) -> str:
    return f"{priority} · {PRIORITY_LABELS.get(priority, '')}".strip(" ·")


def intent_for(category: str) -> str:
    return INTENT_BY_CATEGORY.get(category, "general_advisory")


def recommendation_date(recommendation: Recommendation) -> date:
    return recommendation.created_at.date()
