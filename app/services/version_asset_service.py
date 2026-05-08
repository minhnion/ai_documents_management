from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.models.guideline_version import GuidelineVersion

# Landing AI chunk_id is a v4-style UUID.
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass
class VersionAssetFile:
    path: Path
    media_type: str = "image/png"


class VersionAssetService:
    """Resolve table/figure PNGs cropped during the OCR pipeline.

    Pipeline saves them under
    ``{LOCAL_STORAGE_ROOT}/guidelines/{guideline_id}/{version_id}/pipeline/images/``
    using ``<landing_chunk_id>.png`` as filename.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_asset_file(
        self,
        *,
        version_id: int,
        landing_chunk_id: str,
    ) -> VersionAssetFile:
        if not _UUID_RE.match(landing_chunk_id):
            raise NotFoundException("Version asset", landing_chunk_id)

        version = await self._get_version(version_id)
        images_dir = self._resolve_images_dir(
            guideline_id=version.guideline_id,
            version_id=version.version_id,
        )
        candidate = (images_dir / f"{landing_chunk_id}.png").resolve()

        # Refuse paths that escape the images directory.
        try:
            candidate.relative_to(images_dir)
        except ValueError as exc:
            raise NotFoundException("Version asset", landing_chunk_id) from exc

        if not candidate.exists() or not candidate.is_file():
            raise NotFoundException("Version asset", landing_chunk_id)

        return VersionAssetFile(path=candidate)

    async def _get_version(self, version_id: int) -> GuidelineVersion:
        result = await self.db.execute(
            select(GuidelineVersion).where(GuidelineVersion.version_id == version_id)
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise NotFoundException("GuidelineVersion", version_id)
        return version

    @staticmethod
    def _resolve_images_dir(*, guideline_id: int, version_id: int) -> Path:
        storage_root = Path(settings.LOCAL_STORAGE_ROOT)
        if not storage_root.is_absolute():
            storage_root = (Path.cwd() / storage_root).resolve()
        else:
            storage_root = storage_root.resolve()
        return (
            storage_root
            / "guidelines"
            / str(guideline_id)
            / str(version_id)
            / "pipeline"
            / "images"
        ).resolve()
