"""Pydantic models for LLM categorization output validation."""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from core.constants import ALL_CATEGORIES


class CategorizationResult(BaseModel):
    transaction_id: str
    suggested_category: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: list[str] = Field(default_factory=list)
    status: str = "suggested"

    @field_validator("suggested_category")
    @classmethod
    def _valid_category(cls, v: str) -> str:
        if v not in ALL_CATEGORIES:
            return "Other"
        return v

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class CategorizationBatchResult(BaseModel):
    results: list[CategorizationResult]
    prompt_version: str = "1.0.0"
    model_used: str = ""
    total_input: int = 0
    total_categorized: int = 0
    low_confidence_count: int = 0
