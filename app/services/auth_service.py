from __future__ import annotations

import re
import secrets
import unicodedata

from sqlalchemy import func, select
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


class AuthService:
    ROLE_ADMIN = "admin"
    ROLE_HEALTH_DEPARTMENT = "health_department"
    ROLE_HOSPITAL = "hospital"
    ROLE_DOCTOR = "doctor"

    ROLE_DESCRIPTIONS: dict[str, str] = {
        ROLE_ADMIN: "Full access to all accounts and documents.",
        ROLE_HEALTH_DEPARTMENT: "Cap so y te: manage own documents and create hospital accounts.",
        ROLE_HOSPITAL: "Cap benh vien: inherit parent documents, manage own documents, and create doctor accounts.",
        ROLE_DOCTOR: "Cap bac si: inherit hospital/department documents and manage own documents.",
    }
    ROLE_ORDER: tuple[str, ...] = (
        ROLE_ADMIN,
        ROLE_HEALTH_DEPARTMENT,
        ROLE_HOSPITAL,
        ROLE_DOCTOR,
    )
    CHILD_ROLE_BY_CREATOR: dict[str, str] = {
        ROLE_HEALTH_DEPARTMENT: ROLE_HOSPITAL,
        ROLE_HOSPITAL: ROLE_DOCTOR,
    }
    PARENT_ROLE_BY_ROLE: dict[str, str] = {
        ROLE_HOSPITAL: ROLE_HEALTH_DEPARTMENT,
        ROLE_DOCTOR: ROLE_HOSPITAL,
    }

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @classmethod
    def get_available_roles(cls, current_user: User | None = None) -> list[dict[str, str]]:
        if current_user is None or current_user.role == cls.ROLE_ADMIN:
            role_names = cls.ROLE_ORDER
        else:
            child_role = cls.CHILD_ROLE_BY_CREATOR.get(current_user.role)
            role_names = (child_role,) if child_role else ()
        return [
            {"name": role_name, "description": cls.ROLE_DESCRIPTIONS[role_name]}
            for role_name in role_names
        ]

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @classmethod
    def normalize_role(cls, role: str) -> str:
        normalized = role.strip().lower()
        legacy_role_map = {
            "user": cls.ROLE_HEALTH_DEPARTMENT,
            "editor": cls.ROLE_HEALTH_DEPARTMENT,
            "viewer": cls.ROLE_HEALTH_DEPARTMENT,
        }
        normalized = legacy_role_map.get(normalized, normalized)
        if normalized not in cls.ROLE_DESCRIPTIONS:
            raise BadRequestException(
                "Unknown role. Allowed values: admin, health_department, hospital, doctor."
            )
        return normalized

    async def get_user_by_email(self, email: str) -> User | None:
        normalized_email = self.normalize_email(email)
        stmt = (
            select(User)
            .options(selectinload(User.parent))
            .where(User.email == normalized_email)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> User | None:
        stmt = (
            select(User)
            .options(selectinload(User.parent))
            .where(User.user_id == user_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_users(self, current_user: User) -> list[User]:
        stmt = (
            select(User)
            .options(selectinload(User.parent))
            .order_by(User.role.asc(), User.parent_id.asc().nullsfirst(), User.user_id.asc())
        )
        users = list((await self.db.execute(stmt)).scalars().all())
        if current_user.role == self.ROLE_ADMIN:
            return users

        allowed_ids = self._collect_descendant_ids(users, int(current_user.user_id))
        allowed_ids.add(int(current_user.user_id))
        return [user for user in users if int(user.user_id) in allowed_ids]

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
                role=self.ROLE_ADMIN,
                parent_id=None,
                is_active=True,
            )
            self.db.add(user)
            await self.db.flush()
            return await self.get_user_by_id(user.user_id)

        if user.role != self.ROLE_ADMIN or user.parent_id is not None:
            user.role = self.ROLE_ADMIN
            user.parent_id = None
            await self.db.flush()
        return user

    async def create_user(
        self,
        *,
        current_user: User,
        email: str,
        password: str,
        role: str,
        full_name: str | None = None,
        parent_id: int | None = None,
        parent_name: str | None = None,
        parent_parent_id: int | None = None,
        is_active: bool = True,
    ) -> User:
        normalized_email = self.normalize_email(email)
        if await self.get_user_by_email(normalized_email):
            raise ConflictException(
                f"User with email '{normalized_email}' already exists."
            )

        normalized_role = self.normalize_role(role)
        self._ensure_can_create_role(current_user=current_user, role=normalized_role)
        resolved_parent_id = await self._resolve_parent_id_for_role(
            current_user=current_user,
            role=normalized_role,
            parent_id=parent_id,
            parent_name=parent_name,
            parent_parent_id=parent_parent_id,
        )
        normalized_full_name = self._normalize_display_name(full_name, role=normalized_role)

        user = User(
            email=normalized_email,
            password_hash=get_password_hash(password),
            full_name=normalized_full_name,
            role=normalized_role,
            parent_id=resolved_parent_id,
            created_by_user_id=int(current_user.user_id),
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
        *,
        current_user: User,
        user_id: int,
        role: str,
        parent_id: int | None = None,
        parent_name: str | None = None,
        parent_parent_id: int | None = None,
        is_active: bool | None = None,
    ) -> User:
        user = await self.get_user_by_id(user_id)
        if user is None:
            raise NotFoundException("User", user_id)
        if int(user.user_id) == int(current_user.user_id):
            raise BadRequestException("Cannot change your own role from this screen.")

        normalized_role = self.normalize_role(role)
        self._ensure_can_manage_user(current_user=current_user, target_user=user)
        self._ensure_can_create_role(current_user=current_user, role=normalized_role)

        user.role = normalized_role
        user.parent_id = await self._resolve_parent_id_for_role(
            current_user=current_user,
            role=normalized_role,
            parent_id=parent_id,
            parent_name=parent_name,
            parent_parent_id=parent_parent_id,
        )
        if is_active is not None:
            user.is_active = bool(is_active)
        await self.db.flush()
        updated_user = await self.get_user_by_id(user_id)
        if updated_user is None:
            raise UnprocessableEntityException("Cannot load updated user.")
        return updated_user

    def _ensure_can_create_role(self, *, current_user: User, role: str) -> None:
        if current_user.role == self.ROLE_ADMIN:
            return
        allowed_role = self.CHILD_ROLE_BY_CREATOR.get(current_user.role)
        if role != allowed_role:
            raise BadRequestException("Current account cannot create or assign this role.")

    def _ensure_can_manage_user(self, *, current_user: User, target_user: User) -> None:
        if current_user.role == self.ROLE_ADMIN:
            return
        if int(target_user.parent_id or 0) != int(current_user.user_id):
            raise NotFoundException("User", target_user.user_id)

    async def _resolve_parent_id_for_role(
        self,
        *,
        current_user: User,
        role: str,
        parent_id: int | None,
        parent_name: str | None,
        parent_parent_id: int | None,
    ) -> int | None:
        if role in (self.ROLE_ADMIN, self.ROLE_HEALTH_DEPARTMENT):
            return None

        expected_parent_role = self.PARENT_ROLE_BY_ROLE[role]
        if current_user.role != self.ROLE_ADMIN:
            if parent_id is not None and int(parent_id) != int(current_user.user_id):
                raise BadRequestException("Child account must be created under the current account.")
            return int(current_user.user_id)

        if parent_id is not None:
            parent = await self.get_user_by_id(parent_id)
            if parent is None or not parent.is_active:
                raise NotFoundException("User", parent_id)
            if parent.role != expected_parent_role:
                raise BadRequestException(
                    f"Parent for role '{role}' must have role '{expected_parent_role}'."
                )
            return int(parent.user_id)

        if parent_name and parent_name.strip():
            return await self._get_or_create_placeholder_parent(
                current_user=current_user,
                role=expected_parent_role,
                full_name=parent_name,
                parent_parent_id=parent_parent_id,
            )

        raise BadRequestException(f"Parent account is required for role '{role}'.")

    async def _get_or_create_placeholder_parent(
        self,
        *,
        current_user: User,
        role: str,
        full_name: str,
        parent_parent_id: int | None,
    ) -> int:
        normalized_name = self._normalize_display_name(full_name, role=role)
        resolved_parent_id: int | None = None
        if role == self.ROLE_HOSPITAL:
            if parent_parent_id is None:
                raise BadRequestException("Health department parent is required for a new hospital option.")
            parent = await self.get_user_by_id(parent_parent_id)
            if parent is None or parent.role != self.ROLE_HEALTH_DEPARTMENT:
                raise BadRequestException("Hospital parent must be a health department account.")
            resolved_parent_id = int(parent.user_id)

        existing = (
            await self.db.execute(
                select(User).where(
                    User.role == role,
                    func.lower(func.coalesce(User.full_name, "")) == normalized_name.lower(),
                    User.parent_id.is_(None) if resolved_parent_id is None else User.parent_id == resolved_parent_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return int(existing.user_id)

        placeholder = User(
            email=await self._build_placeholder_email(role=role, full_name=normalized_name),
            full_name=normalized_name,
            password_hash=get_password_hash(secrets.token_urlsafe(32)),
            role=role,
            parent_id=resolved_parent_id,
            created_by_user_id=int(current_user.user_id),
            is_active=False,
        )
        self.db.add(placeholder)
        await self.db.flush()
        return int(placeholder.user_id)

    async def _build_placeholder_email(self, *, role: str, full_name: str) -> str:
        slug = self._slugify(full_name)
        base = f"unit-{role}-{slug}"[:200].strip("-") or f"unit-{role}"
        for index in range(0, 1000):
            suffix = "" if index == 0 else f"-{index}"
            email = f"{base}{suffix}@local.invalid"
            if await self.get_user_by_email(email) is None:
                return email
        raise ConflictException("Cannot generate a placeholder account email.")

    def _normalize_display_name(self, full_name: str | None, *, role: str) -> str | None:
        value = full_name.strip() if full_name else ""
        if value:
            return value
        if role == self.ROLE_ADMIN:
            return None
        raise BadRequestException("Display name is required for unit and doctor accounts.")

    def _collect_descendant_ids(self, users: list[User], root_user_id: int) -> set[int]:
        children_by_parent: dict[int, list[int]] = {}
        for user in users:
            if user.parent_id is None:
                continue
            children_by_parent.setdefault(int(user.parent_id), []).append(int(user.user_id))

        descendants: set[int] = set()
        stack = list(children_by_parent.get(root_user_id, []))
        while stack:
            user_id = stack.pop()
            if user_id in descendants:
                continue
            descendants.add(user_id)
            stack.extend(children_by_parent.get(user_id, []))
        return descendants

    def _slugify(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.strip().lower())
        ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
        return slug or "account"
