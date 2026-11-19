"""AI service main application.

Endpoints:
- POST /internal/ai/categorize            — LLM transaction categorization
- POST /internal/ai/parse-statement        — Parse PDF / Excel / CSV statements
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
from graph.workflow import _enforce_inr_currency, run

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


class ParseStatementRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    file_base64: str = Field(min_length=1)


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
            "currency": "INR",
            "currency_symbol": "₹",
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
                    "reason": f"Based on historical average of ₹{avg:,.0f} across {num_months} month(s).",
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

        result = _enforce_inr_currency(result)
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


def _llm_extract_pdf_statement(raw_bytes: bytes, file_name: str) -> dict[str, Any]:
    """Fallback LLM extraction for unconventional text-based or OCR PDF bank statements."""
    import io
    import pdfplumber
    from core.ingestion import IngestionError, _is_non_transaction_text, _ocr_pdf_pages, extract_statement_metadata
    from core.security import sanitize_text
    from llm_groq import get_groq_client

    page_texts: list[str] = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        if not pdf.pages:
            raise IngestionError("The PDF has no pages.")
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                page_texts.append(t)

    if not page_texts:
        ocr_texts = _ocr_pdf_pages(raw_bytes)
        page_texts = [t for t in ocr_texts if t.strip()]

    full_text = "\n".join(page_texts)
    if not full_text.strip():
        raise IngestionError(
            "No readable text was found in this PDF. Export as CSV/Excel or verify OCR support on this host."
        )

    metadata = extract_statement_metadata(full_text)
    metadata["pages_parsed"] = len(page_texts)

    client = get_groq_client()
    if not client.is_available():
        raise IngestionError(
            "No structured transaction table was detected and the AI fallback is not configured."
        )

    system_prompt = (
        "You extract financial transactions and account metadata from bank statement text. "
        "Return strict JSON only with shape: "
        '{"rows": [{"transaction_date": "DD/MM/YYYY or YYYY-MM-DD", "merchant": "name", '
        '"description": "details", "amount": "123.45", "transaction_type": "expense" or "income", '
        '"transaction_id": "optional ref"}], '
        '"metadata": {"opening_balance": number_or_null, "closing_balance": number_or_null, '
        '"account_number": "string_or_null", "statement_period": "string_or_null"}}. '
        "Ignore headers, footers, account summaries, opening/closing balance lines, page totals, and disclaimers."
    )

    extracted_rows: list[dict[str, Any]] = []
    filtered_count = 0
    row_ref = 1

    # Process statement pages in bounded text chunks
    for idx, page_txt in enumerate(page_texts, start=1):
        snippet = page_txt[:10000]
        user_prompt = f"<statement_page page=\"{idx}\">\n{snippet}\n</statement_page>"
        llm_res = client.call(system_prompt, user_prompt, task="analysis", max_tokens=2000)
        if not isinstance(llm_res, dict):
            continue

        llm_meta = llm_res.get("metadata")
        if isinstance(llm_meta, dict):
            for k in ("opening_balance", "closing_balance", "account_number", "statement_period"):
                if metadata.get(k) is None and llm_meta.get(k) not in (None, "", "null"):
                    metadata[k] = llm_meta[k]

        for raw in llm_res.get("rows") or []:
            if not isinstance(raw, dict):
                continue
            merch = str(raw.get("merchant") or "").strip()
            desc = str(raw.get("description") or "").strip()
            if _is_non_transaction_text(merch) or _is_non_transaction_text(desc):
                filtered_count += 1
                continue
            if not merch and desc:
                merch = desc[:80]
            tx_type = str(raw.get("transaction_type") or "expense").strip().lower()
            if tx_type not in {"income", "expense"}:
                tx_type = "income" if any(w in tx_type for w in ("cr", "credit", "income", "deposit")) else "expense"

            extracted_rows.append({
                "transaction_date": str(raw.get("transaction_date") or "").strip(),
                "merchant": sanitize_text(merch, max_length=280).text,
                "description": sanitize_text(desc, max_length=400).text,
                "amount": str(raw.get("amount") or "").strip(),
                "transaction_type": tx_type,
                "transaction_id": str(raw.get("transaction_id") or "").strip(),
                "_row_ref": row_ref,
            })
            row_ref += 1

    if not extracted_rows:
        raise IngestionError(
            "No transaction rows could be extracted from this PDF. Export the statement as CSV or Excel, or upload a text-based PDF."
        )

    metadata["non_transaction_rows_filtered"] = filtered_count
    notes = [
        f"Extracted {len(extracted_rows)} transactions using AI statement parser across {len(page_texts)} page(s).",
        "Extraction relied on unstructured PDF text; please verify parsed rows.",
    ]
    return {
        "file_name": file_name,
        "file_type": "pdf",
        "rows": extracted_rows,
        "row_count": len(extracted_rows),
        "metadata": metadata,
        "notes": notes,
        "warnings": [],
        "requires_manual_review": True,
    }


@app.post("/internal/ai/parse-statement")
def parse_statement_endpoint(
    request: ParseStatementRequest,
    context: dict = Depends(verified_context),
):
    """Parse an uploaded PDF, Excel, or CSV statement into transaction rows and statement metadata."""
    import base64
    import binascii
    import sys

    root_dir = str(Path(__file__).resolve().parents[1])
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

    from core.ingestion import IngestionError, parse_statement

    try:
        raw_bytes = base64.b64decode(request.file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 file payload: {exc}")

    try:
        outcome = parse_statement(raw_bytes, request.file_name)
        return {
            "file_name": outcome.file_name,
            "file_type": outcome.file_type,
            "rows": outcome.rows,
            "row_count": outcome.row_count,
            "metadata": outcome.metadata,
            "notes": outcome.notes,
            "warnings": outcome.warnings,
            "requires_manual_review": outcome.requires_manual_review,
        }
    except IngestionError as exc:
        if request.file_name.lower().endswith(".pdf"):
            try:
                return _llm_extract_pdf_statement(raw_bytes, request.file_name)
            except IngestionError as llm_exc:
                raise HTTPException(status_code=400, detail=str(llm_exc))
            except Exception as llm_err:
                logger.warning("LLM PDF extraction fallback failed: %s", llm_err)
                raise HTTPException(status_code=400, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Statement parse error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to parse statement: {str(exc)[:200]}")
