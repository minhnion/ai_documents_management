from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    UnprocessableEntityException,
)
from app.core.roles import (
    ALL_ROLES,
    CHILD_ROLES_BY_CREATOR,
    PARENT_ROLES_BY_ROLE,
    ROLE_ADMIN,
    ROLE_CENTRAL_HOSPITAL,
    ROLE_DOCTOR,
    ROLE_HEALTH_DEPARTMENT,
    ROLE_HEALTH_STATION,
    ROLE_HOSPITAL,
    TOP_LEVEL_UNIT_ROLES,
)
from app.core.security import get_password_hash, verify_password
from app.models.guideline import Guideline
from app.models.user import User
from app.services.guideline_delete_service import GuidelineDeleteService


class AuthService:
    ROLE_ADMIN = ROLE_ADMIN
    ROLE_HEALTH_DEPARTMENT = ROLE_HEALTH_DEPARTMENT
    ROLE_CENTRAL_HOSPITAL = ROLE_CENTRAL_HOSPITAL
    ROLE_HOSPITAL = ROLE_HOSPITAL
    ROLE_HEALTH_STATION = ROLE_HEALTH_STATION
    ROLE_DOCTOR = ROLE_DOCTOR

    ROLE_LABELS: dict[str, str] = {
        ROLE_ADMIN: "Admin",
        ROLE_HEALTH_DEPARTMENT: "Sở y tế",
        ROLE_CENTRAL_HOSPITAL: "Bệnh viện Trung ương",
        ROLE_HOSPITAL: "Bệnh viện",
        ROLE_HEALTH_STATION: "Trạm y tế",
        ROLE_DOCTOR: "Bác sĩ",
    }
    ROLE_DESCRIPTIONS: dict[str, str] = {
        ROLE_ADMIN: "Full access to all accounts and documents.",
        ROLE_HEALTH_DEPARTMENT: "Cap so y te: manage own documents and create hospital/health station accounts.",
        ROLE_CENTRAL_HOSPITAL: "Cap benh vien trung uong: manage own documents and create doctor accounts.",
        ROLE_HOSPITAL: "Cap benh vien: inherit parent documents, manage own documents, and create doctor accounts.",
        ROLE_HEALTH_STATION: "Cap tram y te: manage station-specialty documents and create doctor accounts.",
        ROLE_DOCTOR: "Cap bac si: inherit parent documents with read-only access.",
    }
    ROLE_ORDER: tuple[str, ...] = ALL_ROLES
    CHILD_ROLES_BY_CREATOR = CHILD_ROLES_BY_CREATOR
    PARENT_ROLES_BY_ROLE = PARENT_ROLES_BY_ROLE

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @classmethod
    def get_available_roles(cls, current_user: User | None = None) -> list[dict[str, str]]:
        if current_user is None or current_user.role == cls.ROLE_ADMIN:
            role_names = cls.ROLE_ORDER
        else:
            role_names = cls.CHILD_ROLES_BY_CREATOR.get(current_user.role, ())
        return [
            {
                "name": role_name,
                "label": cls.ROLE_LABELS[role_name],
                "description": cls.ROLE_DESCRIPTIONS[role_name],
            }
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
                f"Unknown role. Allowed values: {', '.join(cls.ROLE_ORDER)}."
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

    async def change_password(
        self,
        *,
        current_user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        if not verify_password(current_password, current_user.password_hash):
            raise BadRequestException("Current password is incorrect.")
        if verify_password(new_password, current_user.password_hash):
            raise BadRequestException("New password must be different from current password.")

        current_user.password_hash = get_password_hash(new_password)
        await self.db.flush()

    async def reset_user_password(
        self,
        *,
        current_user: User,
        user_id: int,
        new_password: str,
    ) -> User:
        target_user = await self.get_user_by_id(user_id)
        if target_user is None:
            raise NotFoundException("User", user_id)
        if int(target_user.user_id) == int(current_user.user_id):
            raise BadRequestException("Use change password to update your own password.")

        self._ensure_can_manage_user(current_user=current_user, target_user=target_user)
        target_user.password_hash = get_password_hash(new_password)
        await self.db.flush()

        updated_user = await self.get_user_by_id(user_id)
        if updated_user is None:
            raise UnprocessableEntityException("Cannot load updated user.")
        return updated_user

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
                inherits_global_documents=True,
                is_active=True,
            )
            self.db.add(user)
            await self.db.flush()
            await self.assign_orphan_health_departments_to_admin(int(user.user_id))
            return await self.get_user_by_id(user.user_id)

        if (
            user.role != self.ROLE_ADMIN
            or user.parent_id is not None
            or not user.inherits_global_documents
        ):
            user.role = self.ROLE_ADMIN
            user.parent_id = None
            user.inherits_global_documents = True
            await self.db.flush()
        await self.assign_orphan_health_departments_to_admin(int(user.user_id))
        return user

    async def assign_orphan_health_departments_to_admin(self, admin_user_id: int) -> int:
        result = await self.db.execute(
            update(User)
            .where(User.role == self.ROLE_HEALTH_DEPARTMENT)
            .where(User.parent_id.is_(None))
            .where(User.inherits_global_documents.is_(True))
            .where(User.user_id != admin_user_id)
            .values(parent_id=admin_user_id)
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    async def create_user(
        self,
        *,
        current_user: User,
        email: str,
        password: str,
        role: str,
        full_name: str | None = None,
        parent_id: int | None = None,
        is_active: bool = True,
        inherits_global_documents: bool = True,
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
            inherits_global_documents=bool(inherits_global_documents),
        )
        resolved_inherits_global_documents = await self._resolve_inherits_global_documents(
            current_user=current_user,
            role=normalized_role,
            parent_id=resolved_parent_id,
            requested_value=bool(inherits_global_documents),
        )
        normalized_full_name = self._normalize_display_name(full_name, role=normalized_role)

        user = User(
            email=normalized_email,
            password_hash=get_password_hash(password),
            full_name=normalized_full_name,
            role=normalized_role,
            parent_id=resolved_parent_id,
            inherits_global_documents=resolved_inherits_global_documents,
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
        is_active: bool | None = None,
        inherits_global_documents: bool | None = None,
    ) -> User:
        user = await self.get_user_by_id(user_id)
        if user is None:
            raise NotFoundException("User", user_id)
        if int(user.user_id) == int(current_user.user_id):
            raise BadRequestException("Cannot change your own role from this screen.")

        normalized_role = self.normalize_role(role)
        self._ensure_can_manage_user(current_user=current_user, target_user=user)
        self._ensure_can_create_role(current_user=current_user, role=normalized_role)

        requested_inherits_global_documents = (
            bool(user.inherits_global_documents)
            if inherits_global_documents is None
            else bool(inherits_global_documents)
        )
        user.role = normalized_role
        user.parent_id = await self._resolve_parent_id_for_role(
            current_user=current_user,
            role=normalized_role,
            parent_id=parent_id,
            inherits_global_documents=requested_inherits_global_documents,
        )
        user.inherits_global_documents = await self._resolve_inherits_global_documents(
            current_user=current_user,
            role=normalized_role,
            parent_id=user.parent_id,
            requested_value=requested_inherits_global_documents,
        )
        if is_active is not None:
            user.is_active = bool(is_active)
        await self.db.flush()
        await self._set_global_documents_for_descendants(
            root_user_id=int(user.user_id),
            inherits_global_documents=bool(user.inherits_global_documents),
        )
        updated_user = await self.get_user_by_id(user_id)
        if updated_user is None:
            raise UnprocessableEntityException("Cannot load updated user.")
        return updated_user

    async def delete_user(
        self,
        *,
        current_user: User,
        user_id: int,
    ) -> dict[str, int | list[int]]:
        target_user = await self.get_user_by_id(user_id)
        if target_user is None:
            raise NotFoundException("User", user_id)
        if int(target_user.user_id) == int(current_user.user_id):
            raise BadRequestException("Cannot delete your own account.")
        if target_user.role == self.ROLE_ADMIN:
            raise BadRequestException("Admin accounts cannot be deleted from this screen.")

        self._ensure_can_manage_user(current_user=current_user, target_user=target_user)

        users = list((await self.db.execute(select(User))).scalars().all())
        target_ids = self._collect_descendant_ids(users, int(target_user.user_id))
        target_ids.add(int(target_user.user_id))

        guideline_ids = list(
            (
                await self.db.execute(
                    select(Guideline.guideline_id)
                    .where(Guideline.owner_user_id.in_(target_ids))
                    .order_by(Guideline.guideline_id.asc())
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        delete_service = GuidelineDeleteService(self.db)
        for guideline_id in guideline_ids:
            await delete_service.delete_guideline(int(guideline_id))

        users_by_id = {int(user.user_id): user for user in users}
        for deleted_user_id in self._sort_user_ids_for_bottom_up_delete(users, target_ids):
            user = users_by_id.get(int(deleted_user_id))
            if user is not None:
                await self.db.delete(user)
        await self.db.flush()

        deleted_user_ids = sorted(int(deleted_user_id) for deleted_user_id in target_ids)
        return {
            "deleted_user_id": int(target_user.user_id),
            "deleted_user_ids": deleted_user_ids,
            "deleted_user_count": len(deleted_user_ids),
            "deleted_guideline_count": len(guideline_ids),
        }

    def _ensure_can_create_role(self, *, current_user: User, role: str) -> None:
        if current_user.role == self.ROLE_ADMIN:
            return
        allowed_roles = self.CHILD_ROLES_BY_CREATOR.get(current_user.role, ())
        if role not in allowed_roles:
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
        inherits_global_documents: bool,
    ) -> int | None:
        if role == self.ROLE_ADMIN:
            return None
        if role in TOP_LEVEL_UNIT_ROLES:
            if current_user.role != self.ROLE_ADMIN:
                raise BadRequestException("Top-level unit accounts must be created by an admin.")
            return int(current_user.user_id) if inherits_global_documents else None

        expected_parent_roles = self.PARENT_ROLES_BY_ROLE[role]
        if current_user.role != self.ROLE_ADMIN:
            if parent_id is not None and int(parent_id) != int(current_user.user_id):
                raise BadRequestException("Child account must be created under the current account.")
            return int(current_user.user_id)

        if parent_id is not None:
            parent = await self.get_user_by_id(parent_id)
            if parent is None or not parent.is_active:
                raise NotFoundException("User", parent_id)
            if parent.role not in expected_parent_roles:
                raise BadRequestException(
                    f"Parent for role '{role}' must have one of: {', '.join(expected_parent_roles)}."
                )
            return int(parent.user_id)

        raise BadRequestException(f"Parent account is required for role '{role}'.")

    async def _resolve_inherits_global_documents(
        self,
        *,
        current_user: User,
        role: str,
        parent_id: int | None,
        requested_value: bool,
    ) -> bool:
        if role == self.ROLE_ADMIN:
            return True
        if role in TOP_LEVEL_UNIT_ROLES:
            return bool(requested_value)

        parent = (
            current_user
            if parent_id is not None and int(parent_id) == int(current_user.user_id)
            else await self.get_user_by_id(parent_id or 0)
        )
        if parent is None:
            raise NotFoundException("User", parent_id or 0)
        return bool(parent.inherits_global_documents)

    async def _set_global_documents_for_descendants(
        self,
        root_user_id: int,
        inherits_global_documents: bool,
    ) -> None:
        users = list((await self.db.execute(select(User))).scalars().all())
        descendant_ids = self._collect_descendant_ids(users, root_user_id)
        if not descendant_ids:
            return
        await self.db.execute(
            update(User)
            .where(User.user_id.in_(descendant_ids))
            .values(inherits_global_documents=inherits_global_documents)
        )
        await self.db.flush()

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

    def _sort_user_ids_for_bottom_up_delete(
        self,
        users: list[User],
        user_ids: set[int],
    ) -> list[int]:
        children_by_parent: dict[int, list[int]] = {}
        for user in users:
            if user.parent_id is None:
                continue
            children_by_parent.setdefault(int(user.parent_id), []).append(int(user.user_id))

        depths: dict[int, int] = {}

        def depth_for(user_id: int) -> int:
            if user_id in depths:
                return depths[user_id]
            children = [child_id for child_id in children_by_parent.get(user_id, []) if child_id in user_ids]
            depth = 0 if not children else 1 + max(depth_for(child_id) for child_id in children)
            depths[user_id] = depth
            return depth

        return sorted(user_ids, key=lambda item: (depth_for(item), item))
