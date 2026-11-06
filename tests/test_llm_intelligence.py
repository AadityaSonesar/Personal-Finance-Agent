"""Tests for the LLM intelligence engine, Groq client, and MCP tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

AI_SERVICE_DIR = Path(__file__).resolve().parents[1] / "ai-service"
if str(AI_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_DIR))

from core.config import get_config
from core.constants import ALL_CATEGORIES
from core.tools import call_tool, available_tools
from tests.helpers import raw, stage


def _setup_store(store, config):
    rows = [
        raw(1, "2026-01-05", "Swiggy", "480", description="Lunch"),
        raw(2, "2026-01-06", "Netflix", "649"),
        raw(3, "2026-01-07", "Uber", "220"),
    ]
    stage(store, rows)
    call_tool("validate_transactions", {"dataset_id": store.dataset_id}, config=config)
    return store


def test_groq_config_loading():
    """Verify that Groq model configurations load with appropriate defaults."""
    cfg = get_config()
    assert hasattr(cfg.ai, "groq_model_fast")
    assert hasattr(cfg.ai, "groq_model_analysis")
    assert hasattr(cfg.ai, "groq_model_advisor")
    assert cfg.ai.groq_model_fast == "llama-3.1-8b-instant"
    assert "70b" in cfg.ai.groq_model_analysis


def test_mcp_all_required_tools_registered():
    """Ensure all required MCP tools from Section 11 are registered."""
    tools = available_tools()
    required = [
        "get_user_transactions",
        "get_uncategorized_transactions",
        "validate_transactions",
        "categorize_transactions",
        "save_transaction_category",
        "get_spending_summary",
        "analyze_monthly_spending",
        "detect_anomalies",
        "detect_subscriptions",
        "analyze_budget",
        "calculate_financial_metrics",
        "retrieve_financial_history",
        "generate_financial_report",
        "save_analysis_results",
        "save_forecast_results",
    ]
    for r in required:
        assert r in tools, f"Missing required tool: {r}"


def test_get_user_transactions_tool(store, config):
    """Test get_user_transactions MCP tool."""
    _setup_store(store, config)
    env = call_tool("get_user_transactions", {"dataset_id": store.dataset_id, "limit": 10}, config=config)
    assert env["ok"] is True
    assert "transactions" in env["data"]
    assert env["data"]["count"] <= 10


def test_get_uncategorized_transactions_tool(store, config):
    """Test get_uncategorized_transactions tool returns items needing review."""
    _setup_store(store, config)
    env = call_tool("get_uncategorized_transactions", {"dataset_id": store.dataset_id, "limit": 50}, config=config)
    assert env["ok"] is True
    assert "total_uncategorized" in env["data"]
    assert "transactions" in env["data"]


def test_save_transaction_category_tool(store, config):
    """Test save_transaction_category persists user overrides."""
    _setup_store(store, config)
    txs = store.load_transactions()
    target_id = txs[0].transaction_id

    env = call_tool(
        "save_transaction_category",
        {
            "dataset_id": store.dataset_id,
            "transaction_id": target_id,
            "category": "Healthcare",
            "reason": "Doctor visit confirmed",
        },
        config=config,
    )
    assert env["ok"] is True
    assert env["data"]["saved"] is True

    # Verify updated in store
    reloaded = store.load_transactions()
    matched = [t for t in reloaded if t.transaction_id == target_id][0]
    assert matched.category == "Healthcare"
    assert matched.category_locked is True


def test_spending_summary_and_history_tools(store, config):
    """Test get_spending_summary and retrieve_financial_history tools."""
    _setup_store(store, config)
    env = call_tool("retrieve_financial_history", {"dataset_id": store.dataset_id}, config=config)
    assert env["ok"] is True
    assert env["data"]["transaction_count"] > 0
    assert "total_income" in env["data"]
    assert "total_expense" in env["data"]

    summary_env = call_tool("get_spending_summary", {"dataset_id": store.dataset_id}, config=config)
    assert summary_env["ok"] is True
    assert "spending" in summary_env["data"]


def test_high_risk_save_results_requires_authorization(store, config):
    """Verify high-risk tools enforce authorization check."""
    env = call_tool("save_analysis_results", {"dataset_id": store.dataset_id, "results": {}}, authorized=False, config=config)
    assert env["ok"] is False
    assert env["error"]["type"] == "authorization"


def test_forecast_arithmetic_pydantic_validation():
    """Verify that forecast pydantic schema enforces deterministic net cashflow = income - expenses."""
    from schemas.forecast import ForecastPeriod

    period = ForecastPeriod(
        forecast_period="next_30_days",
        expected_income=50000.0,
        expected_expenses=35000.0,
        expected_net_cashflow=15000.0,
    )
    assert period.expected_net_cashflow == 15000.0

    # If LLM produces inconsistent net cashflow, validator auto-corrects arithmetic
    inconsistent = ForecastPeriod(
        forecast_period="next_30_days",
        expected_income=60000.0,
        expected_expenses=40000.0,
        expected_net_cashflow=99999.0,  # wrong
    )
    assert inconsistent.expected_net_cashflow == 20000.0


def test_categorization_schema_rejects_invalid_categories():
    """Verify that categorization schema defaults invalid category names to Other."""
    from schemas.categorization import CategorizationResult

    res = CategorizationResult(
        transaction_id="TX-123",
        suggested_category="NonExistentCategory123",
        confidence=0.85,
        reason="Test",
    )
    assert res.suggested_category == "Other"

    valid = CategorizationResult(
        transaction_id="TX-456",
        suggested_category="Food and Dining",
        confidence=0.92,
        reason="Restaurant dinner",
    )
    assert valid.suggested_category == "Food and Dining"


def test_workflow_runs_end_to_end_without_api_key():
    """Verify the LangGraph workflow executes gracefully in deterministic fallback mode when GROQ_API_KEY is unset."""
    import sys
    sys.path.insert(0, r"ai-service")
    from graph.workflow import run

    transactions = [
        {
            "transactionId": "TXN-1",
            "merchant": "Swiggy",
            "description": "Dinner delivery",
            "amount": 450.0,
            "type": "expense",
            "category": "Other",
            "date": "2026-03-10T12:00:00",
        },
        {
            "transactionId": "TXN-2",
            "merchant": "Acme Corp",
            "description": "Monthly Salary",
            "amount": 75000.0,
            "type": "income",
            "category": "Income",
            "date": "2026-03-01T09:00:00",
        },
    ]
    budgets = [{"category": "Food and Dining", "monthlyLimit": 5000.0, "month": "2026-03"}]

    result = run("test_user_1", transactions, budgets, user_question="Where did I spend money?")
    assert result["status"] == "completed"
    assert "workflow_id" in result
    assert "result" in result
    res = result["result"]
    assert "spending" in res
    assert "budget_analysis" in res
    assert "health" in res
    assert "forecast" in res
