"""FastMCP server exposing the financial tools over STDIO.

Run directly with:  python -m mcp_server.server
The server owns no state: every tool validates its own inputs and returns a
structured JSON envelope. High-risk tools require explicit authorization.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from core.tools import call_tool

mcp = FastMCP(
    "aurelia-financial-tools",
    instructions=(
        "Tools for validating statements, analysing spending, scoring financial health, "
        "forecasting cash flow, querying transactions, managing budgets and retrieving audit records. "
        "All outputs are JSON envelopes with ok/data/error/meta fields."
    ),
)


def _run(name: str, params: dict[str, Any], authorized: bool = False) -> dict[str, Any]:
    return call_tool(name, params, authorized=authorized)


@mcp.tool(
    name="get_user_transactions",
    description="Retrieve user transactions with optional category filter and pagination limit.",
)
def get_user_transactions(dataset_id: str, limit: int = 100, category: Optional[str] = None) -> dict[str, Any]:
    """Retrieve transactions for a dataset.

    Args:
        dataset_id: Dataset identifier.
        limit: Maximum number of transactions to return (1-1000).
        category: Optional category filter.
    """
    params: dict[str, Any] = {"dataset_id": dataset_id, "limit": limit}
    if category:
        params["category"] = category
    return _run("get_user_transactions", params)


@mcp.tool(
    name="get_uncategorized_transactions",
    description="Retrieve transactions that are uncategorized (category Other or empty) and not locked.",
)
def get_uncategorized_transactions(dataset_id: str, limit: int = 100) -> dict[str, Any]:
    """Retrieve uncategorized transactions needing review.

    Args:
        dataset_id: Dataset identifier.
        limit: Maximum number to return.
    """
    return _run("get_uncategorized_transactions", {"dataset_id": dataset_id, "limit": limit})


@mcp.tool(
    name="parse_statement",
    description="Parse a CSV, Excel or PDF statement stored in the data directory into staged rows.",
)
def parse_statement(dataset_id: str, file_path: str) -> dict[str, Any]:
    """Parse a statement file for a dataset.

    Args:
        dataset_id: Target dataset identifier.
        file_path: Path to the statement, relative to the project data directory.
    """
    return _run("parse_statement", {"dataset_id": dataset_id, "file_path": file_path})


@mcp.tool(
    name="validate_transactions",
    description="Validate staged rows, quarantine rejects and persist normalized transactions.",
)
def validate_transactions(dataset_id: str) -> dict[str, Any]:
    """Validate the staged batch for a dataset.

    Args:
        dataset_id: Dataset identifier holding staged rows.
    """
    return _run("validate_transactions", {"dataset_id": dataset_id})


@mcp.tool(
    name="categorize_transactions",
    description="Apply the rule-based categorization engine, optionally with AI suggestions for unresolved rows.",
)
def categorize_transactions(dataset_id: str, use_ai: bool = False) -> dict[str, Any]:
    """Categorize stored transactions.

    Args:
        dataset_id: Dataset identifier.
        use_ai: Allow AI suggestions for rows the rules could not classify.
    """
    return _run("categorize_transactions", {"dataset_id": dataset_id, "use_ai": use_ai})


@mcp.tool(
    name="save_transaction_category",
    description="Save a confirmed or user-edited category for a transaction.",
)
def save_transaction_category(dataset_id: str, transaction_id: str, category: str, reason: str = "") -> dict[str, Any]:
    """Save an approved category override.

    Args:
        dataset_id: Dataset identifier.
        transaction_id: Transaction identifier.
        category: The confirmed category name.
        reason: Optional explanation or context.
    """
    return _run("save_transaction_category", {
        "dataset_id": dataset_id,
        "transaction_id": transaction_id,
        "category": category,
        "reason": reason,
    })


@mcp.tool(
    name="get_spending_summary",
    description="Compute categorical spending intelligence and metrics for a given reference month.",
)
def get_spending_summary(dataset_id: str, reference_month: Optional[str] = None) -> dict[str, Any]:
    """Compute spending summary.

    Args:
        dataset_id: Dataset identifier.
        reference_month: Optional YYYY-MM month; defaults to latest.
    """
    return _run("get_spending_summary", {"dataset_id": dataset_id, "reference_month": reference_month})


@mcp.tool(
    name="analyze_monthly_spending",
    description="Analyze monthly spending trends across a given number of recent months.",
)
def analyze_monthly_spending(dataset_id: str, months: int = 6) -> dict[str, Any]:
    """Analyze monthly spending over time.

    Args:
        dataset_id: Dataset identifier.
        months: Number of recent months to analyze.
    """
    return _run("analyze_monthly_spending", {"dataset_id": dataset_id, "months": months})


@mcp.tool(
    name="detect_anomalies",
    description="Detect unusual amounts, spending spikes, duplicates, merchant shifts and instruction-like text.",
)
def detect_anomalies(dataset_id: str, reference_month: Optional[str] = None) -> dict[str, Any]:
    """Run anomaly detection.

    Args:
        dataset_id: Dataset identifier.
        reference_month: Optional YYYY-MM month to analyse; defaults to the latest month in data.
    """
    return _run("detect_anomalies", {"dataset_id": dataset_id, "reference_month": reference_month})


@mcp.tool(
    name="detect_subscriptions",
    description="Identify confirmed, potential and verification-needed recurring payments.",
)
def detect_subscriptions(dataset_id: str) -> dict[str, Any]:
    """Run recurring payment detection.

    Args:
        dataset_id: Dataset identifier.
    """
    return _run("detect_subscriptions", {"dataset_id": dataset_id})


@mcp.tool(
    name="analyze_budget",
    description="Analyze category-wise budget adherence, variances, and over-budget alerts.",
)
def analyze_budget(dataset_id: str, reference_month: Optional[str] = None) -> dict[str, Any]:
    """Analyze budget performance.

    Args:
        dataset_id: Dataset identifier.
        reference_month: Optional YYYY-MM month.
    """
    return _run("analyze_budget", {"dataset_id": dataset_id, "reference_month": reference_month})


@mcp.tool(
    name="calculate_financial_metrics",
    description="Calculate combined financial health, savings rate, and spending metrics.",
)
def calculate_financial_metrics(dataset_id: str, reference_month: Optional[str] = None) -> dict[str, Any]:
    """Calculate key financial health and spending metrics.

    Args:
        dataset_id: Dataset identifier.
        reference_month: Optional YYYY-MM month.
    """
    return _run("calculate_financial_metrics", {"dataset_id": dataset_id, "reference_month": reference_month})


@mcp.tool(
    name="calculate_financial_health",
    description="Compute the weighted financial health score with savings, budget and emergency components.",
)
def calculate_financial_health(dataset_id: str, reference_month: Optional[str] = None) -> dict[str, Any]:
    """Score financial health.

    Args:
        dataset_id: Dataset identifier.
        reference_month: Optional YYYY-MM month; defaults to the latest month in data.
    """
    return _run("calculate_financial_health", {"dataset_id": dataset_id, "reference_month": reference_month})


@mcp.tool(
    name="calculate_cashflow",
    description="Forecast income, expenditure and month-end balance from actual data and stated inputs.",
)
def calculate_cashflow(dataset_id: str, reference_month: Optional[str] = None) -> dict[str, Any]:
    """Forecast cash flow.

    Args:
        dataset_id: Dataset identifier.
        reference_month: Optional YYYY-MM month; defaults to the latest month in data.
    """
    return _run("calculate_cashflow", {"dataset_id": dataset_id, "reference_month": reference_month})


@mcp.tool(
    name="retrieve_financial_history",
    description="Retrieve high-level summary of complete financial history including transaction counts and date ranges.",
)
def retrieve_financial_history(dataset_id: str) -> dict[str, Any]:
    """Retrieve financial history summary.

    Args:
        dataset_id: Dataset identifier.
    """
    return _run("retrieve_financial_history", {"dataset_id": dataset_id})


@mcp.tool(
    name="generate_financial_report",
    description="Package a reviewer-facing report. Requires explicit human authorization (authorized=true).",
)
def generate_financial_report(dataset_id: str, authorized: bool = False) -> dict[str, Any]:
    """Generate a report package.

    Args:
        dataset_id: Dataset identifier.
        authorized: Must be true; the caller confirms a human requested this action.
    """
    return _run("generate_financial_report", {"dataset_id": dataset_id, "authorized": authorized}, authorized=authorized)


@mcp.tool(
    name="save_analysis_results",
    description="Persist completed financial analysis findings. Requires authorization.",
)
def save_analysis_results(dataset_id: str, results: dict[str, Any], authorized: bool = True) -> dict[str, Any]:
    """Save analysis findings.

    Args:
        dataset_id: Dataset identifier.
        results: Structured analysis payload.
        authorized: Must be true.
    """
    return _run("save_analysis_results", {"dataset_id": dataset_id, "results": results, "authorized": authorized}, authorized=authorized)


@mcp.tool(
    name="save_forecast_results",
    description="Persist completed cash-flow forecast results. Requires authorization.",
)
def save_forecast_results(dataset_id: str, forecast: dict[str, Any], authorized: bool = True) -> dict[str, Any]:
    """Save forecast results.

    Args:
        dataset_id: Dataset identifier.
        forecast: Structured forecast payload.
        authorized: Must be true.
    """
    return _run("save_forecast_results", {"dataset_id": dataset_id, "forecast": forecast, "authorized": authorized}, authorized=authorized)


@mcp.tool(
    name="retrieve_audit_logs",
    description="Read the append-only audit trail with optional filters.",
)
def retrieve_audit_logs(
    limit: int = 50,
    workflow_id: Optional[str] = None,
    event_type: Optional[str] = None,
    dataset_id: Optional[str] = None,
) -> dict[str, Any]:
    """Read audit records.

    Args:
        limit: Maximum number of records (1-500).
        workflow_id: Filter by workflow identifier.
        event_type: Filter by event type.
        dataset_id: Filter by dataset identifier.
    """
    params: dict[str, Any] = {"limit": limit}
    if workflow_id:
        params["workflow_id"] = workflow_id
    if event_type:
        params["event_type"] = event_type
    if dataset_id:
        params["dataset_id"] = dataset_id
    return _run("retrieve_audit_logs", params)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
