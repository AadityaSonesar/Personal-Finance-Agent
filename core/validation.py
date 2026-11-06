"""Transaction validation, normalization and quarantine.

Malformed records are never dropped silently: each rejection carries human
readable reasons, the offending row and a timestamp, and is appended to
``rejected_records.csv``.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from core.config import AppConfig, get_config
from core.models import (
    RejectedRecord,
    Transaction,
    ValidationOutcome,
    ValidationStats,
    canonical_category,
)
from core.security import (
    detect_instruction_override,
    hash_identifier,
    redact_account,
    sanitize_text,
)

DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y%m%d",
)

AMOUNT_CLEAN = re.compile(r"[₹$€£¥,\s']")
NUMERIC_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
_EXCEL_EPOCH = date(1899, 12, 30)

INCOME_HINTS = {"credit", "cr", "income", "deposit", "received", "inflow", "salary"}
EXPENSE_HINTS = {"debit", "dr", "expense", "withdrawal", "payment", "outflow", "spent"}


def parse_amount(raw: Any) -> tuple[float | None, str]:
    """Return (value, error_reason). Value is a signed magnitude or None."""
    if raw is None:
        return None, "missing amount"
    text = str(raw).strip()
    if not text:
        return None, "missing amount"
    cleaned = AMOUNT_CLEAN.sub("", text)
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1]
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    cleaned = cleaned.lstrip("+")
    if not cleaned or not NUMERIC_RE.match(cleaned):
        return None, f"invalid amount '{text[:24]}'"
    try:
        value = float(cleaned)
    except ValueError:
        return None, f"invalid amount '{text[:24]}'"
    if not math.isfinite(value):
        return None, "non-finite amount"
    if value == 0:
        return None, "zero amount"
    return (-value if negative else value), ""


def parse_date(raw: Any, config: AppConfig | None = None) -> tuple[date | None, str]:
    config = config or get_config()
    if raw is None:
        return None, "missing date"
    text = str(raw).strip()
    if not text:
        return None, "missing date"
    text = text.replace("T00:00:00", "").split(" ")[0] if "T" in text and len(text) > 10 else text

    parsed: date | None = None
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue

    if parsed is None and re.fullmatch(r"\d{5}(\.\d+)?", text):
        try:
            serial = int(float(text))
            if 20000 <= serial <= 80000:
                parsed = _EXCEL_EPOCH + timedelta(days=serial)
        except ValueError:
            parsed = None

    if parsed is None:
        return None, f"invalid date '{text[:24]}'"

    try:
        minimum = date.fromisoformat(config.security.min_valid_date)
    except ValueError:
        minimum = date(2000, 1, 1)
    if parsed < minimum:
        return None, f"date {parsed.isoformat()} is before the supported range"
    if parsed > date.today() + timedelta(days=1):
        return None, f"date {parsed.isoformat()} is in the future"
    return parsed, ""


def determine_type(raw_type: Any, amount: float, category: str | None) -> str:
    hint = str(raw_type or "").strip().casefold()
    if hint in INCOME_HINTS:
        return "income"
    if hint in EXPENSE_HINTS:
        return "expense"
    if category and canonical_category(category) == "Income":
        return "income"
    return "expense" if amount < 0 else "expense"


def _signature(date_value: date, merchant: str, amount: float, tx_type: str, description: str) -> str:
    return "|".join([date_value.isoformat(), merchant.casefold(), f"{amount:.2f}", tx_type, description.casefold()])


def validate_rows(
    rows: Iterable[dict[str, Any]],
    *,
    existing: list[Transaction] | None = None,
    source_file: str = "",
    source: str = "upload",
    config: AppConfig | None = None,
) -> ValidationOutcome:
    """Validate raw parsed rows into transactions plus quarantined rejects."""
    config = config or get_config()
    existing = existing or []
    outcome = ValidationOutcome()
    stats = ValidationStats()

    seen_ids: set[str] = {t.transaction_id for t in existing}
    seen_signatures: set[str] = {t.duplicate_signature for t in existing}

    rows_list = list(rows)
    stats.rows_received = len(rows_list)

    for row in rows_list:
        row_ref = int(row.get("_row_ref", stats.rows_received))
        raw_for_audit = {
            k: (v[:200] if isinstance(v, str) else v)
            for k, v in row.items()
            if not k.startswith("_")
        }
        reasons: list[str] = []

        tx_date, date_error = parse_date(row.get("transaction_date", ""))
        if date_error:
            reasons.append(date_error)

        amount_value, amount_error = parse_amount(row.get("amount", ""))
        if amount_error:
            reasons.append(amount_error)
            if "amount" in amount_error:
                stats.invalid_amounts += 1
        elif amount_value is not None and abs(amount_value) > config.security.max_amount:
            reasons.append(f"amount exceeds the maximum supported value of {config.security.max_amount:,.0f}")
            stats.invalid_amounts += 1

        merchant_raw = sanitize_text(row.get("merchant", ""), max_length=config.security.max_field_length)
        description_raw = sanitize_text(
            row.get("description", ""), max_length=config.security.max_description_length
        )
        merchant = merchant_raw.text
        description = description_raw.text
        if not merchant and description:
            merchant = description[:80]
        if not merchant:
            reasons.append("missing merchant and description")
            stats.missing_fields += 1

        if date_error or amount_error:
            stats.missing_fields += 1 if ("missing" in date_error or "missing" in amount_error) else 0

        injection_flags = sorted(
            set(merchant_raw.flags)
            | set(description_raw.flags)
            | set(detect_instruction_override(row.get("merchant", "")))
            | set(detect_instruction_override(row.get("description", "")))
        )
        if injection_flags:
            stats.sanitized_fields += 1

        if reasons:
            outcome.rejected.append(
                RejectedRecord(
                    row_ref=row_ref,
                    reasons=reasons,
                    raw=raw_for_audit,
                    source_file=source_file,
                )
            )
            continue

        assert amount_value is not None and tx_date is not None
        magnitude = round(abs(amount_value), 2)
        category_raw = str(row.get("category", "") or "").strip()
        category = canonical_category(category_raw) if category_raw else "Other"
        tx_type = determine_type(row.get("transaction_type"), magnitude, category)
        if tx_type == "income":
            category = "Income"
        elif category == "Income":
            category = "Other"

        transaction_id = sanitize_text(row.get("transaction_id", ""), max_length=64).text
        if not transaction_id:
            transaction_id = hash_identifier(
                tx_date.isoformat(), merchant.casefold(), f"{magnitude:.2f}", row_ref, prefix="TX"
            )

        if transaction_id in seen_ids:
            stats.duplicates_rejected += 1
            outcome.rejected.append(
                RejectedRecord(
                    row_ref=row_ref,
                    reasons=[f"duplicate transaction id '{transaction_id}'"],
                    raw=raw_for_audit,
                    source_file=source_file,
                )
            )
            continue

        signature = _signature(tx_date, merchant, magnitude, tx_type, description)
        duplicate_suspect = signature in seen_signatures
        if duplicate_suspect:
            stats.duplicate_suspects += 1

        transaction = Transaction(
            transaction_id=transaction_id,
            transaction_date=tx_date,
            merchant=merchant,
            description=description,
            amount=magnitude,
            transaction_type=tx_type,  # type: ignore[arg-type]
            category=category,
            category_source="statement" if category_raw and category != "Other" else "default",
            account=redact_account(row.get("account", "")),
            currency=config.app.currency,
            source=source,  # type: ignore[arg-type]
            is_duplicate_suspect=duplicate_suspect,
            sanitization_flags=injection_flags,
            row_ref=row_ref,
        )

        seen_ids.add(transaction_id)
        seen_signatures.add(signature)
        outcome.valid.append(transaction)

    stats.validated = len(outcome.valid)
    stats.rejected = len(outcome.rejected)
    stats.validation_rate = round(stats.validated / stats.rows_received, 4) if stats.rows_received else 0.0
    outcome.stats = stats

    if stats.rejected:
        outcome.warnings.append(
            f"{stats.rejected} record(s) were quarantined. Review rejected_records.csv for reasons."
        )
    if stats.duplicate_suspects:
        outcome.warnings.append(
            f"{stats.duplicate_suspects} record(s) match an existing transaction and are flagged as possible duplicates."
        )
    return outcome
