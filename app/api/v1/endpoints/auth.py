from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import ActiveUser, AuthServiceDep, require_roles
from app.core.roles import ACCOUNT_MANAGER_ROLES
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import (
    AvailableRoleResponse,
    ChangePasswordRequest,
    CreateUserRequest,
    DeleteUserResponse,
    LoginRequest,
    LoginResponse,
    PasswordActionResponse,
    ResetUserPasswordRequest,
    UpdateUserRoleRequest,
    UserListResponse,
    UserResponse,
    UserStatsResponse,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


async def _issue_login_token(
    email: str,
    password: str,
    auth_service: AuthServiceDep,
) -> LoginResponse:
    user = await auth_service.authenticate_user(
        email=email,
        password=password,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    access_token = create_access_token(
        subject=str(user.user_id),
        role=user.role,
    )
    return LoginResponse(
        access_token=access_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=LoginResponse, summary="Login")
async def login(payload: LoginRequest, auth_service: AuthServiceDep) -> LoginResponse:
    return await _issue_login_token(
        email=payload.email,
        password=payload.password,
        auth_service=auth_service,
    )


@router.get("/me", response_model=UserResponse, summary="Current User")
async def get_me(current_user: ActiveUser) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch(
    "/password",
    response_model=PasswordActionResponse,
    summary="Change Current User Password",
)
async def change_password(
    payload: ChangePasswordRequest,
    auth_service: AuthServiceDep,
    current_user: ActiveUser,
) -> PasswordActionResponse:
    await auth_service.change_password(
        current_user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return PasswordActionResponse(message="Password changed successfully.")


@router.get(
    "/roles",
    response_model=list[AvailableRoleResponse],
    summary="Available Roles",
)
async def list_roles(
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
) -> list[AvailableRoleResponse]:
    return [
        AvailableRoleResponse(**item)
        for item in auth_service.get_available_roles(current_user)
    ]


@router.get("/users", response_model=UserListResponse, summary="List Users")
async def list_users(
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int | None, Query(ge=1, le=100)] = None,
    role: Annotated[str | None, Query(max_length=50)] = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    is_active: Annotated[bool | None, Query()] = None,
    created_by_user_id: Annotated[int | None, Query(ge=1)] = None,
    parent_id: Annotated[int | None, Query(ge=1)] = None,
) -> UserListResponse:
    users, total = await auth_service.list_users_paginated(
        current_user,
        page=page,
        page_size=page_size,
        role=role,
        search=search,
        is_active=is_active,
        created_by_user_id=created_by_user_id,
        parent_id=parent_id,
    )
    return UserListResponse(
        items=[UserResponse.model_validate(user) for user in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/users/stats",
    response_model=UserStatsResponse,
    summary="User Statistics",
)
async def get_user_stats(
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
    granularity: Annotated[str, Query(pattern="^(day|month|year)$")] = "month",
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> UserStatsResponse:
    stats = await auth_service.get_user_stats(
        current_user,
        granularity=granularity,
        date_from=date_from,
        date_to=date_to,
    )
    return UserStatsResponse(**stats)


@router.post("/users", response_model=UserResponse, summary="Create User")
async def create_user(
    payload: CreateUserRequest,
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
) -> UserResponse:
    user = await auth_service.create_user(
        current_user=current_user,
        email=payload.email,
        password=payload.password,
        role=payload.role,
        full_name=payload.full_name,
        parent_id=payload.parent_id,
        is_active=payload.is_active,
        inherits_global_documents=payload.inherits_global_documents,
    )
    return UserResponse.model_validate(user)


@router.patch(
    "/users/{user_id}/password",
    response_model=UserResponse,
    summary="Reset User Password",
)
async def reset_user_password(
    user_id: int,
    payload: ResetUserPasswordRequest,
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
) -> UserResponse:
    user = await auth_service.reset_user_password(
        current_user=current_user,
        user_id=user_id,
        new_password=payload.new_password,
    )
    return UserResponse.model_validate(user)


@router.patch(
    "/users/{user_id}/role",
    response_model=UserResponse,
    summary="Update User Role",
)
async def update_user_role(
    user_id: int,
    payload: UpdateUserRoleRequest,
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
) -> UserResponse:
    user = await auth_service.update_user_role(
        current_user=current_user,
        user_id=user_id,
        role=payload.role,
        parent_id=payload.parent_id,
        is_active=payload.is_active,
        inherits_global_documents=payload.inherits_global_documents,
    )
    return UserResponse.model_validate(user)


@router.delete(
    "/users/{user_id}",
    response_model=DeleteUserResponse,
    summary="Delete User",
)
async def delete_user(
    user_id: int,
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles(*ACCOUNT_MANAGER_ROLES))],
) -> DeleteUserResponse:
    result = await auth_service.delete_user(
        current_user=current_user,
        user_id=user_id,
    )
    return DeleteUserResponse(**result)
