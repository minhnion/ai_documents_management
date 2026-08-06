from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserSummaryResponse


CostModelType = Literal["ocr", "llm", "embedding"]


class CostRate(BaseModel):
    unit: str
    label: str
    unit_divisor: int = Field(gt=0)
    price_usd: Decimal = Field(ge=0, max_digits=20, decimal_places=12)


class CostModelPricing(BaseModel):
    model_type: CostModelType
    display_name: str
    billing_mode: str
    rates: list[CostRate]


class CostPricingResponse(BaseModel):
    models: list[CostModelPricing]


class CostPricingUpdateRequest(BaseModel):
    models: list[CostModelPricing]


class CostStatisticsPoint(BaseModel):
    period: str
    ocr_cost: Decimal
    llm_cost: Decimal
    embedding_cost: Decimal
    total_cost: Decimal


class CostStatisticsSummary(BaseModel):
    total_cost: Decimal
    ocr_cost: Decimal
    llm_cost: Decimal
    embedding_cost: Decimal
    documents_processed: int
    usage_events: int
    ocr_pages: int
    llm_input_tokens: int
    llm_cached_input_tokens: int
    llm_output_tokens: int
    embedding_input_tokens: int


class CostFilterOptionsResponse(BaseModel):
    accounts: list[UserSummaryResponse]
    groups: list[UserSummaryResponse]


class CostStatisticsResponse(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    summary: CostStatisticsSummary
    timeseries: list[CostStatisticsPoint]
    filters: CostFilterOptionsResponse


class CostUsageEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: int
    document_id: int | None
    version_id: int | None
    occurred_at: datetime
    model_type: CostModelType
    operation: str
    status: str
    usage_source: str
    pricing_status: str
    page_count: int
    request_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    output_chars: int
    cost_usd: Decimal
    account_id: int | None
    group_id: int | None
    account: UserSummaryResponse | None = None
    group: UserSummaryResponse | None = None


class CostHistoryResponse(BaseModel):
    items: list[CostUsageEventItem]
    total: int
    page: int
    page_size: int
