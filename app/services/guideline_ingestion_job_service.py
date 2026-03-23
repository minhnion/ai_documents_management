from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.exceptions import NotFoundException
from app.models.document import Document
from app.models.guideline_version import GuidelineVersion
from app.models.version_ingestion_job import VersionIngestionJob
from app.services.document_ingestion_pipeline_service import DocumentIngestionPipelineService

logger = logging.getLogger(__name__)

_active_ingestion_tasks: set[asyncio.Task[None]] = set()


class GuidelineIngestionJobService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")
    INACTIVE_STATUS: str = "inactive"
    PROCESSING_STATUS: str = "processing"
    FAILED_VERSION_STATUS: str = "failed"

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    IDLE = "idle"
    ACTIVE_JOB_STATUSES = {QUEUED, RUNNING}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue_version_ingestion(
        self,
        *,
        version_id: int,
        document_id: int,
        target_status: str,
    ) -> dict[str, Any]:
        version = await self._get_version_or_raise(version_id, for_update=True)
        document = await self._get_document_or_raise(document_id, version_id=version_id)

        active_job = await self._get_active_job_for_version(version_id)
        if active_job is not None:
            return {
                "accepted": False,
                **self._serialize_job(
                    active_job,
                    guideline_id=int(version.guideline_id),
                    version_status=version.status,
                ),
            }

        job = VersionIngestionJob(
            version_id=version_id,
            document_id=document.document_id,
            target_status=target_status,
            status=self.QUEUED,
            requested_at=self._utcnow(),
            started_at=None,
            finished_at=None,
            previous_active_versions_updated=0,
            error_message=None,
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(job)

        try:
            self._schedule_job(job.job_id)
        except Exception as exc:
            logger.exception(
                "Failed to schedule ingestion job | version_id=%s document_id=%s",
                version_id,
                document_id,
            )
            await self._mark_job_failed(job.job_id, f"Failed to schedule ingestion job: {exc}")
            await self.db.refresh(version)
            await self.db.refresh(job)
            return {
                "accepted": False,
                **self._serialize_job(
                    job,
                    guideline_id=int(version.guideline_id),
                    version_status=version.status,
                ),
            }

        return {
            "accepted": True,
            **self._serialize_job(
                job,
                guideline_id=int(version.guideline_id),
                version_status=version.status,
            ),
        }

    async def get_version_ingestion_status(self, version_id: int) -> dict[str, Any]:
        version = await self._get_version_or_raise(version_id)
        latest_job = await self._get_latest_job_for_version(version_id)
        document = await self._get_primary_document_for_version(version_id)
        if latest_job is None:
            return {
                "job_id": None,
                "guideline_id": int(version.guideline_id),
                "version_id": int(version.version_id),
                "document_id": int(document.document_id) if document else None,
                "status": self.IDLE,
                "version_status": version.status,
                "target_status": None,
                "previous_active_versions_updated": 0,
                "error_message": None,
                "requested_at": None,
                "started_at": None,
                "finished_at": None,
            }
        return self._serialize_job(
            latest_job,
            guideline_id=int(version.guideline_id),
            version_status=version.status,
        )

    def _schedule_job(self, job_id: int) -> None:
        task = asyncio.create_task(self._run_job(job_id))
        _active_ingestion_tasks.add(task)
        task.add_done_callback(_active_ingestion_tasks.discard)

    @classmethod
    async def _run_job(cls, job_id: int) -> None:
        await cls._mark_job_running(job_id)

        version_id: int | None = None
        try:
            async with AsyncSessionLocal() as session:
                service = cls(session)
                job = await service._get_job_or_raise(job_id)
                version = await service._get_version_or_raise(job.version_id, for_update=True)
                document = await service._get_document_or_raise(job.document_id, version_id=job.version_id)
                version_id = int(version.version_id)

                pipeline_service = DocumentIngestionPipelineService(session)
                await pipeline_service.process_document(
                    guideline_id=int(version.guideline_id),
                    version_id=int(version.version_id),
                    document=document,
                )

                previous_active_versions_updated = 0
                if service._is_active_status(job.target_status):
                    previous_active_versions_updated = await service._deactivate_active_versions(
                        guideline_id=int(version.guideline_id),
                        exclude_version_id=int(version.version_id),
                    )
                version.status = job.target_status
                await service._mark_job_succeeded(
                    job_id=job_id,
                    previous_active_versions_updated=previous_active_versions_updated,
                )
                await session.commit()
        except Exception as exc:
            logger.exception(
                "Version ingestion job failed | job_id=%s version_id=%s",
                job_id,
                version_id,
            )
            await cls._mark_job_failed(job_id, str(exc))

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

    async def _get_document_or_raise(self, document_id: int, *, version_id: int) -> Document:
        document = (
            await self.db.execute(
                select(Document).where(
                    Document.document_id == document_id,
                    Document.version_id == version_id,
                )
            )
        ).scalar_one_or_none()
        if document is None:
            raise NotFoundException("Document", document_id)
        return document

    async def _get_primary_document_for_version(self, version_id: int) -> Document | None:
        return (
            await self.db.execute(
                select(Document)
                .where(Document.version_id == version_id)
                .order_by(Document.document_id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _get_active_job_for_version(self, version_id: int) -> VersionIngestionJob | None:
        return (
            await self.db.execute(
                select(VersionIngestionJob)
                .where(
                    VersionIngestionJob.version_id == version_id,
                    VersionIngestionJob.status.in_(tuple(self.ACTIVE_JOB_STATUSES)),
                )
                .order_by(desc(VersionIngestionJob.job_id))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _get_latest_job_for_version(self, version_id: int) -> VersionIngestionJob | None:
        return (
            await self.db.execute(
                select(VersionIngestionJob)
                .where(VersionIngestionJob.version_id == version_id)
                .order_by(desc(VersionIngestionJob.job_id))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _get_job_or_raise(
        self,
        job_id: int,
        *,
        for_update: bool = False,
    ) -> VersionIngestionJob:
        stmt = select(VersionIngestionJob).where(VersionIngestionJob.job_id == job_id)
        if for_update:
            stmt = stmt.with_for_update()
        job = (await self.db.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise NotFoundException("VersionIngestionJob", job_id)
        return job

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

    def _is_active_status(self, status: str | None) -> bool:
        if status is None:
            return False
        return status in self.ACTIVE_STATUSES

    async def _mark_job_succeeded(
        self,
        *,
        job_id: int,
        previous_active_versions_updated: int,
    ) -> None:
        job = await self._get_job_or_raise(job_id, for_update=True)
        job.status = self.SUCCEEDED
        job.finished_at = self._utcnow()
        job.previous_active_versions_updated = int(previous_active_versions_updated)
        job.error_message = None

    @classmethod
    async def _mark_job_running(cls, job_id: int) -> None:
        async with AsyncSessionLocal() as session:
            service = cls(session)
            job = await service._get_job_or_raise(job_id, for_update=True)
            version = await service._get_version_or_raise(job.version_id, for_update=True)
            if job.status == cls.RUNNING:
                return
            job.status = cls.RUNNING
            job.started_at = service._utcnow()
            job.finished_at = None
            job.previous_active_versions_updated = 0
            job.error_message = None
            version.status = cls.PROCESSING_STATUS
            await session.commit()

    @classmethod
    async def _mark_job_failed(cls, job_id: int, error_message: str) -> None:
        async with AsyncSessionLocal() as session:
            service = cls(session)
            try:
                job = await service._get_job_or_raise(job_id, for_update=True)
                version = await service._get_version_or_raise(job.version_id, for_update=True)
            except NotFoundException:
                return
            job.status = cls.FAILED
            if job.started_at is None:
                job.started_at = service._utcnow()
            job.finished_at = service._utcnow()
            job.error_message = error_message[:4000] if error_message else "Unknown ingestion error."
            version.status = cls.FAILED_VERSION_STATUS
            await session.commit()

    def _serialize_job(
        self,
        job: VersionIngestionJob,
        *,
        guideline_id: int,
        version_status: str | None,
    ) -> dict[str, Any]:
        return {
            "job_id": int(job.job_id),
            "guideline_id": guideline_id,
            "version_id": int(job.version_id),
            "document_id": int(job.document_id),
            "status": job.status,
            "version_status": version_status,
            "target_status": job.target_status,
            "previous_active_versions_updated": int(job.previous_active_versions_updated or 0),
            "error_message": job.error_message,
            "requested_at": job.requested_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)
