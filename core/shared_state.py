"""Shared state passed between specialized agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.config import AppConfig
from core.models import (
    AgentResult,
    Alert,
    Anomaly,
    BudgetStatus,
    CashFlowForecast,
    HealthScore,
    Recommendation,
    Subscription,
    Transaction,
    utcnow,
)
from core.storage import DatasetStore
from core.tools import ToolCallFailed, ToolGateway


class DependencyNotMet(RuntimeError):
    """Raised when an agent runs without the outputs it depends on."""


class AgentFailure(RuntimeError):
    """Raised when an agent cannot produce a trustworthy result."""


@dataclass
class SharedState:
    workflow_id: str
    dataset: DatasetStore
    config: AppConfig
    gateway: ToolGateway
    started_at: datetime = field(default_factory=utcnow)
    pending_file: str | None = None
    month: str | None = None
    use_ai: bool = True

    ingestion: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)
    spending: dict[str, Any] = field(default_factory=dict)
    anomalies: list[Anomaly] = field(default_factory=list)
    subscriptions: list[Subscription] = field(default_factory=list)
    budgets: list[BudgetStatus] = field(default_factory=list)
    health: HealthScore | None = None
    forecast: CashFlowForecast | None = None
    recommendations: list[Recommendation] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    ai_meta: dict[str, Any] = field(default_factory=dict)

    results: dict[str, AgentResult] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def dataset_id(self) -> str:
        return self.dataset.dataset_id

    def call_tool(self, name: str, params: dict[str, Any] | None = None, *, authorized: bool = False) -> dict[str, Any]:
        envelope = self.gateway.call(
            name,
            params,
            authorized=authorized,
            workflow_id=self.workflow_id,
            dataset_id=self.dataset_id,
        )
        if not envelope.get("ok"):
            raise ToolCallFailed(name, envelope.get("error") or {})
        return envelope.get("data") or {}

    def ok(self, agent_name: str) -> bool:
        result = self.results.get(agent_name)
        return bool(result and result.status == "ok")

    def require(self, *agent_names: str) -> None:
        missing: list[str] = []
        for name in agent_names:
            result = self.results.get(name)
            if result is None:
                missing.append(f"{name} did not run")
            elif result.status == "skipped":
                missing.append(f"{name} was skipped ({result.skipped_reason})")
            elif result.status == "failed":
                missing.append(f"{name} failed ({result.error})")
        if missing:
            raise DependencyNotMet("; ".join(missing))

    def note(self, message: str) -> None:
        if message and message not in self.notes:
            self.notes.append(message)
