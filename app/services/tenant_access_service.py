from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.roles import ROLE_ADMIN, ROLE_DOCTOR, ROLE_HEALTH_STATION
from app.core.specialties import HEALTH_STATION_SPECIALTY_KEY
from app.core.text_normalization import (
    VIETNAMESE_TRANSLATION_SOURCE,
    VIETNAMESE_TRANSLATION_TARGET,
)
from app.models.document import Document
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion
from app.models.user import User


class TenantAccessService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ensure_guideline_access(
        self,
        *,
        guideline_id: int,
        current_user: User,
        for_update: bool = False,
    ) -> Guideline:
        stmt = select(Guideline).where(Guideline.guideline_id == guideline_id)
        if current_user.role != ROLE_ADMIN:
            if for_update:
                if current_user.role == ROLE_DOCTOR:
                    stmt = stmt.where(Guideline.owner_user_id == -1)
                else:
                    stmt = stmt.where(Guideline.owner_user_id == current_user.user_id)
            else:
                stmt = stmt.where(
                    Guideline.owner_user_id.in_(
                        await self.get_visible_owner_user_ids(current_user)
                    )
                )
            if await self.is_health_station_scope(current_user):
                stmt = stmt.where(self.health_station_specialty_filter())
        if for_update:
            stmt = stmt.with_for_update()
        guideline = (await self.db.execute(stmt)).scalar_one_or_none()
        if guideline is None:
            raise NotFoundException("Guideline", guideline_id)
        return guideline

    async def ensure_version_access(
        self,
        *,
        version_id: int,
        current_user: User,
        for_update: bool = False,
    ) -> tuple[GuidelineVersion, Guideline]:
        stmt = (
            select(GuidelineVersion, Guideline)
            .join(Guideline, Guideline.guideline_id == GuidelineVersion.guideline_id)
            .where(GuidelineVersion.version_id == version_id)
        )
        if current_user.role != ROLE_ADMIN:
            if for_update:
                if current_user.role == ROLE_DOCTOR:
                    stmt = stmt.where(Guideline.owner_user_id == -1)
                else:
                    stmt = stmt.where(Guideline.owner_user_id == current_user.user_id)
            else:
                stmt = stmt.where(
                    Guideline.owner_user_id.in_(
                        await self.get_visible_owner_user_ids(current_user)
                    )
                )
            if await self.is_health_station_scope(current_user):
                stmt = stmt.where(self.health_station_specialty_filter())
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.db.execute(stmt)).first()
        if row is None:
            raise NotFoundException("GuidelineVersion", version_id)
        version, guideline = row
        return version, guideline

    async def ensure_document_access(
        self,
        *,
        document_id: int,
        current_user: User,
    ) -> Document:
        stmt = (
            select(Document)
            .join(GuidelineVersion, GuidelineVersion.version_id == Document.version_id)
            .join(Guideline, Guideline.guideline_id == GuidelineVersion.guideline_id)
            .where(Document.document_id == document_id)
        )
        if current_user.role != ROLE_ADMIN:
            visible_ids = await self.get_visible_owner_user_ids(current_user)
            stmt = stmt.where(Guideline.owner_user_id.in_(visible_ids))
            if await self.is_health_station_scope(current_user):
                stmt = stmt.where(self.health_station_specialty_filter())
        document = (await self.db.execute(stmt)).scalar_one_or_none()
        if document is None:
            raise NotFoundException("Document", document_id)
        return document

    async def get_visible_owner_user_ids(self, current_user: User) -> list[int]:
        owner_ids = [int(current_user.user_id)]
        parent_id = current_user.parent_id
        visited = set(owner_ids)
        global_documents_blocked = not bool(current_user.inherits_global_documents)
        reached_admin_parent = False
        while parent_id is not None and int(parent_id) not in visited:
            parent = await self._get_user_scope_row(int(parent_id))
            if parent is None:
                break
            user_id, next_parent_id, role, inherits_global_documents = parent
            normalized_user_id = int(user_id)
            if role == ROLE_ADMIN:
                reached_admin_parent = True
                if not global_documents_blocked:
                    owner_ids.append(normalized_user_id)
                    visited.add(normalized_user_id)
                break

            owner_ids.append(normalized_user_id)
            visited.add(int(user_id))
            if not bool(inherits_global_documents):
                global_documents_blocked = True
            parent_id = next_parent_id

        if current_user.role != ROLE_ADMIN and reached_admin_parent and not global_documents_blocked:
            for admin_owner_id in await self._get_admin_owner_ids():
                normalized_admin_owner_id = int(admin_owner_id)
                if normalized_admin_owner_id not in visited:
                    owner_ids.append(normalized_admin_owner_id)
                    visited.add(normalized_admin_owner_id)
        return owner_ids

    async def is_health_station_scope(self, current_user: User) -> bool:
        if current_user.role == ROLE_HEALTH_STATION:
            return True
        parent_id = current_user.parent_id
        visited = {int(current_user.user_id)}
        while parent_id is not None and int(parent_id) not in visited:
            parent = await self._get_user_scope_row(int(parent_id))
            if parent is None:
                return False
            user_id, next_parent_id, role, _inherits_global_documents = parent
            if role == ROLE_HEALTH_STATION:
                return True
            if role == ROLE_ADMIN:
                return False
            visited.add(int(user_id))
            parent_id = next_parent_id
        return False

    def health_station_specialty_filter(self):
        return self._normalized_guideline_specialty_expr() == HEALTH_STATION_SPECIALTY_KEY

    def _normalized_guideline_specialty_expr(self):
        lowered = func.lower(func.coalesce(Guideline.chuyen_khoa, ""))
        translated = func.translate(
            lowered,
            VIETNAMESE_TRANSLATION_SOURCE,
            VIETNAMESE_TRANSLATION_TARGET,
        )
        return func.regexp_replace(translated, r"[^a-z0-9]+", "", "g")

    async def _get_user_scope_row(self, user_id: int) -> tuple[int, int | None, str, bool] | None:
        return (
            await self.db.execute(
                select(
                    User.user_id,
                    User.parent_id,
                    User.role,
                    User.inherits_global_documents,
                ).where(User.user_id == user_id)
            )
        ).first()

    async def _get_admin_owner_ids(self) -> list[int]:
        return list(
            (
                await self.db.execute(
                    select(User.user_id).where(User.role == ROLE_ADMIN)
                )
            )
            .scalars()
            .all()
        )

    def can_manage_owner(self, *, current_user: User, owner_user_id: int) -> bool:
        return current_user.role == ROLE_ADMIN or (
            current_user.role != ROLE_DOCTOR
            and int(current_user.user_id) == int(owner_user_id)
        )

    def access_scope(
        self,
        *,
        current_user: User,
        owner_user_id: int,
        owner_role: str | None = None,
    ) -> str:
        if current_user.role == ROLE_ADMIN:
            return "admin"
        if owner_role == ROLE_ADMIN:
            return "global"
        if int(current_user.user_id) == int(owner_user_id):
            return "owned"
        return "inherited"
