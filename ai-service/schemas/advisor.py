"""Pydantic models for AI advisor output validation."""

from __future__ import annotations
from pydantic import BaseModel, Field, model_validator


class EvidenceItem(BaseModel):
    description: str
    value: str
    period: str = ""


class AdvisorResponse(BaseModel):
    direct_answer: str
    financial_evidence: list[EvidenceItem] = Field(default_factory=list)
    pattern_identified: str | None = None
    personalized_recommendation: str | None = None
    expected_impact: str | None = None
    limitations: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    prompt_version: str = "1.0.0"
    model_used: str = ""
    is_insufficient_data: bool = False

    @model_validator(mode="after")
    def _flag_insufficient(self) -> "AdvisorResponse":
        if "insufficient_data" in self.direct_answer.lower():
            self.is_insufficient_data = True
        return self
