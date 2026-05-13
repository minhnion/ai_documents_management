from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
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
            stmt = stmt.where(Guideline.organization_id == self._user_organization_id(current_user))
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
            stmt = stmt.where(Guideline.organization_id == self._user_organization_id(current_user))
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
            organization_id = self._user_organization_id(current_user)
            stmt = stmt.where(
                or_(
                    Document.organization_id == organization_id,
                    Guideline.organization_id == organization_id,
                )
            )
        document = (await self.db.execute(stmt)).scalar_one_or_none()
        if document is None:
            raise NotFoundException("Document", document_id)
        return document

    def _user_organization_id(self, current_user: User) -> int:
        if current_user.organization_id is None:
            raise BadRequestException("User account has no organization assigned.")
        return int(current_user.organization_id)
