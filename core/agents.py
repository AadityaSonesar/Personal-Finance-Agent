"""Specialized financial agents.

Each agent has one responsibility, explicit dependencies, structured output and
its own failure path. Agents never fabricate results: when an upstream stage is
missing they raise ``DependencyNotMet`` and the orchestrator records a skip.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.advisory import attach_ai_explanations, build_recommendations
from core.alerts import apply_sla, attach_notifications, build_alerts
from core.anomalies import detect_anomalies
from core.budgets import analyze_budgets, budget_summary
from core.constants import AGENT_NAMES
from core.models import (
    Alert,
    Anomaly,
    BudgetStatus,
    CashFlowForecast,
    HealthScore,
    Recommendation,
    Subscription,
)
from core.shared_state import AgentFailure, SharedState
from core.spending import available_months, build_spending_intelligence
from core.subscriptions import subscription_summary


class BaseAgent:
    name: str = "base_agent"
    label: str = "Agent"
    requires: tuple[str, ...] = ()

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        raise NotImplementedError

    async def _tool(self, state: SharedState, name: str, params: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(state.call_tool, name, params)


class IngestionAgent(BaseAgent):
    name = "ingestion_agent"
    label = "Ingestion"

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        if state.pending_file:
            data = await self._tool(
                state,
                "parse_statement",
                {"dataset_id": state.dataset_id, "file_path": state.pending_file},
            )
            state.ingestion = data
            state.note(
                f"Parsed {data.get('rows_extracted', 0)} row(s) from {data.get('file_name', 'the statement')}."
            )
            if data.get("requires_manual_review"):
                state.note("PDF extraction was incomplete; parsed rows require manual verification.")
            return (
                {
                    "file_name": data.get("file_name"),
                    "file_type": data.get("file_type"),
                    "rows_extracted": data.get("rows_extracted", 0),
                    "requires_manual_review": data.get("requires_manual_review", False),
                },
                f"Ingested {data.get('rows_extracted', 0)} row(s) from {data.get('file_name', 'statement')}",
            )

        staging = state.dataset.load_staging()
        staged_rows = staging.get("rows") or []
        meta = staging.get("meta") or {}
        existing = state.dataset.load_transactions()
        if not staged_rows and not existing:
            raise AgentFailure(
                "No statement is staged and no validated transactions exist. Upload a statement before running the analysis."
            )
        state.ingestion = {
            "file_name": meta.get("file_name", ""),
            "rows_extracted": len(staged_rows),
            "requires_manual_review": bool(meta.get("requires_manual_review", False)),
            "already_validated": bool(existing),
        }
        summary = (
            f"{len(staged_rows)} staged row(s) ready for validation"
            if staged_rows
            else f"Using {len(existing)} previously validated transactions"
        )
        return (dict(state.ingestion), summary)


class ValidationAgent(BaseAgent):
    name = "validation_agent"
    label = "Validation"
    requires = ("ingestion_agent",)

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("ingestion_agent")
        staging = state.dataset.load_staging()
        staged_rows = staging.get("rows") or []

        if staged_rows:
            data = await self._tool(state, "validate_transactions", {"dataset_id": state.dataset_id})
            state.validation = data
        else:
            existing = state.dataset.load_transactions()
            if not existing:
                raise AgentFailure("No staged rows and no stored transactions are available.")
            stats = state.dataset.load_artifact("validation", {}) or {}
            state.validation = {
                "stats": stats,
                "added": 0,
                "skipped_existing": 0,
                "total_transactions": len(existing),
                "warnings": ["Validation used previously stored transactions; no new staged batch was present."],
            }
            state.note(state.validation["warnings"][0])

        state.transactions = state.dataset.load_transactions()
        if not state.transactions:
            raise AgentFailure(
                "No transaction could be validated. Review rejected_records.csv before re-running the analysis."
            )

        stats = state.validation.get("stats", {}) or {}
        rejected = int(stats.get("rejected", 0) or 0)
        if rejected:
            state.note(f"{rejected} record(s) were quarantined during validation; see Audit → Rejected records.")
        if state.validation.get("warnings"):
            for warning in state.validation["warnings"][:3]:
                state.note(str(warning))
        if state.validation.get("requires_manual_review"):
            state.note("Statement extraction requires manual review before results are relied upon.")

        return (
            {
                "stats": stats,
                "total_transactions": len(state.transactions),
                "added": state.validation.get("added", 0),
            },
            f"Validated {stats.get('validated', len(state.transactions))} record(s); "
            f"{rejected} quarantined",
        )


class CategorizationAgent(BaseAgent):
    name = "categorization_agent"
    label = "Categorization"
    requires = ("validation_agent",)

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("validation_agent")
        data = await self._tool(
            state,
            "categorize_transactions",
            {"dataset_id": state.dataset_id, "use_ai": bool(state.use_ai)},
        )
        state.transactions = state.dataset.load_transactions()
        state.ai_meta = data.get("ai", {}) or state.ai_meta
        sources = data.get("counts_by_source", {}) or {}
        summaries = ", ".join(f"{k}={v}" for k, v in sorted(sources.items()))
        if data.get("unresolved"):
            state.note(
                f"{data['unresolved']} transaction(s) remain uncategorized; assign categories to teach merchant rules."
            )
        return (
            {
                "counts_by_source": sources,
                "by_category": data.get("by_category", {}),
                "unresolved": data.get("unresolved", 0),
                "ai": state.ai_meta,
            },
            f"Categorized {data.get('total', 0)} transaction(s) ({summaries})",
        )


class SubscriptionAgent(BaseAgent):
    name = "subscription_agent"
    label = "Subscription intelligence"
    requires = ("validation_agent",)

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("validation_agent")
        data = await self._tool(state, "detect_subscriptions", {"dataset_id": state.dataset_id})
        subscriptions: list[Subscription] = []
        for item in data.get("subscriptions", []) or []:
            try:
                subscriptions.append(Subscription.model_validate(item))
            except Exception:
                continue
        state.subscriptions = subscriptions
        summary = data.get("summary", {}) or subscription_summary(subscriptions)
        if summary.get("needs_verification"):
            state.note(
                f"{summary['needs_verification']} recurring payment(s) need verification; usage data is not available "
                "from statements."
            )
        return (
            {"count": len(subscriptions), "summary": summary},
            f"Found {len(subscriptions)} recurring payment(s) "
            f"({summary.get('confirmed', 0)} confirmed, {summary.get('potential', 0)} potential, "
            f"{summary.get('needs_verification', 0)} needing verification)",
        )


class AnomalyAgent(BaseAgent):
    name = "anomaly_agent"
    label = "Anomaly detection"
    requires = ("categorization_agent",)

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("categorization_agent")
        params: dict[str, Any] = {"dataset_id": state.dataset_id}
        if state.month:
            params["reference_month"] = state.month
        data = await self._tool(state, "detect_anomalies", params)
        findings: list[Anomaly] = []
        for item in data.get("anomalies", []) or []:
            try:
                findings.append(Anomaly.model_validate(item))
            except Exception:
                continue
        # The tool truncates its response; reload the persisted set for completeness.
        stored = state.dataset.load_artifact("anomalies", []) or []
        findings = []
        for item in stored:
            try:
                findings.append(Anomaly.model_validate(item))
            except Exception:
                continue
        state.anomalies = findings
        critical = sum(1 for a in findings if a.severity in {"critical", "high"})
        if critical:
            state.note(f"{critical} anomaly signal(s) are high or critical and require review.")
        return (
            {
                "count": len(findings),
                "summary": data.get("summary", {}),
                "high_or_critical": critical,
            },
            f"Detected {len(findings)} anomaly signal(s), {critical} high or critical",
        )


class SpendingAgent(BaseAgent):
    name = "spending_intelligence_agent"
    label = "Spending intelligence"
    requires = ("categorization_agent",)

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("categorization_agent")
        transactions = state.transactions or state.dataset.load_transactions()
        month = state.month or (available_months(transactions)[-1] if transactions else None)
        if not month:
            raise AgentFailure("No month is available for spending analysis.")
        state.month = month
        spending = build_spending_intelligence(transactions, reference_month=month)
        if not spending.get("available"):
            raise AgentFailure(spending.get("reason", "Spending intelligence could not be computed."))
        state.spending = spending
        state.dataset.save_artifact("spending", spending)
        highest = spending.get("highest_category") or {}
        return (
            {
                "month": month,
                "month_expense": spending.get("month_expense"),
                "month_income": spending.get("month_income"),
                "highest_category": highest.get("category"),
                "months_covered": spending.get("months_covered"),
            },
            f"Analysed {month}: spend {spending.get('month_expense', 0):,.0f}, "
            f"top category {highest.get('category', 'n/a')}",
        )


class BudgetAgent(BaseAgent):
    name = "budget_agent"
    label = "Budget analysis"
    requires = ("categorization_agent",)

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("categorization_agent")
        transactions = state.transactions or state.dataset.load_transactions()
        budgets = state.dataset.effective_budgets()
        month = state.month or (available_months(transactions)[-1] if transactions else None)
        statuses = analyze_budgets(transactions, budgets, month=month, config=state.config)
        state.budgets = statuses
        state.month = month
        state.dataset.save_artifact("budgets_analysis", [s.model_dump(mode="json") for s in statuses])
        summary = budget_summary(statuses, month)
        if summary["over_budget_count"]:
            state.note(f"{summary['over_budget_count']} category budget(s) exceeded.")
        return (
            {"month": month, "summary": summary, "count": len(statuses)},
            f"Checked {len(statuses)} category budget(s); {summary['over_budget_count']} over limit",
        )


class FinancialScoringAgent(BaseAgent):
    name = "financial_scoring_agent"
    label = "Financial health scoring"
    requires = ("spending_intelligence_agent", "budget_agent")

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("spending_intelligence_agent", "budget_agent")
        params: dict[str, Any] = {"dataset_id": state.dataset_id}
        if state.month:
            params["reference_month"] = state.month
        data = await self._tool(state, "calculate_financial_health", params)
        health_payload = data.get("health") or state.dataset.load_artifact("health", {}) or {}
        if not health_payload:
            raise AgentFailure("Health scoring produced no result.")
        state.health = HealthScore.model_validate(health_payload)
        statuses = state.dataset.load_artifact("budgets_analysis", []) or []
        if statuses:
            state.budgets = [BudgetStatus.model_validate(s) for s in statuses]
        score = state.health.score
        if score is None:
            state.note("Financial health could not be scored; required inputs are missing.")
            return (
                {"score": None, "complete": False, "unavailable": state.health.unavailable_components},
                "Health score unavailable with the current inputs",
            )
        if not state.health.complete:
            state.note(
                "Health score is provisional ("
                + ", ".join(state.health.unavailable_components)
                + " unavailable)."
            )
        return (
            {
                "score": score,
                "band": state.health.band,
                "complete": state.health.complete,
                "available_weight": state.health.available_weight,
            },
            f"Health score {score:.1f}/100 ({state.health.band})",
        )


class CashFlowAgent(BaseAgent):
    name = "cashflow_agent"
    label = "Cash flow forecasting"
    requires = ("subscription_agent", "budget_agent")

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("subscription_agent", "budget_agent")
        params: dict[str, Any] = {"dataset_id": state.dataset_id}
        if state.month:
            params["reference_month"] = state.month
        data = await self._tool(state, "calculate_cashflow", params)
        payload = data.get("forecast") or state.dataset.load_artifact("cashflow", {}) or {}
        if not payload:
            raise AgentFailure("Cash flow forecasting produced no result.")
        state.forecast = CashFlowForecast.model_validate(payload)
        if state.forecast.deficit:
            state.note("A cash flow deficit is projected for the selected month.")
        return (
            {
                "month": state.forecast.month,
                "projected_expense": state.forecast.projected_expense,
                "predicted_end_balance": state.forecast.predicted_end_balance,
                "deficit": state.forecast.deficit,
                "method": state.forecast.projection_method,
            },
            f"Forecast for {state.forecast.month}: projected spend "
            f"{state.forecast.projected_expense if state.forecast.projected_expense is not None else 'n/a'}"
            + (", deficit projected" if state.forecast.deficit else ""),
        )


class FinancialAdvisorAgent(BaseAgent):
    name = "advisor_agent"
    label = "Financial advisory"
    requires = (
        "financial_scoring_agent",
        "cashflow_agent",
        "anomaly_agent",
        "subscription_agent",
    )

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require(*self.requires)
        if state.health is None or state.forecast is None:
            raise AgentFailure("Advisory requires a health score and a cash flow forecast.")
        month = state.month or state.forecast.month
        recommendations: list[Recommendation] = build_recommendations(
            month=month,
            spending=state.spending,
            budgets=state.budgets,
            health=state.health,
            forecast=state.forecast,
            anomalies=state.anomalies,
            subscriptions=state.subscriptions,
            config=state.config,
            workflow_id=state.workflow_id,
        )
        ai_meta = attach_ai_explanations(recommendations, config=state.config)
        state.ai_meta = {**state.ai_meta, **ai_meta}
        state.recommendations = recommendations
        state.dataset.save_artifact(
            "recommendations", [r.model_dump(mode="json") for r in recommendations]
        )
        explained = sum(1 for r in recommendations if r.ai_generated)
        if ai_meta.get("available") and ai_meta.get("accepted", 0) == 0 and recommendations:
            state.note("AI interpretations were unavailable; deterministic explanations are shown instead.")
        return (
            {
                "count": len(recommendations),
                "by_priority": _counts(recommendations),
                "ai": {k: v for k, v in ai_meta.items() if k in {"mode", "available", "reason", "accepted", "rejected"}},
                "ai_explanations": explained,
            },
            f"Generated {len(recommendations)} recommendation(s) ({explained} with AI interpretation)",
        )


class NotificationAgent(BaseAgent):
    name = "notification_agent"
    label = "Alerts and notification"
    requires = ("advisor_agent", "anomaly_agent")

    async def run(self, state: SharedState) -> tuple[dict[str, Any], str]:
        state.require("advisor_agent", "anomaly_agent")
        existing = state.dataset.load_artifact("alerts", []) or []
        alerts = build_alerts(
            state.recommendations,
            state.anomalies,
            workflow_id=state.workflow_id,
            config=state.config,
            existing=existing,
        )
        alerts = attach_notifications(alerts, config=state.config)
        alerts = [apply_sla(alert, config=state.config) for alert in alerts]
        state.alerts = alerts
        state.dataset.save_artifact("alerts", [a.model_dump(mode="json") for a in alerts])

        confirmed = sum(1 for a in alerts if a.notification_delivered and not a.resolved_at)
        pending = sum(1 for a in alerts if not a.notification_delivered and not a.resolved_at)
        if pending:
            state.note(f"{pending} alert notification(s) could not be confirmed and are marked undelivered.")
        return (
            {
                "count": len(alerts),
                "by_priority": _alert_counts(alerts),
                "notifications_confirmed": confirmed,
                "notifications_unconfirmed": pending,
            },
            f"Prepared {len(alerts)} alert(s); {confirmed} notification(s) confirmed",
        )


def _counts(recommendations: list[Recommendation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for recommendation in recommendations:
        counts[recommendation.priority] = counts.get(recommendation.priority, 0) + 1
    return counts


def _alert_counts(alerts: list[Alert]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for alert in alerts:
        counts[alert.priority] = counts.get(alert.priority, 0) + 1
    return counts


AGENT_CLASSES = [
    IngestionAgent,
    ValidationAgent,
    CategorizationAgent,
    AnomalyAgent,
    SubscriptionAgent,
    SpendingAgent,
    BudgetAgent,
    FinancialScoringAgent,
    CashFlowAgent,
    FinancialAdvisorAgent,
    NotificationAgent,
]

assert {cls.name for cls in AGENT_CLASSES} == set(AGENT_NAMES), "Agent registry drifted from constants"
