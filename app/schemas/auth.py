from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AvailableRoleResponse(BaseModel):
    name: str
    description: str


class UserSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    full_name: str | None = None
    role: str
    parent_id: int | None = None
    is_active: bool


class UserResponse(UserSummaryResponse):
    parent: UserSummaryResponse | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    updated_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=512)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=512)
    role: str = "health_department"
    parent_id: int | None = Field(default=None, gt=0)
    is_active: bool = True


class UpdateUserRoleRequest(BaseModel):
    role: str
    parent_id: int | None = Field(default=None, gt=0)
    is_active: bool | None = None


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
