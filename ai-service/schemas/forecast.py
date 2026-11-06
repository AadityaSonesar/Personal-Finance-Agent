"""Pydantic models for LLM forecast output validation."""

from __future__ import annotations
from pydantic import BaseModel, Field, model_validator


class RecurringPayment(BaseModel):
    merchant: str
    estimated_amount: float = Field(ge=0)
    frequency: str = "monthly"
    confidence: float = Field(ge=0.0, le=1.0)


class ForecastPeriod(BaseModel):
    forecast_period: str
    expected_income: float | None = None
    expected_expenses: float | None = None
    expected_net_cashflow: float | None = None
    estimated_closing_balance: None = None  # Always null unless opening balance provided
    recurring_payments_identified: list[RecurringPayment] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    assumptions: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    cashflow_risks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_arithmetic(self) -> "ForecastPeriod":
        """Deterministic check: net = income - expenses."""
        if self.expected_income is not None and self.expected_expenses is not None:
            computed = round(self.expected_income - self.expected_expenses, 2)
            if self.expected_net_cashflow is not None:
                if abs(computed - self.expected_net_cashflow) > 1.0:
                    # Fix the arithmetic rather than reject
                    self.expected_net_cashflow = computed
            else:
                self.expected_net_cashflow = computed
        return self


class DataQuality(BaseModel):
    months_of_history: int = 0
    sufficient_for_forecast: bool = False
    missing_data: list[str] = Field(default_factory=list)


class ForecastResult(BaseModel):
    forecasts: list[ForecastPeriod]
    data_quality: DataQuality
    prompt_version: str = "1.0.0"
    model_used: str = ""
