from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion
from app.models.section import Section

logger = logging.getLogger(__name__)


class GuidelineDeleteService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")
    INACTIVE_STATUS: str = "inactive"
    DEFAULT_ACTIVE_STATUS: str = "active"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def delete_guideline(self, guideline_id: int) -> dict[str, int]:
        await self._ensure_guideline_exists_for_update(guideline_id)
        deleted_version_count = await self._count_versions(guideline_id)
        guideline_dir = self._build_guideline_storage_dir(guideline_id)

        await self._delete_guideline_graph(guideline_id)
        await self.db.flush()

        self._delete_dir_safely(guideline_dir)
        return {
            "guideline_id": guideline_id,
            "deleted_version_count": deleted_version_count,
        }

    async def delete_version(self, version_id: int) -> dict[str, int | None]:
        guideline_id, version_status = await self._get_version_for_update(version_id)
        version_dir = self._build_version_storage_dir(
            guideline_id=guideline_id,
            version_id=version_id,
        )

        await self._delete_version_graph(version_id)
        await self.db.flush()

        promoted_version_id: int | None = None
        if self._is_active_status(version_status):
            promoted_version_id = await self._promote_latest_remaining_version(
                guideline_id=guideline_id
            )

        remaining_version_count = await self._count_versions(guideline_id)

        self._delete_dir_safely(version_dir)
        self._delete_dir_if_empty(version_dir.parent)

        return {
            "guideline_id": guideline_id,
            "deleted_version_id": version_id,
            "promoted_version_id": promoted_version_id,
            "remaining_version_count": remaining_version_count,
        }

    async def _ensure_guideline_exists_for_update(self, guideline_id: int) -> None:
        existing_guideline_id = (
            await self.db.execute(
                select(Guideline.guideline_id)
                .where(Guideline.guideline_id == guideline_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing_guideline_id is None:
            raise NotFoundException("Guideline", guideline_id)

    async def _get_version_for_update(self, version_id: int) -> tuple[int, str]:
        row = (
            await self.db.execute(
                select(
                    GuidelineVersion.guideline_id,
                    GuidelineVersion.status,
                )
                .where(GuidelineVersion.version_id == version_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            raise NotFoundException("GuidelineVersion", version_id)
        guideline_id, status = row
        return int(guideline_id), self._normalize_status(status)

    async def _count_versions(self, guideline_id: int) -> int:
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(GuidelineVersion)
                .where(GuidelineVersion.guideline_id == guideline_id)
            )
        ).scalar_one()
        return int(total or 0)

    async def _promote_latest_remaining_version(
        self,
        guideline_id: int,
    ) -> int | None:
        promoted = (
            await self.db.execute(
                select(GuidelineVersion)
                .where(GuidelineVersion.guideline_id == guideline_id)
                .order_by(
                    GuidelineVersion.release_date.desc().nullslast(),
                    GuidelineVersion.version_id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if promoted is None:
            return None

        await self.db.execute(
            update(GuidelineVersion)
            .where(GuidelineVersion.guideline_id == guideline_id)
            .where(GuidelineVersion.version_id != promoted.version_id)
            .where(func.lower(GuidelineVersion.status).in_(self.ACTIVE_STATUSES))
            .values(status=self.INACTIVE_STATUS)
        )
        promoted.status = self.DEFAULT_ACTIVE_STATUS
        await self.db.flush()
        return int(promoted.version_id)

    async def _delete_version_graph(self, version_id: int) -> None:
        await self.db.execute(delete(Chunk).where(Chunk.version_id == version_id))
        await self.db.execute(delete(Section).where(Section.version_id == version_id))
        await self.db.execute(delete(Document).where(Document.version_id == version_id))
        await self.db.execute(
            delete(GuidelineVersion).where(GuidelineVersion.version_id == version_id)
        )

    async def _delete_guideline_graph(self, guideline_id: int) -> None:
        version_ids_subquery = select(GuidelineVersion.version_id).where(
            GuidelineVersion.guideline_id == guideline_id
        )
        await self.db.execute(
            delete(Chunk).where(Chunk.version_id.in_(version_ids_subquery))
        )
        await self.db.execute(
            delete(Section).where(Section.version_id.in_(version_ids_subquery))
        )
        await self.db.execute(
            delete(Document).where(Document.version_id.in_(version_ids_subquery))
        )
        await self.db.execute(
            delete(GuidelineVersion).where(
                GuidelineVersion.guideline_id == guideline_id
            )
        )
        await self.db.execute(
            delete(Guideline).where(Guideline.guideline_id == guideline_id)
        )

    def _normalize_status(self, status: str | None) -> str:
        if status is None:
            return ""
        return status.strip().lower()

    def _is_active_status(self, status: str) -> bool:
        return status in self.ACTIVE_STATUSES

    def _build_guideline_storage_dir(self, guideline_id: int) -> Path:
        return self._storage_root() / "guidelines" / str(guideline_id)

    def _build_version_storage_dir(self, guideline_id: int, version_id: int) -> Path:
        return self._build_guideline_storage_dir(guideline_id) / str(version_id)

    def _storage_root(self) -> Path:
        root = Path(settings.LOCAL_STORAGE_ROOT)
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        else:
            root = root.resolve()
        return root

    def _delete_dir_safely(self, target_dir: Path) -> None:
        if not target_dir.exists():
            return
        root = self._storage_root()
        try:
            target_dir.resolve().relative_to(root)
        except ValueError:
            logger.warning(
                "Skip deleting directory outside storage root: %s",
                target_dir.as_posix(),
            )
            return

        try:
            shutil.rmtree(target_dir, ignore_errors=False)
        except Exception:
            logger.exception(
                "Failed to delete storage directory: %s",
                target_dir.as_posix(),
            )

    def _delete_dir_if_empty(self, target_dir: Path) -> None:
        try:
            if target_dir.exists() and target_dir.is_dir():
                next(target_dir.iterdir())
                return
        except StopIteration:
            try:
                target_dir.rmdir()
            except Exception:
                logger.exception(
                    "Failed to delete empty directory: %s",
                    target_dir.as_posix(),
                )
        except Exception:
            logger.exception(
                "Failed checking directory emptiness: %s",
                target_dir.as_posix(),
            )
