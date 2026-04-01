from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion


class GuidelineMetadataService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")
    INACTIVE_STATUS: str = "inactive"
    DEFAULT_ACTIVE_STATUS: str = "active"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def update_guideline_metadata(
        self,
        guideline_id: int,
        patch: dict[str, Any],
    ) -> Guideline:
        if not patch:
            raise BadRequestException("At least one guideline metadata field is required.")

        guideline = await self._get_guideline_or_raise(guideline_id, for_update=True)

        if "title" in patch:
            guideline.title = self._normalize_required_text(
                patch["title"],
                field_name="title",
            )
        if "ten_benh" in patch:
            guideline.ten_benh = self._normalize_optional_text(patch["ten_benh"])
        if "publisher" in patch:
            guideline.publisher = self._normalize_optional_text(patch["publisher"])
        if "chuyen_khoa" in patch:
            guideline.chuyen_khoa = self._normalize_optional_text(patch["chuyen_khoa"])

        await self.db.flush()
        return guideline

    async def update_version_metadata(
        self,
        version_id: int,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if not patch:
            raise BadRequestException("At least one version metadata field is required.")

        version = await self._get_version_or_raise(version_id, for_update=True)
        old_status = self._normalize_status(version.status)

        if "version_label" in patch:
            version.version_label = self._normalize_optional_text(patch["version_label"])
        if "release_date" in patch:
            version.release_date = self._coerce_date_or_none(
                patch["release_date"],
                field_name="release_date",
            )
        if "effective_from" in patch:
            version.effective_from = self._coerce_date_or_none(
                patch["effective_from"],
                field_name="effective_from",
            )
        if "effective_to" in patch:
            version.effective_to = self._coerce_date_or_none(
                patch["effective_to"],
                field_name="effective_to",
            )

        self._validate_version_dates(
            effective_from=version.effective_from,
            effective_to=version.effective_to,
        )

        previous_active_versions_updated = 0
        promoted_version_id: int | None = None
        if "status" in patch:
            new_status = self._normalize_status(patch["status"])
            version.status = new_status

            if self._is_active_status(new_status):
                previous_active_versions_updated = await self._deactivate_active_versions(
                    guideline_id=int(version.guideline_id),
                    exclude_version_id=int(version.version_id),
                )
            elif self._is_active_status(old_status):
                promoted_version_id, previous_active_versions_updated = (
                    await self._promote_latest_remaining_version(
                        guideline_id=int(version.guideline_id),
                        exclude_version_id=int(version.version_id),
                    )
                )

        await self.db.flush()
        return {
            "guideline_id": int(version.guideline_id),
            "version_id": int(version.version_id),
            "version_label": version.version_label,
            "release_date": version.release_date,
            "status": version.status,
            "effective_from": version.effective_from,
            "effective_to": version.effective_to,
            "promoted_version_id": promoted_version_id,
            "previous_active_versions_updated": int(previous_active_versions_updated),
        }

    async def _get_guideline_or_raise(
        self,
        guideline_id: int,
        *,
        for_update: bool = False,
    ) -> Guideline:
        stmt = select(Guideline).where(Guideline.guideline_id == guideline_id)
        if for_update:
            stmt = stmt.with_for_update()
        guideline = (await self.db.execute(stmt)).scalar_one_or_none()
        if guideline is None:
            raise NotFoundException("Guideline", guideline_id)
        return guideline

    async def _get_version_or_raise(
        self,
        version_id: int,
        *,
        for_update: bool = False,
    ) -> GuidelineVersion:
        stmt = select(GuidelineVersion).where(GuidelineVersion.version_id == version_id)
        if for_update:
            stmt = stmt.with_for_update()
        version = (await self.db.execute(stmt)).scalar_one_or_none()
        if version is None:
            raise NotFoundException("GuidelineVersion", version_id)
        return version

    async def _deactivate_active_versions(
        self,
        *,
        guideline_id: int,
        exclude_version_id: int,
    ) -> int:
        stmt = (
            update(GuidelineVersion)
            .where(GuidelineVersion.guideline_id == guideline_id)
            .where(GuidelineVersion.version_id != exclude_version_id)
            .where(
                func.lower(func.coalesce(GuidelineVersion.status, "")).in_(self.ACTIVE_STATUSES)
            )
            .values(status=self.INACTIVE_STATUS)
        )
        result = await self.db.execute(stmt)
        return int(result.rowcount or 0)

    async def _promote_latest_remaining_version(
        self,
        *,
        guideline_id: int,
        exclude_version_id: int,
    ) -> tuple[int | None, int]:
        promoted = (
            await self.db.execute(
                select(GuidelineVersion)
                .where(GuidelineVersion.guideline_id == guideline_id)
                .where(GuidelineVersion.version_id != exclude_version_id)
                .order_by(
                    GuidelineVersion.release_date.desc().nullslast(),
                    GuidelineVersion.version_id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()

        if promoted is None:
            result = await self.db.execute(
                update(GuidelineVersion)
                .where(GuidelineVersion.guideline_id == guideline_id)
                .where(GuidelineVersion.version_id != exclude_version_id)
                .where(
                    func.lower(func.coalesce(GuidelineVersion.status, "")).in_(self.ACTIVE_STATUSES)
                )
                .values(status=self.INACTIVE_STATUS)
            )
            return None, int(result.rowcount or 0)

        result = await self.db.execute(
            update(GuidelineVersion)
            .where(GuidelineVersion.guideline_id == guideline_id)
            .where(GuidelineVersion.version_id != exclude_version_id)
            .where(GuidelineVersion.version_id != promoted.version_id)
            .where(
                func.lower(func.coalesce(GuidelineVersion.status, "")).in_(self.ACTIVE_STATUSES)
            )
            .values(status=self.INACTIVE_STATUS)
        )
        promoted.status = self.DEFAULT_ACTIVE_STATUS
        return int(promoted.version_id), int(result.rowcount or 0)

    def _validate_version_dates(
        self,
        *,
        effective_from: date | None,
        effective_to: date | None,
    ) -> None:
        if effective_from and effective_to and effective_to < effective_from:
            raise BadRequestException("effective_to must be greater than or equal to effective_from.")

    def _normalize_required_text(self, value: object, *, field_name: str) -> str:
        if value is None:
            raise BadRequestException(f"{field_name} cannot be null.")
        text = str(value).strip()
        if not text:
            raise BadRequestException(f"{field_name} cannot be blank.")
        return text

    def _normalize_optional_text(self, value: object | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _coerce_date_or_none(self, value: object | None, *, field_name: str) -> date | None:
        if value is None:
            return None
        if isinstance(value, date):
            return value
        raise BadRequestException(f"{field_name} must be a valid date.")

    def _normalize_status(self, value: object | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        return text or None

    def _is_active_status(self, status: str | None) -> bool:
        if status is None:
            return False
        return status in self.ACTIVE_STATUSES
