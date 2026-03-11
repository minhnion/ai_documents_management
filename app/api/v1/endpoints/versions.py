from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ActiveUser, DBSession
from app.schemas.guideline import (
    VersionWorkspaceResponse,
    WorkspaceDocumentInfo,
    WorkspaceGuidelineInfo,
    WorkspaceSectionNode,
    WorkspaceVersionInfo,
)
from app.services.guideline_workspace_service import GuidelineWorkspaceService

router = APIRouter(prefix="/versions", tags=["Versions"])


@router.get(
    "/{version_id}/workspace",
    response_model=VersionWorkspaceResponse,
    summary="Get Version Workspace",
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
            workspace_data["guideline"]
        ),
        version=WorkspaceVersionInfo.model_validate(workspace_data["version"]),
        documents=[
            WorkspaceDocumentInfo.model_validate(document)
            for document in workspace_data["documents"]
        ],
        toc=[
            WorkspaceSectionNode.model_validate(node)
            for node in workspace_data["toc"]
        ],
        section_count=int(workspace_data["section_count"]),
        suspect_score_threshold=float(workspace_data["suspect_score_threshold"]),
        suspect_section_count=int(workspace_data["suspect_section_count"]),
        full_text=workspace_data["full_text"],
    )
