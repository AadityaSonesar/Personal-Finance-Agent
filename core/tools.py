"""Tool registry: one implementation for the MCP server and the local fallback.

Every tool has a typed request model, a declared risk level and a structured
JSON envelope. High-risk tools require explicit authorization before they run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.anomalies import detect_anomalies, summarize_anomalies
from core.audit import log_event, read_audit_logs
from core.budgets import analyze_budgets
from core.cashflow import forecast_cashflow
from core.categorization import categorize_transactions
from core.config import AppConfig, get_config
from core.formatting import money
from core.health import calculate_health, health_history_entry
from core.ingestion import IngestionError, parse_statement_path
from core.llm import AIClient
from core.models import Anomaly, HealthScore, Subscription, Transaction
from core.observability import get_logger
from core.reporting import build_report_markdown
from core.security import assert_safe_json, safe_data_path
from core.spending import available_months, build_spending_intelligence
from core.storage import DatasetStore, get_dataset, resolve_dataset
from core.subscriptions import detect_subscriptions, subscription_summary
from core.validation import validate_rows

logger = get_logger("aurelia.tools")

HIGH_RISK = "high"


# --------------------------------------------------------------------------- #
# Errors and request models
# --------------------------------------------------------------------------- #


class ToolError(Exception):
    error_type = "internal"
    retryable = False


class ToolValidationError(ToolError):
    error_type = "validation_error"


class ToolNotFoundError(ToolError):
    error_type = "not_found"


class ToolSecurityError(ToolError):
    error_type = "security"


class ToolAuthorizationError(ToolError):
    error_type = "authorization"


class ToolCallFailed(RuntimeError):
    """Raised by callers when a tool envelope reports failure."""

    def __init__(self, tool: str, error: dict[str, Any]) -> None:
        self.tool = tool
        self.error = error
        super().__init__(f"{tool} failed: {error.get('message', 'unknown error')}")


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class DatasetRequest(_Request):
    dataset_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")


class ParseStatementRequest(DatasetRequest):
    file_path: str = Field(min_length=1, max_length=400)
    account: str = Field(default="", max_length=80)


class CategorizeRequest(DatasetRequest):
    use_ai: bool = False


class AnalysisRequest(DatasetRequest):
    reference_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class ReportRequest(DatasetRequest):
    authorized: bool = False


class AuditRequest(_Request):
    limit: int = Field(default=50, ge=1, le=500)
    workflow_id: str | None = None
    event_type: str | None = None
    dataset_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+$")


class GetTransactionsRequest(DatasetRequest):
    limit: int = Field(default=100, ge=1, le=1000)
    category: str | None = None


class GetUncategorizedRequest(DatasetRequest):
    limit: int = Field(default=100, ge=1, le=1000)


class SaveTransactionCategoryRequest(DatasetRequest):
    transaction_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    reason: str = Field(default="", max_length=500)


class MonthlySpendingRequest(DatasetRequest):
    months: int = Field(default=6, ge=1, le=36)


class SaveResultsRequest(DatasetRequest):
    authorized: bool = True
    results: dict[str, Any] = Field(default_factory=dict)


class SaveForecastRequest(DatasetRequest):
    authorized: bool = True
    forecast: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    model: type[BaseModel]
    handler: Callable[[Any, AppConfig], dict[str, Any]]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _dataset(request: DatasetRequest, config: AppConfig) -> DatasetStore:
    store = get_dataset(request.dataset_id, config=config)
    if not store:
        raise ToolNotFoundError(f"Dataset '{request.dataset_id}' was not found.")
    return store


def _latest_month(transactions: list[Transaction]) -> str | None:
    months = available_months(transactions)
    return months[-1] if months else None


def _subscriptions(store: DatasetStore) -> list[Subscription]:
    raw = store.load_artifact("subscriptions", []) or []
    result: list[Subscription] = []
    for item in raw:
        try:
            result.append(Subscription.model_validate(item))
        except Exception:
            continue
    return result


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #


def _handle_parse_statement(request: ParseStatementRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    if store.is_demo:
        raise ToolSecurityError(
            "Uploaded statements cannot be added to a demonstration dataset. Create or select an uploaded dataset first."
        )
    path = safe_data_path(request.file_path, data_root=config.data_root, must_exist=True)
    allowed = {ext.casefold() for ext in config.security.allowed_extensions}
    if path.suffix.casefold() not in allowed:
        raise ToolValidationError(f"Unsupported file extension '{path.suffix}'.")

    try:
        outcome = parse_statement_path(path, config=config)
    except IngestionError as exc:
        raise ToolValidationError(str(exc)) from exc

    meta = {
        "file_name": outcome.file_name,
        "file_type": outcome.file_type,
        "row_count": outcome.row_count,
        "extraction_complete": outcome.extraction_complete,
        "requires_manual_review": outcome.requires_manual_review,
        "notes": outcome.notes,
        "warnings": outcome.warnings,
        "staged_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save_staging(outcome.rows, meta)
    return {
        "dataset_id": store.dataset_id,
        "file_name": outcome.file_name,
        "file_type": outcome.file_type,
        "rows_extracted": outcome.row_count,
        "extraction_complete": outcome.extraction_complete,
        "requires_manual_review": outcome.requires_manual_review,
        "notes": outcome.notes,
        "warnings": outcome.warnings,
        "staged": True,
    }


def _handle_validate_transactions(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    staging = store.load_staging()
    rows = staging.get("rows") or []
    meta = staging.get("meta") or {}
    if not rows:
        raise ToolValidationError(
            "No staged rows are available. Parse a statement first, or the staged batch has already been validated."
        )

    outcome = validate_rows(
        rows,
        existing=store.load_transactions(),
        source_file=str(meta.get("file_name", "")),
        source=store.data_source,
        config=config,
    )
    if outcome.rejected:
        store.append_rejected(outcome.rejected)
    merge = store.add_transactions(outcome.valid)
    stats = outcome.stats.model_dump(mode="json")
    store.save_artifact(
        "validation",
        {
            **stats,
            "source_file": str(meta.get("file_name", "")),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "added": merge["added"],
            "source": store.data_source,
        },
    )
    store.save_staging([], {**meta, "validated": True})
    store.touch()

    return {
        "dataset_id": store.dataset_id,
        "stats": stats,
        "added": merge["added"],
        "skipped_existing": merge["skipped"],
        "total_transactions": merge["total"],
        "rejected_sample": [
            {"row_ref": r.row_ref, "reasons": r.reasons, "raw": r.raw} for r in outcome.rejected[:10]
        ],
        "warnings": outcome.warnings,
        "source_file": meta.get("file_name", ""),
        "requires_manual_review": bool(meta.get("requires_manual_review", False)),
    }


def _handle_categorize(request: CategorizeRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    transactions = store.load_transactions()
    if not transactions:
        raise ToolNotFoundError("No validated transactions are available to categorize.")

    overrides = store.load_overrides()
    ai_suggestions: dict[str, str] = {}
    ai_meta: dict[str, Any] = {"used": False}

    if request.use_ai:
        client = AIClient(config)
        status = client.status()
        ai_meta = {**status.as_dict(), "used": False}
        if status.available:
            unresolved = [
                t for t in transactions
                if t.transaction_type == "expense"
                and not t.category_locked
                and t.category == "Other"
                and t.transaction_id not in overrides.get("transactions", {})
            ]
            if unresolved:
                batch = [
                    {"id": t.transaction_id, "merchant": t.merchant, "description": t.description}
                    for t in unresolved[: config.ai.max_batch_size]
                ]
                result = client.categorize(batch)
                if result.ok:
                    ai_suggestions = result.payload.get("categories", {})
                    ai_meta.update(
                        {
                            "used": True,
                            "accepted": result.accepted,
                            "rejected": result.rejected,
                            "latency_ms": round(result.latency_ms, 1),
                        }
                    )
                else:
                    ai_meta.update({"used": False, "error": result.error})

    outcome = categorize_transactions(
        transactions,
        config=config,
        transaction_overrides=overrides.get("transactions", {}),
        merchant_overrides=overrides.get("merchants", {}),
        ai_suggestions=ai_suggestions,
    )
    store.save_transactions(outcome.transactions)
    return {
        "dataset_id": store.dataset_id,
        "total": len(outcome.transactions),
        "counts_by_source": outcome.counts_by_source,
        "by_category": outcome.by_category,
        "unresolved": outcome.by_category.get("Other", 0),
        "warnings": outcome.warnings,
        "ai": ai_meta,
    }


def _handle_detect_anomalies(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    transactions = store.load_transactions()
    if not transactions:
        raise ToolNotFoundError("No validated transactions are available for anomaly analysis.")
    month = request.reference_month or _latest_month(transactions)
    findings: list[Anomaly] = detect_anomalies(transactions, config=config, reference_month=month)
    store.save_artifact("anomalies", [a.model_dump(mode="json") for a in findings])
    return {
        "dataset_id": store.dataset_id,
        "month": month,
        "count": len(findings),
        "summary": summarize_anomalies(findings),
        "anomalies": [a.model_dump(mode="json") for a in findings[:25]],
    }


def _handle_detect_subscriptions(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    transactions = store.load_transactions()
    if not transactions:
        raise ToolNotFoundError("No validated transactions are available for subscription analysis.")
    subscriptions = detect_subscriptions(transactions, config=config)
    store.save_artifact("subscriptions", [s.model_dump(mode="json") for s in subscriptions])
    return {
        "dataset_id": store.dataset_id,
        "count": len(subscriptions),
        "summary": subscription_summary(subscriptions),
        "subscriptions": [s.model_dump(mode="json") for s in subscriptions[:25]],
    }


def _handle_financial_health(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    transactions = store.load_transactions()
    if not transactions:
        raise ToolNotFoundError("No validated transactions are available for health scoring.")
    profile = store.load_profile()
    budgets = store.effective_budgets()
    month = request.reference_month or _latest_month(transactions)
    statuses = analyze_budgets(transactions, budgets, month=month, config=config)
    health: HealthScore = calculate_health(
        transactions,
        profile=profile,
        budgets=budgets,
        config=config,
        month=month,
        budget_statuses=statuses,
    )
    store.save_artifact("budgets_analysis", [s.model_dump(mode="json") for s in statuses])
    store.save_artifact("health", health.model_dump(mode="json"))
    store.append_health_history(health_history_entry(health))
    spending = build_spending_intelligence(transactions, reference_month=month)
    store.save_artifact("spending", spending)
    return {
        "dataset_id": store.dataset_id,
        "health": health.model_dump(mode="json"),
        "score": health.score,
        "complete": health.complete,
    }


def _handle_cashflow(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    transactions = store.load_transactions()
    if not transactions:
        raise ToolNotFoundError("No validated transactions are available for cash flow forecasting.")
    profile = store.load_profile()
    budgets = store.effective_budgets()
    month = request.reference_month or _latest_month(transactions)
    forecast = forecast_cashflow(
        transactions,
        profile=profile,
        subscriptions=_subscriptions(store),
        budgets=budgets,
        config=config,
        month=month,
    )
    store.save_artifact("cashflow", forecast.model_dump(mode="json"))
    return {
        "dataset_id": store.dataset_id,
        "forecast": forecast.model_dump(mode="json"),
        "deficit": forecast.deficit,
        "predicted_end_balance": forecast.predicted_end_balance,
    }


def _handle_generate_report(request: ReportRequest, config: AppConfig) -> dict[str, Any]:
    if not request.authorized:
        raise ToolAuthorizationError(
            "Generating a report package requires explicit human authorization."
        )
    store = _dataset(request, config)
    markdown, summary = build_report_markdown(store, config)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    reports_dir = config.data_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    relative = f"reports/{store.dataset_id}-{stamp}.md"
    path = safe_data_path(relative, data_root=config.data_root)
    path.write_text(markdown, encoding="utf-8")
    return {
        "dataset_id": store.dataset_id,
        "report_path": relative,
        "summary": summary,
        "bytes": len(markdown.encode("utf-8")),
        "demonstration_data": store.is_demo,
    }


def _handle_retrieve_audit_logs(request: AuditRequest, config: AppConfig) -> dict[str, Any]:
    rows = read_audit_logs(
        limit=request.limit,
        workflow_id=request.workflow_id,
        event_type=request.event_type,
        dataset_id=request.dataset_id,
        config=config,
    )
    return {"count": len(rows), "logs": rows}


def _handle_get_user_transactions(request: GetTransactionsRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    if request.category:
        txs = [t for t in txs if t.category.casefold() == request.category.casefold()]
    selected = txs[:request.limit]
    return {
        "dataset_id": store.dataset_id,
        "total": len(txs),
        "count": len(selected),
        "transactions": [t.model_dump(mode="json") for t in selected],
    }


def _handle_get_uncategorized_transactions(request: GetUncategorizedRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    uncategorized = [t for t in txs if t.category in ("Other", "") and not t.category_locked]
    selected = uncategorized[:request.limit]
    return {
        "dataset_id": store.dataset_id,
        "total_uncategorized": len(uncategorized),
        "count": len(selected),
        "transactions": [t.model_dump(mode="json") for t in selected],
    }


def _handle_save_transaction_category(request: SaveTransactionCategoryRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    updated = False
    for i, t in enumerate(txs):
        if t.transaction_id == request.transaction_id:
            txs[i] = t.model_copy(update={
                "category": request.category,
                "category_source": "override",
                "category_locked": True,
            })
            updated = True
            break
    if updated:
        store.save_transactions(txs)
    store.set_transaction_category(request.transaction_id, request.category)
    return {
        "dataset_id": store.dataset_id,
        "transaction_id": request.transaction_id,
        "category": request.category,
        "reason": request.reason,
        "saved": True,
    }


def _handle_get_spending_summary(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    month = request.reference_month or _latest_month(txs)
    spending = build_spending_intelligence(txs, reference_month=month)
    return {
        "dataset_id": store.dataset_id,
        "reference_month": month,
        "spending": spending,
    }


def _handle_analyze_monthly_spending(request: MonthlySpendingRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    months = available_months(txs)[-request.months:]
    history = {}
    for m in months:
        history[m] = build_spending_intelligence(txs, reference_month=m)
    return {
        "dataset_id": store.dataset_id,
        "months": months,
        "history": history,
    }


def _handle_analyze_budget(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    month = request.reference_month or _latest_month(txs)
    budgets = store.effective_budgets()
    analysis = analyze_budgets(txs, budgets, month=month, config=config)
    return {
        "dataset_id": store.dataset_id,
        "reference_month": month,
        "budget_statuses": [b.model_dump(mode="json") for b in analysis],
    }


def _handle_calculate_financial_metrics(request: AnalysisRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    month = request.reference_month or _latest_month(txs)
    profile = store.load_profile()
    budgets = store.effective_budgets()
    budget_statuses = analyze_budgets(txs, budgets, month=month, config=config)
    health = calculate_health(txs, profile=profile, budgets=budgets, budget_statuses=budget_statuses, config=config, month=month)
    spending = build_spending_intelligence(txs, reference_month=month)
    return {
        "dataset_id": store.dataset_id,
        "reference_month": month,
        "health_score": health.score,
        "health_band": health.band.label if health.band else "",
        "score_breakdown": health.score_breakdown,
        "total_spend": spending.get("total_spend"),
        "total_income": spending.get("total_income"),
        "savings_rate": health.savings_rate,
    }


def _handle_retrieve_financial_history(request: DatasetRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    txs = store.load_transactions()
    months = available_months(txs)
    income_txs = [t for t in txs if t.transaction_type == "income"]
    expense_txs = [t for t in txs if t.transaction_type == "expense"]
    return {
        "dataset_id": store.dataset_id,
        "transaction_count": len(txs),
        "income_count": len(income_txs),
        "expense_count": len(expense_txs),
        "total_income": round(sum(t.amount for t in income_txs), 2),
        "total_expense": round(sum(t.amount for t in expense_txs), 2),
        "available_months": months,
        "first_transaction_date": txs[0].transaction_date.isoformat() if txs else None,
        "last_transaction_date": txs[-1].transaction_date.isoformat() if txs else None,
    }


def _handle_save_analysis_results(request: SaveResultsRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    store.save_artifact("analysis_results", request.results)
    return {
        "dataset_id": store.dataset_id,
        "saved": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _handle_save_forecast_results(request: SaveForecastRequest, config: AppConfig) -> dict[str, Any]:
    store = _dataset(request, config)
    store.save_artifact("forecast_results", request.forecast)
    return {
        "dataset_id": store.dataset_id,
        "saved": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


TOOL_SPECS: dict[str, ToolSpec] = {
    "get_user_transactions": ToolSpec(
        name="get_user_transactions",
        description="Retrieve user transactions with optional category filter and pagination limit.",
        risk="low",
        model=GetTransactionsRequest,
        handler=_handle_get_user_transactions,
    ),
    "get_uncategorized_transactions": ToolSpec(
        name="get_uncategorized_transactions",
        description="Retrieve transactions that need review or are categorized as Other.",
        risk="low",
        model=GetUncategorizedRequest,
        handler=_handle_get_uncategorized_transactions,
    ),
    "save_transaction_category": ToolSpec(
        name="save_transaction_category",
        description="Save a confirmed or user-edited category for a transaction.",
        risk="medium",
        model=SaveTransactionCategoryRequest,
        handler=_handle_save_transaction_category,
    ),
    "get_spending_summary": ToolSpec(
        name="get_spending_summary",
        description="Compute categorical spending intelligence and metrics for a given reference month.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_get_spending_summary,
    ),
    "analyze_monthly_spending": ToolSpec(
        name="analyze_monthly_spending",
        description="Analyze monthly spending trends across a given number of recent months.",
        risk="low",
        model=MonthlySpendingRequest,
        handler=_handle_analyze_monthly_spending,
    ),
    "analyze_budget": ToolSpec(
        name="analyze_budget",
        description="Analyze category-wise budget adherence and variances.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_analyze_budget,
    ),
    "calculate_financial_metrics": ToolSpec(
        name="calculate_financial_metrics",
        description="Calculate combined financial health, savings rate, and spending metrics.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_calculate_financial_metrics,
    ),
    "retrieve_financial_history": ToolSpec(
        name="retrieve_financial_history",
        description="Retrieve high-level summary of complete financial history.",
        risk="low",
        model=DatasetRequest,
        handler=_handle_retrieve_financial_history,
    ),
    "save_analysis_results": ToolSpec(
        name="save_analysis_results",
        description="Persist completed financial analysis results.",
        risk=HIGH_RISK,
        model=SaveResultsRequest,
        handler=_handle_save_analysis_results,
    ),
    "save_forecast_results": ToolSpec(
        name="save_forecast_results",
        description="Persist completed cash-flow forecast results.",
        risk=HIGH_RISK,
        model=SaveForecastRequest,
        handler=_handle_save_forecast_results,
    ),
    "parse_statement": ToolSpec(
        name="parse_statement",
        description="Parse a CSV, Excel or PDF statement from the data directory into staged transaction rows.",
        risk="medium",
        model=ParseStatementRequest,
        handler=_handle_parse_statement,
    ),
    "validate_transactions": ToolSpec(
        name="validate_transactions",
        description="Validate staged rows, quarantine rejects and persist normalized transactions.",
        risk="medium",
        model=AnalysisRequest,
        handler=_handle_validate_transactions,
    ),
    "categorize_transactions": ToolSpec(
        name="categorize_transactions",
        description="Apply rule-based categorization (optionally AI-assisted) and persist categories.",
        risk="medium",
        model=CategorizeRequest,
        handler=_handle_categorize,
    ),
    "detect_anomalies": ToolSpec(
        name="detect_anomalies",
        description="Detect large payments, spikes, duplicates and instruction-like transaction text.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_detect_anomalies,
    ),
    "detect_subscriptions": ToolSpec(
        name="detect_subscriptions",
        description="Identify confirmed, potential and verification-needed recurring payments.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_detect_subscriptions,
    ),
    "calculate_financial_health": ToolSpec(
        name="calculate_financial_health",
        description="Compute the weighted financial health score with component breakdowns.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_financial_health,
    ),
    "calculate_cashflow": ToolSpec(
        name="calculate_cashflow",
        description="Project income, expenditure and month-end balance from actual data.",
        risk="low",
        model=AnalysisRequest,
        handler=_handle_cashflow,
    ),
    "generate_financial_report": ToolSpec(
        name="generate_financial_report",
        description="Package a reviewer-facing report. Requires explicit authorization.",
        risk=HIGH_RISK,
        model=ReportRequest,
        handler=_handle_generate_report,
    ),
    "retrieve_audit_logs": ToolSpec(
        name="retrieve_audit_logs",
        description="Read the append-only audit trail with optional filters.",
        risk="low",
        model=AuditRequest,
        handler=_handle_retrieve_audit_logs,
    ),
}


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


def _envelope(
    name: str,
    *,
    ok: bool,
    data: Any,
    error: dict[str, Any] | None,
    started: float,
    risk: str,
    config: AppConfig,
) -> dict[str, Any]:
    payload = {
        "ok": ok,
        "tool": name,
        "data": data,
        "error": error,
        "meta": {
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "risk": risk,
            "rule_version": config.rule_version,
            "mode": None,
        },
    }
    try:
        assert_safe_json(payload)
    except ValueError as exc:
        return {
            "ok": False,
            "tool": name,
            "data": None,
            "error": {"type": "serialization_error", "message": str(exc), "retryable": False},
            "meta": {
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "risk": risk,
                "rule_version": config.rule_version,
                "mode": None,
            },
        }
    return payload


def call_tool(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    authorized: bool = False,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """Validate, authorize and execute one tool, returning a JSON envelope."""
    config = config or get_config()
    started = time.perf_counter()
    params = dict(params or {})
    spec = TOOL_SPECS.get(name)
    if spec is None:
        return _envelope(
            name,
            ok=False,
            data=None,
            error={
                "type": "unknown_tool",
                "message": f"Unknown tool '{name}'. Available: {', '.join(sorted(TOOL_SPECS))}.",
                "retryable": False,
            },
            started=started,
            risk="unknown",
            config=config,
        )

    if spec.risk == HIGH_RISK and not (authorized or bool(params.get("authorized"))):
        return _envelope(
            name,
            ok=False,
            data=None,
            error={
                "type": "authorization",
                "message": f"'{name}' is a high-risk tool and requires explicit human authorization.",
                "retryable": False,
            },
            started=started,
            risk=spec.risk,
            config=config,
        )

    if spec.risk == HIGH_RISK and authorized:
        # The caller's authorization is carried into the request so the handler
        # records the same decision that the gateway enforced.
        params.setdefault("authorized", True)

    try:
        request = spec.model.model_validate(params)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error.get('loc', []))}: {error.get('msg')}"
            for error in exc.errors()[:5]
        )
        return _envelope(
            name,
            ok=False,
            data=None,
            error={
                "type": "validation_error",
                "message": f"Invalid parameters for '{name}': {details}",
                "retryable": False,
            },
            started=started,
            risk=spec.risk,
            config=config,
        )

    try:
        data = spec.handler(request, config)
    except ToolError as exc:
        return _envelope(
            name,
            ok=False,
            data=None,
            error={"type": exc.error_type, "message": str(exc), "retryable": exc.retryable},
            started=started,
            risk=spec.risk,
            config=config,
        )
    except FileNotFoundError as exc:
        return _envelope(
            name,
            ok=False,
            data=None,
            error={"type": "not_found", "message": str(exc), "retryable": False},
            started=started,
            risk=spec.risk,
            config=config,
        )
    except PermissionError as exc:
        return _envelope(
            name,
            ok=False,
            data=None,
            error={"type": "security", "message": str(exc), "retryable": False},
            started=started,
            risk=spec.risk,
            config=config,
        )
    except Exception as exc:  # unexpected failure is reported, never hidden
        logger.exception("Tool %s failed unexpectedly", name)
        return _envelope(
            name,
            ok=False,
            data=None,
            error={
                "type": "internal_error",
                "message": f"{type(exc).__name__}: {exc}"[:300],
                "retryable": True,
            },
            started=started,
            risk=spec.risk,
            config=config,
        )

    return _envelope(name, ok=True, data=data, error=None, started=started, risk=spec.risk, config=config)


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "risk": spec.risk,
            "parameters": spec.model.model_json_schema(),
            "requires_authorization": spec.risk == HIGH_RISK,
        }
        for spec in TOOL_SPECS.values()
    ]


def available_tools() -> list[str]:
    return sorted(TOOL_SPECS)


# --------------------------------------------------------------------------- #
# Gateway (MCP preferred, approved local fallback)
# --------------------------------------------------------------------------- #


class ToolGateway:
    """Routes tool calls through MCP when healthy, else the local registry."""

    def __init__(self, config: AppConfig | None = None, *, prefer_mcp: bool | None = None) -> None:
        self.config = config or get_config()
        self.prefer_mcp = (
            self.config.mcp.prefer_for_workflow if prefer_mcp is None else prefer_mcp
        ) and self.config.mcp.enabled
        self.notes: list[str] = []
        self.modes: list[str] = []
        self.last_mode = "local"
        self._client = None

    def _get_client(self):
        if self._client is None:
            from mcp_server.client import get_runtime

            self._client = get_runtime(self.config)
        return self._client

    @property
    def layer(self) -> str:
        if not self.prefer_mcp:
            return "local"
        if not self.modes:
            return "mcp" if self.config.mcp.enabled else "local"
        if all(mode == "mcp" for mode in self.modes):
            return "mcp"
        if any(mode == "mcp" for mode in self.modes):
            return "mixed"
        return "local_fallback"

    def call(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        authorized: bool = False,
        workflow_id: str = "",
        dataset_id: str = "",
    ) -> dict[str, Any]:
        params = dict(params or {})
        envelope: dict[str, Any] | None = None
        mode = "local"

        if self.prefer_mcp:
            try:
                client = self._get_client()
                ready, reason = client.ensure_ready()
                if ready:
                    try:
                        envelope = client.call_tool(name, params, authorized=authorized)
                        mode = "mcp"
                    except Exception as exc:
                        note = f"MCP call for '{name}' failed ({type(exc).__name__}); used the approved local fallback."
                        self.notes.append(note)
                        logger.warning(note)
                else:
                    note = f"MCP layer unavailable ({reason}); used the approved local fallback."
                    if note not in self.notes:
                        self.notes.append(note)
                    mode = "local_fallback"
            except Exception as exc:
                note = f"MCP client could not start ({type(exc).__name__}); used the approved local fallback."
                self.notes.append(note)
                mode = "local_fallback"

        if envelope is None:
            envelope = call_tool(name, params, authorized=authorized, config=self.config)
            if self.prefer_mcp and mode == "local":
                mode = "local_fallback"

        self.last_mode = mode
        self.modes.append(mode)
        envelope.setdefault("meta", {})
        envelope["meta"]["mode"] = mode

        error = envelope.get("error") or {}
        log_event(
            actor=name,
            event_type="tool_call",
            actor_type="tool",
            status="ok" if envelope.get("ok") else "failed",
            workflow_id=workflow_id,
            dataset_id=dataset_id or str(params.get("dataset_id", "")),
            latency_ms=float(envelope.get("meta", {}).get("duration_ms", 0.0) or 0.0),
            error=str(error.get("message", "")),
            detail={
                "mode": mode,
                "risk": envelope.get("meta", {}).get("risk"),
                "error_type": error.get("type", ""),
            },
            config=self.config,
        )
        return envelope

    def call_or_raise(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        authorized: bool = False,
        workflow_id: str = "",
        dataset_id: str = "",
    ) -> dict[str, Any]:
        envelope = self.call(
            name,
            params,
            authorized=authorized,
            workflow_id=workflow_id,
            dataset_id=dataset_id,
        )
        if not envelope.get("ok"):
            raise ToolCallFailed(name, envelope.get("error") or {})
        return envelope.get("data") or {}

    def status(self) -> dict[str, Any]:
        info = {
            "layer": self.layer,
            "prefer_mcp": self.prefer_mcp,
            "notes": self.notes[-5:],
        }
        if self.prefer_mcp:
            try:
                info.update(self._get_client().status())
            except Exception as exc:
                info.update({"state": "failed", "available": False, "reason": str(exc)})
        return info
