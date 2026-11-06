"""Hybrid workflow orchestration.

Dependency graph:
    ingestion → validation → {categorization ∥ subscriptions}
    categorization → {anomalies ∥ spending ∥ budgets}
    {spending, budgets} → scoring → cash flow → advisory → alerts
    advisory + anomalies → notification → human review gate

Independent stages run concurrently with ``asyncio.gather``; dependent stages run
in order. Agent latency is measured, failures are isolated, and a failed upstream
stage prevents downstream agents from producing misleading numbers.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Sequence
from uuid import uuid4

from core.agents import (
    AnomalyAgent,
    BaseAgent,
    BudgetAgent,
    CategorizationAgent,
    CashFlowAgent,
    FinancialAdvisorAgent,
    FinancialScoringAgent,
    IngestionAgent,
    NotificationAgent,
    SpendingAgent,
    SubscriptionAgent,
    ValidationAgent,
)
from core.audit import log_event
from core.config import AppConfig, get_config
from core.models import AgentResult, WorkflowResult, utcnow
from core.shared_state import SharedState
from core.storage import DatasetStore
from core.tools import ToolGateway


PARALLEL_GROUP_A: tuple[type[BaseAgent], ...] = (CategorizationAgent, SubscriptionAgent)
PARALLEL_GROUP_B: tuple[type[BaseAgent], ...] = (AnomalyAgent, SpendingAgent, BudgetAgent)
SEQUENTIAL_TAIL: tuple[type[BaseAgent], ...] = (
    FinancialScoringAgent,
    CashFlowAgent,
    FinancialAdvisorAgent,
    NotificationAgent,
)


class WorkflowOrchestrator:
    """Coordinates the agent pipeline for one dataset."""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        gateway: ToolGateway | None = None,
        prefer_mcp: bool | None = None,
    ) -> None:
        self.config = config or get_config()
        self.gateway = gateway or ToolGateway(self.config, prefer_mcp=prefer_mcp)

    async def run(
        self,
        store: DatasetStore,
        *,
        pending_file: str | None = None,
        use_ai: bool = True,
        trigger: str = "manual",
        reference_month: str | None = None,
    ) -> WorkflowResult:
        config = self.config
        workflow_id = f"WF-{uuid4().hex[:12].upper()}"
        state = SharedState(
            workflow_id=workflow_id,
            dataset=store,
            config=config,
            gateway=self.gateway,
            pending_file=pending_file,
            use_ai=use_ai,
            month=reference_month,
        )
        wall_started = time.perf_counter()
        log_event(
            actor="orchestrator",
            actor_type="system",
            event_type="workflow_start",
            status="running",
            workflow_id=workflow_id,
            dataset_id=store.dataset_id,
            detail={"trigger": trigger, "pending_file": bool(pending_file), "use_ai": use_ai},
            config=config,
        )

        results: list[AgentResult] = []

        # -- ingestion and validation (sequential) --------------------------
        for agent in (IngestionAgent(), ValidationAgent()):
            results.append(await self._execute(agent, state))

        # -- first parallel group ------------------------------------------
        parallel_started = time.perf_counter()
        group_a = [cls() for cls in PARALLEL_GROUP_A]
        results.extend(await self._run_group(group_a, state))
        group_a_ms = (time.perf_counter() - parallel_started) * 1000

        # -- second parallel group -----------------------------------------
        parallel_started = time.perf_counter()
        group_b = [cls() for cls in PARALLEL_GROUP_B]
        results.extend(await self._run_group(group_b, state))
        group_b_ms = (time.perf_counter() - parallel_started) * 1000
        parallel_ms = group_a_ms + group_b_ms

        # -- dependent tail -------------------------------------------------
        sequential_started = time.perf_counter()
        for cls in SEQUENTIAL_TAIL:
            results.append(await self._execute(cls(), state))
        sequential_ms = (time.perf_counter() - sequential_started) * 1000

        # -- review gate ----------------------------------------------------
        review = self._review_gate(state)
        log_event(
            actor="review_gate",
            actor_type="system",
            event_type="review_gate",
            status="pending" if review["pending"] else "clear",
            workflow_id=workflow_id,
            dataset_id=store.dataset_id,
            priority="P1" if review["p1_pending"] else "",
            health_score=state.health.score if state.health else None,
            detail=review,
            config=config,
        )

        total_ms = (time.perf_counter() - wall_started) * 1000
        status = self._status(state, results)
        result = WorkflowResult(
            workflow_id=workflow_id,
            dataset_id=store.dataset_id,
            status=status,
            started_at=state.started_at,
            finished_at=utcnow(),
            total_ms=round(total_ms, 2),
            parallel_ms=round(parallel_ms, 2),
            sequential_ms=round(sequential_ms, 2),
            tool_layer=self.gateway.layer,
            agents=results,
            health_score=state.health.score if state.health else None,
            priority_counts=_priority_counts(state),
            alert_count=len(state.alerts),
            recommendation_count=len(state.recommendations),
            anomalies=state.anomalies,
            subscriptions=state.subscriptions,
            budgets=state.budgets,
            recommendations=state.recommendations,
            alerts=state.alerts,
            health=state.health,
            forecast=state.forecast,
            spending=state.spending,
            errors=state.errors,
            notes=[*state.notes, *self.gateway.notes][:24],
            data_source=store.data_source,
        )

        store.save_workflow(result.model_dump(mode="json"))
        log_event(
            actor="orchestrator",
            actor_type="system",
            event_type="workflow_complete",
            status=status,
            workflow_id=workflow_id,
            dataset_id=store.dataset_id,
            health_score=result.health_score,
            latency_ms=total_ms,
            detail={
                "agents_ok": sum(1 for r in results if r.status == "ok"),
                "agents_failed": sum(1 for r in results if r.status == "failed"),
                "agents_skipped": sum(1 for r in results if r.status == "skipped"),
                "parallel_ms": result.parallel_ms,
                "sequential_ms": result.sequential_ms,
                "tool_layer": result.tool_layer,
                "recommendations": result.recommendation_count,
                "alerts": result.alert_count,
            },
            config=config,
        )
        return result

    async def _run_group(self, agents: Sequence[BaseAgent], state: SharedState) -> list[AgentResult]:
        tasks = [self._execute(agent, state) for agent in agents]
        settled = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[AgentResult] = []
        for agent, outcome in zip(agents, settled):
            if isinstance(outcome, AgentResult):
                results.append(outcome)
                continue
            failure = AgentResult(
                agent=agent.name,
                status="failed",
                error=f"{type(outcome).__name__}: {outcome}",
                error_type=type(outcome).__name__,
            )
            state.errors.append(f"{agent.name}: {failure.error}")
            state.results[agent.name] = failure
            results.append(failure)
        return results

    async def _execute(self, agent: BaseAgent, state: SharedState) -> AgentResult:
        started_at = datetime.now(state.started_at.tzinfo) if state.started_at.tzinfo else utcnow()
        started = time.perf_counter()

        missing = [name for name in agent.requires if not state.ok(name)]
        if missing:
            reason = "; ".join(
                f"{name} {state.results[name].status if name in state.results else 'not executed'}"
                for name in missing
            )
            result = AgentResult(
                agent=agent.name,
                status="skipped",
                skipped_reason=f"dependency not satisfied ({reason})",
                started_at=started_at,
                summary=f"{agent.label} skipped",
            )
            state.results[agent.name] = result
            log_event(
                actor=agent.name,
                actor_type="agent",
                event_type="agent_execution",
                status="skipped",
                workflow_id=state.workflow_id,
                dataset_id=state.dataset_id,
                error=result.skipped_reason,
                detail={"label": agent.label, "requires": list(agent.requires)},
                config=state.config,
            )
            return result

        output: dict[str, Any] = {}
        summary = ""
        error = ""
        error_type = ""
        status = "ok"
        try:
            output, summary = await agent.run(state)
        except Exception as exc:  # failure isolation per agent
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"[:400]
            error_type = type(exc).__name__
            state.errors.append(f"{agent.name}: {error}")
            summary = f"{agent.label} failed"

        duration_ms = (time.perf_counter() - started) * 1000
        result = AgentResult(
            agent=agent.name,
            status=status,  # type: ignore[arg-type]
            duration_ms=round(duration_ms, 2),
            started_at=started_at,
            error=error,
            error_type=error_type,
            summary=summary[:280],
            output=output,
        )
        state.results[agent.name] = result
        log_event(
            actor=agent.name,
            actor_type="agent",
            event_type="agent_execution",
            status=status,
            workflow_id=state.workflow_id,
            dataset_id=state.dataset_id,
            health_score=state.health.score if state.health else None,
            latency_ms=duration_ms,
            error=error,
            detail={
                "label": agent.label,
                "summary": summary[:240],
                "requires": list(agent.requires),
                "output_keys": sorted(output.keys())[:12],
            },
            config=state.config,
        )
        return result

    def _review_gate(self, state: SharedState) -> dict[str, Any]:
        decisions = {str(a.get("recommendation_id")) for a in state.dataset.load_approvals()}
        pending = [r for r in state.recommendations if r.recommendation_id not in decisions]
        p1_pending = [r for r in pending if r.priority == "P1"]
        if pending:
            state.note(
                f"{len(pending)} recommendation(s) await human review; nothing was executed automatically."
            )
        return {
            "recommendations": len(state.recommendations),
            "pending": len(pending),
            "p1_pending": len(p1_pending),
            "requires_review": bool(pending),
        }

    @staticmethod
    def _status(state: SharedState, results: list[AgentResult]) -> str:
        if not state.transactions:
            return "failed"
        failed = [r for r in results if r.status == "failed"]
        skipped = [r for r in results if r.status == "skipped"]
        if failed or skipped:
            return "partial"
        return "completed"


def _priority_counts(state: SharedState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for alert in state.alerts:
        counts[alert.priority] = counts.get(alert.priority, 0) + 1
    return counts


def run_workflow(
    store: DatasetStore,
    *,
    pending_file: str | None = None,
    use_ai: bool = True,
    trigger: str = "manual",
    reference_month: str | None = None,
    config: AppConfig | None = None,
    prefer_mcp: bool | None = None,
) -> WorkflowResult:
    """Synchronous entry point used by the dashboard."""
    orchestrator = WorkflowOrchestrator(config or get_config(), prefer_mcp=prefer_mcp)
    return asyncio.run(
        orchestrator.run(
            store,
            pending_file=pending_file,
            use_ai=use_ai,
            trigger=trigger,
            reference_month=reference_month,
        )
    )
