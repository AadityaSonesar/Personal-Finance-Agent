"""Canonical taxonomy and vocabulary shared by every module.

Kept dependency-free so configuration, models and the tool layer can all
import it without import cycles.
"""

from __future__ import annotations

RULE_VERSION = "1.0.0"

CATEGORY_FOOD = "Food and Dining"
CATEGORY_TRANSPORT = "Transportation"
CATEGORY_SHOPPING = "Shopping"
CATEGORY_ENTERTAINMENT = "Entertainment"
CATEGORY_HEALTHCARE = "Healthcare"
CATEGORY_EDUCATION = "Education"
CATEGORY_HOUSING = "Rent and Housing"
CATEGORY_UTILITIES = "Utilities"
CATEGORY_SUBSCRIPTIONS = "Subscriptions"
CATEGORY_INVESTMENTS = "Investments"
CATEGORY_SAVINGS = "Savings"
CATEGORY_OTHER = "Other"
CATEGORY_INCOME = "Income"

EXPENSE_CATEGORIES: tuple[str, ...] = (
    CATEGORY_FOOD,
    CATEGORY_TRANSPORT,
    CATEGORY_SHOPPING,
    CATEGORY_ENTERTAINMENT,
    CATEGORY_HEALTHCARE,
    CATEGORY_EDUCATION,
    CATEGORY_HOUSING,
    CATEGORY_UTILITIES,
    CATEGORY_SUBSCRIPTIONS,
    CATEGORY_INVESTMENTS,
    CATEGORY_SAVINGS,
    CATEGORY_OTHER,
)

ALL_CATEGORIES: tuple[str, ...] = EXPENSE_CATEGORIES + (CATEGORY_INCOME,)

#: Loose spellings users and bank exports produce, mapped to the taxonomy.
CATEGORY_ALIASES: dict[str, str] = {
    "food": CATEGORY_FOOD,
    "food and dining": CATEGORY_FOOD,
    "dining": CATEGORY_FOOD,
    "restaurant": CATEGORY_FOOD,
    "groceries": CATEGORY_FOOD,
    "transport": CATEGORY_TRANSPORT,
    "transportation": CATEGORY_TRANSPORT,
    "travel": CATEGORY_TRANSPORT,
    "fuel": CATEGORY_TRANSPORT,
    "shopping": CATEGORY_SHOPPING,
    "retail": CATEGORY_SHOPPING,
    "entertainment": CATEGORY_ENTERTAINMENT,
    "health": CATEGORY_HEALTHCARE,
    "healthcare": CATEGORY_HEALTHCARE,
    "medical": CATEGORY_HEALTHCARE,
    "education": CATEGORY_EDUCATION,
    "rent": CATEGORY_HOUSING,
    "housing": CATEGORY_HOUSING,
    "rent and housing": CATEGORY_HOUSING,
    "utilities": CATEGORY_UTILITIES,
    "bills": CATEGORY_UTILITIES,
    "subscription": CATEGORY_SUBSCRIPTIONS,
    "subscriptions": CATEGORY_SUBSCRIPTIONS,
    "investments": CATEGORY_INVESTMENTS,
    "investment": CATEGORY_INVESTMENTS,
    "savings": CATEGORY_SAVINGS,
    "other": CATEGORY_OTHER,
    "misc": CATEGORY_OTHER,
    "income": CATEGORY_INCOME,
    "salary": CATEGORY_INCOME,
}

TRANSACTION_TYPES = ("expense", "income")
CATEGORY_SOURCES = ("override", "statement", "learned", "merchant_map", "keyword_rule", "ai", "default")

PRIORITIES = ("P1", "P2", "P3", "P4")
PRIORITY_LABELS = {
    "P1": "Critical",
    "P2": "High",
    "P3": "Medium",
    "P4": "Low",
}

RISK_LEVELS = ("low", "medium", "high")

ANOMALY_TYPES = (
    "unusually_large_transaction",
    "spending_spike",
    "repeated_transaction",
    "duplicate_payment",
    "unusual_merchant_activity",
    "unexpected_category_spend",
    "suspicious_merchant_instruction",
)

SUBSCRIPTION_STATUSES = ("confirmed_recurring", "potential_subscription", "needs_verification")

REVIEW_DECISIONS = ("APPROVE", "EDIT", "HOLD", "REQUEST_INFO")

AGENT_NAMES = (
    "ingestion_agent",
    "validation_agent",
    "categorization_agent",
    "anomaly_agent",
    "subscription_agent",
    "spending_intelligence_agent",
    "budget_agent",
    "financial_scoring_agent",
    "cashflow_agent",
    "advisor_agent",
    "notification_agent",
)

#: Ordered pipeline used by the orchestrator and rendered by the monitoring UI.
WORKFLOW_STAGES = (
    ("ingestion", "Ingestion"),
    ("validation", "Validation"),
    ("parallel_analysis", "Parallel analysis"),
    ("dependent_analysis", "Sequential analysis"),
    ("scoring", "Health assessment"),
    ("forecast", "Cash flow forecast"),
    ("advisory", "Recommendations"),
    ("alerts", "Alerts & notification"),
    ("review", "Human review gate"),
)
