"""Categorization precedence and determinism."""

from __future__ import annotations

from datetime import date

from core.categorization import categorize_transactions, classify_one, normalize_merchant
from tests.helpers import tx


def _config_with_rules(config):
    cfg = config.model_copy(deep=True)
    cfg.financial.category_rules.merchant_map = {"swiggy": "Food and Dining"}
    cfg.financial.category_rules.keyword_rules = [
        {"category": "Healthcare", "keywords": ["pharmacy", "clinic"]}
    ]
    return cfg


def test_merchant_normalization_is_stable():
    assert normalize_merchant("SWIGGY*ORDER 12345") == "swiggy"
    assert normalize_merchant("Netflix.com") == "netflix com"


def test_merchant_map_drives_category(config):
    cfg = _config_with_rules(config)
    transaction = tx(1, date(2026, 1, 5), "SWIGGY*ORDER 991", 480)
    category, source = classify_one(transaction, config=cfg)
    assert category == "Food and Dining"
    assert source == "merchant_map"


def test_keyword_rules_apply_when_merchant_is_unknown(config):
    cfg = _config_with_rules(config)
    transaction = tx(1, date(2026, 1, 5), "Apollo", 260, description="pharmacy purchase")
    category, source = classify_one(transaction, config=cfg)
    assert category == "Healthcare"
    assert source == "keyword_rule"


def test_rule_engine_outranks_ai_suggestion(config):
    cfg = _config_with_rules(config)
    transaction = tx(1, date(2026, 1, 5), "Swiggy", 480)
    category, source = classify_one(transaction, config=cfg, ai_suggestion="Shopping")
    assert category == "Food and Dining"
    assert source == "merchant_map"


def test_ai_is_used_only_as_a_fallback(config):
    cfg = _config_with_rules(config)
    transaction = tx(1, date(2026, 1, 5), "Zenith Academy", 900)
    category, source = classify_one(transaction, config=cfg, ai_suggestion="Education")
    assert category == "Education"
    assert source == "ai"


def test_statement_category_precedes_merchant_map(config):
    cfg = _config_with_rules(config)
    transaction = tx(
        1, date(2026, 1, 5), "Swiggy", 480,
        category="Shopping", category_source="statement",
    )
    category, source = classify_one(transaction, config=cfg)
    assert category == "Shopping"
    assert source == "statement"


def test_learned_merchant_preference_beats_configured_map(config):
    cfg = _config_with_rules(config)
    transaction = tx(1, date(2026, 1, 5), "Swiggy", 480)
    category, source = classify_one(transaction, config=cfg, merchant_overrides={"swiggy": "Shopping"})
    assert category == "Shopping"
    assert source == "learned"


def test_transaction_override_has_highest_priority(config):
    cfg = _config_with_rules(config)
    transaction = tx(1, date(2026, 1, 5), "Swiggy", 480)
    category, source = classify_one(
        transaction, config=cfg, transaction_overrides={transaction.transaction_id: "Entertainment"}
    )
    assert category == "Entertainment"
    assert source == "override"


def test_income_is_always_income(config):
    transaction = tx(1, date(2026, 1, 1), "Employer", 90000, tx_type="income", category="Other")
    category, source = classify_one(transaction, config=config)
    assert category == "Income"
    assert source == "default"


def test_unknown_merchant_falls_back_to_other(config):
    transaction = tx(1, date(2026, 1, 5), "Zzz Unknown Vendor", 100)
    assert classify_one(transaction, config=config) == ("Other", "default")


def test_batch_categorization_is_deterministic(config):
    cfg = _config_with_rules(config)
    transactions = [
        tx(1, date(2026, 1, 5), "Swiggy", 480),
        tx(2, date(2026, 1, 6), "Apollo", 260, description="pharmacy"),
        tx(3, date(2026, 1, 7), "Mystery Shop", 90),
    ]
    first = categorize_transactions(transactions, config=cfg)
    second = categorize_transactions(transactions, config=cfg)
    assert [t.category for t in first.transactions] == [t.category for t in second.transactions]
    assert first.counts_by_source == second.counts_by_source
    assert first.by_category == second.by_category
    assert first.by_category["Other"] == 1
