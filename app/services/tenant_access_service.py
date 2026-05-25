from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
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
        if current_user.role != "admin":
            if for_update:
                if current_user.role == "doctor":
                    stmt = stmt.where(Guideline.owner_user_id == -1)
                else:
                    stmt = stmt.where(Guideline.owner_user_id == current_user.user_id)
            else:
                stmt = stmt.where(
                    Guideline.owner_user_id.in_(
                        await self.get_visible_owner_user_ids(current_user)
                    )
                )
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
        if current_user.role != "admin":
            if for_update:
                if current_user.role == "doctor":
                    stmt = stmt.where(Guideline.owner_user_id == -1)
                else:
                    stmt = stmt.where(Guideline.owner_user_id == current_user.user_id)
            else:
                stmt = stmt.where(
                    Guideline.owner_user_id.in_(
                        await self.get_visible_owner_user_ids(current_user)
                    )
                )
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
        if current_user.role != "admin":
            visible_ids = await self.get_visible_owner_user_ids(current_user)
            stmt = stmt.where(Guideline.owner_user_id.in_(visible_ids))
        document = (await self.db.execute(stmt)).scalar_one_or_none()
        if document is None:
            raise NotFoundException("Document", document_id)
        return document

    async def get_visible_owner_user_ids(self, current_user: User) -> list[int]:
        owner_ids = [int(current_user.user_id)]
        parent_id = current_user.parent_id
        visited = set(owner_ids)
        while parent_id is not None and int(parent_id) not in visited:
            parent = (
                await self.db.execute(
                    select(User.user_id, User.parent_id).where(User.user_id == parent_id)
                )
            ).first()
            if parent is None:
                break
            user_id, next_parent_id = parent
            owner_ids.append(int(user_id))
            visited.add(int(user_id))
            parent_id = next_parent_id

        if current_user.role != "admin":
            admin_owner_ids = list(
                (
                    await self.db.execute(
                        select(User.user_id).where(User.role == "admin")
                    )
                )
                .scalars()
                .all()
            )
            for admin_owner_id in admin_owner_ids:
                normalized_admin_owner_id = int(admin_owner_id)
                if normalized_admin_owner_id not in visited:
                    owner_ids.append(normalized_admin_owner_id)
                    visited.add(normalized_admin_owner_id)
        return owner_ids

    def can_manage_owner(self, *, current_user: User, owner_user_id: int) -> bool:
        return current_user.role == "admin" or (
            current_user.role != "doctor"
            and int(current_user.user_id) == int(owner_user_id)
        )

    def access_scope(
        self,
        *,
        current_user: User,
        owner_user_id: int,
        owner_role: str | None = None,
    ) -> str:
        if current_user.role == "admin":
            return "admin"
        if owner_role == "admin":
            return "global"
        if int(current_user.user_id) == int(owner_user_id):
            return "owned"
        return "inherited"
