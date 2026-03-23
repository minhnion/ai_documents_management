from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import ActiveUser, DBSession, require_roles
from app.schemas.guideline import (
    BulkSectionContentUpdateRequest,
    BulkSectionContentUpdateResponse,
    DeleteGuidelineVersionResponse,
    RebuildVersionChunksResponse,
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
from app.services.guideline_workspace_service import GuidelineWorkspaceService

router = APIRouter(prefix='/versions', tags=['Versions'])


@router.get(
    '/{version_id}/workspace',
    response_model=VersionWorkspaceResponse,
    summary='Get Version Workspace',
)
async def get_version_workspace(
    version_id: int,
    db: DBSession,
    _: ActiveUser,
    include_full_text: Annotated[bool, Query()] = True,
    suspect_threshold: Annotated[float | None, Query(gt=0.0, lt=1.0)] = None,
) -> VersionWorkspaceResponse:
    service = GuidelineWorkspaceService(db)
    workspace_data = await service.get_workspace(
        version_id=version_id,
        include_full_text=include_full_text,
        suspect_threshold=suspect_threshold,
    )
    return VersionWorkspaceResponse(
        guideline=WorkspaceGuidelineInfo.model_validate(
            workspace_data['guideline']
        ),
        version=WorkspaceVersionInfo.model_validate(workspace_data['version']),
        documents=[
            WorkspaceDocumentInfo.model_validate(document)
            for document in workspace_data['documents']
        ],
        toc=[
            WorkspaceSectionNode.model_validate(node)
            for node in workspace_data['toc']
        ],
        section_count=int(workspace_data['section_count']),
        suspect_score_threshold=float(workspace_data['suspect_score_threshold']),
        suspect_section_count=int(workspace_data['suspect_section_count']),
        full_text=workspace_data['full_text'],
    )


@router.patch(
    '/{version_id}/sections/content',
    response_model=BulkSectionContentUpdateResponse,
    summary='Bulk Update Section Content and Heading',
)
async def bulk_update_section_content(
    version_id: int,
    payload: BulkSectionContentUpdateRequest,
    db: DBSession,
    _: Annotated[object, Depends(require_roles('editor', 'admin'))],
) -> BulkSectionContentUpdateResponse:
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
    _: ActiveUser,
) -> VersionIngestionStatusResponse:
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
    _: Annotated[object, Depends(require_roles('editor', 'admin'))],
) -> RebuildVersionChunksResponse:
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
    _: ActiveUser,
) -> VersionChunkRebuildStatusResponse:
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
    _: Annotated[object, Depends(require_roles('editor', 'admin'))],
) -> DeleteGuidelineVersionResponse:
    service = GuidelineDeleteService(db)
    result = await service.delete_version(version_id)
    return DeleteGuidelineVersionResponse(**result)
