"""Alert triage, SLA tracking, confirmed delivery and audit trail."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.alerts import (
    alert_counts,
    attach_notifications,
    build_alerts,
    deliver_notification,
    sla_state,
    triage,
)
from core.audit import log_event, read_audit_logs
from core.metrics import compute_metrics
from core.models import Alert, Recommendation


def _recommendation(priority: str = "P2") -> Recommendation:
    return Recommendation(
        recommendation_id=f"REC-{priority}",
        title=f"{priority} finding",
        category="budget",
        priority=priority,
        facts=["Food spending is 18,000 against a 12,000 budget."],
        evidence={},
    )


def _alert(priority: str, *, created_minutes_ago: float, resolved_minutes_ago: float | None = None) -> Alert:
    now = datetime.now(timezone.utc)
    created = now - timedelta(minutes=created_minutes_ago)
    deadline = created + timedelta(minutes={"P1": 15, "P2": 60, "P3": 240, "P4": 1440}[priority])
    resolved = now - timedelta(minutes=resolved_minutes_ago) if resolved_minutes_ago is not None else None
    return Alert(
        alert_id=f"AL-{priority}",
        subject="Test alert",
        priority=priority,
        created_at=created,
        deadline=deadline,
        sla_minutes={"P1": 15, "P2": 60, "P3": 240, "P4": 1440}[priority],
        resolved_at=resolved,
        status="resolved" if resolved else "open",
    )


def test_sla_minutes_come_from_configuration(config):
    for priority in ("P1", "P2", "P3", "P4"):
        assessment = triage(_recommendation(priority), config=config)
        assert assessment["sla_minutes"] == config.alerts.sla_minutes[priority]
    assert config.alerts.sla_minutes == {"P1": 15, "P2": 60, "P3": 240, "P4": 1440}


def test_triage_requires_human_review(config):
    assessment = triage(_recommendation("P1"), config=config)
    assert assessment["requires_human"] is True
    assert assessment["confidence"] > 0
    assert assessment["intent"]


def test_sla_states_transition_with_time(config):
    assert sla_state(_alert("P1", created_minutes_ago=5), config=config)["state"] == "on_track"
    assert sla_state(_alert("P1", created_minutes_ago=13), config=config)["state"] == "approaching"
    assert sla_state(_alert("P1", created_minutes_ago=20), config=config)["state"] == "breached"


def test_resolved_alerts_report_met_or_breached(config):
    met = _alert("P2", created_minutes_ago=50, resolved_minutes_ago=20)
    assert sla_state(met, config=config)["state"] == "met"
    late = _alert("P1", created_minutes_ago=60, resolved_minutes_ago=1)
    assert sla_state(late, config=config)["state"] == "breached"


def test_local_notification_delivery_is_confirmed(config):
    alert = _alert("P3", created_minutes_ago=1)
    outcome = deliver_notification(alert, config=config)
    assert outcome["delivered"] is True
    assert outcome["confirmed"] is True
    assert outcome["attempt_count"] == 1
    assert outcome["attempts"][0]["ok"] is True


def test_webhook_failure_retries_then_reports_undelivered(config):
    cfg = config.model_copy(deep=True)
    cfg.alerts.notification.channel = "webhook"
    cfg.alerts.notification.webhook_url = "http://127.0.0.1:9/hook"
    cfg.alerts.notification.retries = 2
    cfg.alerts.notification.base_delay_ms = 1
    cfg.alerts.notification.timeout_seconds = 1

    outcome = deliver_notification(_alert("P2", created_minutes_ago=2), config=cfg)
    assert outcome["delivered"] is False
    assert outcome["confirmed"] is False
    assert outcome["attempt_count"] == 2
    assert all(not attempt["ok"] for attempt in outcome["attempts"])


def test_notifications_are_not_resent(store, config):
    alerts = build_alerts([_recommendation()], [], config=config, workflow_id="WF-TEST")
    first = attach_notifications(alerts, config=config)
    assert first[0].notification_delivered is True
    second = attach_notifications(first, config=config)
    assert len(second[0].notifications) == len(first[0].notifications)


def test_alert_counts_and_critical_anomaly_triage(config):
    from core.models import Anomaly

    anomaly = Anomaly(
        anomaly_id="AN-TEST",
        anomaly_type="future_dated_transaction",
        severity="critical",
        score=1.0,
        title="Future-dated charge",
        evidence=["Date is after today."],
    )
    alerts = build_alerts([], [anomaly], config=config)
    assert alerts and alerts[0].priority == "P1"
    counts = alert_counts(alerts, config=config)
    assert counts["by_priority"]["P1"] == 1
    assert counts["open"] == 1


def test_audit_log_is_append_only_and_filterable(config):
    before = len(read_audit_logs(limit=0, config=config))
    first = log_event(
        actor="test_agent", event_type="agent_execution", workflow_id="WF-A", config=config
    )
    second = log_event(
        actor="test_agent", event_type="agent_execution", workflow_id="WF-B", config=config
    )
    rows = read_audit_logs(limit=0, config=config)
    assert len(rows) >= before + 2
    ids = {row["event_id"] for row in rows}
    assert {first, second}.issubset(ids)
    filtered = read_audit_logs(workflow_id="WF-A", config=config)
    assert filtered and all(row["workflow_id"] == "WF-A" for row in filtered)
    assert all(row["rule_version"] == config.rule_version for row in filtered)


def test_metrics_report_insufficient_data_honestly(store, config):
    from core.storage import create_dataset

    empty = create_dataset("Metrics empty", kind="upload", config=config)
    metrics = compute_metrics(empty, config=config)
    assert metrics["metrics"]["validation_rate"]["available"] is False
    assert metrics["metrics"]["validation_rate"]["display"] == "Insufficient data"
    assert metrics["metrics"]["sla_compliance"]["available"] is False
    assert metrics["counts"]["transactions"] == 0


def test_metrics_use_recorded_tool_calls(store, config):
    log_event(
        actor="detect_anomalies",
        actor_type="tool",
        event_type="tool_call",
        status="ok",
        dataset_id=store.dataset_id,
        latency_ms=12.5,
        config=config,
    )
    metrics = compute_metrics(store, config=config)
    tool_metric = metrics["metrics"]["tool_success"]
    assert tool_metric["available"] is True
    assert tool_metric["value"] == 1.0
    assert metrics["recent_tool_calls"]
