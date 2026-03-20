from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models.chunk import Chunk
from app.models.guideline_version import GuidelineVersion
from app.services.chunk_generation_service import ChunkGenerationService


class GuidelineChunkService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def rebuild_version_chunks(self, version_id: int) -> dict[str, int]:
        await self._ensure_version_exists(version_id)
        deleted_chunk_count = await self._count_chunks_for_version(version_id)
        chunk_stats = await ChunkGenerationService(self.db).rebuild_chunks_for_version(
            version_id
        )
        created_chunk_count = int(chunk_stats.get('chunk_count', 0))
        return {
            'version_id': version_id,
            'deleted_chunk_count': deleted_chunk_count,
            'created_chunk_count': created_chunk_count,
        }

    async def _ensure_version_exists(self, version_id: int) -> None:
        version = (
            await self.db.execute(
                select(GuidelineVersion.version_id).where(
                    GuidelineVersion.version_id == version_id
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundException('GuidelineVersion', version_id)

    async def _count_chunks_for_version(self, version_id: int) -> int:
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.version_id == version_id)
            )
        ).scalar_one()
        return int(total or 0)
