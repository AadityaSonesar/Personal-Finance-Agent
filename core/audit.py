"""Append-only audit trail.

Records are never updated or deleted. ``audit_log.csv`` lives at the data root so
one workflow can be traced across datasets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.config import AppConfig, get_config
from core.storage import append_csv_rows, data_root, read_csv_rows

AUDIT_FIELDS = [
    "timestamp",
    "workflow_id",
    "event_id",
    "actor",
    "actor_type",
    "event_type",
    "status",
    "priority",
    "health_score",
    "latency_ms",
    "reviewer_action",
    "error",
    "rule_version",
    "dataset_id",
    "detail",
]

ACTOR_TYPES = ("agent", "tool", "system", "human")


def audit_path(config: AppConfig | None = None):
    return data_root(config) / "audit_log.csv"


def log_event(
    *,
    actor: str,
    event_type: str,
    status: str = "ok",
    workflow_id: str = "",
    actor_type: str = "system",
    priority: str = "",
    health_score: float | None = None,
    latency_ms: float | None = None,
    reviewer_action: str = "",
    error: str = "",
    dataset_id: str = "",
    detail: dict[str, Any] | None = None,
    config: AppConfig | None = None,
) -> str:
    """Append one audit record and return its event id."""
    config = config or get_config()
    event_id = f"EV-{uuid4().hex[:12].upper()}"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workflow_id": workflow_id,
        "event_id": event_id,
        "actor": actor,
        "actor_type": actor_type if actor_type in ACTOR_TYPES else "system",
        "event_type": event_type,
        "status": status,
        "priority": priority,
        "health_score": "" if health_score is None else round(float(health_score), 1),
        "latency_ms": "" if latency_ms is None else round(float(latency_ms), 2),
        "reviewer_action": reviewer_action,
        "error": error[:400],
        "rule_version": config.rule_version,
        "dataset_id": dataset_id,
        "detail": detail or {},
    }
    append_csv_rows(audit_path(config), AUDIT_FIELDS, [row])
    return event_id


def read_audit_logs(
    *,
    limit: int = 200,
    workflow_id: str | None = None,
    event_type: str | None = None,
    actor: str | None = None,
    dataset_id: str | None = None,
    status: str | None = None,
    config: AppConfig | None = None,
) -> list[dict[str, Any]]:
    """Return the newest matching audit rows."""
    rows = read_csv_rows(audit_path(config))
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if workflow_id and row.get("workflow_id") != workflow_id:
            continue
        if event_type and row.get("event_type") != event_type:
            continue
        if actor and row.get("actor") != actor:
            continue
        if dataset_id and row.get("dataset_id") != dataset_id:
            continue
        if status and row.get("status") != status:
            continue
        filtered.append(row)
    if limit > 0:
        filtered = filtered[-limit:]
    return list(reversed(filtered))


def count_audit_events(config: AppConfig | None = None) -> int:
    return len(read_csv_rows(audit_path(config)))


def workflow_trace(workflow_id: str, *, config: AppConfig | None = None) -> list[dict[str, Any]]:
    return read_audit_logs(limit=0, workflow_id=workflow_id, config=config)
