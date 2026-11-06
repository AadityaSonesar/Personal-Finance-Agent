"""Formatting helpers so numbers, dates and money render consistently."""

from __future__ import annotations

from datetime import date, datetime

from core.config import get_config


def symbol() -> str:
    return get_config().currency_symbol


def money(value: float | None, *, decimals: int = 0, signed: bool = False) -> str:
    """Format an amount with the configured currency symbol."""
    if value is None:
        return "—"
    prefix = "+" if signed and value > 0 else ""
    body = f"{prefix}{symbol()}{abs(value):,.{decimals}f}"
    return f"-{body}" if value < 0 else body


def compact_money(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 10_000_000:
        return f"{sign}{symbol()}{magnitude / 10_000_000:.2f}Cr"
    if magnitude >= 100_000:
        return f"{sign}{symbol()}{magnitude / 100_000:.2f}L"
    if magnitude >= 1_000:
        return f"{sign}{symbol()}{magnitude / 1_000:.1f}K"
    return f"{sign}{symbol()}{magnitude:,.0f}"


def percent(value: float | None, *, decimals: int = 1, scale: float = 1.0) -> str:
    if value is None:
        return "—"
    return f"{value * scale * 100:.{decimals}f}%"


def points(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def date_label(value: date | datetime | str | None, fmt: str = "%d %b %Y") -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime(fmt)


def month_label(month_key: str) -> str:
    try:
        return datetime.strptime(month_key, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return month_key
