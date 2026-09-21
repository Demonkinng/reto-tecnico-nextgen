from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_CENTS = 9223372036854775807
Cents = Annotated[int, Field(strict=True, gt=0, le=MAX_CENTS, description="Centavos de USD: 1050 = USD 10.50")]


class TransactionStatus(StrEnum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class JobStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_account_id: UUID
    destination_account_id: UUID
    amount: Cents
    currency: Literal["USD"] = "USD"

    @model_validator(mode="after")
    def different_accounts(self):
        if self.source_account_id == self.destination_account_id:
            raise ValueError("Las cuentas de origen y destino deben ser diferentes")
        return self


class TransferResponse(BaseModel):
    transaction_id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    amount: int
    currency: str
    status: TransactionStatus
    trace_id: str
    created_at: datetime
    completed_at: datetime | None


class AccountResponse(BaseModel):
    account_id: UUID
    balance: int
    currency: str


class AIResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: UUID
    recommendation: Annotated[str, Field(min_length=1, max_length=2000)]
    mode: Literal["mock", "api"]


class RecommendationResponse(BaseModel):
    transaction_id: UUID
    status: JobStatus
    attempts: int
    result: AIResult | None
    last_error_code: str | None
