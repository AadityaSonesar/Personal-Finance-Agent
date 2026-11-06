"""Application services used by the dashboard.

Keeps Streamlit code thin and makes the same operations available to tests.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from core.config import AppConfig, get_config
from core.security import safe_data_path, sanitize_filename
from core.sample_data import DEMO_NAME, build_demo_rows, demo_note, demo_statement_csv
from core.storage import (
    DatasetStore,
    active_dataset_id,
    create_dataset,
    data_root,
    get_dataset,
    list_datasets,
    resolve_dataset,
    set_active_dataset,
)
from core.alerts import acknowledge, resolve
from core.categorization import normalize_merchant
from core.governance import DECISION_LABELS, record_decision
from core.models import Alert, Recommendation, Transaction, canonical_category
from core.storage import FinancialProfile
from core.tools import ToolCallFailed, ToolGateway
from core.orchestrator import WorkflowOrchestrator


def current_store(config: AppConfig | None = None) -> DatasetStore | None:
    return resolve_dataset(None, config=config)


def ensure_upload_dataset(name: str = "My finances", config: AppConfig | None = None) -> DatasetStore:
    config = config or get_config()
    existing = resolve_dataset(None, config=config)
    if existing and not existing.is_demo:
        return existing
    store = create_dataset(name, kind="upload", config=config)
    set_active_dataset(store.dataset_id, config=config)
    return store


def demo_store(config: AppConfig | None = None) -> DatasetStore:
    """Return the existing demonstration dataset or create a fresh one."""
    config = config or get_config()
    for meta in list_datasets(config):
        if meta.get("kind") == "demo":
            store = get_dataset(str(meta["id"]), config=config)
            if store:
                return store
    store = create_dataset(DEMO_NAME, kind="demo", note=demo_note(), config=config)
    return store


def stage_demo_rows(store: DatasetStore, *, config: AppConfig | None = None) -> dict[str, Any]:
    """Load demonstration rows into an isolated demo dataset."""
    if not store.is_demo:
        raise ValueError("Demonstration data can only be loaded into a demonstration dataset.")
    config = config or get_config()
    if store.load_transactions() or (store.load_staging().get("rows") or []):
        return {
            "staged": False,
            "message": "Demonstration data is already loaded in this dataset.",
            "dataset_id": store.dataset_id,
        }
    rows, profile = build_demo_rows()
    meta = {
        "file_name": "demo_statement.csv",
        "file_type": "csv",
        "row_count": len(rows),
        "extraction_complete": True,
        "requires_manual_review": False,
        "notes": [demo_note()],
        "warnings": [],
        "staged_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save_staging(rows, meta)
    (store.root / "demo_statement.csv").write_text(demo_statement_csv(rows), encoding="utf-8")
    store.save_profile(FinancialProfile.model_validate(profile))
    set_active_dataset(store.dataset_id, config=config)
    return {"staged": True, "rows": len(rows), "dataset_id": store.dataset_id}


def stage_uploaded_file(
    store: DatasetStore,
    file_name: str,
    payload: bytes,
    *,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Persist an uploaded statement inside the data area and stage it for parsing."""
    config = config or get_config()
    if store.is_demo:
        raise ValueError("Uploads cannot be added to a demonstration dataset. Switch to an uploaded dataset.")
    max_bytes = config.app.max_upload_mb * 1024 * 1024
    if len(payload) > max_bytes:
        raise ValueError(f"File exceeds the {config.app.max_upload_mb} MB limit.")
    safe_name = sanitize_filename(file_name)
    target_name = f"{uuid4().hex[:8]}-{safe_name}"
    relative = f"staging/{target_name}"
    path = safe_data_path(relative, data_root=config.data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "relative_path": relative,
        "file_name": safe_name,
        "bytes": len(payload),
        "dataset_id": store.dataset_id,
    }


def run_analysis(
    store: DatasetStore,
    *,
    pending_file: str | None = None,
    use_ai: bool = True,
    prefer_mcp: bool | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Run the full workflow and return a JSON-safe result."""
    config = config or get_config()
    orchestrator = WorkflowOrchestrator(config, prefer_mcp=prefer_mcp)
    result = asyncio.run(
        orchestrator.run(store, pending_file=pending_file, use_ai=use_ai, trigger="dashboard")
    )
    store.touch()
    return result.model_dump(mode="json")


def save_profile(store: DatasetStore, profile: FinancialProfile) -> None:
    store.save_profile(profile)


def set_category(
    store: DatasetStore,
    transaction_id: str,
    category: str,
    *,
    learn_merchant: bool = True,
) -> dict[str, Any]:
    """Apply a manual category correction and optionally teach the merchant rule."""
    canonical = canonical_category(category)
    transactions = store.load_transactions()
    target = next((t for t in transactions if t.transaction_id == transaction_id), None)
    if target is None:
        raise ValueError("Transaction not found in this dataset.")

    updated = [
        t.model_copy(update={"category": canonical, "category_source": "override"})
        if t.transaction_id == transaction_id
        else t
        for t in transactions
    ]
    store.save_transactions(updated)
    store.set_transaction_category(transaction_id, canonical)
    learned = None
    if learn_merchant and target.transaction_type == "expense":
        learned = normalize_merchant(target.merchant)
        if learned:
            store.set_merchant_preference(learned, canonical)
    return {
        "transaction_id": transaction_id,
        "category": canonical,
        "learned_merchant": learned,
    }


def save_budgets(store: DatasetStore, budgets: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(v)) for k, v in budgets.items()}
    store.save_budget_overrides(clean)
    return store.effective_budgets()


def latest_workflow(store: DatasetStore) -> dict[str, Any] | None:
    workflows = store.load_workflows()
    return workflows[0] if workflows else None


def dataset_status(store: DatasetStore) -> dict[str, Any]:
    transactions = store.load_transactions()
    rejected = store.load_rejected()
    staging = store.load_staging()
    health = store.load_artifact("health", {}) or {}
    return {
        "dataset_id": store.dataset_id,
        "name": store.name,
        "kind": store.kind,
        "is_demo": store.is_demo,
        "transactions": len(transactions),
        "rejected": len(rejected),
        "staged": len(staging.get("rows") or []),
        "has_health": bool(health),
        "source_files": (staging.get("meta") or {}).get("file_name", ""),
    }


def available_dataset_ids(config: AppConfig | None = None) -> list[str]:
    return [str(meta["id"]) for meta in list_datasets(config)]


def review_recommendation(
    store: DatasetStore,
    recommendation: Recommendation,
    *,
    decision: str,
    reviewer: str,
    notes: str = "",
    edited_action: str = "",
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Record a review decision and update the linked alert state."""
    config = config or get_config()
    record = record_decision(
        store,
        recommendation,
        decision=decision,
        reviewer=reviewer,
        notes=notes,
        edited_action=edited_action,
        config=config,
    )
    alerts = [Alert.model_validate(a) for a in (store.load_artifact("alerts", []) or [])]
    updated: list[Alert] = []
    for alert in alerts:
        if alert.recommendation_id == recommendation.recommendation_id and not alert.resolved_at:
            if decision in {"APPROVE", "EDIT"}:
                alert = resolve(
                    alert,
                    resolution=f"{DECISION_LABELS[decision]} by {record.reviewer} — advisory only, no funds moved.",
                    reviewer=record.reviewer,
                )
            else:
                alert = acknowledge(alert, reviewer=record.reviewer).model_copy(
                    update={"resolution": f"{DECISION_LABELS[decision]} — follow-up required."}
                )
        updated.append(alert)
    if updated:
        store.save_artifact("alerts", [a.model_dump(mode="json") for a in updated])
    return record.model_dump(mode="json")


def acknowledge_alert(store: DatasetStore, alert_id: str, *, reviewer: str) -> dict[str, Any]:
    alerts = [Alert.model_validate(a) for a in (store.load_artifact("alerts", []) or [])]
    updated = [acknowledge(a, reviewer=reviewer) if a.alert_id == alert_id and not a.resolved_at else a for a in alerts]
    store.save_artifact("alerts", [a.model_dump(mode="json") for a in updated])
    return {"alert_id": alert_id, "status": "acknowledged"}


def resolve_alert(
    store: DatasetStore, alert_id: str, *, resolution: str, reviewer: str
) -> dict[str, Any]:
    alerts = [Alert.model_validate(a) for a in (store.load_artifact("alerts", []) or [])]
    updated = [
        resolve(a, resolution=resolution, reviewer=reviewer) if a.alert_id == alert_id and not a.resolved_at else a
        for a in alerts
    ]
    store.save_artifact("alerts", [a.model_dump(mode="json") for a in updated])
    return {"alert_id": alert_id, "status": "resolved"}


def generate_report(
    store: DatasetStore,
    *,
    config: AppConfig | None = None,
    prefer_mcp: bool | None = None,
) -> dict[str, Any]:
    """Generate the report package through the tool layer (high-risk authorized tool)."""
    config = config or get_config()
    gateway = ToolGateway(config, prefer_mcp=prefer_mcp)
    envelope = gateway.call(
        "generate_financial_report",
        {"dataset_id": store.dataset_id, "authorized": True},
        authorized=True,
        dataset_id=store.dataset_id,
    )
    if not envelope.get("ok"):
        raise ToolCallFailed("generate_financial_report", envelope.get("error") or {})
    return envelope.get("data") or {}


def read_report(relative_path: str, *, config: AppConfig | None = None) -> str:
    config = config or get_config()
    path = safe_data_path(relative_path, data_root=config.data_root, must_exist=True)
    return path.read_text(encoding="utf-8")
