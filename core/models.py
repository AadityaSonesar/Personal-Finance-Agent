"""Domain models shared by agents, tools, storage and the dashboard."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.constants import ALL_CATEGORIES, ANOMALY_TYPES, CATEGORY_ALIASES, PRIORITIES, RISK_LEVELS


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _base() -> ConfigDict:
    return ConfigDict(extra="ignore", protected_namespaces=())


class Transaction(BaseModel):
    """A single normalized financial transaction.

    ``amount`` is always a positive magnitude; direction lives in
    ``transaction_type``. This keeps arithmetic explicit and prevents
    sign-convention bugs in aggregation.
    """

    model_config = _base()

    transaction_id: str
    transaction_date: date
    merchant: str
    description: str = ""
    amount: float = Field(gt=0)
    transaction_type: Literal["expense", "income"] = "expense"
    category: str = "Other"
    account: str = ""
    currency: str = "INR"
    source: Literal["upload", "demo"] = "upload"
    category_source: str = "default"
    category_locked: bool = False
    is_duplicate_suspect: bool = False
    sanitization_flags: list[str] = Field(default_factory=list)
    row_ref: int = 0
    ingested_at: datetime = Field(default_factory=utcnow)

    @field_validator("category")
    @classmethod
    def _canonical_category(cls, value: str) -> str:
        return canonical_category(value)

    @property
    def signed_amount(self) -> float:
        return round(-self.amount if self.transaction_type == "expense" else self.amount, 2)

    @property
    def month_key(self) -> str:
        return self.transaction_date.strftime("%Y-%m")

    @property
    def duplicate_signature(self) -> str:
        return "|".join(
            [
                self.transaction_date.isoformat(),
                self.merchant.casefold(),
                f"{self.amount:.2f}",
                self.transaction_type,
                self.description.casefold(),
            ]
        )


def canonical_category(value: str | None) -> str:
    """Map loose category labels onto the taxonomy, defaulting to Other."""
    if not value:
        return "Other"
    text = str(value).strip()
    for candidate in ALL_CATEGORIES:
        if text.casefold() == candidate.casefold():
            return candidate
    alias = CATEGORY_ALIASES.get(text.casefold())
    return alias or "Other"


class RejectedRecord(BaseModel):
    model_config = _base()

    timestamp: datetime = Field(default_factory=utcnow)
    row_ref: int
    reasons: list[str]
    raw: dict[str, Any] = Field(default_factory=dict)
    source_file: str = ""


class IngestionOutcome(BaseModel):
    model_config = _base()

    file_name: str
    file_type: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    extraction_complete: bool = True
    requires_manual_review: bool = False
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationStats(BaseModel):
    model_config = _base()

    rows_received: int = 0
    validated: int = 0
    rejected: int = 0
    duplicates_rejected: int = 0
    duplicate_suspects: int = 0
    invalid_amounts: int = 0
    missing_fields: int = 0
    sanitized_fields: int = 0
    validation_rate: float = 0.0


class ValidationOutcome(BaseModel):
    model_config = _base()

    valid: list[Transaction] = Field(default_factory=list)
    rejected: list[RejectedRecord] = Field(default_factory=list)
    stats: ValidationStats = Field(default_factory=ValidationStats)
    warnings: list[str] = Field(default_factory=list)


class Anomaly(BaseModel):
    model_config = _base()

    anomaly_id: str
    anomaly_type: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    score: float = 0.5
    merchant: str = ""
    amount: float = 0.0
    transaction_ids: list[str] = Field(default_factory=list)
    category: str = ""
    title: str = ""
    evidence: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=utcnow)
    date_ref: str = ""

    @field_validator("anomaly_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        return value if value in ANOMALY_TYPES else "unusually_large_transaction"


class Subscription(BaseModel):
    model_config = _base()

    subscription_id: str
    merchant: str
    category: str = "Subscriptions"
    average_amount: float
    intervals_days: list[int] = Field(default_factory=list)
    interval_days: float = 30.0
    occurrences: int
    first_charge: date
    last_charge: date
    next_expected_date: date
    status: str = "potential_subscription"
    confidence: float = 0.5
    usage_verified: bool = False
    days_since_last_charge: int = 0
    annualized_cost: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    transaction_ids: list[str] = Field(default_factory=list)


class BudgetStatus(BaseModel):
    model_config = _base()

    category: str
    limit: float
    actual: float
    utilization: float = 0.0
    remaining: float = 0.0
    status: Literal["ok", "warning", "over", "unbudgeted"] = "ok"
    overrun: float = 0.0
    share_of_total: float = 0.0


class HealthComponent(BaseModel):
    model_config = _base()

    key: str
    label: str
    available: bool
    normalized: float | None = None
    points: float | None = None
    max_points: float
    weighted_contribution: float | None = None
    detail: str = ""
    metric: float | None = None
    target: float | None = None
    missing_reason: str = ""
    improvement: str = ""


class HealthScore(BaseModel):
    model_config = _base()

    score: float | None = None
    complete: bool = True
    available_weight: float = 0.0
    components: list[HealthComponent] = Field(default_factory=list)
    band: str = "Insufficient data"
    interpretation: str = ""
    improvements: list[str] = Field(default_factory=list)
    unavailable_components: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=utcnow)
    rule_version: str = ""


class UpcomingBill(BaseModel):
    model_config = _base()

    label: str
    amount: float = Field(ge=0)
    due_date: date | None = None


class CashFlowForecast(BaseModel):
    model_config = _base()

    month: str
    monthly_income: float | None = None
    income_source: str = "unavailable"
    income_received_mtd: float = 0.0
    expected_inflow_remaining: float | None = None
    actual_expense_mtd: float = 0.0
    projected_expense: float | None = None
    projection_method: str = "unavailable"
    historical_average_expense: float | None = None
    run_rate_expense: float | None = None
    recurring_monthly_cost: float = 0.0
    upcoming_bills_total: float = 0.0
    upcoming_bills: list[UpcomingBill] = Field(default_factory=list)
    net_savings: float | None = None
    current_balance: float | None = None
    predicted_end_balance: float | None = None
    expected_outflow_remaining: float | None = None
    shortfall: float | None = None
    deficit: bool = False
    budget_total: float = 0.0
    budget_utilization: float | None = None
    days_elapsed: int = 0
    days_in_month: int = 30
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=utcnow)


class Recommendation(BaseModel):
    model_config = _base()

    recommendation_id: str
    title: str
    category: str
    priority: str = "P3"
    risk_level: str = "low"
    facts: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    ai_explanation: str = ""
    ai_generated: bool = False
    ai_status: str = "deterministic"
    expected_benefit: str = ""
    next_action: str = ""
    requires_approval: bool = True
    status: str = "pending_review"
    source_agents: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    alert_id: str = ""
    workflow_id: str = ""

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str) -> str:
        return value if value in PRIORITIES else "P3"

    @field_validator("risk_level")
    @classmethod
    def _risk(cls, value: str) -> str:
        return value if value in RISK_LEVELS else "low"


class Alert(BaseModel):
    model_config = _base()

    alert_id: str
    subject: str
    priority: str = "P3"
    intent: str = ""
    complexity: str = "low"
    confidence: float = 0.6
    requires_human: bool = True
    source: str = ""
    recommendation_id: str = ""
    anomaly_id: str = ""
    evidence: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    deadline: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str = ""
    status: str = "open"
    escalation: str = "on_track"
    sla_minutes: int = 240
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    notification_delivered: bool = False
    workflow_id: str = ""


class ApprovalDecision(BaseModel):
    model_config = _base()

    recommendation_id: str
    decision: str
    reviewer: str = ""
    notes: str = ""
    edited_action: str = ""
    decided_at: datetime = Field(default_factory=utcnow)
    workflow_id: str = ""
    execution_scope: str = "advisory_only"
    external_authorization: bool = False


class AgentResult(BaseModel):
    model_config = _base()

    agent: str
    status: Literal["ok", "failed", "skipped"] = "ok"
    duration_ms: float = 0.0
    started_at: datetime = Field(default_factory=utcnow)
    error: str = ""
    error_type: str = ""
    summary: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    skipped_reason: str = ""


class WorkflowResult(BaseModel):
    model_config = _base()

    workflow_id: str
    dataset_id: str
    status: Literal["completed", "partial", "failed"] = "completed"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    total_ms: float = 0.0
    parallel_ms: float = 0.0
    sequential_ms: float = 0.0
    tool_layer: str = "local"
    agents: list[AgentResult] = Field(default_factory=list)
    health_score: float | None = None
    priority_counts: dict[str, int] = Field(default_factory=dict)
    alert_count: int = 0
    recommendation_count: int = 0
    anomalies: list[Anomaly] = Field(default_factory=list)
    subscriptions: list[Subscription] = Field(default_factory=list)
    budgets: list[BudgetStatus] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    health: HealthScore | None = None
    forecast: CashFlowForecast | None = None
    spending: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    data_source: str = "upload"
