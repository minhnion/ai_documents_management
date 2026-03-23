from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.exceptions import NotFoundException
from app.models.chunk import Chunk
from app.models.chunk_rebuild_job import ChunkRebuildJob
from app.models.guideline_version import GuidelineVersion
from app.services.chunk_generation_service import ChunkGenerationService

logger = logging.getLogger(__name__)

_active_chunk_rebuild_tasks: set[asyncio.Task[None]] = set()


class GuidelineChunkService:
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    IDLE = 'idle'
    ACTIVE_STATUSES = {QUEUED, RUNNING}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue_version_chunk_rebuild(self, version_id: int) -> dict[str, Any]:
        await self._ensure_version_exists(version_id, for_update=True)
        active_job = await self._get_active_job_for_version(version_id)
        if active_job is not None:
            return {
                'accepted': False,
                **self._serialize_job(active_job),
            }

        job = ChunkRebuildJob(
            version_id=version_id,
            status=self.QUEUED,
            requested_at=self._utcnow(),
            started_at=None,
            finished_at=None,
            deleted_chunk_count=0,
            created_chunk_count=0,
            error_message=None,
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(job)

        self._schedule_rebuild_task(job.job_id)
        return {
            'accepted': True,
            **self._serialize_job(job),
        }

    async def get_version_chunk_rebuild_status(self, version_id: int) -> dict[str, Any]:
        await self._ensure_version_exists(version_id)
        latest_job = await self._get_latest_job_for_version(version_id)
        if latest_job is None:
            return {
                'job_id': None,
                'version_id': version_id,
                'status': self.IDLE,
                'deleted_chunk_count': 0,
                'created_chunk_count': 0,
                'error_message': None,
                'requested_at': None,
                'started_at': None,
                'finished_at': None,
            }
        return self._serialize_job(latest_job)

    def _schedule_rebuild_task(self, job_id: int) -> None:
        task = asyncio.create_task(self._run_rebuild_task(job_id))
        _active_chunk_rebuild_tasks.add(task)
        task.add_done_callback(_active_chunk_rebuild_tasks.discard)

    @classmethod
    async def _run_rebuild_task(cls, job_id: int) -> None:
        await cls._mark_job_running(job_id)

        version_id: int | None = None
        try:
            async with AsyncSessionLocal() as session:
                service = cls(session)
                job = await service._get_job_or_raise(job_id)
                version_id = int(job.version_id)
                deleted_chunk_count = await service._count_chunks_for_version(version_id)
                chunk_stats = await ChunkGenerationService(session).rebuild_chunks_for_version(version_id)
                created_chunk_count = int(chunk_stats.get('chunk_count', 0))
                await service._mark_job_succeeded(
                    job_id=job_id,
                    deleted_chunk_count=deleted_chunk_count,
                    created_chunk_count=created_chunk_count,
                )
                await session.commit()
        except Exception as exc:
            logger.exception(
                'Chunk rebuild job failed | job_id=%s version_id=%s',
                job_id,
                version_id,
            )
            await cls._mark_job_failed(job_id, str(exc))

    async def _ensure_version_exists(self, version_id: int, *, for_update: bool = False) -> None:
        stmt = select(GuidelineVersion.version_id).where(GuidelineVersion.version_id == version_id)
        if for_update:
            stmt = stmt.with_for_update()
        version = (await self.db.execute(stmt)).scalar_one_or_none()
        if version is None:
            raise NotFoundException('GuidelineVersion', version_id)

    async def _get_active_job_for_version(self, version_id: int) -> ChunkRebuildJob | None:
        return (
            await self.db.execute(
                select(ChunkRebuildJob)
                .where(
                    ChunkRebuildJob.version_id == version_id,
                    ChunkRebuildJob.status.in_(tuple(self.ACTIVE_STATUSES)),
                )
                .order_by(desc(ChunkRebuildJob.job_id))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _get_latest_job_for_version(self, version_id: int) -> ChunkRebuildJob | None:
        return (
            await self.db.execute(
                select(ChunkRebuildJob)
                .where(ChunkRebuildJob.version_id == version_id)
                .order_by(desc(ChunkRebuildJob.job_id))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _get_job_or_raise(self, job_id: int, *, for_update: bool = False) -> ChunkRebuildJob:
        stmt = select(ChunkRebuildJob).where(ChunkRebuildJob.job_id == job_id)
        if for_update:
            stmt = stmt.with_for_update()
        job = (await self.db.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise NotFoundException('ChunkRebuildJob', job_id)
        return job

    async def _count_chunks_for_version(self, version_id: int) -> int:
        total = (
            await self.db.execute(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.version_id == version_id)
            )
        ).scalar_one()
        return int(total or 0)

    async def _mark_job_succeeded(
        self,
        *,
        job_id: int,
        deleted_chunk_count: int,
        created_chunk_count: int,
    ) -> None:
        job = await self._get_job_or_raise(job_id, for_update=True)
        job.status = self.SUCCEEDED
        job.finished_at = self._utcnow()
        job.deleted_chunk_count = int(deleted_chunk_count)
        job.created_chunk_count = int(created_chunk_count)
        job.error_message = None

    @classmethod
    async def _mark_job_running(cls, job_id: int) -> None:
        async with AsyncSessionLocal() as session:
            service = cls(session)
            job = await service._get_job_or_raise(job_id, for_update=True)
            if job.status == cls.RUNNING:
                return
            job.status = cls.RUNNING
            job.started_at = service._utcnow()
            job.finished_at = None
            job.error_message = None
            job.deleted_chunk_count = 0
            job.created_chunk_count = 0
            await session.commit()

    @classmethod
    async def _mark_job_failed(cls, job_id: int, error_message: str) -> None:
        async with AsyncSessionLocal() as session:
            service = cls(session)
            try:
                job = await service._get_job_or_raise(job_id, for_update=True)
            except NotFoundException:
                return
            job.status = cls.FAILED
            if job.started_at is None:
                job.started_at = service._utcnow()
            job.finished_at = service._utcnow()
            job.error_message = error_message[:4000] if error_message else 'Unknown chunk rebuild error.'
            await session.commit()

    def _serialize_job(self, job: ChunkRebuildJob) -> dict[str, Any]:
        return {
            'job_id': int(job.job_id),
            'version_id': int(job.version_id),
            'status': job.status,
            'deleted_chunk_count': int(job.deleted_chunk_count or 0),
            'created_chunk_count': int(job.created_chunk_count or 0),
            'error_message': job.error_message,
            'requested_at': job.requested_at,
            'started_at': job.started_at,
            'finished_at': job.finished_at,
        }

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)
