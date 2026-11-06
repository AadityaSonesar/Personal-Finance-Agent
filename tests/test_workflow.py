"""End-to-end orchestration, failure isolation and determinism."""

from __future__ import annotations

from datetime import date

from core.audit import read_audit_logs
from core.metrics import compute_metrics
from core.orchestrator import run_workflow
from core.sample_data import build_demo_rows
from core.storage import create_dataset
from tests.helpers import stage

TODAY = date(2026, 3, 20)
AGENT_COUNT = 11


def _prepare(store, config):
    rows, profile = build_demo_rows(today=TODAY)
    from core.storage import FinancialProfile

    store.save_profile(FinancialProfile.model_validate(profile))
    stage(store, rows, "demonstration.csv")


def test_demo_workflow_runs_end_to_end(demo_store, config):
    _prepare(demo_store, config)
    result = run_workflow(demo_store, prefer_mcp=False, use_ai=False, config=config)

    assert result.status == "completed", result.errors
    assert len(result.agents) == AGENT_COUNT
    assert all(a.status == "ok" for a in result.agents), [
        (a.agent, a.status, a.error) for a in result.agents
    ]
    assert result.tool_layer == "local"
    assert result.health_score is not None
    assert result.forecast is not None
    assert result.recommendation_count > 0
    assert result.alert_count > 0
    assert result.total_ms > 0

    types = {a.anomaly_type for a in result.anomalies}
    assert "suspicious_merchant_instruction" in types
    assert any(r.priority == "P1" for r in result.recommendations)
    assert all(alert.notification_delivered for alert in result.alerts)

    assert len(demo_store.load_rejected()) >= 4
    transactions = demo_store.load_transactions()
    assert transactions and all(t.source == "demo" for t in transactions)
    assert len(demo_store.load_workflows()) == 1


def test_workflow_records_audit_trace(demo_store, config):
    _prepare(demo_store, config)
    result = run_workflow(demo_store, prefer_mcp=False, use_ai=False, config=config)

    rows = read_audit_logs(workflow_id=result.workflow_id, config=config)
    event_types = {row["event_type"] for row in rows}
    assert {"workflow_start", "workflow_complete", "tool_call", "agent_execution", "review_gate"} <= event_types
    assert all(row["rule_version"] == config.rule_version for row in rows)
    # Tool calls are attributed to the dataset they operated on.
    assert any(row["event_type"] == "tool_call" and row["dataset_id"] == demo_store.dataset_id for row in rows)


def test_empty_dataset_is_reported_as_failed(config):
    empty = create_dataset("Empty upload", kind="upload", config=config)
    result = run_workflow(empty, prefer_mcp=False, use_ai=False, config=config)

    assert result.status == "failed"
    assert result.health is None
    assert result.recommendation_count == 0
    assert result.alert_count == 0
    statuses = {a.agent: a.status for a in result.agents}
    assert statuses["ingestion_agent"] == "failed"
    # Downstream agents must not invent results after an upstream failure.
    assert statuses["validation_agent"] == "skipped"
    assert statuses["advisor_agent"] == "skipped"
    assert result.errors


def test_repeated_runs_are_deterministic(demo_store, config):
    _prepare(demo_store, config)
    first = run_workflow(demo_store, prefer_mcp=False, use_ai=False, config=config)
    second = run_workflow(demo_store, prefer_mcp=False, use_ai=False, config=config)

    assert first.health_score == second.health_score
    assert first.forecast.projected_expense == second.forecast.projected_expense
    assert [r.recommendation_id for r in first.recommendations] == [
        r.recommendation_id for r in second.recommendations
    ]
    assert [a.anomaly_id for a in first.anomalies] == [a.anomaly_id for a in second.anomalies]
    # Alerts are stable and are not re-notified on a second run.
    assert first.alert_count == second.alert_count
    assert [len(a.notifications) for a in second.alerts] == [
        len(a.notifications) for a in first.alerts
    ]


def test_review_gate_blocks_nothing_automatically(demo_store, config):
    _prepare(demo_store, config)
    result = run_workflow(demo_store, prefer_mcp=False, use_ai=False, config=config)
    assert result.recommendations
    assert all(r.status == "pending_review" for r in result.recommendations)
    assert all(r.requires_approval for r in result.recommendations)
    assert demo_store.load_approvals() == []


def test_metrics_are_computed_from_the_run(demo_store, config):
    _prepare(demo_store, config)
    run_workflow(demo_store, prefer_mcp=False, use_ai=False, config=config)
    metrics = compute_metrics(demo_store, config=config)

    assert metrics["metrics"]["validation_rate"]["available"] is True
    assert metrics["metrics"]["workflow_success"]["value"] == 1.0
    assert metrics["metrics"]["tool_success"]["available"] is True
    assert metrics["metrics"]["review_completion"]["value"] == 0.0
    assert metrics["counts"]["transactions"] > 0
    assert metrics["agent_table"]
