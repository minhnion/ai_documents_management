from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import ActiveUser, DBSession, require_roles
from app.schemas.guideline import (
    CreateGuidelineResponse,
    CreateGuidelineVersionResponse,
    DeleteGuidelineResponse,
    GuidelineListItem,
    GuidelineListResponse,
    GuidelineVersionItem,
    GuidelineVersionListResponse,
    GuidelineVersionSummary,
)
from app.services.guideline_command_service import GuidelineCommandService
from app.services.guideline_delete_service import GuidelineDeleteService
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


@router.post("", response_model=CreateGuidelineResponse, summary="Create Guideline")
async def create_guideline(
    db: DBSession,
    _: Annotated[object, Depends(require_roles("editor", "admin"))],
    title: Annotated[str, Form(min_length=1, max_length=1000)],
    file: Annotated[UploadFile, File()],
    publisher: Annotated[str | None, Form(max_length=500)] = None,
    chuyen_khoa: Annotated[str | None, Form(max_length=255)] = None,
    version_label: Annotated[str | None, Form(max_length=50)] = None,
    release_date: Annotated[date | None, Form()] = None,
    effective_from: Annotated[date | None, Form()] = None,
    effective_to: Annotated[date | None, Form()] = None,
    status: Annotated[str | None, Form(max_length=50)] = "active",
) -> CreateGuidelineResponse:
    service = GuidelineCommandService(db)
    guideline, guideline_version, document = await service.create_guideline(
        title=title,
        publisher=publisher,
        chuyen_khoa=chuyen_khoa,
        version_label=version_label,
        release_date=release_date,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        upload_file=file,
        doc_type="pdf",
    )
    return CreateGuidelineResponse(
        guideline_id=guideline.guideline_id,
        version_id=guideline_version.version_id,
        document_id=document.document_id,
        storage_uri=document.storage_uri,
    )


@router.delete(
    "/{guideline_id}",
    response_model=DeleteGuidelineResponse,
    summary="Delete Guideline",
)
async def delete_guideline(
    guideline_id: int,
    db: DBSession,
    _: Annotated[object, Depends(require_roles("admin"))],
) -> DeleteGuidelineResponse:
    service = GuidelineDeleteService(db)
    result = await service.delete_guideline(guideline_id)
    return DeleteGuidelineResponse(**result)


@router.post(
    "/{guideline_id}/versions",
    response_model=CreateGuidelineVersionResponse,
    summary="Create Guideline Version",
)
async def create_guideline_version(
    guideline_id: int,
    db: DBSession,
    _: Annotated[object, Depends(require_roles("editor", "admin"))],
    file: Annotated[UploadFile, File()],
    version_label: Annotated[str | None, Form(max_length=50)] = None,
    release_date: Annotated[date | None, Form()] = None,
    effective_from: Annotated[date | None, Form()] = None,
    effective_to: Annotated[date | None, Form()] = None,
    status: Annotated[str | None, Form(max_length=50)] = "active",
) -> CreateGuidelineVersionResponse:
    service = GuidelineCommandService(db)
    _, guideline_version, document, previous_active_versions_updated = (
        await service.create_guideline_version(
            guideline_id=guideline_id,
            version_label=version_label,
            release_date=release_date,
            effective_from=effective_from,
            effective_to=effective_to,
            status=status,
            upload_file=file,
            doc_type="pdf",
        )
    )
    return CreateGuidelineVersionResponse(
        guideline_id=guideline_id,
        version_id=guideline_version.version_id,
        status=guideline_version.status,
        previous_active_versions_updated=previous_active_versions_updated,
        document_id=document.document_id if document else None,
        storage_uri=document.storage_uri if document else None,
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
