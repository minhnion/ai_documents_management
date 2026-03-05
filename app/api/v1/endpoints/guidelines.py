from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ActiveUser, DBSession
from app.schemas.guideline import (
    GuidelineListItem,
    GuidelineListResponse,
    GuidelineVersionItem,
    GuidelineVersionListResponse,
    GuidelineVersionSummary,
)
from app.services.guideline_query_service import GuidelineQueryService

router = APIRouter(prefix="/guidelines", tags=["Guidelines"])


@router.get("", response_model=GuidelineListResponse, summary="List Guidelines")
async def list_guidelines(
    db: DBSession,
    _: ActiveUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=255)] = None,
    title: Annotated[str | None, Query(max_length=255)] = None,
    publisher: Annotated[str | None, Query(max_length=255)] = None,
    chuyen_khoa: Annotated[str | None, Query(max_length=255)] = None,
) -> GuidelineListResponse:
    service = GuidelineQueryService(db)
    guidelines, active_versions, total = await service.list_guidelines(
        page=page,
        page_size=page_size,
        search=search,
        title=title,
        publisher=publisher,
        chuyen_khoa=chuyen_khoa,
    )

    items: list[GuidelineListItem] = []
    for guideline in guidelines:
        active_version_data = active_versions.get(guideline.guideline_id)
        active_version = (
            GuidelineVersionSummary(**active_version_data)
            if active_version_data is not None
            else None
        )
        item = GuidelineListItem.model_validate(guideline)
        item.active_version = active_version
        items.append(item)

    return GuidelineListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{guideline_id}/versions",
    response_model=GuidelineVersionListResponse,
    summary="List Guideline Versions",
)
async def list_guideline_versions(
    guideline_id: int,
    db: DBSession,
    _: ActiveUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query(max_length=50)] = None,
) -> GuidelineVersionListResponse:
    service = GuidelineQueryService(db)
    versions, total = await service.list_guideline_versions(
        guideline_id=guideline_id,
        page=page,
        page_size=page_size,
        status=status,
    )
    return GuidelineVersionListResponse(
        guideline_id=guideline_id,
        items=[GuidelineVersionItem.model_validate(version) for version in versions],
        total=total,
        page=page,
        page_size=page_size,
    )
