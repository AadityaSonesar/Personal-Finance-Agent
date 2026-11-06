"""AI service main application.

Endpoints:
- POST /internal/ai/categorize            — LLM transaction categorization
- POST /internal/ai/analyze               — Full financial analysis (deterministic + LLM)
- POST /internal/ai/forecast              — Cash-flow forecast
- POST /internal/ai/report                — Full report package
- POST /internal/ai/chat                  — Financial advisor chat
- POST /internal/ai/budget-recommendations — LLM budget recommendations
- GET  /internal/ai/status                — LLM provider status
- GET  /health                            — Service health check
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ROOT_ENV)

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from schemas.contracts import AiRequest, AiResponse
from services.auth import verified_context
from graph.workflow import run

logger = logging.getLogger("aurelia.main")
app = FastAPI(title="Aurelia internal AI service", docs_url=None, redoc_url=None)


# -- Request/Response Models --------------------------------------------------

class CategorizationRequest(BaseModel):
    transactions: list[dict[str, Any]] = Field(default_factory=list)


class CategorizationResponse(BaseModel):
    results: list[dict[str, Any]]
    total_input: int
    total_categorized: int
    model_used: str = ""
    prompt_version: str = "1.0.0"


class BudgetRecommendationRequest(BaseModel):
    transactions: list[dict[str, Any]] = Field(default_factory=list)
    budgets: list[dict[str, Any]] = Field(default_factory=list)


# -- Helpers ------------------------------------------------------------------

def _make_payload(req: AiRequest) -> tuple[list, list]:
    return (
        [x.model_dump(mode="json") for x in req.transactions],
        [x.model_dump() for x in req.budgets],
    )


def _full_run(user_id: str, transactions: list, budgets: list, question: str = "") -> dict:
    return run(user_id, transactions, budgets, user_question=question)


# -- Endpoints ----------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/internal/ai/status")
def ai_status(context: dict = Depends(verified_context)):
    """Return LLM provider availability status."""
    groq_key = bool(os.getenv("GROQ_API_KEY", "").strip())
    models = {
        "fast": os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant"),
        "analysis": os.getenv("GROQ_MODEL_ANALYSIS", "llama-3.3-70b-versatile"),
        "advisor": os.getenv("GROQ_MODEL_ADVISOR", "llama-3.3-70b-versatile"),
    }
    return {
        "provider": "groq",
        "available": groq_key,
        "models": models,
        "configured": groq_key,
        "mode": "llm" if groq_key else "deterministic",
    }


@app.post("/internal/ai/categorize", response_model=CategorizationResponse)
def categorize(
    request: CategorizationRequest,
    context: dict = Depends(verified_context),
):
    """LLM-powered transaction categorization endpoint."""
    if not request.transactions:
        return CategorizationResponse(results=[], total_input=0, total_categorized=0)

    try:
        from llm_groq import categorize_transactions_llm
        results = categorize_transactions_llm(request.transactions)
        return CategorizationResponse(
            results=results,
            total_input=len(request.transactions),
            total_categorized=len(results),
            model_used=os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant"),
            prompt_version="1.0.0",
        )
    except Exception as exc:
        logger.error("Categorization endpoint error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Categorization failed: {str(exc)[:200]}")


@app.post("/internal/ai/analyze", response_model=AiResponse)
def analyze(request: AiRequest, context: dict = Depends(verified_context)):
    transactions, budgets = _make_payload(request)
    result = _full_run(context["sub"], transactions, budgets)
    return result


@app.post("/internal/ai/forecast", response_model=AiResponse)
def forecast(request: AiRequest, context: dict = Depends(verified_context)):
    transactions, budgets = _make_payload(request)
    result = _full_run(context["sub"], transactions, budgets)
    return result


@app.post("/internal/ai/report", response_model=AiResponse)
def report(request: AiRequest, context: dict = Depends(verified_context)):
    transactions, budgets = _make_payload(request)
    result = _full_run(context["sub"], transactions, budgets)
    return result


@app.post("/internal/ai/chat")
def chat(request: AiRequest, context: dict = Depends(verified_context)):
    """Financial advisor chat endpoint."""
    transactions, budgets = _make_payload(request)
    question = request.prompt or ""
    result = _full_run(context["sub"], transactions, budgets, question=question)

    advisor_resp = result.get("result", {}).get("advisor_response", {})
    if not advisor_resp or not advisor_resp.get("direct_answer"):
        result["result"]["advisor_message"] = (
            "Ask a specific question about your finances and I will analyze your records to answer it. "
            "For example: 'Where am I spending the most?' or 'Which subscriptions am I paying for?'"
        )
    else:
        result["result"]["advisor_message"] = advisor_resp.get("direct_answer", "")

    return result


@app.post("/internal/ai/budget-recommendations")
def budget_recommendations(
    request: BudgetRecommendationRequest,
    context: dict = Depends(verified_context),
):
    """Generate LLM-powered budget recommendations."""
    try:
        from llm_groq import get_groq_client
        from prompts.budget_analysis import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
        from core.spending import build_spending_intelligence, available_months
        from core.models import Transaction

        transactions = []
        for raw in request.transactions:
            try:
                raw_dict = raw if isinstance(raw, dict) else raw.model_dump()
                d_val = raw_dict.get("date", raw_dict.get("transaction_date"))
                if hasattr(d_val, "date") and callable(d_val.date):
                    d_val = d_val.date()
                elif isinstance(d_val, str) and "T" in d_val:
                    d_val = d_val.split("T")[0]

                transactions.append(Transaction.model_validate({
                    "transaction_id": raw_dict.get("transactionId", raw_dict.get("transaction_id", "")),
                    "merchant": raw_dict.get("merchant", ""),
                    "description": raw_dict.get("description", ""),
                    "amount": raw_dict.get("amount", 0),
                    "transaction_type": raw_dict.get("type", raw_dict.get("transaction_type", "expense")),
                    "category": raw_dict.get("category", "Other"),
                    "transaction_date": d_val,
                    "source": "upload",
                }))
            except Exception:
                continue

        if not transactions:
            raise HTTPException(status_code=400, detail="No valid transactions provided")

        # Build category-wise aggregates directly from valid transactions
        category_spending: dict[str, dict[str, Any]] = {}
        monthly_incomes: dict[str, float] = {}

        for tx in transactions:
            m_str = tx.transaction_date.strftime("%Y-%m")
            if tx.transaction_type == "income":
                monthly_incomes[m_str] = monthly_incomes.get(m_str, 0.0) + tx.amount
            elif tx.transaction_type == "expense":
                cat = tx.category or "Other"
                if cat not in category_spending:
                    category_spending[cat] = {"total": 0.0, "count": 0}
                category_spending[cat]["total"] = round(category_spending[cat]["total"] + tx.amount, 2)
                category_spending[cat]["count"] += 1

        months = available_months(transactions)
        num_months = max(1, len(months))
        existing_budgets = {b.get("category", ""): b.get("monthlyLimit", 0) for b in request.budgets}
        avg_income_val = round(sum(monthly_incomes.values()) / max(1, len(monthly_incomes)), 2) if monthly_incomes else 0.0

        financial_summary = {
            "category_spending": category_spending,
            "months_analyzed": num_months,
            "date_range": f"{months[0]} to {months[-1]}" if len(months) >= 2 else (months[0] if months else "unknown"),
            "existing_budgets": existing_budgets,
            "average_monthly_income": avg_income_val,
        }

        def _rule_based_fallback():
            recs = []
            for cat, details in category_spending.items():
                spent = details.get("total", 0) if isinstance(details, dict) else details
                avg = round(spent / num_months, 2)
                recs.append({
                    "category": cat,
                    "historical_average": avg,
                    "current_budget": existing_budgets.get(cat),
                    "recommended_budget": round(avg * 0.95, 2),
                    "reason": f"Based on historical average of {avg:,.0f} across {num_months} month(s).",
                    "supporting_period": f"Last {num_months} month(s)",
                    "confidence": 0.6,
                    "requires_user_approval": True,
                    "change_from_current": round(avg * 0.95 - existing_budgets.get(cat, 0), 2) if cat in existing_budgets else None,
                    "expected_savings_impact": round(avg * 0.05, 2),
                })
            return {
                "recommendations": recs,
                "overall_assessment": {
                    "total_recommended_budget": round(sum(r["recommended_budget"] for r in recs), 2),
                    "vs_average_income": f"{(sum(r['recommended_budget'] for r in recs) / avg_income_val * 100):.1f}%" if avg_income_val > 0 else "N/A",
                    "data_quality": f"Calculated from {num_months} month(s) of history",
                    "limitations": ["Fallback rule-based recommendation applied."],
                },
                "prompt_version": "1.0.0",
                "model_used": "rule_based_fallback",
            }

        client = get_groq_client()
        if not client.is_available():
            return _rule_based_fallback()

        user_message = USER_PROMPT_TEMPLATE.format(
            financial_summary_json=json.dumps(financial_summary, ensure_ascii=False, default=str),
            history_months=num_months,
            avg_income=avg_income_val,
            existing_budgets=json.dumps(existing_budgets),
        )

        result = client.call(SYSTEM_PROMPT, user_message, task="analysis")
        if not result or not result.get("recommendations"):
            return _rule_based_fallback()

        for rec in result.get("recommendations", []):
            rec["requires_user_approval"] = True

        result["prompt_version"] = "1.0.0"
        result["model_used"] = os.getenv("GROQ_MODEL_ANALYSIS", "llama-3.3-70b-versatile")
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Budget recommendations error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Budget recommendations failed: {str(exc)[:200]}")
