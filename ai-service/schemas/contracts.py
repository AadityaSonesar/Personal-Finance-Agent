from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    transactionId: str = ""
    merchant: str = "Unknown"
    description: str = ""
    amount: float = Field(gt=0)
    type: Literal["income", "expense"] = "expense"
    category: str = "Other"
    date: datetime

class BudgetInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str
    monthlyLimit: float = Field(ge=0)
    month: str = ""

class AiRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    transactions: list[TransactionInput] = []
    budgets: list[BudgetInput] = []
    prompt: str = ""
    reference_month: str | None = None

class AiResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    workflow_id: str
    status: Literal["completed", "failed", "requires_approval"]
    result: dict[str, Any]
    errors: list[str] = []
