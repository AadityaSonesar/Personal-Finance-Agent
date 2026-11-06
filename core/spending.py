"""Spending intelligence: deterministic aggregations over validated data."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from core.constants import EXPENSE_CATEGORIES
from core.models import Transaction


def _frame(transactions: list[Transaction]) -> pd.DataFrame:
    if not transactions:
        return pd.DataFrame(
            columns=[
                "transaction_id", "transaction_date", "merchant", "description",
                "amount", "transaction_type", "category", "month", "day",
            ]
        )
    records = [
        {
            "transaction_id": t.transaction_id,
            "transaction_date": pd.Timestamp(t.transaction_date),
            "merchant": t.merchant,
            "description": t.description,
            "amount": float(t.amount),
            "transaction_type": t.transaction_type,
            "category": t.category,
            "is_duplicate_suspect": bool(t.is_duplicate_suspect),
        }
        for t in transactions
    ]
    frame = pd.DataFrame(records)
    frame["month"] = frame["transaction_date"].dt.strftime("%Y-%m")
    frame["day"] = frame["transaction_date"].dt.strftime("%Y-%m-%d")
    return frame


def monthly_totals(transactions: list[Transaction]) -> list[dict[str, Any]]:
    frame = _frame(transactions)
    if frame.empty:
        return []
    grouped = (
        frame.pivot_table(index="month", columns="transaction_type", values="amount", aggfunc="sum")
        .fillna(0.0)
        .sort_index()
    )
    rows: list[dict[str, Any]] = []
    for month, row in grouped.iterrows():
        expense = float(row.get("expense", 0.0))
        income = float(row.get("income", 0.0))
        rows.append(
            {
                "month": str(month),
                "expense": round(expense, 2),
                "income": round(income, 2),
                "net": round(income - expense, 2),
            }
        )
    return rows


def current_month_key(transactions: list[Transaction]) -> str | None:
    if not transactions:
        return None
    return max(t.month_key for t in transactions)


def build_spending_intelligence(
    transactions: list[Transaction],
    *,
    reference_month: str | None = None,
) -> dict[str, Any]:
    """Build every spending view the dashboard and advisory engine consume."""
    frame = _frame(transactions)
    if frame.empty:
        return {
            "available": False,
            "reason": "No validated transactions are available yet.",
            "total_income": 0.0,
            "total_expense": 0.0,
            "net": 0.0,
            "savings_rate": None,
            "category_breakdown": [],
            "monthly": [],
            "daily": [],
            "merchants": [],
            "highest_category": None,
            "month_over_month": None,
            "income_vs_expense": {},
            "months_covered": 0,
            "reference_month": None,
            "first_date": None,
            "last_date": None,
        }

    expenses = frame[frame["transaction_type"] == "expense"]
    income = frame[frame["transaction_type"] == "income"]
    reference_month = reference_month or str(frame["month"].max())
    month_frame = expenses[expenses["month"] == reference_month]
    previous_months = sorted(m for m in expenses["month"].unique() if m < reference_month)

    total_expense = float(expenses["amount"].sum())
    total_income = float(income["amount"].sum())
    net = total_income - total_expense
    savings_rate = (net / total_income) if total_income > 0 else None

    category_breakdown = []
    if not month_frame.empty:
        grouped = month_frame.groupby("category")["amount"].agg(["sum", "count", "mean"]).sort_values("sum", ascending=False)
        month_total = float(grouped["sum"].sum()) or 1.0
        for category, row in grouped.iterrows():
            category_breakdown.append(
                {
                    "category": str(category),
                    "amount": round(float(row["sum"]), 2),
                    "count": int(row["count"]),
                    "average": round(float(row["mean"]), 2),
                    "share": round(float(row["sum"]) / month_total, 4),
                }
            )

    merchants = []
    if not month_frame.empty:
        grouped = month_frame.groupby("merchant")["amount"].agg(["sum", "count", "mean"]).sort_values("sum", ascending=False)
        for merchant, row in grouped.head(15).iterrows():
            merchants.append(
                {
                    "merchant": str(merchant),
                    "amount": round(float(row["sum"]), 2),
                    "count": int(row["count"]),
                    "average": round(float(row["mean"]), 2),
                }
            )

    daily = []
    if not month_frame.empty:
        grouped = month_frame.groupby("day")["amount"].sum().sort_index()
        for day, amount in grouped.items():
            daily.append({"date": str(day), "amount": round(float(amount), 2)})

    monthly = monthly_totals(transactions)

    mom = None
    if previous_months:
        previous = previous_months[-1]
        current_total = float(month_frame["amount"].sum())
        previous_total = float(expenses[expenses["month"] == previous]["amount"].sum())
        delta = current_total - previous_total
        mom = {
            "current_month": reference_month,
            "previous_month": previous,
            "current_total": round(current_total, 2),
            "previous_total": round(previous_total, 2),
            "delta": round(delta, 2),
            "change_ratio": round(delta / previous_total, 4) if previous_total else None,
        }

    this_month_income = float(income[income["month"] == reference_month]["amount"].sum())
    return {
        "available": True,
        "reference_month": reference_month,
        "months_covered": int(frame["month"].nunique()),
        "first_date": str(frame["transaction_date"].min().date()),
        "last_date": str(frame["transaction_date"].max().date()),
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "net": round(net, 2),
        "savings_rate": round(savings_rate, 4) if savings_rate is not None else None,
        "month_income": round(this_month_income, 2),
        "month_expense": round(float(month_frame["amount"].sum()), 2),
        "month_transactions": int(len(month_frame)),
        "category_breakdown": category_breakdown,
        "highest_category": category_breakdown[0] if category_breakdown else None,
        "monthly": monthly,
        "daily": daily,
        "merchants": merchants,
        "month_over_month": mom,
        "income_vs_expense": {
            "income": round(this_month_income, 2),
            "expense": round(float(month_frame["amount"].sum()), 2),
            "ratio": round(
                float(month_frame["amount"].sum()) / this_month_income, 4
            ) if this_month_income > 0 else None,
        },
    }


def category_month_matrix(transactions: list[Transaction]) -> pd.DataFrame:
    """Category × month expense totals, used for spike detection."""
    frame = _frame(transactions)
    if frame.empty:
        return pd.DataFrame()
    expenses = frame[frame["transaction_type"] == "expense"]
    if expenses.empty:
        return pd.DataFrame()
    matrix = expenses.pivot_table(index="month", columns="category", values="amount", aggfunc="sum").fillna(0.0)
    return matrix.sort_index()


def expense_basis(transactions: list[Transaction], *, months: int = 3) -> tuple[float | None, list[str]]:
    """Average monthly expense over the most recent complete months.

    Returns (average, months_used). The newest month is excluded when at least
    two months exist, because a partial month understates true expenditure.
    """
    monthly = monthly_totals(transactions)
    expense_months = [m for m in monthly if m["expense"] > 0]
    if not expense_months:
        return None, []
    complete = expense_months[:-1] if len(expense_months) >= 2 else expense_months
    used = complete[-months:]
    if not used:
        return None, []
    values = [m["expense"] for m in used]
    return round(float(np.mean(values)), 2), [m["month"] for m in used]


def month_expense(transactions: list[Transaction], month: str) -> float:
    return round(sum(t.amount for t in transactions if t.transaction_type == "expense" and t.month_key == month), 2)


def month_income(transactions: list[Transaction], month: str) -> float:
    return round(sum(t.amount for t in transactions if t.transaction_type == "income" and t.month_key == month), 2)


def spending_by_category(transactions: list[Transaction], month: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for t in transactions:
        if t.transaction_type == "expense" and t.month_key == month:
            totals[t.category] += t.amount
    return {k: round(v, 2) for k, v in totals.items()}


def available_months(transactions: list[Transaction]) -> list[str]:
    return sorted({t.month_key for t in transactions})


def days_in_month(month_key: str) -> int:
    year, month = (int(part) for part in month_key.split("-"))
    if month == 12:
        return 31
    next_month = date(year + (month // 12), (month % 12) + 1, 1)
    return (next_month - date(year, month, 1)).days


def sorted_categories() -> list[str]:
    return list(EXPENSE_CATEGORIES)
