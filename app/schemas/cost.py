from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserSummaryResponse


class OcrPricing(BaseModel):
    input_char_price: Decimal = Field(ge=0)
    output_char_price: Decimal = Field(ge=0)
    page_price: Decimal = Field(ge=0)


class VlmPricing(BaseModel):
    input_token_price: Decimal = Field(ge=0)
    output_token_price: Decimal = Field(ge=0)


class CostPricingResponse(BaseModel):
    ocr: OcrPricing
    vlm: VlmPricing


class CostPricingUpdateRequest(CostPricingResponse):
    pass


class CostStatisticsPoint(BaseModel):
    period: str
    ocr_cost: Decimal
    vlm_cost: Decimal
    total_cost: Decimal


class CostStatisticsSummary(BaseModel):
    total_cost: Decimal
    ocr_cost: Decimal
    vlm_cost: Decimal
    documents_processed: int


class CostFilterOptionsResponse(BaseModel):
    accounts: list[UserSummaryResponse]
    groups: list[UserSummaryResponse]


class CostStatisticsResponse(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    summary: CostStatisticsSummary
    timeseries: list[CostStatisticsPoint]
    filters: CostFilterOptionsResponse


class CostHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cost_history_id: int
    document_id: int
    user_id: int | None
    account_id: int | None
    group_id: int | None
    created_at: datetime
    ocr_input_chars: int
    ocr_output_chars: int
    ocr_pages: int
    ocr_cost: Decimal
    vlm_input_tokens: int
    vlm_output_tokens: int
    vlm_cost: Decimal
    total_cost: Decimal
    account: UserSummaryResponse | None = None
    group: UserSummaryResponse | None = None


class CostHistoryResponse(BaseModel):
    items: list[CostHistoryItem]
    total: int
    page: int
    page_size: int
