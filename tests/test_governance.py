"""Human-in-the-loop governance."""

from __future__ import annotations

import pytest

from core.governance import (
    apply_decisions,
    record_decision,
    review_summary,
    validate_decision,
)
from core.models import Recommendation


def _recommendation() -> Recommendation:
    return Recommendation(
        recommendation_id="REC-TEST-1",
        title="Reduce dining spend",
        category="spending",
        priority="P2",
        risk_level="medium",
        facts=["Food spending is 18,000 against a 12,000 budget."],
        evidence={"budget_overrun": 6000},
        next_action="Set a 12,000 dining limit for next month.",
    )


def test_hold_requires_a_note():
    with pytest.raises(ValueError):
        validate_decision("HOLD", notes="  ")


def test_edit_requires_a_revised_action():
    with pytest.raises(ValueError):
        validate_decision("EDIT", notes="Tighten it", edited_action="")


def test_unsupported_decision_is_rejected():
    with pytest.raises(ValueError):
        validate_decision("EXECUTE", notes="go")


def test_approval_is_recorded_and_audited(store, config):
    recommendation = _recommendation()
    record = record_decision(
        store,
        recommendation,
        decision="APPROVE",
        reviewer="Asha",
        notes="Agreed.",
        config=config,
    )
    assert record.execution_scope == "advisory_only"
    assert record.external_authorization is False
    assert store.load_approvals()[0]["recommendation_id"] == "REC-TEST-1"

    from core.audit import read_audit_logs

    rows = read_audit_logs(
        event_type="review_decision", dataset_id=store.dataset_id, config=config
    )
    assert rows and rows[0]["reviewer_action"] == "APPROVE"
    assert rows[0]["actor_type"] == "human"


def test_approval_never_authorizes_an_external_transaction(store, config):
    record = record_decision(
        store, _recommendation(), decision="APPROVE", reviewer="Ravi", config=config
    )
    payload = store.load_approvals()[0]
    assert payload["execution_scope"] == "advisory_only"
    assert payload["external_authorization"] is False
    assert record.decision == "APPROVE"


def test_decision_status_is_applied_to_recommendations(store, config):
    recommendation = _recommendation()
    record_decision(
        store,
        recommendation,
        decision="EDIT",
        reviewer="Meera",
        notes="Cap it lower.",
        edited_action="Set a 10,000 dining limit.",
        config=config,
    )
    decisions = {a["recommendation_id"]: a for a in store.load_approvals()}
    updated = apply_decisions([recommendation], decisions)
    assert updated[0].status == "edited"

    summary = review_summary([recommendation], decisions)
    assert summary["pending"] == 0
    assert summary["completion_rate"] == 1.0
    assert summary["by_decision"]["EDIT"] == 1


def test_review_summary_counts_pending(store):
    recommendations = [
        _recommendation(),
        _recommendation().model_copy(update={"recommendation_id": "REC-TEST-2", "priority": "P1"}),
    ]
    summary = review_summary(recommendations, {})
    assert summary["pending"] == 2
    assert summary["p1_pending"] == 1
    assert summary["completion_rate"] == 0.0
