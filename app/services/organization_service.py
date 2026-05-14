from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.organization import Organization


class OrganizationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def normalize_slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.strip().lower())
        ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
        if not slug:
            raise BadRequestException("Organization name is required.")
        return slug[:120]

    async def list_organizations(self, *, active_only: bool = True) -> list[Organization]:
        filters = []
        if active_only:
            filters.append(Organization.is_active.is_(True))
        stmt = (
            select(Organization)
            .where(*filters)
            .order_by(Organization.name.asc(), Organization.organization_id.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_organization(self, organization_id: int) -> Organization:
        organization = (
            await self.db.execute(
                select(Organization).where(
                    Organization.organization_id == organization_id,
                    Organization.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if organization is None:
            raise NotFoundException("Organization", organization_id)
        return organization

    async def get_by_slug(self, slug: str) -> Organization | None:
        normalized_slug = self.normalize_slug(slug)
        return (
            await self.db.execute(
                select(Organization).where(
                    func.lower(Organization.slug) == normalized_slug,
                    Organization.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def create_organization(self, name: str) -> Organization:
        normalized_name = name.strip()
        slug = self.normalize_slug(normalized_name)
        existing = await self.get_by_slug(slug)
        if existing is not None:
            raise ConflictException(f"Organization '{normalized_name}' already exists.")

        organization = Organization(slug=slug, name=normalized_name, is_active=True)
        self.db.add(organization)
        await self.db.flush()
        return organization

    async def get_or_create_organization(self, name: str) -> Organization:
        normalized_name = name.strip()
        slug = self.normalize_slug(normalized_name)
        existing = await self.get_by_slug(slug)
        if existing is not None:
            return existing

        organization = Organization(slug=slug, name=normalized_name, is_active=True)
        self.db.add(organization)
        await self.db.flush()
        return organization

    async def resolve_from_payload(
        self,
        *,
        organization_id: int | None,
        organization_name: str | None,
    ) -> Organization:
        if organization_id is not None:
            return await self.get_organization(organization_id)
        if organization_name and organization_name.strip():
            return await self.get_or_create_organization(organization_name)
        raise BadRequestException("Organization is required for user role.")
