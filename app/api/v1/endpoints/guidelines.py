from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import ActiveUser, DBSession, require_roles
from app.schemas.guideline import (
    CreateGuidelineResponse,
    CreateGuidelineVersionResponse,
    DeleteGuidelineResponse,
    GuidelineFilterOptionsResponse,
    GuidelineListItem,
    GuidelineListResponse,
    GuidelineVersionItem,
    GuidelineVersionListResponse,
    GuidelineVersionSummary,
    UpdateGuidelineMetadataRequest,
    UpdateGuidelineMetadataResponse,
)
from app.services.guideline_command_service import GuidelineCommandService
from app.services.guideline_delete_service import GuidelineDeleteService
from app.services.guideline_metadata_service import GuidelineMetadataService
from app.services.guideline_query_service import GuidelineQueryService
from app.services.tenant_access_service import TenantAccessService

router = APIRouter(prefix="/guidelines", tags=["Guidelines"])


@router.get(
    "/filter-options",
    response_model=GuidelineFilterOptionsResponse,
    summary="Get Filter Options",
)
async def get_filter_options(
    db: DBSession,
    current_user: ActiveUser,
    organization_id: Annotated[int | None, Query(gt=0)] = None,
) -> GuidelineFilterOptionsResponse:
    service = GuidelineQueryService(db)
    options = await service.get_filter_options(
        current_user=current_user,
        organization_id=organization_id,
    )
    return GuidelineFilterOptionsResponse(**options)


@router.get("", response_model=GuidelineListResponse, summary="List Guidelines")
async def list_guidelines(
    db: DBSession,
    current_user: ActiveUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=255)] = None,
    title: Annotated[str | None, Query(max_length=255)] = None,
    ten_benh: Annotated[str | None, Query(max_length=255)] = None,
    publisher: Annotated[str | None, Query(max_length=255)] = None,
    chuyen_khoa: Annotated[str | None, Query(max_length=255)] = None,
    organization_id: Annotated[int | None, Query(gt=0)] = None,
) -> GuidelineListResponse:
    service = GuidelineQueryService(db)
    guidelines, active_versions, total = await service.list_guidelines(
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        title=title,
        ten_benh=ten_benh,
        publisher=publisher,
        chuyen_khoa=chuyen_khoa,
        organization_id=organization_id,
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


@router.post("", response_model=CreateGuidelineResponse, status_code=202, summary="Create Guideline")
async def create_guideline(
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles("user", "admin"))],
    title: Annotated[str, Form(min_length=1, max_length=1000)],
    file: Annotated[UploadFile, File()],
    ten_benh: Annotated[str | None, Form(max_length=500)] = None,
    publisher: Annotated[str | None, Form(max_length=500)] = None,
    chuyen_khoa: Annotated[str | None, Form(max_length=255)] = None,
    organization_id: Annotated[int | None, Form()] = None,
    organization_name: Annotated[str | None, Form(max_length=255)] = None,
    version_label: Annotated[str | None, Form(max_length=50)] = None,
    release_date: Annotated[date | None, Form()] = None,
    effective_from: Annotated[date | None, Form()] = None,
    effective_to: Annotated[date | None, Form()] = None,
    status: Annotated[str | None, Form(max_length=50)] = "active",
) -> CreateGuidelineResponse:
    service = GuidelineCommandService(db)
    guideline, guideline_version, document, job_result = await service.create_guideline(
        current_user=current_user,
        title=title,
        ten_benh=ten_benh,
        publisher=publisher,
        chuyen_khoa=chuyen_khoa,
        organization_id=organization_id,
        organization_name=organization_name,
        version_label=version_label,
        release_date=release_date,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        upload_file=file,
        doc_type="pdf",
    )
    return CreateGuidelineResponse(
        accepted=bool(job_result["accepted"]),
        guideline_id=guideline.guideline_id,
        organization_id=guideline.organization_id,
        version_id=guideline_version.version_id,
        document_id=document.document_id,
        storage_uri=document.storage_uri,
        job_id=job_result.get("job_id"),
        pipeline_status=str(job_result["status"]),
        version_status=job_result.get("version_status"),
        target_status=job_result.get("target_status"),
    )


@router.patch(
    "/{guideline_id}",
    response_model=UpdateGuidelineMetadataResponse,
    summary="Update Guideline Metadata",
)
async def update_guideline_metadata(
    guideline_id: int,
    payload: UpdateGuidelineMetadataRequest,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles("user", "admin"))],
) -> UpdateGuidelineMetadataResponse:
    await TenantAccessService(db).ensure_guideline_access(
        guideline_id=guideline_id,
        current_user=current_user,
        for_update=True,
    )
    service = GuidelineMetadataService(db)
    guideline = await service.update_guideline_metadata(
        guideline_id=guideline_id,
        patch=payload.model_dump(exclude_unset=True),
    )
    return UpdateGuidelineMetadataResponse.model_validate(guideline)


@router.delete(
    "/{guideline_id}",
    response_model=DeleteGuidelineResponse,
    summary="Delete Guideline",
)
async def delete_guideline(
    guideline_id: int,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles("user", "admin"))],
) -> DeleteGuidelineResponse:
    await TenantAccessService(db).ensure_guideline_access(
        guideline_id=guideline_id,
        current_user=current_user,
        for_update=True,
    )
    service = GuidelineDeleteService(db)
    result = await service.delete_guideline(guideline_id)
    return DeleteGuidelineResponse(**result)


@router.post(
    "/{guideline_id}/versions",
    response_model=CreateGuidelineVersionResponse,
    status_code=202,
    summary="Create Guideline Version",
)
async def create_guideline_version(
    guideline_id: int,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles("user", "admin"))],
    file: Annotated[UploadFile, File()],
    version_label: Annotated[str | None, Form(max_length=50)] = None,
    release_date: Annotated[date | None, Form()] = None,
    effective_from: Annotated[date | None, Form()] = None,
    effective_to: Annotated[date | None, Form()] = None,
    status: Annotated[str | None, Form(max_length=50)] = "active",
) -> CreateGuidelineVersionResponse:
    service = GuidelineCommandService(db)
    (_, guideline_version, document, job_result) = await service.create_guideline_version(
        current_user=current_user,
        guideline_id=guideline_id,
        version_label=version_label,
        release_date=release_date,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        upload_file=file,
        doc_type="pdf",
    )
    return CreateGuidelineVersionResponse(
        accepted=bool(job_result["accepted"]),
        guideline_id=guideline_id,
        version_id=guideline_version.version_id,
        status=guideline_version.status,
        previous_active_versions_updated=int(job_result.get("previous_active_versions_updated", 0) or 0),
        document_id=document.document_id if document else None,
        storage_uri=document.storage_uri if document else None,
        job_id=job_result.get("job_id"),
        pipeline_status=str(job_result["status"]),
        version_status=job_result.get("version_status"),
        target_status=job_result.get("target_status"),
    )


@router.get(
    "/{guideline_id}/versions",
    response_model=GuidelineVersionListResponse,
    summary="List Guideline Versions",
)
async def list_guideline_versions(
    guideline_id: int,
    db: DBSession,
    current_user: ActiveUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query(max_length=50)] = None,
) -> GuidelineVersionListResponse:
    service = GuidelineQueryService(db)
    versions, total = await service.list_guideline_versions(
        current_user=current_user,
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
