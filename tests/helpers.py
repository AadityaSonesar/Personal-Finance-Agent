"""Builders for transactions and raw statement rows."""

from __future__ import annotations

from datetime import date
from typing import Any

from core.models import Transaction


def tx(
    index: int,
    when: date,
    merchant: str,
    amount: float,
    *,
    tx_type: str = "expense",
    category: str = "Other",
    description: str = "",
    **kwargs: Any,
) -> Transaction:
    return Transaction(
        transaction_id=f"T{index:04d}",
        transaction_date=when,
        merchant=merchant,
        description=description,
        amount=amount,
        transaction_type=tx_type,  # type: ignore[arg-type]
        category=category,
        **kwargs,
    )


def raw(
    ref: int,
    when: str,
    merchant: str,
    amount: str,
    *,
    tx_type: str = "Debit",
    description: str = "",
    transaction_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "transaction_id": transaction_id if transaction_id is not None else f"R{ref:04d}",
        "transaction_date": when,
        "merchant": merchant,
        "description": description,
        "amount": amount,
        "transaction_type": tx_type,
        "_row_ref": ref,
    }
    row.update(extra)
    return row


def stage(store, rows: list[dict[str, Any]], file_name: str = "statement.csv") -> None:
    store.save_staging(
        rows,
        {
            "file_name": file_name,
            "file_type": "csv",
            "row_count": len(rows),
            "extraction_complete": True,
            "requires_manual_review": False,
            "notes": [],
            "warnings": [],
        },
    )


def monthly_series(
    merchant: str,
    amount: float,
    day: int,
    months: list[tuple[int, int]],
    *,
    start_index: int = 1,
    category: str = "Subscriptions",
) -> list[Transaction]:
    result = []
    for offset, (year, month) in enumerate(months):
        result.append(
            tx(
                start_index + offset,
                date(year, month, day),
                merchant,
                amount,
                category=category,
            )
        )
    return result
