from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundException
from app.core.text_normalization import (
    VIETNAMESE_TRANSLATION_SOURCE,
    VIETNAMESE_TRANSLATION_TARGET,
    normalize_search_text,
)
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion
from app.models.user import User
from app.services.tenant_access_service import TenantAccessService


class GuidelineQueryService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_guidelines(
        self,
        current_user: User,
        page: int,
        page_size: int,
        search: str | None = None,
        title: str | None = None,
        ten_benh: str | None = None,
        publisher: str | None = None,
        chuyen_khoa: str | None = None,
        owner_user_id: int | None = None,
    ) -> tuple[list[Guideline], dict[int, dict[str, object]], int]:
        filters = await self._build_guideline_filters(
            current_user=current_user,
            search=search,
            title=title,
            ten_benh=ten_benh,
            publisher=publisher,
            chuyen_khoa=chuyen_khoa,
            owner_user_id=owner_user_id,
        )
        offset = (page - 1) * page_size

        total_stmt = select(func.count()).select_from(Guideline).where(*filters)
        total = int((await self.db.execute(total_stmt)).scalar_one())

        guidelines_stmt = (
            select(Guideline)
            .options(selectinload(Guideline.owner))
            .where(*filters)
            .order_by(Guideline.guideline_id.desc())
            .offset(offset)
            .limit(page_size)
        )
        guidelines = list((await self.db.execute(guidelines_stmt)).scalars().all())

        guideline_ids = [guideline.guideline_id for guideline in guidelines]
        active_versions = await self._get_active_versions(guideline_ids)
        return guidelines, active_versions, total

    async def list_guideline_versions(
        self,
        current_user: User,
        guideline_id: int,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[GuidelineVersion], int]:
        guideline_exists = (
            await self.db.execute(
                select(Guideline.guideline_id).where(
                    Guideline.guideline_id == guideline_id,
                    *(await self._build_tenant_filters(current_user=current_user)),
                )
            )
        ).scalar_one_or_none()
        if guideline_exists is None:
            raise NotFoundException("Guideline", guideline_id)

        filters = [GuidelineVersion.guideline_id == guideline_id]
        if status and status.strip():
            normalized_status = status.strip().lower()
            filters.append(
                func.lower(func.coalesce(GuidelineVersion.status, ""))
                == normalized_status
            )

        offset = (page - 1) * page_size

        total_stmt = select(func.count()).select_from(GuidelineVersion).where(*filters)
        total = int((await self.db.execute(total_stmt)).scalar_one())

        versions_stmt = (
            select(GuidelineVersion)
            .where(*filters)
            .order_by(
                GuidelineVersion.release_date.desc().nullslast(),
                GuidelineVersion.version_id.desc(),
            )
            .offset(offset)
            .limit(page_size)
        )
        versions = list((await self.db.execute(versions_stmt)).scalars().all())
        return versions, total

    async def _build_guideline_filters(
        self,
        current_user: User,
        search: str | None,
        title: str | None,
        ten_benh: str | None,
        publisher: str | None,
        chuyen_khoa: str | None,
        owner_user_id: int | None,
    ) -> list[object]:
        filters: list[object] = await self._build_tenant_filters(
            current_user=current_user,
            owner_user_id=owner_user_id,
        )

        normalized_search = normalize_search_text(search)
        if normalized_search:
            keyword = f"%{normalized_search}%"
            filters.append(
                or_(
                    self._normalized_text_expr(Guideline.title).like(keyword),
                    self._normalized_text_expr(Guideline.ten_benh).like(keyword),
                    self._normalized_text_expr(Guideline.publisher).like(keyword),
                    self._normalized_text_expr(Guideline.chuyen_khoa).like(keyword),
                )
            )
        self._append_normalized_contains_filter(filters=filters, column=Guideline.title, value=title)
        self._append_normalized_contains_filter(filters=filters, column=Guideline.ten_benh, value=ten_benh)
        self._append_normalized_contains_filter(filters=filters, column=Guideline.publisher, value=publisher)
        self._append_normalized_contains_filter(filters=filters, column=Guideline.chuyen_khoa, value=chuyen_khoa)

        return filters

    async def _build_tenant_filters(
        self,
        *,
        current_user: User,
        owner_user_id: int | None = None,
    ) -> list[object]:
        access_service = TenantAccessService(self.db)
        if current_user.role == "admin":
            return [Guideline.owner_user_id == owner_user_id] if owner_user_id else []

        filters: list[object] = []
        visible_owner_ids = await access_service.get_visible_owner_user_ids(current_user)
        if owner_user_id is not None:
            if int(owner_user_id) not in visible_owner_ids:
                filters.append(Guideline.owner_user_id == -1)
            else:
                filters.append(Guideline.owner_user_id == owner_user_id)
        else:
            filters.append(Guideline.owner_user_id.in_(visible_owner_ids))
        if await access_service.is_health_station_scope(current_user):
            filters.append(access_service.health_station_specialty_filter())
        return filters

    def _append_normalized_contains_filter(
        self,
        *,
        filters: list[object],
        column,
        value: str | None,
    ) -> None:
        normalized_value = normalize_search_text(value)
        if not normalized_value:
            return
        filters.append(self._normalized_text_expr(column).like(f"%{normalized_value}%"))

    def _normalized_text_expr(self, column):
        lowered = func.lower(func.coalesce(column, ""))
        translated = func.translate(
            lowered,
            VIETNAMESE_TRANSLATION_SOURCE,
            VIETNAMESE_TRANSLATION_TARGET,
        )
        return func.regexp_replace(translated, r"[^a-z0-9]+", "", "g")

    async def get_filter_options(
        self,
        current_user: User,
        owner_user_id: int | None = None,
    ) -> dict[str, list[str]]:
        tenant_filters = await self._build_tenant_filters(
            current_user=current_user,
            owner_user_id=owner_user_id,
        )
        publishers_stmt = (
            select(Guideline.publisher)
            .where(*tenant_filters)
            .where(Guideline.publisher.isnot(None))
            .where(Guideline.publisher != "")
            .distinct()
            .order_by(Guideline.publisher)
        )
        publishers = [row for row in (await self.db.execute(publishers_stmt)).scalars().all()]

        ten_benh_stmt = (
            select(Guideline.ten_benh)
            .where(*tenant_filters)
            .where(Guideline.ten_benh.isnot(None))
            .where(Guideline.ten_benh != "")
            .distinct()
            .order_by(Guideline.ten_benh)
        )
        ten_benhs = [row for row in (await self.db.execute(ten_benh_stmt)).scalars().all()]

        return {
            "publishers": publishers,
            "ten_benhs": ten_benhs,
        }

    async def _get_active_versions(
        self,
        guideline_ids: list[int],
    ) -> dict[int, dict[str, object]]:
        if not guideline_ids:
            return {}

        ranked_active_versions = (
            select(
                GuidelineVersion.guideline_id.label("guideline_id"),
                GuidelineVersion.version_id.label("version_id"),
                GuidelineVersion.version_label.label("version_label"),
                GuidelineVersion.status.label("status"),
                GuidelineVersion.release_date.label("release_date"),
                GuidelineVersion.effective_from.label("effective_from"),
                GuidelineVersion.effective_to.label("effective_to"),
                func.row_number()
                .over(
                    partition_by=GuidelineVersion.guideline_id,
                    order_by=(
                        GuidelineVersion.release_date.desc().nullslast(),
                        GuidelineVersion.version_id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(GuidelineVersion.guideline_id.in_(guideline_ids))
            .where(
                func.lower(func.coalesce(GuidelineVersion.status, "")).in_(
                    self.ACTIVE_STATUSES
                )
            )
            .subquery()
        )

        rows = (
            await self.db.execute(
                select(ranked_active_versions).where(ranked_active_versions.c.rn == 1)
            )
        ).mappings().all()
        return {
            int(row["guideline_id"]): {
                "version_id": row["version_id"],
                "version_label": row["version_label"],
                "status": row["status"],
                "release_date": row["release_date"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
            }
            for row in rows
        }
