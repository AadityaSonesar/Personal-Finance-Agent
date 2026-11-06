"""Deterministic anomaly and fraud-signal detection.

Thresholds are configuration-driven and every finding records the arithmetic
that produced it, so a reviewer can audit the conclusion.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any

import numpy as np

from core.categorization import normalize_merchant
from core.config import AppConfig, get_config
from core.models import Anomaly, Transaction
from core.security import hash_identifier, sanitize_text

SECURITY_FLAGS = {
    "instruction_override",
    "role_manipulation",
    "balance_tampering",
    "approval_bypass",
    "secret_exfiltration",
    "tool_invocation",
    "boundary_markers",
}

MAX_ANOMALIES = 60


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _severity(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _spike_score(ratio: float) -> float:
    return _clamp(0.45 + 0.2 * math.log2(max(ratio, 1.5)))


def detect_anomalies(
    transactions: list[Transaction],
    *,
    config: AppConfig | None = None,
    reference_month: str | None = None,
) -> list[Anomaly]:
    """Run every deterministic detector and return scored, deduplicated findings."""
    config = config or get_config()
    cfg = config.financial.anomaly
    expenses = [t for t in transactions if t.transaction_type == "expense"]
    if not expenses:
        return []

    findings: list[Anomaly] = []
    reference_month = reference_month or max(t.month_key for t in expenses)
    months = sorted({t.month_key for t in expenses})
    prior_months = [m for m in months if m < reference_month]

    # 1 — unusually large transaction -------------------------------------
    by_category: dict[str, list[float]] = defaultdict(list)
    for t in expenses:
        by_category[t.category].append(t.amount)
    overall_median = float(np.median([t.amount for t in expenses]))

    large_findings: list[Anomaly] = []
    for t in expenses:
        peer_amounts = by_category.get(t.category, [])
        if len(peer_amounts) >= cfg.large_transaction_min_history:
            reference = float(np.median(peer_amounts))
            basis = f"median {t.category} expense"
        else:
            reference = overall_median
            basis = "median expense across all categories"
        if reference <= 0:
            continue

        ratio = t.amount / reference
        threshold = max(cfg.large_transaction_absolute, cfg.large_transaction_multiplier * reference)
        if t.amount < threshold:
            continue

        score = _clamp(0.45 + 0.18 * math.log2(max(ratio, 2.0)))
        large_findings.append(
            Anomaly(
                anomaly_id=hash_identifier("large", t.transaction_id, prefix="AN"),
                anomaly_type="unusually_large_transaction",
                severity=_severity(score),
                score=round(score, 3),
                merchant=t.merchant,
                amount=round(t.amount, 2),
                transaction_ids=[t.transaction_id],
                category=t.category,
                title=f"Unusually large payment at {t.merchant}",
                evidence=[
                    f"Amount is {ratio:.1f}× the {basis} of {reference:,.0f}.",
                    f"Threshold applied: max({cfg.large_transaction_absolute:,.0f}, "
                    f"{cfg.large_transaction_multiplier:.1f}× reference).",
                ],
                metrics={"ratio": round(ratio, 2), "reference": round(reference, 2)},
                date_ref=t.transaction_date.isoformat(),
            )
        )
    large_findings.sort(key=lambda a: a.score, reverse=True)
    findings.extend(large_findings[:10])

    # 2 — overall monthly spending spike ----------------------------------
    monthly_totals: dict[str, float] = defaultdict(float)
    for t in expenses:
        monthly_totals[t.month_key] += t.amount
    if len(prior_months) >= cfg.spike_min_months:
        prior_values = [monthly_totals[m] for m in prior_months]
        baseline = float(np.mean(prior_values))
        current = monthly_totals.get(reference_month, 0.0)
        if baseline > 0:
            ratio = current / baseline
            if ratio >= cfg.spike_multiplier:
                score = _spike_score(ratio)
                findings.append(
                    Anomaly(
                        anomaly_id=hash_identifier("spike", reference_month, f"{current:.2f}", prefix="AN"),
                        anomaly_type="spending_spike",
                        severity=_severity(score),
                        score=round(score, 3),
                        amount=round(current, 2),
                        category="All categories",
                        title=f"Total spending in {reference_month} is {ratio:.1f}× the recent average",
                        evidence=[
                            f"Month-to-date spending is {current:,.0f} against a {len(prior_values)}-month average of {baseline:,.0f}.",
                            f"Spike threshold: {cfg.spike_multiplier:.1f}× the trailing average.",
                        ],
                        metrics={"ratio": round(ratio, 2), "baseline": round(baseline, 2)},
                        date_ref=reference_month,
                    )
                )

    # 3 — exact duplicate payments ---------------------------------------
    duplicate_groups: dict[tuple[str, str, float], list[Transaction]] = defaultdict(list)
    for t in expenses:
        duplicate_groups[(normalize_merchant(t.merchant), t.transaction_date.isoformat(), round(t.amount, 2))].append(t)
    for (merchant_key, day, amount), group in duplicate_groups.items():
        if len(group) < 2 or amount <= 0:
            continue
        score = _clamp(0.6 + 0.1 * (len(group) - 2) + (0.1 if amount >= cfg.critical_amount else 0))
        findings.append(
            Anomaly(
                anomaly_id=hash_identifier("dupe", merchant_key, day, f"{amount:.2f}", prefix="AN"),
                anomaly_type="duplicate_payment",
                severity=_severity(score),
                score=round(score, 3),
                merchant=group[0].merchant,
                amount=round(amount * len(group), 2),
                transaction_ids=[t.transaction_id for t in group],
                category=group[0].category,
                title=f"Possible duplicate payment at {group[0].merchant}",
                evidence=[
                    f"{len(group)} identical charges of {amount:,.0f} on {day}.",
                    "Confirm with the merchant or statement before treating this as fraud.",
                ],
                metrics={"occurrences": len(group), "unit_amount": round(amount, 2)},
                date_ref=day,
            )
        )

    # 4 — repeated identical charges --------------------------------------
    repeated: dict[tuple[str, float], list[Transaction]] = defaultdict(list)
    for t in expenses:
        repeated[(normalize_merchant(t.merchant), round(t.amount, 2))].append(t)
    for (merchant_key, amount), group in repeated.items():
        if len(group) < cfg.repeated_transaction_threshold:
            continue
        if len({t.transaction_date for t in group}) < 2:
            continue  # already covered by duplicate payment detection
        spread = (max(t.transaction_date for t in group) - min(t.transaction_date for t in group)).days
        if spread > cfg.repeated_transaction_window_days * 3:
            continue
        score = _clamp(0.4 + 0.08 * len(group))
        findings.append(
            Anomaly(
                anomaly_id=hash_identifier("repeat", merchant_key, f"{amount:.2f}", len(group), prefix="AN"),
                anomaly_type="repeated_transaction",
                severity=_severity(score),
                score=round(score, 3),
                merchant=group[0].merchant,
                amount=round(amount * len(group), 2),
                transaction_ids=[t.transaction_id for t in group],
                category=group[0].category,
                title=f"Repeated identical charges at {group[0].merchant}",
                evidence=[
                    f"{len(group)} charges of {amount:,.0f} between "
                    f"{min(t.transaction_date for t in group).isoformat()} and "
                    f"{max(t.transaction_date for t in group).isoformat()}.",
                    "Repeated equal amounts can indicate a split order, a billing error or an unlisted subscription.",
                ],
                metrics={"occurrences": len(group), "unit_amount": round(amount, 2)},
                date_ref=max(t.transaction_date for t in group).isoformat(),
            )
        )

    # 5 — unusual merchant activity ---------------------------------------
    merchant_monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in expenses:
        merchant_monthly[normalize_merchant(t.merchant)][t.month_key] += t.amount
    for merchant_key, per_month in merchant_monthly.items():
        prior = [per_month[m] for m in prior_months if per_month.get(m)]
        if len(prior) < 2:
            continue
        current = per_month.get(reference_month, 0.0)
        baseline = float(np.mean(prior))
        if baseline <= 0 or current <= 0:
            continue
        ratio = current / baseline
        if ratio < cfg.merchant_activity_multiplier:
            continue
        score = _clamp(0.4 + 0.15 * math.log2(max(ratio, 2)))
        findings.append(
            Anomaly(
                anomaly_id=hash_identifier("merchant", merchant_key, reference_month, prefix="AN"),
                anomaly_type="unusual_merchant_activity",
                severity=_severity(score),
                score=round(score, 3),
                merchant=merchant_key.title(),
                amount=round(current, 2),
                category="Merchant",
                title=f"Unusual activity at {merchant_key.title()}",
                evidence=[
                    f"Spending with this merchant reached {current:,.0f} in {reference_month}, "
                    f"{ratio:.1f}× the average of {baseline:,.0f}.",
                ],
                metrics={"ratio": round(ratio, 2), "baseline": round(baseline, 2)},
                date_ref=reference_month,
            )
        )

    # 6 — unexpected category expenditure ---------------------------------
    category_monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in expenses:
        category_monthly[t.category][t.month_key] += t.amount
    for category, per_month in category_monthly.items():
        prior = [per_month[m] for m in prior_months if per_month.get(m)]
        if len(prior) < cfg.spike_min_months:
            continue
        current = per_month.get(reference_month, 0.0)
        baseline = float(np.mean(prior))
        if baseline <= 0 or current <= 0:
            continue
        ratio = current / baseline
        if ratio < cfg.unexpected_category_multiplier:
            continue
        score = _clamp(0.4 + 0.15 * math.log2(max(ratio, 2)))
        findings.append(
            Anomaly(
                anomaly_id=hash_identifier("category", category, reference_month, prefix="AN"),
                anomaly_type="unexpected_category_spend",
                severity=_severity(score),
                score=round(score, 3),
                amount=round(current, 2),
                category=category,
                title=f"{category} spending is {ratio:.1f}× its recent norm",
                evidence=[
                    f"{category} reached {current:,.0f} in {reference_month} against a "
                    f"{len(prior)}-month average of {baseline:,.0f}.",
                ],
                metrics={"ratio": round(ratio, 2), "baseline": round(baseline, 2)},
                date_ref=reference_month,
            )
        )

    # 7 — instruction-like text inside transaction data -------------------
    for t in expenses:
        flags = sorted(set(t.sanitization_flags) & SECURITY_FLAGS)
        if not flags:
            continue
        snippet = sanitize_text(t.description or t.merchant, max_length=90).text
        findings.append(
            Anomaly(
                anomaly_id=hash_identifier("injection", t.transaction_id, prefix="AN"),
                anomaly_type="suspicious_merchant_instruction",
                severity="critical",
                score=1.0,
                merchant=t.merchant,
                amount=round(t.amount, 2),
                transaction_ids=[t.transaction_id],
                category=t.category,
                title="Instruction-like text embedded in transaction data",
                evidence=[
                    f"Transaction text triggered: {', '.join(f.replace('_', ' ') for f in flags)}.",
                    f"Text was retained as inert description data: “{snippet}”",
                    "No embedded instruction was executed and no balance, score or approval state changed.",
                ],
                metrics={"flags": flags},
                date_ref=t.transaction_date.isoformat(),
            )
        )

    findings.sort(key=lambda a: a.score, reverse=True)
    return findings[:MAX_ANOMALIES]


def summarize_anomalies(anomalies: list[Anomaly]) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    for anomaly in anomalies:
        by_type[anomaly.anomaly_type] += 1
        by_severity[anomaly.severity] += 1
    return {
        "total": len(anomalies),
        "by_type": dict(sorted(by_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "highest_score": max((a.score for a in anomalies), default=0.0),
        "critical_count": by_severity.get("critical", 0) + by_severity.get("high", 0),
    }


def duplicate_flagged(transactions: list[Transaction]) -> list[Transaction]:
    return [t for t in transactions if t.is_duplicate_suspect]


def window_start(days: int, *, today: date | None = None) -> date:
    today = today or date.today()
    return date.fromordinal(today.toordinal() - days)
