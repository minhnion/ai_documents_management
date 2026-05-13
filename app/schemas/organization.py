from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: int
    slug: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationListResponse(BaseModel):
    items: list[OrganizationResponse]
    total: int
