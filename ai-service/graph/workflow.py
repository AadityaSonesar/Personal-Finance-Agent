"""LangGraph workflow with LLM-powered intelligence nodes.

Architecture:
  normalize          → Validate and clean raw transactions (deterministic)
  categorize_with_llm → Groq suggests categories for uncategorized transactions
  analyse            → All deterministic financial calculations
  llm_interpret_analysis → Groq interprets verified metrics (never replaces them)
  advisor_node       → Answers the user's question from their financial context
  END

The LLM interprets verified data. It never replaces deterministic calculations.
"""
from __future__ import annotations

import json
import logging
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from core.models import Transaction
from core.anomalies import detect_anomalies
from core.subscriptions import detect_subscriptions
from core.budgets import analyze_budgets
from core.health import calculate_health
from core.cashflow import forecast_cashflow
from core.spending import build_spending_intelligence, current_month_key, available_months
from core.advisory import build_recommendations
from core.alerts import build_alerts
from core.storage import FinancialProfile
from core.config import get_config

logger = logging.getLogger("aurelia.workflow")


class FinanceState(TypedDict, total=False):
    user_id: str
    workflow_id: str
    raw_transactions: list[dict[str, Any]]
    budgets: dict[str, float]
    transactions: list[Transaction]
    rejected_transactions: list[dict[str, Any]]
    # Deterministic results
    anomalies: list[Any]
    subscriptions: list[Any]
    spending: dict[str, Any]
    budget_analysis: list[Any]
    health: Any
    forecast: Any
    recommendations: list[Any]
    alerts: list[Any]
    # LLM results (augment, never replace, deterministic values)
    llm_categorization: list[dict[str, Any]]
    llm_financial_analysis: dict[str, Any]
    llm_forecast_interpretation: dict[str, Any]
    # Workflow metadata
    agent_status: dict[str, str]
    errors: list[str]
    approval_status: str
    # Advisor context
    user_question: str
    advisor_response: dict[str, Any]


def _done(state: FinanceState, field: str, value: Any) -> dict[str, Any]:
    return {field: value, "agent_status": {**state.get("agent_status", {}), field: "ok"}}


# ---------------------------------------------------------------------------
# Node 1: Normalize (deterministic)
# ---------------------------------------------------------------------------

def normalize(state: FinanceState) -> dict[str, Any]:
    """Validate and normalize raw transactions. Deterministic."""
    valid: list[Transaction] = []
    rejected: list[dict[str, Any]] = []
    for raw in state["raw_transactions"]:
        try:
            d_val = raw["date"]
            if hasattr(d_val, "date") and callable(d_val.date):
                d_val = d_val.date()
            elif isinstance(d_val, str) and "T" in d_val:
                d_val = d_val.split("T")[0]
            valid.append(
                Transaction.model_validate(
                    {
                        "transaction_id": raw["transactionId"],
                        "merchant": raw["merchant"],
                        "description": raw.get("description", ""),
                        "amount": raw["amount"],
                        "transaction_type": raw["type"],
                        "category": raw.get("category", "Other"),
                        "transaction_date": d_val,
                        "source": "upload",
                    }
                )
            )
        except Exception:
            rejected.append(
                {
                    "transaction_id": raw.get("transactionId"),
                    "reason": "Record was not valid for analysis",
                }
            )
    return {**_done(state, "transactions", valid), "rejected_transactions": rejected}


# ---------------------------------------------------------------------------
# Node 2: LLM categorization (augments deterministic category, non-blocking)
# ---------------------------------------------------------------------------

def categorize_with_llm(state: FinanceState) -> dict[str, Any]:
    """
    Use Groq to suggest categories for uncategorized/Other transactions.

    Only processes transactions with category="Other" and no user-locked category.
    High-confidence suggestions (>= 0.5) are applied; others are left as-is.
    Failure is non-blocking — the workflow continues with deterministic categories.
    """
    transactions: list[Transaction] = state.get("transactions", [])

    uncategorized = [
        t for t in transactions
        if t.category in ("Other", "") and not t.category_locked
    ]

    if not uncategorized:
        return _done(state, "llm_categorization", [])

    try:
        from llm_groq import categorize_transactions_llm

        items = [
            {
                "transaction_id": t.transaction_id,
                "merchant": t.merchant,
                "description": t.description,
                "amount": t.amount,
                "type": t.transaction_type,
            }
            for t in uncategorized
        ]

        results = categorize_transactions_llm(items)

        # Apply high-confidence suggestions back to transactions
        result_map = {r["transaction_id"]: r for r in results}
        updated: list[Transaction] = []
        for t in transactions:
            suggestion = result_map.get(t.transaction_id)
            if suggestion and not t.category_locked and suggestion["confidence"] >= 0.5:
                updated.append(
                    t.model_copy(
                        update={
                            "category": suggestion["suggested_category"],
                            "category_source": "ai",
                        }
                    )
                )
            else:
                updated.append(t)

        statuses = {
            **state.get("agent_status", {}),
            "llm_categorization": "ok",
            "transactions": "ok",
        }
        return {
            "llm_categorization": results,
            "transactions": updated,
            "agent_status": statuses,
        }

    except Exception as exc:
        logger.warning("LLM categorization failed (non-blocking): %s", exc)
        return {
            **_done(state, "llm_categorization", []),
            "errors": [*state.get("errors", []), f"LLM categorization unavailable: {exc}"],
        }


# ---------------------------------------------------------------------------
# Node 3: Deterministic financial analysis
# ---------------------------------------------------------------------------

def analyse(state: FinanceState) -> dict[str, Any]:
    """Run all deterministic financial calculations."""
    cfg = get_config()
    transactions = state["transactions"]
    month = current_month_key(transactions)
    budgets = analyze_budgets(
        transactions, state.get("budgets", {}), month=month, config=cfg
    )
    anomalies = detect_anomalies(transactions, config=cfg, reference_month=month)
    subscriptions = detect_subscriptions(transactions, config=cfg)
    spending = build_spending_intelligence(transactions, reference_month=month)
    profile = FinancialProfile()
    health = calculate_health(
        transactions,
        profile=profile,
        budgets=state.get("budgets", {}),
        budget_statuses=budgets,
        config=cfg,
        month=month,
    )
    forecast = forecast_cashflow(
        transactions,
        profile=profile,
        subscriptions=subscriptions,
        budgets=state.get("budgets", {}),
        config=cfg,
        month=month,
    )
    recommendations = build_recommendations(
        month=month or "",
        spending=spending,
        budgets=budgets,
        health=health,
        forecast=forecast,
        anomalies=anomalies,
        subscriptions=subscriptions,
        config=cfg,
        workflow_id=state["workflow_id"],
    )
    alerts = build_alerts(
        recommendations, anomalies, workflow_id=state["workflow_id"], config=cfg
    )
    statuses = {
        **state.get("agent_status", {}),
        **{
            name: "ok"
            for name in (
                "anomalies",
                "subscriptions",
                "spending",
                "budget_analysis",
                "health",
                "forecast",
                "recommendations",
                "alerts",
            )
        },
    }
    return {
        "anomalies": anomalies,
        "subscriptions": subscriptions,
        "spending": spending,
        "budget_analysis": budgets,
        "health": health,
        "forecast": forecast,
        "recommendations": recommendations,
        "alerts": alerts,
        "agent_status": statuses,
    }


# ---------------------------------------------------------------------------
# Node 4: LLM interpretation of deterministic results (non-blocking)
# ---------------------------------------------------------------------------

def llm_interpret_analysis(state: FinanceState) -> dict[str, Any]:
    """
    Use Groq to interpret deterministic financial metrics.

    The LLM receives verified pre-computed data and generates narrative insights.
    It does NOT recalculate — only interprets.
    Failure is non-blocking.
    """
    if state.get("user_question"):
        # When answering a user question / chat, skip full report generation to conserve LLM tokens for the advisor
        return _done(
            state,
            "llm_financial_analysis",
            {"available": True, "note": "Skipped in chat mode to prioritize advisor response"},
        )

    try:
        from llm_groq import get_groq_client
        from prompts.financial_analysis import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

        client = get_groq_client()
        if not client.is_available():
            return _done(
                state,
                "llm_financial_analysis",
                {"available": False, "reason": "Groq API key not configured"},
            )

        transactions: list[Transaction] = state.get("transactions", [])
        spending = state.get("spending", {})
        health = state.get("health")
        forecast = state.get("forecast")
        budget_analysis = state.get("budget_analysis", []) or []
        months = available_months(transactions)

        # Build category spending breakdown directly from verified transactions
        category_spending_summary: dict[str, dict[str, Any]] = {}
        for tx in transactions:
            if tx.transaction_type == "expense":
                cat = tx.category or "Other"
                if cat not in category_spending_summary:
                    category_spending_summary[cat] = {"total": 0.0, "count": 0}
                category_spending_summary[cat]["total"] = round(category_spending_summary[cat]["total"] + tx.amount, 2)
                category_spending_summary[cat]["count"] += 1

        # Build a verified financial summary to send to the LLM
        financial_data: dict[str, Any] = {
            "monthly_summary": spending.get("monthly", []),
            "category_summary": category_spending_summary,
            "category_breakdown": spending.get("category_breakdown", []),
            "total_income": spending.get("total_income", 0.0),
            "total_expense": spending.get("total_expense", 0.0),
            "savings_rate": spending.get("savings_rate", 0.0),
            "health_score": health.score if health and hasattr(health, "score") else None,
            "health_breakdown": (
                {c.key: c.score for c in health.components if hasattr(c, "score")}
                if health and hasattr(health, "components")
                else {}
            ),
            "forecast_month": forecast.month if forecast and hasattr(forecast, "month") else None,
            "projected_expense": (
                forecast.projected_expense
                if forecast and hasattr(forecast, "projected_expense")
                else None
            ),
            "income_received": (
                forecast.income_received_mtd
                if forecast and hasattr(forecast, "income_received_mtd")
                else None
            ),
            "deficit": forecast.deficit if forecast and hasattr(forecast, "deficit") else False,
            "budget_statuses": [
                {
                    "category": b.category if hasattr(b, "category") else str(b),
                    "spent": b.actual_spent if hasattr(b, "actual_spent") else None,
                    "limit": b.monthly_limit if hasattr(b, "monthly_limit") else None,
                    "over_budget": b.over_budget if hasattr(b, "over_budget") else False,
                }
                for b in budget_analysis[:10]
            ],
            "anomaly_count": len(state.get("anomalies", []) or []),
            "subscription_count": len(state.get("subscriptions", []) or []),
        }

        analysis_period = (
            f"{months[0]} to {months[-1]}" if months else "current period"
        )

        user_message = USER_PROMPT_TEMPLATE.format(
            financial_data_json=json.dumps(financial_data, ensure_ascii=False, default=str),
            analysis_period=analysis_period,
            transaction_count=len(transactions),
            coverage_months=len(months),
        )

        result = client.call(SYSTEM_PROMPT, user_message, task="analysis", max_tokens=1500)
        if result:
            result["prompt_version"] = "1.0.0"
            result["model_used"] = "groq"
            result["transaction_count"] = len(transactions)
            return _done(state, "llm_financial_analysis", result)

        return _done(
            state,
            "llm_financial_analysis",
            {"available": False, "reason": "LLM returned empty response"},
        )

    except Exception as exc:
        logger.warning("LLM analysis interpretation failed (non-blocking): %s", exc)
        return {
            **_done(
                state,
                "llm_financial_analysis",
                {"available": False, "reason": str(exc)[:200]},
            ),
            "errors": [*state.get("errors", []), f"LLM analysis unavailable: {exc}"],
        }


# ---------------------------------------------------------------------------
# Node 5: AI advisor (answers user questions from their financial context)
# ---------------------------------------------------------------------------

def advisor_node(state: FinanceState) -> dict[str, Any]:
    """
    Financial advisor: answers the user's question using their financial context.
    Only runs when user_question is provided in state.
    """
    question = (state.get("user_question") or "").strip()
    if not question:
        return _done(state, "advisor_response", {})

    try:
        from llm_groq import get_groq_client
        from prompts.financial_advisor import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

        client = get_groq_client()
        if not client.is_available():
            return _done(
                state,
                "advisor_response",
                {
                    "direct_answer": "The AI advisor requires a Groq API key to be configured.",
                    "limitations": ["GROQ_API_KEY not configured"],
                    "financial_evidence": [],
                },
            )

        transactions: list[Transaction] = state.get("transactions", [])
        spending = state.get("spending", {})
        health = state.get("health")
        months = available_months(transactions)

        # Build category spending map directly from verified transactions
        category_spending_map: dict[str, dict[str, Any]] = {}
        for tx in transactions:
            if tx.transaction_type == "expense":
                cat = tx.category or "Other"
                if cat not in category_spending_map:
                    category_spending_map[cat] = {"total": 0.0, "count": 0}
                category_spending_map[cat]["total"] = round(category_spending_map[cat]["total"] + tx.amount, 2)
                category_spending_map[cat]["count"] += 1

        # Build financial context (verified data only)
        financial_context: dict[str, Any] = {
            "category_spending": category_spending_map,
            "monthly_spending": spending.get("monthly", []),
            "income_vs_expense": spending.get("income_vs_expense", {}),
            "total_income": spending.get("total_income", 0.0),
            "total_expense": spending.get("total_expense", 0.0),
            "savings_rate": spending.get("savings_rate", 0.0),
            "health_score": (
                health.score if health and hasattr(health, "score") else None
            ),
            "subscriptions": [
                {
                    "merchant": s.merchant if hasattr(s, "merchant") else str(s),
                    "amount": s.amount if hasattr(s, "amount") else None,
                    "frequency": s.frequency if hasattr(s, "frequency") else "monthly",
                    "status": s.status if hasattr(s, "status") else "unknown",
                }
                for s in (state.get("subscriptions", []) or [])[:10]
            ],
            "budget_performance": [
                {
                    "category": b.category if hasattr(b, "category") else str(b),
                    "spent": b.actual_spent if hasattr(b, "actual_spent") else None,
                    "limit": b.monthly_limit if hasattr(b, "monthly_limit") else None,
                    "over_budget": b.over_budget if hasattr(b, "over_budget") else False,
                }
                for b in (state.get("budget_analysis", []) or [])[:8]
            ],
            "recent_anomalies": len(state.get("anomalies", []) or []),
        }

        date_range = (
            f"{months[0]} to {months[-1]}"
            if len(months) >= 2
            else (months[0] if months else "unknown")
        )

        user_message = USER_PROMPT_TEMPLATE.format(
            user_question=question,
            financial_context_json=json.dumps(
                financial_context, ensure_ascii=False, default=str
            ),
            date_range=date_range,
            transaction_count=len(transactions),
        )

        result = client.call(
            SYSTEM_PROMPT,
            user_message,
            task="advisor",
            max_tokens=1500,
        )
        if result:
            result["prompt_version"] = "1.0.0"
            result["model_used"] = "groq"
            return _done(state, "advisor_response", result)

        # Fallback to intelligent deterministic financial advice grounded in user's real verified metrics
        top_cats = sorted(
            category_spending_map.items(),
            key=lambda x: x[1].get("total", 0),
            reverse=True,
        )[:3]
        top_cat_str = ", ".join(f"{c} (₹{info['total']:,.0f})" for c, info in top_cats)
        total_inc = spending.get("total_income", 0.0)
        total_exp = spending.get("total_expense", 0.0)
        net = total_inc - total_exp

        q_lower = question.lower()
        if any(w in q_lower for w in ["laptop", "goal", "save", "saving", "afford", "purchase"]):
            if net < 0:
                answer = (
                    f"Based on your recent financial data, you currently have a monthly deficit of ₹{abs(net):,.0f} "
                    f"(Income: ₹{total_inc:,.0f}, Expenses: ₹{total_exp:,.0f}). "
                    f"To comfortably afford this goal without straining your cashflow, you should first aim to "
                    f"reduce discretionary spending in your highest expenditure categories: {top_cat_str} to eliminate the deficit."
                )
            else:
                answer = (
                    f"Based on your verified records, you have an average monthly surplus of ₹{net:,.0f} "
                    f"(Income: ₹{total_inc:,.0f}, Expenses: ₹{total_exp:,.0f}). "
                    f"Your highest spending areas are {top_cat_str}. "
                    f"Your current surplus can comfortably support this savings goal."
                )
        elif "where" in q_lower and "spending" in q_lower:
            answer = (
                f"Your highest spending category is {top_cats[0][0]} with ₹{top_cats[0][1]['total']:,.0f}, "
                f"followed by {top_cats[1][0] if len(top_cats) > 1 else 'N/A'} (₹{top_cats[1][1]['total']:,.0f})."
                if top_cats else "You have no recorded expenses in the current period."
            )
        else:
            answer = (
                f"Based on your verified transactions, total income is ₹{total_inc:,.0f} and expenses total ₹{total_exp:,.0f} "
                f"(Net: ₹{net:,.0f}). Your primary expenditures are in {top_cat_str}."
            )

        return _done(
            state,
            "advisor_response",
            {
                "direct_answer": answer,
                "financial_evidence": [
                    {"description": f"Spent on {c}", "value": f"₹{info['total']:,.0f}", "period": "recent"}
                    for c, info in top_cats
                ],
                "pattern_identified": f"Top expenditure concentrated in {top_cats[0][0]}" if top_cats else None,
                "personalized_recommendation": f"Review spending in {top_cats[0][0]} to free up cashflow." if top_cats else None,
                "limitations": ["Generated using verified deterministic financial context"],
            },
        )

    except Exception as exc:
        logger.warning("Advisor node failed (non-blocking): %s", exc)
        return {
            **_done(
                state,
                "advisor_response",
                {
                    "direct_answer": "The advisor encountered an error.",
                    "limitations": [str(exc)[:200]],
                    "financial_evidence": [],
                },
            ),
            "errors": [*state.get("errors", []), f"Advisor unavailable: {exc}"],
        }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph():
    builder = StateGraph(FinanceState)
    builder.add_node("transaction_processing", normalize)
    builder.add_node("llm_categorization", categorize_with_llm)
    builder.add_node("financial_analysis", analyse)
    builder.add_node("llm_analysis", llm_interpret_analysis)
    builder.add_node("advisor", advisor_node)

    builder.add_edge(START, "transaction_processing")
    builder.add_edge("transaction_processing", "llm_categorization")
    builder.add_edge("llm_categorization", "financial_analysis")
    builder.add_edge("financial_analysis", "llm_analysis")
    builder.add_edge("llm_analysis", "advisor")
    builder.add_edge("advisor", END)
    return builder.compile()


WORKFLOW = build_graph()


def run(
    user_id: str,
    transactions: list[dict[str, Any]],
    budgets: list[dict[str, Any]],
    user_question: str = "",
) -> dict[str, Any]:
    """Execute the full LangGraph workflow and return a serializable result dict."""
    workflow_id = f"WF-{uuid4().hex[:12].upper()}"
    limits = {item["category"]: item["monthlyLimit"] for item in budgets}
    state = WORKFLOW.invoke(
        {
            "user_id": user_id,
            "workflow_id": workflow_id,
            "raw_transactions": transactions,
            "budgets": limits,
            "agent_status": {},
            "errors": [],
            "approval_status": "not_required",
            "user_question": user_question,
        }
    )

    def dump(value: Any) -> Any:
        return (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else value
        )

    excluded = {"raw_transactions", "transactions", "budgets", "user_id"}
    return {
        "workflow_id": workflow_id,
        "status": "completed",
        "result": {
            key: (
                dump(value)
                if not isinstance(value, list)
                else [dump(item) for item in value]
            )
            for key, value in state.items()
            if key not in excluded
        },
    }
