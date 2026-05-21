from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.api.deps import ActiveUser, DBSession, require_roles
from app.schemas.guideline import (
    BulkSectionContentUpdateRequest,
    BulkSectionContentUpdateResponse,
    DeleteGuidelineVersionResponse,
    RebuildVersionChunksResponse,
    UpdateGuidelineVersionMetadataRequest,
    UpdateGuidelineVersionMetadataResponse,
    VersionIngestionStatusResponse,
    VersionChunkRebuildStatusResponse,
    VersionWorkspaceResponse,
    WorkspaceDocumentInfo,
    WorkspaceGuidelineInfo,
    WorkspaceSectionNode,
    WorkspaceVersionInfo,
)
from app.services.guideline_chunk_service import GuidelineChunkService
from app.services.guideline_delete_service import GuidelineDeleteService
from app.services.guideline_edit_service import (
    GuidelineEditService,
    SectionContentUpdate,
)
from app.services.guideline_ingestion_job_service import GuidelineIngestionJobService
from app.services.guideline_metadata_service import GuidelineMetadataService
from app.services.guideline_workspace_service import GuidelineWorkspaceService
from app.services.tenant_access_service import TenantAccessService
from app.services.version_asset_service import VersionAssetService

router = APIRouter(prefix='/versions', tags=['Versions'])

MANAGE_ROLES = ('health_department', 'hospital', 'doctor', 'admin')


def _version_access_flags(current_user, owner_user_id: int) -> tuple[bool, str]:
    can_manage = current_user.role == 'admin' or int(current_user.user_id) == int(owner_user_id)
    access_scope = (
        'admin'
        if current_user.role == 'admin'
        else 'owned' if int(current_user.user_id) == int(owner_user_id) else 'inherited'
    )
    return can_manage, access_scope


@router.get(
    '/{version_id}/workspace',
    response_model=VersionWorkspaceResponse,
    summary='Get Version Workspace',
)
async def get_version_workspace(
    version_id: int,
    db: DBSession,
    current_user: ActiveUser,
    include_full_text: Annotated[bool, Query()] = True,
    suspect_threshold: Annotated[float | None, Query(gt=0.0, lt=1.0)] = None,
) -> VersionWorkspaceResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
    )
    service = GuidelineWorkspaceService(db)
    workspace_data = await service.get_workspace(
        version_id=version_id,
        include_full_text=include_full_text,
        suspect_threshold=suspect_threshold,
    )
    guideline = workspace_data['guideline']
    can_manage, access_scope = _version_access_flags(current_user, guideline.owner_user_id)
    return VersionWorkspaceResponse(
        guideline=WorkspaceGuidelineInfo.model_validate(guideline),
        version=WorkspaceVersionInfo.model_validate(workspace_data['version']),
        documents=[
            WorkspaceDocumentInfo.model_validate(document)
            for document in workspace_data['documents']
        ],
        pipeline_mode_used=workspace_data['pipeline_mode_used'],
        positioning_mode=str(workspace_data['positioning_mode']),
        toc=[
            WorkspaceSectionNode.model_validate(node)
            for node in workspace_data['toc']
        ],
        section_count=int(workspace_data['section_count']),
        suspect_score_threshold=float(workspace_data['suspect_score_threshold']),
        suspect_section_count=int(workspace_data['suspect_section_count']),
        full_text=workspace_data['full_text'],
        can_edit=can_manage,
        can_delete=can_manage,
        access_scope=access_scope,
    )


@router.patch(
    '/{version_id}',
    response_model=UpdateGuidelineVersionMetadataResponse,
    summary='Update Guideline Version Metadata',
)
async def update_guideline_version_metadata(
    version_id: int,
    payload: UpdateGuidelineVersionMetadataRequest,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles(*MANAGE_ROLES))],
) -> UpdateGuidelineVersionMetadataResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
        for_update=True,
    )
    service = GuidelineMetadataService(db)
    result = await service.update_version_metadata(
        version_id=version_id,
        patch=payload.model_dump(exclude_unset=True),
    )
    return UpdateGuidelineVersionMetadataResponse(**result)


@router.patch(
    '/{version_id}/sections/content',
    response_model=BulkSectionContentUpdateResponse,
    summary='Bulk Update Section Content and Heading',
)
async def bulk_update_section_content(
    version_id: int,
    payload: BulkSectionContentUpdateRequest,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles(*MANAGE_ROLES))],
) -> BulkSectionContentUpdateResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
        for_update=True,
    )
    service = GuidelineEditService(db)
    result = await service.bulk_update_section_content(
        version_id=version_id,
        updates=[
            SectionContentUpdate(
                section_id=item.section_id,
                content=item.content,
                heading=item.heading,
            )
            for item in payload.updates
        ],
    )
    return BulkSectionContentUpdateResponse(**result)


@router.get(
    '/{version_id}/pipeline/status',
    response_model=VersionIngestionStatusResponse,
    summary='Get Version Ingestion Pipeline Status',
)
async def get_version_ingestion_status(
    version_id: int,
    db: DBSession,
    current_user: ActiveUser,
) -> VersionIngestionStatusResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
    )
    service = GuidelineIngestionJobService(db)
    result = await service.get_version_ingestion_status(version_id)
    return VersionIngestionStatusResponse(**result)


@router.post(
    '/{version_id}/chunks/rebuild',
    response_model=RebuildVersionChunksResponse,
    status_code=202,
    summary='Enqueue Version Chunk Rebuild',
)
async def rebuild_version_chunks(
    version_id: int,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles(*MANAGE_ROLES))],
) -> RebuildVersionChunksResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
        for_update=True,
    )
    service = GuidelineChunkService(db)
    result = await service.enqueue_version_chunk_rebuild(version_id)
    return RebuildVersionChunksResponse(**result)


@router.get(
    '/{version_id}/chunks/status',
    response_model=VersionChunkRebuildStatusResponse,
    summary='Get Version Chunk Rebuild Status',
)
async def get_version_chunk_rebuild_status(
    version_id: int,
    db: DBSession,
    current_user: ActiveUser,
) -> VersionChunkRebuildStatusResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
    )
    service = GuidelineChunkService(db)
    result = await service.get_version_chunk_rebuild_status(version_id)
    return VersionChunkRebuildStatusResponse(**result)


@router.delete(
    '/{version_id}',
    response_model=DeleteGuidelineVersionResponse,
    summary='Delete Guideline Version',
)
async def delete_guideline_version(
    version_id: int,
    db: DBSession,
    current_user: Annotated[object, Depends(require_roles(*MANAGE_ROLES))],
) -> DeleteGuidelineVersionResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
        for_update=True,
    )
    service = GuidelineDeleteService(db)
    result = await service.delete_version(version_id)
    return DeleteGuidelineVersionResponse(**result)


@router.get(
    '/{version_id}/assets/{landing_chunk_id}',
    summary='Get Version Asset (table/figure PNG)',
    responses={
        200: {'description': 'PNG image cropped from the source PDF.'},
        404: {'description': 'Version or asset not found.'},
    },
)
async def get_version_asset(
    version_id: int,
    landing_chunk_id: str,
    db: DBSession,
    current_user: ActiveUser,
) -> FileResponse:
    await TenantAccessService(db).ensure_version_access(
        version_id=version_id,
        current_user=current_user,
    )
    service = VersionAssetService(db)
    asset = await service.get_asset_file(
        version_id=version_id,
        landing_chunk_id=landing_chunk_id,
    )
    return FileResponse(
        path=str(asset.path),
        media_type=asset.media_type,
        headers={'Cache-Control': 'public, max-age=86400'},
    )
