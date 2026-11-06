"""Groq LLM integration using LangChain.

This module provides the LLM calls for the AI service.
All calls enforce data-grounding: LLMs only interpret verified data.

Design principles:
- Untrusted financial data is wrapped in XML tags and separated from instructions
- All outputs are validated against Pydantic schemas
- Numbers in LLM output are checked against provided facts
- API key is read only from environment, never from config files
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("aurelia.groq")

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|```\s*$", re.MULTILINE)


def _get_groq_key() -> str | None:
    for name in ("GROQ_API_KEY", "FINWELL_AI_API_KEY"):
        val = os.getenv(name, "").strip()
        if val:
            return val
    return None


def _get_model(task: str = "fast") -> str:
    model_map = {
        "fast": os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b"),
        "analysis": os.getenv("GROQ_MODEL_ANALYSIS", "openai/gpt-oss-20b"),
        "advisor": os.getenv("GROQ_MODEL_ADVISOR", "openai/gpt-oss-20b"),
        "categorization": os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b"),
    }
    return model_map.get(task, model_map["fast"])


def _parse_json_response(content: str) -> dict[str, Any] | None:
    """Extract JSON from LLM response, handling markdown fences."""
    cleaned = _FENCE_RE.sub("", content).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class GroqClient:
    """Groq client with structured output validation."""

    def __init__(self) -> None:
        self._key = _get_groq_key()

    def is_available(self) -> bool:
        return bool(self._key)

    def call(
        self,
        system_prompt: str,
        user_message: str,
        task: str = "fast",
        temperature: float = 0.1,
        max_retries: int = 2,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | None:
        """Call Groq and return parsed JSON dict, or None on failure."""
        if not self._key:
            return None
        model = _get_model(task)
        effective_max_tokens = max_tokens or int(os.getenv("GROQ_MAX_TOKENS", "900"))
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                from groq import Groq
                client = Groq(api_key=self._key)
                res = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    max_tokens=effective_max_tokens,
                )
                content = res.choices[0].message.content or ""
                parsed = _parse_json_response(content)
                if parsed is not None:
                    return parsed
                logger.warning("LLM returned non-JSON response (attempt %d)", attempt + 1)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                logger.warning("Groq call failed (attempt %d): %s", attempt + 1, last_error)
                if attempt < max_retries:
                    time.sleep(min(4.0, 0.5 * (2**attempt)))
        logger.error("Groq call failed after %d attempts: %s", max_retries + 1, last_error)
        return None

    def call_validated(
        self,
        system_prompt: str,
        user_message: str,
        schema: type[T],
        task: str = "fast",
        temperature: float = 0.1,
    ) -> T | None:
        """Call Groq and validate response against a Pydantic schema."""
        parsed = self.call(system_prompt, user_message, task=task, temperature=temperature)
        if parsed is None:
            return None
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            logger.warning("LLM output failed schema validation: %s", exc)
            return None


# Module-level singleton
_client: GroqClient | None = None


def get_groq_client() -> GroqClient:
    global _client
    if _client is None:
        _client = GroqClient()
    return _client


def categorize_transactions_llm(
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Categorize a batch of transactions using Groq LLM with robust batching and
    smart Indian/UPI statement extraction and deterministic fallback rules.
    """
    from prompts.transaction_categorization import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
    from core.constants import ALL_CATEGORIES
    from core.security import sanitize_text
    from core.indian_categorizer import clean_merchant_text, rule_classify_single

    if not transactions:
        return []

    client = get_groq_client()
    allowed = ", ".join(f'"{c}"' for c in ALL_CATEGORIES)

    # Sanitize and pre-clean
    safe_transactions = []
    for t in transactions:
        tid = str(t.get("transaction_id", ""))[:64]
        raw_m = str(t.get("merchant", ""))
        raw_d = str(t.get("description", ""))
        amt = float(t.get("amount") or 0.0)
        ttype = str(t.get("type", t.get("transaction_type", "expense")))
        cleaned_m = clean_merchant_text(raw_m, raw_d)

        safe_transactions.append(
            {
                "transaction_id": tid,
                "merchant": sanitize_text(cleaned_m or raw_m, max_length=150).text,
                "raw_merchant": raw_m,
                "description": sanitize_text(raw_d, max_length=150).text,
                "amount": amt,
                "type": ttype,
            }
        )

    # First pass: high-confidence rule classification (fast, deterministic)
    all_validated_map: dict[str, dict[str, Any]] = {}
    needs_llm: list[dict[str, Any]] = []

    for item in safe_transactions:
        tid = item["transaction_id"]
        rule_res = rule_classify_single(
            item["raw_merchant"],
            item["description"],
            item["amount"],
            item["type"],
        )
        if rule_res["confidence"] >= 0.65:
            all_validated_map[tid] = {
                "transaction_id": tid,
                "suggested_category": rule_res["suggested_category"],
                "confidence": rule_res["confidence"],
                "reason": rule_res["reason"],
                "evidence": rule_res.get("evidence", [item["merchant"][:60]]),
                "status": "suggested",
            }
        else:
            needs_llm.append(item)

    # Second pass: LLM for ambiguous/unrecognized items (in batches of 6)
    BATCH_SIZE = 6
    for i in range(0, len(needs_llm), BATCH_SIZE):
        batch = needs_llm[i : i + BATCH_SIZE]
        batch_prompt_items = [
            {
                "transaction_id": b["transaction_id"],
                "merchant": b["merchant"],
                "description": b["description"],
                "amount": b["amount"],
                "type": b["type"],
            }
            for b in batch
        ]

        llm_batch_map: dict[str, dict[str, Any]] = {}
        if client.is_available():
            try:
                user_message = USER_PROMPT_TEMPLATE.format(
                    allowed_categories=allowed,
                    transactions_json=json.dumps(batch_prompt_items, ensure_ascii=False),
                    count=len(batch),
                )
                groq_resp = client.call(
                    SYSTEM_PROMPT,
                    user_message,
                    task="categorization",
                    max_tokens=1500,
                    max_retries=0,
                )
                if groq_resp and isinstance(groq_resp.get("results"), list):
                    for r in groq_resp["results"]:
                        if isinstance(r, dict):
                            rid = str(r.get("transaction_id", ""))
                            rcat = str(r.get("suggested_category", "Other"))
                            if rcat in ALL_CATEGORIES:
                                llm_batch_map[rid] = {
                                    "suggested_category": rcat,
                                    "confidence": float(r.get("confidence", 0.85)),
                                    "reason": str(r.get("reason", "")),
                                    "evidence": r.get("evidence", []),
                                }
            except Exception as e:
                logger.warning("Groq batch categorization error: %s", e)

        for b in batch:
            tid = b["transaction_id"]
            llm_item = llm_batch_map.get(tid)
            if llm_item and llm_item["suggested_category"] != "Other" and llm_item["confidence"] >= 0.5:
                all_validated_map[tid] = {
                    "transaction_id": tid,
                    "suggested_category": llm_item["suggested_category"],
                    "confidence": llm_item["confidence"],
                    "reason": llm_item["reason"] or f"Categorized by AI as {llm_item['suggested_category']}.",
                    "evidence": [str(e) for e in llm_item.get("evidence", [])[:5]],
                    "status": "suggested",
                }
            else:
                rule_res = rule_classify_single(
                    b["raw_merchant"],
                    b["description"],
                    b["amount"],
                    b["type"],
                )
                reason = rule_res["reason"]
                if llm_item and llm_item.get("reason"):
                    reason = llm_item["reason"]
                all_validated_map[tid] = {
                    "transaction_id": tid,
                    "suggested_category": rule_res["suggested_category"],
                    "confidence": rule_res["confidence"],
                    "reason": reason,
                    "evidence": rule_res.get("evidence", [b["merchant"][:60]]),
                    "status": "suggested",
                }

    # Return in original order
    return [all_validated_map[t["transaction_id"]] for t in safe_transactions if t["transaction_id"] in all_validated_map]
