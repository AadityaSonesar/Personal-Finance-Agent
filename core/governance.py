"""Human-in-the-loop governance for consequential recommendations."""

from __future__ import annotations

from collections import Counter
from typing import Any

from core.audit import log_event
from core.config import AppConfig, get_config
from core.constants import REVIEW_DECISIONS
from core.models import ApprovalDecision, Recommendation, utcnow
from core.storage import DatasetStore

STATUS_BY_DECISION = {
    "APPROVE": "approved",
    "EDIT": "edited",
    "HOLD": "held",
    "REQUEST_INFO": "info_requested",
}

DECISION_LABELS = {
    "APPROVE": "Approved",
    "EDIT": "Approved with edits",
    "HOLD": "On hold",
    "REQUEST_INFO": "Information requested",
}

#: Approving a recommendation never authorizes an external money movement.
EXECUTION_POLICY = (
    "Approval records a review decision inside this application only. It does not move funds, "
    "change a subscription, contact a merchant or modify any external account. A separate, explicit "
    "authorization is required before any such action."
)


def decision_requirements(decision: str) -> dict[str, Any]:
    return {
        "APPROVE": {"requires_notes": False, "label": "Approve", "description": "Accept the recommendation as advisory."},
        "EDIT": {"requires_notes": True, "label": "Edit & approve", "description": "Record a modified action, then approve it."},
        "HOLD": {"requires_notes": True, "label": "Hold", "description": "Defer the recommendation with a reason."},
        "REQUEST_INFO": {"requires_notes": True, "label": "Request info", "description": "Ask for more evidence before deciding."},
    }.get(decision, {"requires_notes": True, "label": decision, "description": "Unsupported decision."})


def validate_decision(decision: str, *, notes: str, edited_action: str = "") -> None:
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"Unsupported decision '{decision}'. Allowed: {', '.join(REVIEW_DECISIONS)}.")
    requirements = decision_requirements(decision)
    if requirements["requires_notes"] and not (notes or "").strip():
        raise ValueError(f"A note is required to record a {requirements['label']} decision.")
    if decision == "EDIT" and not (edited_action or "").strip():
        raise ValueError("An edited recommendation must include the revised action.")


def record_decision(
    store: DatasetStore,
    recommendation: Recommendation,
    *,
    decision: str,
    reviewer: str,
    notes: str = "",
    edited_action: str = "",
    config: AppConfig | None = None,
) -> ApprovalDecision:
    """Persist a reviewer decision and append it to the audit trail."""
    config = config or get_config()
    validate_decision(decision, notes=notes, edited_action=edited_action)

    record = ApprovalDecision(
        recommendation_id=recommendation.recommendation_id,
        decision=decision,
        reviewer=(reviewer or "Reviewer").strip()[:80],
        notes=notes.strip()[:1000],
        edited_action=edited_action.strip()[:500],
        decided_at=utcnow(),
        workflow_id=recommendation.workflow_id,
        execution_scope="advisory_only",
        external_authorization=False,
    )
    store.save_approval(record.model_dump(mode="json"))
    log_event(
        actor=record.reviewer,
        actor_type="human",
        event_type="review_decision",
        status="ok",
        workflow_id=recommendation.workflow_id,
        priority=recommendation.priority,
        reviewer_action=decision,
        dataset_id=store.dataset_id,
        detail={
            "recommendation_id": recommendation.recommendation_id,
            "title": recommendation.title,
            "risk_level": recommendation.risk_level,
            "notes": record.notes,
            "execution_scope": record.execution_scope,
        },
        config=config,
    )
    return record


def latest_decisions(store: DatasetStore) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for approval in store.load_approvals():
        latest[str(approval.get("recommendation_id"))] = approval
    return latest


def apply_decisions(
    recommendations: list[Recommendation],
    decisions: dict[str, dict[str, Any]],
) -> list[Recommendation]:
    """Copy recommendations with their review status applied."""
    updated: list[Recommendation] = []
    for recommendation in recommendations:
        decision = decisions.get(recommendation.recommendation_id)
        if not decision:
            updated.append(recommendation)
            continue
        status = STATUS_BY_DECISION.get(str(decision.get("decision", "")), recommendation.status)
        updated.append(recommendation.model_copy(update={"status": status}))
    return updated


def review_summary(
    recommendations: list[Recommendation],
    decisions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    counts = Counter()
    pending = 0
    for recommendation in recommendations:
        decision = decisions.get(recommendation.recommendation_id)
        if decision:
            counts[str(decision.get("decision", "UNKNOWN"))] += 1
        else:
            pending += 1
    total = len(recommendations)
    decided = total - pending
    return {
        "total": total,
        "pending": pending,
        "decided": decided,
        "completion_rate": round(decided / total, 4) if total else None,
        "by_decision": dict(counts),
        "p1_pending": sum(
            1
            for r in recommendations
            if r.priority == "P1" and r.recommendation_id not in decisions
        ),
    }
