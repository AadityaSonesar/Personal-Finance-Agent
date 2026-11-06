"""System performance metrics computed from real execution records only.

Any metric without observations is reported as unavailable rather than as zero,
so the dashboard never implies measurement that did not happen.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from core.audit import read_audit_logs
from core.config import AppConfig, get_config
from core.formatting import money, percent
from core.models import utcnow
from core.storage import DatasetStore


def _metric(
    label: str,
    *,
    value: float | None = None,
    display: str = "—",
    sample: int = 0,
    unit: str = "",
    note: str = "",
    available: bool | None = None,
) -> dict[str, Any]:
    if available is None:
        available = value is not None and sample > 0
    return {
        "label": label,
        "value": value,
        "display": display if available else "Insufficient data",
        "sample": sample,
        "unit": unit,
        "note": note,
        "available": available,
    }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_metrics(store: DatasetStore, *, config: AppConfig | None = None) -> dict[str, Any]:
    config = config or get_config()
    transactions = store.load_transactions()
    rejected = store.load_rejected()
    workflows = store.load_workflows()
    anomalies = store.load_artifact("anomalies", []) or []
    subscriptions = store.load_artifact("subscriptions", []) or []
    recommendations = store.load_artifact("recommendations", []) or []
    alerts = store.load_artifact("alerts", []) or []
    approvals = store.load_approvals()
    validation = store.load_artifact("validation", {}) or {}
    audit_rows = read_audit_logs(limit=0, dataset_id=store.dataset_id, config=config)

    metrics: dict[str, Any] = {}

    # -- data quality ------------------------------------------------------
    received = int(validation.get("rows_received", 0) or 0)
    validated = int(validation.get("validated", 0) or 0)
    rejected_count = len(rejected)
    if received > 0:
        rate = round(validated / received, 4)
        metrics["validation_rate"] = _metric(
            "Data validation rate",
            value=rate,
            display=f"{rate * 100:.1f}%",
            sample=received,
            note=f"{validated} of {received} rows validated.",
        )
    else:
        metrics["validation_rate"] = _metric(
            "Data validation rate", note="No ingestion has been recorded for this dataset yet.", available=False
        )

    metrics["rejected_records"] = _metric(
        "Rejected records",
        value=float(rejected_count),
        display=f"{rejected_count}",
        sample=received or rejected_count,
        note="Rows quarantined in rejected_records.csv with reasons." if rejected_count else "No rejected rows.",
    )
    duplicates = int(validation.get("duplicates_rejected", 0) or 0) + int(validation.get("duplicate_suspects", 0) or 0)
    metrics["duplicates"] = _metric(
        "Duplicate detections",
        value=float(duplicates),
        display=f"{duplicates}",
        sample=received,
        note="Duplicate IDs quarantined plus same-day duplicate suspects flagged.",
    )

    # -- agent latency -----------------------------------------------------
    agent_latencies: dict[str, list[float]] = defaultdict(list)
    for workflow in workflows:
        for agent in workflow.get("agents", []) or []:
            if agent.get("status") in {"ok", "failed"} and agent.get("duration_ms") is not None:
                agent_latencies[str(agent.get("agent"))].append(float(agent["duration_ms"]))
    agent_table = [
        {
            "agent": name,
            "average_ms": round(sum(values) / len(values), 1),
            "runs": len(values),
            "max_ms": round(max(values), 1),
        }
        for name, values in sorted(agent_latencies.items())
    ]
    if agent_table:
        overall = [v for values in agent_latencies.values() for v in values]
        metrics["agent_latency"] = _metric(
            "Average agent latency",
            value=round(sum(overall) / len(overall), 1),
            display=f"{sum(overall) / len(overall):.0f} ms",
            sample=len(overall),
            unit="ms",
            note=f"Across {len(agent_latencies)} agent(s).",
        )
    else:
        metrics["agent_latency"] = _metric(
            "Average agent latency", note="No agent executions recorded yet.", available=False
        )

    parallel = [float(w.get("parallel_ms", 0)) for w in workflows if float(w.get("parallel_ms", 0) or 0) > 0]
    sequential = [float(w.get("sequential_ms", 0)) for w in workflows if float(w.get("sequential_ms", 0) or 0) > 0]
    metrics["parallel_latency"] = _metric(
        "Parallel stage latency",
        value=round(sum(parallel) / len(parallel), 1) if parallel else None,
        display=f"{sum(parallel) / len(parallel):.0f} ms" if parallel else "—",
        sample=len(parallel),
        unit="ms",
        note="Wall-clock time of the concurrent analysis stage.",
        available=bool(parallel),
    )
    metrics["sequential_latency"] = _metric(
        "Sequential stage latency",
        value=round(sum(sequential) / len(sequential), 1) if sequential else None,
        display=f"{sum(sequential) / len(sequential):.0f} ms" if sequential else "—",
        sample=len(sequential),
        unit="ms",
        note="Sum of dependent stages (scoring → forecast → advisory → alerts).",
        available=bool(sequential),
    )

    # -- workflow and tool health -----------------------------------------
    statuses = [str(w.get("status", "")) for w in workflows]
    if statuses:
        completed = sum(1 for s in statuses if s == "completed")
        success_rate = round(completed / len(statuses), 4)
        metrics["workflow_success"] = _metric(
            "Workflow success rate",
            value=success_rate,
            display=f"{success_rate * 100:.1f}%",
            sample=len(statuses),
            note=f"{completed} of {len(statuses)} runs completed without a failing stage.",
        )
    else:
        metrics["workflow_success"] = _metric(
            "Workflow success rate", note="No workflow runs recorded yet.", available=False
        )

    tool_rows = [row for row in audit_rows if row.get("event_type") == "tool_call"]
    if tool_rows:
        ok = sum(1 for row in tool_rows if row.get("status") == "ok")
        tool_rate = round(ok / len(tool_rows), 4)
        metrics["tool_success"] = _metric(
            "Tool execution success rate",
            value=tool_rate,
            display=f"{tool_rate * 100:.1f}%",
            sample=len(tool_rows),
            note=f"{ok} of {len(tool_rows)} tool calls succeeded.",
        )
    else:
        metrics["tool_success"] = _metric(
            "Tool execution success rate", note="No tool calls recorded yet.", available=False
        )

    # -- analysis outputs --------------------------------------------------
    critical = sum(1 for a in anomalies if a.get("severity") in {"critical", "high"})
    metrics["anomalies"] = _metric(
        "Anomalies detected",
        value=float(len(anomalies)),
        display=f"{len(anomalies)}",
        sample=max(len(transactions), 1),
        note=f"{critical} rated high or critical; {len(subscriptions)} recurring payments evaluated.",
    )

    # -- alerts and human review ------------------------------------------
    resolved = [a for a in alerts if a.get("resolved_at")]
    if resolved:
        minutes = []
        for alert in resolved:
            created = _parse_ts(alert.get("created_at"))
            done = _parse_ts(alert.get("resolved_at"))
            if created and done:
                minutes.append(max(0.0, (done - created).total_seconds() / 60))
        if minutes:
            avg = round(sum(minutes) / len(minutes), 1)
            metrics["alert_resolution"] = _metric(
                "Average alert resolution time",
                value=avg,
                display=f"{avg:.0f} min",
                sample=len(minutes),
                unit="min",
                note="From alert creation to recorded resolution.",
            )
        else:
            metrics["alert_resolution"] = _metric(
                "Average alert resolution time", note="Resolved alerts lack parseable timestamps.", available=False
            )
    else:
        metrics["alert_resolution"] = _metric(
            "Average alert resolution time", note="No alerts have been resolved yet.", available=False
        )

    if resolved:
        within = 0
        for alert in resolved:
            created = _parse_ts(alert.get("created_at"))
            done = _parse_ts(alert.get("resolved_at"))
            deadline = _parse_ts(alert.get("deadline"))
            if created and done and deadline and done <= deadline:
                within += 1
        compliance = round(within / len(resolved), 4)
        metrics["sla_compliance"] = _metric(
            "SLA compliance",
            value=compliance,
            display=f"{compliance * 100:.1f}%",
            sample=len(resolved),
            note=f"{within} of {len(resolved)} resolved alerts met their deadline.",
        )
    else:
        metrics["sla_compliance"] = _metric(
            "SLA compliance", note="Compliance needs at least one resolved alert.", available=False
        )

    total_pending = len(recommendations)
    decided_ids = {str(a.get("recommendation_id")) for a in approvals}
    if total_pending:
        decided = sum(1 for rec in recommendations if str(rec.get("recommendation_id")) in decided_ids)
        rate = round(decided / total_pending, 4)
        metrics["review_completion"] = _metric(
            "Human review completion",
            value=rate,
            display=f"{rate * 100:.1f}%",
            sample=total_pending,
            note=f"{decided} of {total_pending} recommendations carry a reviewer decision.",
        )
    else:
        metrics["review_completion"] = _metric(
            "Human review completion", note="No recommendations have been generated yet.", available=False
        )

    breached = 0
    now = utcnow()
    for alert in alerts:
        deadline = _parse_ts(alert.get("deadline"))
        if deadline and not alert.get("resolved_at") and now > deadline:
            breached += 1

    return {
        "metrics": metrics,
        "agent_table": agent_table,
        "counts": {
            "transactions": len(transactions),
            "rejected": rejected_count,
            "anomalies": len(anomalies),
            "subscriptions": len(subscriptions),
            "recommendations": len(recommendations),
            "alerts": len(alerts),
            "alerts_open": sum(1 for a in alerts if not a.get("resolved_at")),
            "alerts_breached": breached,
            "workflows": len(workflows),
            "audit_events": len(audit_rows),
        },
        "recent_tool_calls": [row for row in audit_rows if row.get("event_type") == "tool_call"][:25],
        "recent_failures": [row for row in audit_rows if row.get("status") == "failed"][:15],
    }


def metric_value(metrics: dict[str, Any], key: str) -> float | None:
    entry = metrics.get("metrics", {}).get(key, {})
    value = entry.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def format_metric(entry: dict[str, Any]) -> str:
    return str(entry.get("display", "—"))


def sample_note(entry: dict[str, Any]) -> str:
    if not entry.get("available"):
        return entry.get("note") or "Not enough data yet."
    return f"{entry.get('note', '')} (n={entry.get('sample', 0)})".strip()
