from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DBSession, require_roles
from app.core.roles import ROLE_ADMIN
from app.schemas.cost import (
    CostHistoryResponse,
    CostPricingResponse,
    CostPricingUpdateRequest,
    CostStatisticsResponse,
)
from app.services.cost_calculation_service import CostCalculationService

router = APIRouter(prefix="/admin/cost", tags=["Admin Cost"])


def get_cost_service(db: DBSession) -> CostCalculationService:
    return CostCalculationService(db)


CostServiceDep = Annotated[CostCalculationService, Depends(get_cost_service)]
AdminUser = Annotated[object, Depends(require_roles(ROLE_ADMIN))]


@router.get("/pricing", response_model=CostPricingResponse, summary="Get Model Pricing")
async def get_pricing(
    cost_service: CostServiceDep,
    current_user: AdminUser,
) -> CostPricingResponse:
    return CostPricingResponse(**(await cost_service.get_pricing()))


@router.put("/pricing", response_model=CostPricingResponse, summary="Update Model Pricing")
async def update_pricing(
    payload: CostPricingUpdateRequest,
    cost_service: CostServiceDep,
    current_user: AdminUser,
) -> CostPricingResponse:
    pricing = await cost_service.update_pricing(
        ocr_input_char_price=payload.ocr.input_char_price,
        ocr_output_char_price=payload.ocr.output_char_price,
        ocr_page_price=payload.ocr.page_price,
        vlm_input_token_price=payload.vlm.input_token_price,
        vlm_output_token_price=payload.vlm.output_token_price,
    )
    return CostPricingResponse(**pricing)


@router.get("/statistics", response_model=CostStatisticsResponse, summary="Cost Statistics")
async def get_statistics(
    cost_service: CostServiceDep,
    current_user: AdminUser,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
    groupId: Annotated[int | None, Query(ge=1)] = None,
    accountId: Annotated[int | None, Query(ge=1)] = None,
) -> CostStatisticsResponse:
    stats = await cost_service.get_statistics(
        date_from=from_,
        date_to=to,
        group_id=groupId,
        account_id=accountId,
    )
    return CostStatisticsResponse(**stats)


@router.get("/history", response_model=CostHistoryResponse, summary="Cost History")
async def list_history(
    cost_service: CostServiceDep,
    current_user: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
    groupId: Annotated[int | None, Query(ge=1)] = None,
    accountId: Annotated[int | None, Query(ge=1)] = None,
) -> CostHistoryResponse:
    items, total = await cost_service.list_history(
        page=page,
        page_size=page_size,
        date_from=from_,
        date_to=to,
        group_id=groupId,
        account_id=accountId,
    )
    return CostHistoryResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
