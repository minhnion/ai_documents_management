from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    UnprocessableEntityException,
)
from app.core.security import get_password_hash, verify_password
from app.models.user import User


class AuthService:
    ROLE_DESCRIPTIONS: dict[str, str] = {
        "admin": "Full access to user and guideline management.",
        "editor": "Can create/update guideline versions and documents.",
        "viewer": "Read-only access to guideline data.",
    }
    ROLE_ORDER: tuple[str, ...] = ("admin", "editor", "viewer")

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
                "Unknown role. Allowed values: admin, editor, viewer."
            )
        return normalized

    async def get_user_by_email(self, email: str) -> User | None:
        normalized_email = self.normalize_email(email)
        stmt = select(User).where(User.email == normalized_email)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> User | None:
        stmt = select(User).where(User.user_id == user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_users(self) -> list[User]:
        stmt = select(User).order_by(User.user_id)
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
            await self.db.flush()
        return user

    async def create_user(
        self,
        email: str,
        password: str,
        role: str,
        full_name: str | None = None,
        is_active: bool = True,
    ) -> User:
        normalized_email = self.normalize_email(email)
        if await self.get_user_by_email(normalized_email):
            raise ConflictException(
                f"User with email '{normalized_email}' already exists."
            )

        user = User(
            email=normalized_email,
            password_hash=get_password_hash(password),
            full_name=full_name,
            role=self.normalize_role(role),
            is_active=is_active,
        )
        self.db.add(user)
        await self.db.flush()
        created_user = await self.get_user_by_id(user.user_id)
        if created_user is None:
            raise UnprocessableEntityException("Cannot load created user.")
        return created_user

    async def update_user_role(self, user_id: int, role: str) -> User:
        user = await self.get_user_by_id(user_id)
        if user is None:
            raise NotFoundException("User", user_id)

        user.role = self.normalize_role(role)
        await self.db.flush()
        updated_user = await self.get_user_by_id(user_id)
        if updated_user is None:
            raise UnprocessableEntityException("Cannot load updated user.")
        return updated_user
