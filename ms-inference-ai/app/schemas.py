from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: UUID
    amount: Annotated[int, Field(strict=True, gt=0, le=9223372036854775807,
                                 description="Centavos de USD: 2500 representa USD 25")]
    currency: Literal["USD"]


class RecommendationResponse(BaseModel):
    transaction_id: UUID
    recommendation: Annotated[str, Field(min_length=1, max_length=2000)]
    mode: Literal["mock"] = "mock"
