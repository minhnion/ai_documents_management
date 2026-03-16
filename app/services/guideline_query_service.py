from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.text_normalization import (
    VIETNAMESE_TRANSLATION_SOURCE,
    VIETNAMESE_TRANSLATION_TARGET,
    normalize_search_text,
)
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion


class GuidelineQueryService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_guidelines(
        self,
        page: int,
        page_size: int,
        search: str | None = None,
        title: str | None = None,
        ten_benh: str | None = None,
        publisher: str | None = None,
        chuyen_khoa: str | None = None,
    ) -> tuple[list[Guideline], dict[int, dict[str, object]], int]:
        filters = self._build_guideline_filters(
            search=search,
            title=title,
            ten_benh=ten_benh,
            publisher=publisher,
            chuyen_khoa=chuyen_khoa,
        )
        offset = (page - 1) * page_size

        total_stmt = select(func.count()).select_from(Guideline).where(*filters)
        total = int((await self.db.execute(total_stmt)).scalar_one())

        guidelines_stmt = (
            select(Guideline)
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
        guideline_id: int,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> tuple[list[GuidelineVersion], int]:
        guideline_exists = (
            await self.db.execute(
                select(Guideline.guideline_id).where(
                    Guideline.guideline_id == guideline_id
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

        total_stmt = (
            select(func.count())
            .select_from(GuidelineVersion)
            .where(*filters)
        )
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

    def _build_guideline_filters(
        self,
        search: str | None,
        title: str | None,
        ten_benh: str | None,
        publisher: str | None,
        chuyen_khoa: str | None,
    ) -> list[object]:
        filters: list[object] = []

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
        self._append_normalized_contains_filter(
            filters=filters,
            column=Guideline.title,
            value=title,
        )
        self._append_normalized_contains_filter(
            filters=filters,
            column=Guideline.ten_benh,
            value=ten_benh,
        )
        self._append_normalized_contains_filter(
            filters=filters,
            column=Guideline.publisher,
            value=publisher,
        )
        self._append_normalized_contains_filter(
            filters=filters,
            column=Guideline.chuyen_khoa,
            value=chuyen_khoa,
        )

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
        filters.append(
            self._normalized_text_expr(column).like(f"%{normalized_value}%")
        )

    def _normalized_text_expr(self, column):
        lowered = func.lower(func.coalesce(column, ""))
        translated = func.translate(
            lowered,
            VIETNAMESE_TRANSLATION_SOURCE,
            VIETNAMESE_TRANSLATION_TARGET,
        )
        return func.regexp_replace(translated, r"[^a-z0-9]+", "", "g")

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

        stmt = select(ranked_active_versions).where(ranked_active_versions.c.rn == 1)
        rows = (await self.db.execute(stmt)).mappings().all()
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
