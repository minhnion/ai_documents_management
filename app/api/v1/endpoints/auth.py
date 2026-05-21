from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import ActiveUser, AuthServiceDep, require_roles
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import (
    AvailableRoleResponse,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    UpdateUserRoleRequest,
    UserListResponse,
    UserResponse,
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


@router.get(
    "/roles",
    response_model=list[AvailableRoleResponse],
    summary="Available Roles",
)
async def list_roles(
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles("admin", "health_department", "hospital"))],
) -> list[AvailableRoleResponse]:
    return [
        AvailableRoleResponse(**item)
        for item in auth_service.get_available_roles(current_user)
    ]


@router.get("/users", response_model=UserListResponse, summary="List Users")
async def list_users(
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles("admin", "health_department", "hospital"))],
) -> UserListResponse:
    users = await auth_service.list_users(current_user)
    return UserListResponse(
        items=[UserResponse.model_validate(user) for user in users],
        total=len(users),
    )


@router.post("/users", response_model=UserResponse, summary="Create User")
async def create_user(
    payload: CreateUserRequest,
    auth_service: AuthServiceDep,
    current_user: Annotated[object, Depends(require_roles("admin", "health_department", "hospital"))],
) -> UserResponse:
    user = await auth_service.create_user(
        current_user=current_user,
        email=payload.email,
        password=payload.password,
        role=payload.role,
        full_name=payload.full_name,
        parent_id=payload.parent_id,
        parent_name=payload.parent_name,
        parent_parent_id=payload.parent_parent_id,
        is_active=payload.is_active,
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
    current_user: Annotated[object, Depends(require_roles("admin", "health_department", "hospital"))],
) -> UserResponse:
    user = await auth_service.update_user_role(
        current_user=current_user,
        user_id=user_id,
        role=payload.role,
        parent_id=payload.parent_id,
        parent_name=payload.parent_name,
        parent_parent_id=payload.parent_parent_id,
        is_active=payload.is_active,
    )
    return UserResponse.model_validate(user)
