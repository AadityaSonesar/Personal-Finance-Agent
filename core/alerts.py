"""Alert triage, SLA tracking and confirmed notification delivery."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from core.advisory import intent_for
from core.config import AppConfig, get_config
from core.constants import PRIORITIES, PRIORITY_LABELS
from core.models import Alert, Anomaly, Recommendation, utcnow
from core.observability import get_logger
from core.security import hash_identifier
from core.storage import notification_path

logger = get_logger("aurelia.alerts")


def triage(recommendation: Recommendation, *, config: AppConfig | None = None) -> dict[str, Any]:
    """Classify an alert across intent, complexity, priority and confidence."""
    config = config or get_config()
    priority = recommendation.priority if recommendation.priority in PRIORITIES else "P3"
    complexity = "low"
    if len(recommendation.facts) >= 4:
        complexity = "medium"
    if len(recommendation.facts) >= 5 or len(recommendation.evidence.get("anomaly_ids", []) or []) > 2:
        complexity = "high"

    confidence = 0.95 if not recommendation.ai_generated else 0.85
    if not recommendation.facts:
        confidence = 0.5

    return {
        "intent": intent_for(recommendation.category),
        "complexity": complexity,
        "priority": priority,
        "confidence": round(confidence, 2),
        "requires_human": True,
        "sla_minutes": config.alerts.sla_minutes[priority],
    }


def build_alerts(
    recommendations: list[Recommendation],
    anomalies: list[Anomaly],
    *,
    workflow_id: str = "",
    config: AppConfig | None = None,
    existing: list[dict[str, Any]] | None = None,
) -> list[Alert]:
    config = config or get_config()
    existing = existing or []
    existing_ids = {str(a.get("alert_id")) for a in existing}
    existing_by_id = {str(a.get("alert_id")): a for a in existing}

    alerts: list[Alert] = []
    covered_anomalies: set[str] = set()

    for recommendation in recommendations:
        assessment = triage(recommendation, config=config)
        alert_id = hash_identifier(
            "alert", recommendation.recommendation_id, recommendation.priority, prefix="AL"
        )
        covered_anomalies.update(str(a) for a in recommendation.evidence.get("anomaly_ids", []) or [])
        if alert_id in existing_ids:
            prior = existing_by_id[alert_id]
            alerts.append(
                Alert.model_validate(
                    {
                        **prior,
                        "subject": recommendation.title,
                    }
                )
            )
            continue

        created = utcnow()
        alerts.append(
            Alert(
                alert_id=alert_id,
                subject=recommendation.title,
                priority=assessment["priority"],
                intent=assessment["intent"],
                complexity=assessment["complexity"],
                confidence=assessment["confidence"],
                requires_human=True,
                source="recommendation",
                recommendation_id=recommendation.recommendation_id,
                evidence=recommendation.facts[:4],
                created_at=created,
                deadline=created + timedelta(minutes=assessment["sla_minutes"]),
                sla_minutes=assessment["sla_minutes"],
                workflow_id=workflow_id,
            )
        )

    for anomaly in anomalies:
        if anomaly.anomaly_id in covered_anomalies:
            continue
        if anomaly.score < 0.65:
            continue
        priority = "P1" if anomaly.score >= config.alerts.triage.p1_anomaly_score else "P2"
        alert_id = hash_identifier("alert", anomaly.anomaly_id, priority, prefix="AL")
        if alert_id in existing_ids:
            prior = existing_by_id[alert_id]
            alerts.append(Alert.model_validate(prior))
            continue
        created = utcnow()
        minutes = config.alerts.sla_minutes[priority]
        alerts.append(
            Alert(
                alert_id=alert_id,
                subject=anomaly.title or "Unusual financial activity detected",
                priority=priority,
                intent=intent_for("fraud" if anomaly.anomaly_type in {"duplicate_payment", "suspicious_merchant_instruction"} else "spending"),
                complexity="medium",
                confidence=round(min(1.0, 0.6 + anomaly.score / 3), 2),
                requires_human=True,
                source="anomaly",
                anomaly_id=anomaly.anomaly_id,
                evidence=anomaly.evidence[:4],
                created_at=created,
                deadline=created + timedelta(minutes=minutes),
                sla_minutes=minutes,
                workflow_id=workflow_id,
            )
        )

    alerts.sort(key=lambda a: (PRIORITIES.index(a.priority), a.deadline))
    return alerts


def sla_state(alert: Alert | dict[str, Any], *, config: AppConfig | None = None, now: datetime | None = None) -> dict[str, Any]:
    config = config or get_config()
    now = now or utcnow()
    payload = alert.model_dump(mode="json") if isinstance(alert, Alert) else dict(alert)

    def _ts(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    created = _ts(payload.get("created_at"))
    deadline = _ts(payload.get("deadline"))
    resolved = _ts(payload.get("resolved_at"))
    if not created or not deadline:
        return {"state": "unknown", "remaining_minutes": None, "threshold_minutes": None}

    reference = resolved or now
    remaining = round((deadline - reference).total_seconds() / 60, 1)
    total = max((deadline - created).total_seconds() / 60, 1)
    elapsed_ratio = max(0.0, min(2.0, (reference - created).total_seconds() / 60 / total))

    if resolved:
        state = "met" if resolved <= deadline else "breached"
    elif remaining < 0:
        state = "breached"
    elif elapsed_ratio >= config.alerts.escalation_ratio:
        state = "approaching"
    else:
        state = "on_track"

    return {
        "state": state,
        "remaining_minutes": remaining,
        "elapsed_ratio": round(elapsed_ratio, 3),
        "resolved": bool(resolved),
    }


def apply_sla(alert: Alert, *, config: AppConfig | None = None) -> Alert:
    state = sla_state(alert, config=config)
    label = {
        "met": "met",
        "breached": "breached",
        "approaching": "approaching",
        "on_track": "on_track",
        "unknown": "unknown",
    }.get(str(state["state"]), "on_track")
    if alert.resolved_at:
        alert.status = "resolved"
    return alert.model_copy(update={"escalation": label})


def _digest(alert_id: str, subject: str) -> str:
    return hashlib.sha256(f"{alert_id}|{subject}".encode("utf-8")).hexdigest()[:16]


def _deliver_local(alert: Alert, *, config: AppConfig) -> tuple[bool, str]:
    path = notification_path(config)
    record = {
        "alert_id": alert.alert_id,
        "priority": alert.priority,
        "subject": alert.subject,
        "delivered_at": utcnow().isoformat(),
        "channel": "file",
        "digest": _digest(alert.alert_id, alert.subject),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Delivery is only reported as confirmed after reading the record back.
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        return False, f"Local outbox could not be read back: {exc}"
    if not lines:
        return False, "Local outbox is empty after write."
    last = json.loads(lines[-1])
    if last.get("digest") == record["digest"] and last.get("alert_id") == alert.alert_id:
        return True, f"Confirmed in the local notification outbox ({path.name})."
    return False, "Read-back verification did not match the written record."


def _deliver_webhook(alert: Alert, *, config: AppConfig) -> tuple[bool, str]:
    url = getattr(config.alerts.notification, "webhook_url", "") or ""
    if not url:
        return False, "No webhook URL configured."
    if not url.startswith(("https://", "http://")):
        return False, "Webhook URL must be http(s)."
    payload = json.dumps(
        {
            "alert_id": alert.alert_id,
            "priority": alert.priority,
            "subject": alert.subject,
            "intent": alert.intent,
            "deadline": alert.deadline.isoformat(),
            "evidence": alert.evidence,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=config.alerts.notification.timeout_seconds) as response:
            status = getattr(response, "status", 0)
            if 200 <= status < 300:
                return True, f"Webhook confirmed with HTTP {status}."
            return False, f"Webhook responded with HTTP {status}."
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"Webhook delivery failed: {type(exc).__name__}"


def deliver_notification(
    alert: Alert,
    *,
    config: AppConfig | None = None,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Attempt delivery with bounded exponential backoff and confirmation."""
    config = config or get_config()
    cfg = config.alerts.notification
    attempts_allowed = max_attempts or cfg.retries
    channel = str(cfg.channel or "file").lower()
    attempts: list[dict[str, Any]] = []
    delivered = False
    detail = ""

    for attempt in range(1, attempts_allowed + 1):
        started = time.perf_counter()
        try:
            if channel == "webhook":
                delivered, detail = _deliver_webhook(alert, config=config)
            else:
                delivered, detail = _deliver_local(alert, config=config)
        except Exception as exc:  # never let a channel failure break the workflow
            delivered, detail = False, f"{type(exc).__name__}"
        attempts.append(
            {
                "attempt": attempt,
                "channel": channel,
                "ok": delivered,
                "detail": detail,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "at": utcnow().isoformat(),
            }
        )
        if delivered:
            break
        if attempt < attempts_allowed:
            delay = min(cfg.max_delay_ms, cfg.base_delay_ms * (2 ** (attempt - 1)))
            time.sleep(delay / 1000.0)

    if not delivered:
        logger.warning("Alert %s was not confirmed as delivered after %s attempt(s).", alert.alert_id, len(attempts))

    return {
        "channel": channel,
        "delivered": delivered,
        "confirmed": delivered,
        "attempts": attempts,
        "detail": detail,
        "attempt_count": len(attempts),
    }


def attach_notifications(
    alerts: list[Alert],
    *,
    config: AppConfig | None = None,
    only_undelivered: bool = True,
) -> list[Alert]:
    """Deliver un-notified alerts and record the confirmed outcome."""
    updated: list[Alert] = []
    for alert in alerts:
        if only_undelivered and alert.notification_delivered:
            updated.append(alert)
            continue
        outcome = deliver_notification(alert, config=config)
        updated.append(
            alert.model_copy(
                update={
                    "notifications": [*alert.notifications, outcome],
                    "notification_delivered": bool(outcome["delivered"]),
                }
            )
        )
    return updated


def acknowledge(alert: Alert, *, reviewer: str = "") -> Alert:
    return alert.model_copy(
        update={
            "acknowledged_at": utcnow(),
            "status": "acknowledged",
            "resolution": f"Acknowledged by {reviewer}".strip(),
        }
    )


def resolve(alert: Alert, *, resolution: str, reviewer: str = "") -> Alert:
    return alert.model_copy(
        update={
            "resolved_at": utcnow(),
            "status": "resolved",
            "resolution": (resolution or f"Resolved by {reviewer}").strip(),
        }
    )


def priority_label(priority: str) -> str:
    return f"{priority} {PRIORITY_LABELS.get(priority, '')}"


def alert_counts(alerts: list[Alert | dict[str, Any]], *, config: AppConfig | None = None) -> dict[str, Any]:
    counts = {p: 0 for p in PRIORITIES}
    breached = 0
    open_alerts = 0
    for alert in alerts:
        payload = alert if isinstance(alert, dict) else alert.model_dump(mode="json")
        priority = str(payload.get("priority", "P3"))
        if priority in counts:
            counts[priority] += 1
        if not payload.get("resolved_at"):
            open_alerts += 1
            if sla_state(payload, config=config)["state"] == "breached":
                breached += 1
    return {"by_priority": counts, "open": open_alerts, "breached": breached}


def triage_explanation(alert: Alert) -> str:
    return (
        f"{priority_label(alert.priority)} · intent {alert.intent.replace('_', ' ')} · "
        f"complexity {alert.complexity} · confidence {alert.confidence:.0%} · "
        f"human review {'required' if alert.requires_human else 'optional'}"
    )
