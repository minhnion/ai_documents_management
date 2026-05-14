from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import ActiveUser, DBSession, require_roles
from app.core.exceptions import BadRequestException
from app.schemas.organization import (
    CreateOrganizationRequest,
    OrganizationListResponse,
    OrganizationResponse,
)
from app.services.organization_service import OrganizationService

router = APIRouter(prefix="/organizations", tags=["Organizations"])


@router.get("", response_model=OrganizationListResponse, summary="List Organizations")
async def list_organizations(
    db: DBSession,
    current_user: ActiveUser,
) -> OrganizationListResponse:
    service = OrganizationService(db)
    if current_user.role == "admin":
        organizations = await service.list_organizations(active_only=True)
    else:
        if current_user.organization_id is None:
            raise BadRequestException("User account has no organization assigned.")
        organizations = [await service.get_organization(current_user.organization_id)]
    return OrganizationListResponse(
        items=[OrganizationResponse.model_validate(item) for item in organizations],
        total=len(organizations),
    )


@router.post("", response_model=OrganizationResponse, summary="Create Organization")
async def create_organization(
    payload: CreateOrganizationRequest,
    db: DBSession,
    _: Annotated[object, Depends(require_roles("admin"))],
) -> OrganizationResponse:
    organization = await OrganizationService(db).create_organization(payload.name)
    return OrganizationResponse.model_validate(organization)
