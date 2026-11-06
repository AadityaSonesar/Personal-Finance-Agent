"""Report packaging for reviewer-facing exports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.config import AppConfig
from core.storage import DatasetStore


def _line(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def build_report_markdown(store: DatasetStore, config: AppConfig) -> tuple[str, dict[str, Any]]:
    health = store.load_artifact("health", {}) or {}
    forecast = store.load_artifact("cashflow", {}) or {}
    anomalies = store.load_artifact("anomalies", []) or []
    subscriptions = store.load_artifact("subscriptions", []) or []
    recommendations = store.load_artifact("recommendations", []) or []
    alerts = store.load_artifact("alerts", []) or []
    approvals = store.load_approvals()
    validation = store.load_artifact("validation", {}) or {}
    profile = store.load_profile()
    transactions = store.load_transactions()

    generated = datetime.now(timezone.utc)
    lines: list[str] = []
    lines.append(f"# Financial Wellness Report — {store.name}")
    lines.append("")
    lines.append(f"Generated: {generated.isoformat(timespec='seconds')}")
    lines.append(f"Dataset: `{store.dataset_id}` · source: {'DEMONSTRATION DATA' if store.is_demo else 'uploaded statements'}")
    lines.append(f"Rule version: {config.rule_version}")
    lines.append("")

    lines.append("## Data basis")
    lines.append("")
    lines.append(f"- Validated transactions: {len(transactions)}")
    lines.append(f"- Rows received: {_line(validation.get('rows_received'), '0')}")
    lines.append(f"- Rejected rows: {_line(validation.get('rejected'), '0')}")
    lines.append(f"- Duplicate suspects: {_line(validation.get('duplicate_suspects'), '0')}")
    if profile.monthly_income is not None:
        lines.append(f"- Declared monthly income: {profile.monthly_income:,.2f}")
    if profile.emergency_fund is not None:
        lines.append(f"- Declared emergency fund: {profile.emergency_fund:,.2f}")
    if profile.current_balance is not None:
        lines.append(f"- Declared current balance: {profile.current_balance:,.2f}")
    lines.append("")

    lines.append("## Financial health")
    lines.append("")
    if health.get("score") is None:
        lines.append("No health score could be calculated from the available information.")
    else:
        lines.append(
            f"**Score: {health.get('score')}/100 ({_line(health.get('band'))})** — "
            f"{'complete' if health.get('complete') else 'provisional'}"
        )
        lines.append("")
        for component in health.get("components", []) or []:
            if component.get("available"):
                lines.append(
                    f"- {component.get('label')}: {component.get('points')}/"
                    f"{component.get('max_points')} points — {component.get('detail')}"
                )
            else:
                lines.append(
                    f"- {component.get('label')}: unavailable ({component.get('missing_reason')})"
                )
    lines.append("")

    lines.append("## Cash flow")
    lines.append("")
    if forecast:
        lines.append(f"- Month: {_line(forecast.get('month'))}")
        lines.append(f"- Actual expenditure so far: {_line(forecast.get('actual_expense_mtd'))}")
        lines.append(f"- Projected expenditure: {_line(forecast.get('projected_expense'))}")
        lines.append(f"- Predicted month-end balance: {_line(forecast.get('predicted_end_balance'))}")
        lines.append(f"- Projection method: {_line(forecast.get('projection_method'))}")
        for assumption in forecast.get("assumptions", []) or []:
            lines.append(f"  - Assumption: {assumption}")
    else:
        lines.append("No forecast has been calculated yet.")
    lines.append("")

    lines.append("## Anomalies")
    lines.append("")
    if anomalies:
        for anomaly in anomalies[:12]:
            lines.append(
                f"- [{anomaly.get('severity', '').upper()}] {anomaly.get('title')} — "
                f"score {anomaly.get('score')}"
            )
    else:
        lines.append("No anomalies were detected in the analysed data.")
    lines.append("")

    lines.append("## Recurring payments")
    lines.append("")
    if subscriptions:
        for subscription in subscriptions[:12]:
            lines.append(
                f"- {subscription.get('merchant')}: {_line(subscription.get('average_amount'))} every "
                f"~{_line(subscription.get('interval_days'))} days · status "
                f"`{subscription.get('status')}` · usage verified: "
                f"{'yes' if subscription.get('usage_verified') else 'no'}"
            )
    else:
        lines.append("No recurring payment pattern was identified.")
    lines.append("")

    decision_by_rec = {str(a.get("recommendation_id")): a for a in approvals}
    lines.append("## Recommendations and review status")
    lines.append("")
    if recommendations:
        for recommendation in recommendations[:15]:
            decision = decision_by_rec.get(str(recommendation.get("recommendation_id")))
            state = f"{decision.get('decision')} by {decision.get('reviewer')}" if decision else "pending review"
            lines.append(f"### {recommendation.get('priority')} · {recommendation.get('title')}")
            lines.append("")
            lines.append(f"Status: {state}")
            for fact in recommendation.get("facts", []) or []:
                lines.append(f"- {fact}")
            if recommendation.get("ai_explanation"):
                lines.append(f"- AI interpretation: {recommendation.get('ai_explanation')}")
            lines.append("")
    else:
        lines.append("No recommendations have been generated yet.")
    lines.append("")

    lines.append("## Alerts")
    lines.append("")
    if alerts:
        for alert in alerts[:15]:
            lines.append(
                f"- {alert.get('priority')} · {alert.get('subject')} · deadline {_line(alert.get('deadline'))} · "
                f"status {_line(alert.get('status'))} · notification "
                f"{'confirmed' if alert.get('notification_delivered') else 'not confirmed'}"
            )
    else:
        lines.append("No alerts are open.")
    lines.append("")

    lines.append("## Important limitations")
    lines.append("")
    lines.append("- All financial figures above are computed from the dataset described in *Data basis*.")
    lines.append("- Projections are estimates, clearly labelled, and depend on the stated assumptions.")
    lines.append("- Nothing in this report constitutes regulated financial advice or a guarantee of returns.")
    lines.append("- No transaction, subscription or account was modified automatically.")
    lines.append("")

    summary = {
        "transactions": len(transactions),
        "rejected": int(validation.get("rejected", 0) or 0),
        "health_score": health.get("score"),
        "anomalies": len(anomalies),
        "subscriptions": len(subscriptions),
        "recommendations": len(recommendations),
        "alerts": len(alerts),
        "approvals": len(approvals),
    }
    return "\n".join(lines), summary
