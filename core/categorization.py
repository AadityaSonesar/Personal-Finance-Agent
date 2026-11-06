"""Deterministic transaction categorization.

Precedence is fixed and testable:
locked override → statement category → learned merchant preference → configured
merchant map → keyword rules → AI suggestion (only if still unclassified) →
"Other". The rule engine always outranks AI output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.config import AppConfig, get_config
from core.constants import EXPENSE_CATEGORIES
from core.models import Transaction, canonical_category
from core.security import sanitize_text

_NON_WORD = re.compile(r"[^a-z0-9 ]")
_MULTI_SPACE = re.compile(r"\s+")
_TRAILING_DIGITS = re.compile(r"\b\d{2,}\b")
_COMPANY_SUFFIXES = re.compile(
    r"\b(pvt|private|ltd|limited|llp|inc|incorporated|corp|corporation|co|company|technologies|services)\b"
)
_NOISE = re.compile(r"\b(pos|upi|neft|imps|ach|ref|txn|payment|paid|order|online|debit|card)\b")


def normalize_merchant(value: str) -> str:
    """Reduce a messy statement merchant string to a stable lookup key."""
    text = sanitize_text(value, max_length=160).text.casefold()
    text = text.replace("*", " ").replace("/", " ").replace("-", " ").replace("_", " ")
    text = _NON_WORD.sub(" ", text)
    text = _COMPANY_SUFFIXES.sub(" ", text)
    text = _TRAILING_DIGITS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text[:80]


@dataclass
class CategorizationOutcome:
    transactions: list[Transaction] = field(default_factory=list)
    counts_by_source: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)
    ai_used: int = 0
    ai_rejected: int = 0
    warnings: list[str] = field(default_factory=list)


def _match_merchant_map(key: str, merchant_map: dict[str, str]) -> str | None:
    if not key:
        return None
    best: tuple[int, str] | None = None
    for raw_key, category in merchant_map.items():
        candidate = normalize_merchant(raw_key)
        if not candidate:
            continue
        if key == candidate or key.startswith(candidate + " ") or candidate in key.split():
            score = len(candidate)
            if best is None or score > best[0]:
                best = (score, category)
    return best[1] if best else None


def _match_keywords(text: str, rules: list[dict[str, Any]]) -> str | None:
    lowered = text.casefold()
    for rule in rules:
        category = canonical_category(str(rule.get("category", "")))
        for keyword in rule.get("keywords", []) or []:
            token = str(keyword).casefold().strip()
            if token and token in lowered:
                return category
    return None


def classify_one(
    transaction: Transaction,
    *,
    config: AppConfig | None = None,
    transaction_overrides: dict[str, str] | None = None,
    merchant_overrides: dict[str, str] | None = None,
    ai_suggestion: str | None = None,
) -> tuple[str, str]:
    """Return (category, source) for a single transaction."""
    config = config or get_config()
    transaction_overrides = transaction_overrides or {}
    merchant_overrides = merchant_overrides or {}

    if transaction.category_locked:
        return canonical_category(transaction.category), "override"

    override = transaction_overrides.get(transaction.transaction_id)
    if override:
        return canonical_category(override), "override"

    if transaction.transaction_type == "income":
        return "Income", "statement" if transaction.category_source == "statement" else "default"

    if transaction.category_source == "statement" and transaction.category in EXPENSE_CATEGORIES:
        return transaction.category, "statement"

    merchant_key = normalize_merchant(transaction.merchant)
    learned = _match_merchant_map(merchant_key, merchant_overrides)
    if learned:
        return canonical_category(learned), "learned"

    configured = _match_merchant_map(merchant_key, config.financial.category_rules.merchant_map)
    if configured:
        return canonical_category(configured), "merchant_map"

    haystack = f"{transaction.merchant} {transaction.description}"
    keyword = _match_keywords(haystack, config.financial.category_rules.keyword_rules)
    if keyword:
        return keyword, "keyword_rule"

    if ai_suggestion:
        candidate = canonical_category(ai_suggestion)
        if candidate in EXPENSE_CATEGORIES and candidate != "Other":
            return candidate, "ai"

    return "Other", "default"


def categorize_transactions(
    transactions: list[Transaction],
    *,
    config: AppConfig | None = None,
    transaction_overrides: dict[str, str] | None = None,
    merchant_overrides: dict[str, str] | None = None,
    ai_suggestions: dict[str, str] | None = None,
) -> CategorizationOutcome:
    """Categorize a batch deterministically and report provenance counts."""
    config = config or get_config()
    ai_suggestions = ai_suggestions or {}
    outcome = CategorizationOutcome()

    for transaction in transactions:
        category, source = classify_one(
            transaction,
            config=config,
            transaction_overrides=transaction_overrides,
            merchant_overrides=merchant_overrides,
            ai_suggestion=ai_suggestions.get(transaction.transaction_id),
        )
        if source == "ai":
            outcome.ai_used += 1
        updated = transaction.model_copy(update={"category": category, "category_source": source})
        outcome.transactions.append(updated)
        outcome.counts_by_source[source] = outcome.counts_by_source.get(source, 0) + 1
        outcome.by_category[category] = outcome.by_category.get(category, 0) + 1

    unresolved = outcome.by_category.get("Other", 0)
    if unresolved:
        outcome.warnings.append(
            f"{unresolved} transaction(s) remain uncategorized. Assign a category to teach the merchant rules."
        )
    return outcome


def merchant_key(transaction: Transaction) -> str:
    return normalize_merchant(transaction.merchant)
