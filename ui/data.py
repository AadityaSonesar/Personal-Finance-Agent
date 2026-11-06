"""Typed artifact loaders for the dashboard."""

from __future__ import annotations

from typing import Any

from core.models import (
    Alert,
    Anomaly,
    BudgetStatus,
    CashFlowForecast,
    HealthScore,
    Recommendation,
    Subscription,
    Transaction,
)
from core.storage import DatasetStore


def _models(model: type, items: Any) -> list[Any]:
    result: list[Any] = []
    for item in items or []:
        try:
            result.append(model.model_validate(item))
        except Exception:
            continue
    return result


def transactions(store: DatasetStore) -> list[Transaction]:
    return store.load_transactions()


def rejected(store: DatasetStore) -> list[dict[str, str]]:
    return store.load_rejected()


def anomalies(store: DatasetStore) -> list[Anomaly]:
    return _models(Anomaly, store.load_artifact("anomalies", []))


def subscriptions(store: DatasetStore) -> list[Subscription]:
    return _models(Subscription, store.load_artifact("subscriptions", []))


def budgets(store: DatasetStore) -> list[BudgetStatus]:
    return _models(BudgetStatus, store.load_artifact("budgets_analysis", []))


def health(store: DatasetStore) -> HealthScore | None:
    payload = store.load_artifact("health", None)
    if not payload:
        return None
    try:
        return HealthScore.model_validate(payload)
    except Exception:
        return None


def forecast(store: DatasetStore) -> CashFlowForecast | None:
    payload = store.load_artifact("cashflow", None)
    if not payload:
        return None
    try:
        return CashFlowForecast.model_validate(payload)
    except Exception:
        return None


def spending(store: DatasetStore) -> dict[str, Any]:
    return store.load_artifact("spending", {}) or {}


def recommendations(store: DatasetStore) -> list[Recommendation]:
    return _models(Recommendation, store.load_artifact("recommendations", []))


def alerts(store: DatasetStore) -> list[Alert]:
    return _models(Alert, store.load_artifact("alerts", []))


def decisions(store: DatasetStore) -> dict[str, dict[str, Any]]:
    return {str(a.get("recommendation_id")): a for a in store.load_approvals()}


def workflows(store: DatasetStore) -> list[dict[str, Any]]:
    return store.load_workflows()


def health_history(store: DatasetStore) -> list[dict[str, Any]]:
    return store.load_artifact("health_history", []) or []


def validation(store: DatasetStore) -> dict[str, Any]:
    return store.load_artifact("validation", {}) or {}


def ingestion_meta(store: DatasetStore) -> dict[str, Any]:
    return (store.load_staging().get("meta") or {})


def staging_rows(store: DatasetStore) -> list[dict[str, Any]]:
    return store.load_staging().get("rows") or []


def latest_workflow(store: DatasetStore) -> dict[str, Any] | None:
    flows = workflows(store)
    return flows[0] if flows else None


def profile(store: DatasetStore):
    return store.load_profile()
