"""Tool contracts: validation, authorization, confinement and fallback."""

from __future__ import annotations

import pytest

from core.tools import ToolGateway, call_tool
from tests.helpers import raw, stage

ROWS = [
    raw(1, "2026-01-05", "Swiggy", "480", description="Lunch"),
    raw(2, "2026-01-06", "Netflix", "649"),
    raw(3, "2026-01-07", "Uber", "220"),
    raw(4, "2026-01-08", "Broken Row", "n/a"),
]


def _validated_store(store, config):
    stage(store, ROWS)
    result = call_tool("validate_transactions", {"dataset_id": store.dataset_id}, config=config)
    assert result["ok"], result["error"]
    return store


def test_unknown_tool_is_rejected(config):
    result = call_tool("do_something_else", {}, config=config)
    assert result["ok"] is False
    assert result["error"]["type"] == "unknown_tool"


@pytest.mark.parametrize(
    "params",
    [
        {"dataset_id": "bad id!"},
        {"dataset_id": ""},
        {"dataset_id": "ok", "unexpected": 1},
    ],
)
def test_invalid_parameters_are_rejected(config, params):
    result = call_tool("detect_anomalies", params, config=config)
    assert result["ok"] is False
    assert result["error"]["type"] == "validation_error"


def test_missing_dataset_reports_not_found(config):
    result = call_tool("detect_anomalies", {"dataset_id": "does-not-exist"}, config=config)
    assert result["ok"] is False
    assert result["error"]["type"] == "not_found"


def test_high_risk_tool_requires_authorization(store, config):
    result = call_tool(
        "generate_financial_report",
        {"dataset_id": store.dataset_id},
        config=config,
    )
    assert result["ok"] is False
    assert result["error"]["type"] == "authorization"
    assert result["meta"]["risk"] == "high"


def test_report_tool_runs_with_authorization(store, config):
    _validated_store(store, config)
    call_tool("categorize_transactions", {"dataset_id": store.dataset_id}, config=config)
    call_tool("calculate_financial_health", {"dataset_id": store.dataset_id}, config=config)
    call_tool("calculate_cashflow", {"dataset_id": store.dataset_id}, config=config)

    result = call_tool(
        "generate_financial_report",
        {"dataset_id": store.dataset_id},
        authorized=True,
        config=config,
    )
    assert result["ok"], result["error"]
    relative = result["data"]["report_path"]
    assert relative.startswith("reports/")
    report_file = config.data_root / relative
    assert report_file.exists()
    assert result["data"]["bytes"] > 0


def test_path_traversal_in_parse_statement_is_blocked(store, config):
    result = call_tool(
        "parse_statement",
        {"dataset_id": store.dataset_id, "file_path": "../../../app.py"},
        config=config,
    )
    assert result["ok"] is False
    assert result["error"]["type"] in {"security", "not_found"}


def test_parse_statement_accepts_a_staged_upload(store, config):
    import io

    from core.security import safe_data_path
    from core.sample_data import demo_statement_csv

    csv_text = demo_statement_csv(ROWS)
    relative = "staging/upload.csv"
    path = safe_data_path(relative, data_root=config.data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(csv_text.encode("utf-8"))

    result = call_tool(
        "parse_statement",
        {"dataset_id": store.dataset_id, "file_path": relative},
        config=config,
    )
    assert result["ok"], result["error"]
    assert result["data"]["rows_extracted"] == len(ROWS)


def test_demo_dataset_rejects_uploaded_statements(demo_store, config):
    result = call_tool(
        "parse_statement",
        {"dataset_id": demo_store.dataset_id, "file_path": "staging/x.csv"},
        config=config,
    )
    assert result["ok"] is False
    assert result["error"]["type"] == "security"


def test_envelopes_carry_metadata(store, config):
    _validated_store(store, config)
    result = call_tool("detect_anomalies", {"dataset_id": store.dataset_id}, config=config)
    assert result["ok"]
    assert result["meta"]["duration_ms"] >= 0
    assert result["meta"]["risk"] in {"low", "medium", "high"}
    assert result["meta"]["rule_version"] == config.rule_version


def test_gateway_uses_local_layer_and_audits_calls(store, config):
    _validated_store(store, config)
    gateway = ToolGateway(config, prefer_mcp=False)
    envelope = gateway.call(
        "detect_anomalies",
        {"dataset_id": store.dataset_id},
        dataset_id=store.dataset_id,
    )
    assert envelope["ok"]
    assert envelope["meta"]["mode"] == "local"
    assert gateway.layer == "local"

    from core.audit import read_audit_logs

    rows = read_audit_logs(event_type="tool_call", dataset_id=store.dataset_id, config=config)
    assert any(row["actor"] == "detect_anomalies" and row["status"] == "ok" for row in rows)


def test_gateway_falls_back_when_mcp_is_unavailable(monkeypatch, store, config, tmp_path):
    cfg = config.model_copy(deep=True)
    cfg.mcp.enabled = True
    cfg.mcp.prefer_for_workflow = True

    class Offline:
        def ensure_ready(self):
            return False, "connection refused"

        def status(self):
            return {"state": "failed"}

    import mcp_server.client as client_module

    monkeypatch.setattr(client_module, "get_runtime", lambda config=None: Offline())
    gateway = ToolGateway(cfg, prefer_mcp=True)
    envelope = gateway.call("retrieve_audit_logs", {"limit": 5}, authorized=False)
    assert envelope["ok"]
    assert envelope["meta"]["mode"] == "local_fallback"
    assert gateway.layer == "local_fallback"
    assert any("unavailable" in note for note in gateway.notes)


def test_gateway_falls_back_when_mcp_call_fails(monkeypatch, config):
    cfg = config.model_copy(deep=True)
    cfg.mcp.enabled = True
    cfg.mcp.prefer_for_workflow = True

    class Broken:
        def ensure_ready(self):
            return True, ""

        def call_tool(self, name, params, authorized=False):
            raise RuntimeError("transport closed")

        def status(self):
            return {}

    import mcp_server.client as client_module

    monkeypatch.setattr(client_module, "get_runtime", lambda config=None: Broken())
    gateway = ToolGateway(cfg, prefer_mcp=True)
    envelope = gateway.call("retrieve_audit_logs", {"limit": 5})
    assert envelope["ok"]
    assert envelope["meta"]["mode"] == "local_fallback"
    assert any("failed" in note for note in gateway.notes)
