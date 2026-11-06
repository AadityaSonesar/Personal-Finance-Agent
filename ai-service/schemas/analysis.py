"""Pydantic models for LLM financial analysis output validation."""

from __future__ import annotations
from pydantic import BaseModel, Field


class TopCategory(BaseModel):
    category: str
    amount: float = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class RecommendedAction(BaseModel):
    action: str
    reason: str
    expected_impact: str | None = None


class ExpenseAnalysis(BaseModel):
    summary: str
    top_categories: list[TopCategory] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class IncomeAnalysis(BaseModel):
    summary: str
    patterns: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class FinancialAnalysisResult(BaseModel):
    executive_summary: str
    income_analysis: IncomeAnalysis
    expense_analysis: ExpenseAnalysis
    spending_trends: str
    financial_strengths: list[str] = Field(default_factory=list)
    financial_weaknesses: list[str] = Field(default_factory=list)
    areas_for_improvement: list[str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    data_limitations: list[str] = Field(default_factory=list)
    prompt_version: str = "1.0.0"
    model_used: str = ""
    transaction_count: int = 0
    analysis_period: str = ""
