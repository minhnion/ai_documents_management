from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    UnprocessableEntityException,
)
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.services.organization_service import OrganizationService


class AuthService:
    ROLE_DESCRIPTIONS: dict[str, str] = {
        "admin": "Full access to all organizations, users, and documents.",
        "user": "Can manage documents within one assigned organization.",
    }
    ROLE_ORDER: tuple[str, ...] = ("admin", "user")

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @classmethod
    def get_available_roles(cls) -> list[dict[str, str]]:
        return [
            {"name": role_name, "description": cls.ROLE_DESCRIPTIONS[role_name]}
            for role_name in cls.ROLE_ORDER
        ]

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @classmethod
    def normalize_role(cls, role: str) -> str:
        normalized = role.strip().lower()
        if normalized not in cls.ROLE_DESCRIPTIONS:
            raise BadRequestException(
                "Unknown role. Allowed values: admin, user."
            )
        return normalized

    async def get_user_by_email(self, email: str) -> User | None:
        normalized_email = self.normalize_email(email)
        stmt = (
            select(User)
            .options(selectinload(User.organization))
            .where(User.email == normalized_email)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> User | None:
        stmt = (
            select(User)
            .options(selectinload(User.organization))
            .where(User.user_id == user_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_users(self) -> list[User]:
        stmt = (
            select(User)
            .options(selectinload(User.organization))
            .order_by(User.user_id)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def authenticate_user(self, email: str, password: str) -> User | None:
        user = await self.get_user_by_email(email)
        if user is None:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    async def ensure_default_admin(
        self,
        email: str,
        password: str,
        full_name: str,
    ) -> User | None:
        normalized_email = self.normalize_email(email)
        if not normalized_email or not password:
            return None

        user = await self.get_user_by_email(normalized_email)
        if user is None:
            user = User(
                email=normalized_email,
                full_name=full_name.strip() if full_name else "System Admin",
                password_hash=get_password_hash(password),
                role="admin",
                is_active=True,
            )
            self.db.add(user)
            await self.db.flush()
            return await self.get_user_by_id(user.user_id)

        if user.role != "admin":
            user.role = "admin"
            user.organization_id = None
            await self.db.flush()
        return user

    async def create_user(
        self,
        email: str,
        password: str,
        role: str,
        full_name: str | None = None,
        organization_id: int | None = None,
        organization_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        normalized_email = self.normalize_email(email)
        if await self.get_user_by_email(normalized_email):
            raise ConflictException(
                f"User with email '{normalized_email}' already exists."
            )

        normalized_role = self.normalize_role(role)
        resolved_organization_id = await self._resolve_organization_id_for_role(
            role=normalized_role,
            organization_id=organization_id,
            organization_name=organization_name,
        )

        user = User(
            email=normalized_email,
            password_hash=get_password_hash(password),
            full_name=full_name,
            role=normalized_role,
            organization_id=resolved_organization_id,
            is_active=is_active,
        )
        self.db.add(user)
        await self.db.flush()
        created_user = await self.get_user_by_id(user.user_id)
        if created_user is None:
            raise UnprocessableEntityException("Cannot load created user.")
        return created_user

    async def update_user_role(
        self,
        user_id: int,
        role: str,
        organization_id: int | None = None,
        organization_name: str | None = None,
    ) -> User:
        user = await self.get_user_by_id(user_id)
        if user is None:
            raise NotFoundException("User", user_id)

        normalized_role = self.normalize_role(role)
        user.role = normalized_role
        target_organization_id = (
            None
            if organization_name and organization_name.strip()
            else organization_id if organization_id is not None else user.organization_id
        )
        user.organization_id = await self._resolve_organization_id_for_role(
            role=normalized_role,
            organization_id=target_organization_id,
            organization_name=organization_name,
        )
        await self.db.flush()
        updated_user = await self.get_user_by_id(user_id)
        if updated_user is None:
            raise UnprocessableEntityException("Cannot load updated user.")
        return updated_user

    async def _resolve_organization_id_for_role(
        self,
        *,
        role: str,
        organization_id: int | None,
        organization_name: str | None,
    ) -> int | None:
        if role == "admin":
            return None
        organization = await OrganizationService(self.db).resolve_from_payload(
            organization_id=organization_id,
            organization_name=organization_name,
        )
        return int(organization.organization_id)
