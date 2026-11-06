"""Page registry for the dashboard."""

from __future__ import annotations

from ui.views import (
    advisor,
    alerts_page,
    audit_page,
    budgets,
    forecast,
    health_page,
    intelligence,
    overview,
    transactions,
)

PAGES = {
    "Overview": overview.render,
    "Transactions": transactions.render,
    "Budget Management": budgets.render,
    "Financial Intelligence": intelligence.render,
    "Financial Health": health_page.render,
    "Cash Flow Forecast": forecast.render,
    "AI Financial Advisor": advisor.render,
    "Alerts & Approvals": alerts_page.render,
    "Audit & Monitoring": audit_page.render,
}
