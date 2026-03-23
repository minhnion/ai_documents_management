from __future__ import annotations

from datetime import date
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.exceptions import (
    AppException,
    BadRequestException,
    NotFoundException,
    UnprocessableEntityException,
)
from app.models.document import Document
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion
from app.services.guideline_ingestion_job_service import GuidelineIngestionJobService

logger = logging.getLogger(__name__)


class GuidelineCommandService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")
    INACTIVE_STATUS: str = "inactive"
    PROCESSING_STATUS: str = "processing"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_guideline(
        self,
        title: str,
        ten_benh: str | None,
        publisher: str | None,
        chuyen_khoa: str | None,
        version_label: str | None,
        release_date: date | None,
        effective_from: date | None,
        effective_to: date | None,
        status: str | None,
        upload_file: UploadFile,
        doc_type: str = "pdf",
    ) -> tuple[Guideline, GuidelineVersion, Document, dict[str, Any]]:
        self._validate_create_payload(title=title, upload_file=upload_file)
        self._validate_version_dates(effective_from=effective_from, effective_to=effective_to)
        target_status = self._normalize_status(status)

        guideline = Guideline(
            title=title.strip(),
            ten_benh=ten_benh.strip() if ten_benh else None,
            publisher=publisher.strip() if publisher else None,
            chuyen_khoa=chuyen_khoa.strip() if chuyen_khoa else None,
        )
        self.db.add(guideline)
        await self.db.flush()

        resolved_version_label = await self._resolve_version_label(
            guideline_id=guideline.guideline_id,
            version_label=version_label,
        )
        guideline_version = GuidelineVersion(
            guideline_id=guideline.guideline_id,
            version_label=resolved_version_label,
            release_date=release_date,
            effective_from=effective_from,
            effective_to=effective_to,
            status=self.PROCESSING_STATUS,
        )
        self.db.add(guideline_version)
        await self.db.flush()

        storage_path = self._build_storage_path(
            guideline_id=guideline.guideline_id,
            version_id=guideline_version.version_id,
            original_filename=upload_file.filename or "source.pdf",
        )

        try:
            await self._write_upload_file(
                upload_file=upload_file,
                destination=storage_path,
            )

            document = Document(
                version_id=guideline_version.version_id,
                doc_type=doc_type,
                storage_uri=storage_path.as_posix(),
                page_count=None,
                image_uri=None,
            )
            self.db.add(document)
            await self.db.flush()

            job_result = await GuidelineIngestionJobService(self.db).enqueue_version_ingestion(
                version_id=guideline_version.version_id,
                document_id=document.document_id,
                target_status=target_status,
            )
        except AppException:
            self._cleanup_file(storage_path)
            logger.exception(
                "Create guideline failed before enqueue | title=%s",
                title,
            )
            raise
        except Exception as exc:
            self._cleanup_file(storage_path)
            logger.exception(
                "Create guideline failed with unexpected error | title=%s",
                title,
            )
            raise UnprocessableEntityException(
                f"Cannot enqueue uploaded guideline: {exc}"
            ) from exc

        return guideline, guideline_version, document, job_result

    async def create_guideline_version(
        self,
        guideline_id: int,
        version_label: str | None,
        release_date: date | None,
        effective_from: date | None,
        effective_to: date | None,
        status: str | None,
        upload_file: UploadFile,
        doc_type: str = "pdf",
    ) -> tuple[Guideline, GuidelineVersion, Document, dict[str, Any]]:
        self._validate_pdf_upload(upload_file)
        self._validate_version_dates(effective_from=effective_from, effective_to=effective_to)
        target_status = self._normalize_status(status)

        guideline = await self._get_guideline_for_update(guideline_id)
        resolved_version_label = await self._resolve_version_label(
            guideline_id=guideline_id,
            version_label=version_label,
        )

        guideline_version = GuidelineVersion(
            guideline_id=guideline_id,
            version_label=resolved_version_label,
            release_date=release_date,
            effective_from=effective_from,
            effective_to=effective_to,
            status=self.PROCESSING_STATUS,
        )
        self.db.add(guideline_version)
        await self.db.flush()

        storage_path: Path | None = None
        try:
            storage_path = self._build_storage_path(
                guideline_id=guideline.guideline_id,
                version_id=guideline_version.version_id,
                original_filename=upload_file.filename or "source.pdf",
            )
            await self._write_upload_file(
                upload_file=upload_file,
                destination=storage_path,
            )
            document = Document(
                version_id=guideline_version.version_id,
                doc_type=doc_type,
                storage_uri=storage_path.as_posix(),
                page_count=None,
                image_uri=None,
            )
            self.db.add(document)
            await self.db.flush()

            job_result = await GuidelineIngestionJobService(self.db).enqueue_version_ingestion(
                version_id=guideline_version.version_id,
                document_id=document.document_id,
                target_status=target_status,
            )
        except AppException:
            if storage_path is not None:
                self._cleanup_file(storage_path)
            logger.exception(
                "Create guideline version failed before enqueue | guideline_id=%s",
                guideline_id,
            )
            raise
        except Exception as exc:
            if storage_path is not None:
                self._cleanup_file(storage_path)
            logger.exception(
                "Create guideline version failed with unexpected error | guideline_id=%s",
                guideline_id,
            )
            raise UnprocessableEntityException(
                f"Cannot enqueue guideline version: {exc}"
            ) from exc

        return guideline, guideline_version, document, job_result

    def _validate_create_payload(self, title: str, upload_file: UploadFile) -> None:
        if not title or not title.strip():
            raise BadRequestException("Guideline title is required.")
        self._validate_pdf_upload(upload_file)

    def _validate_pdf_upload(self, upload_file: UploadFile | None) -> None:
        if upload_file is None:
            raise BadRequestException("PDF file is required.")
        filename = (upload_file.filename or "").strip()
        if not filename:
            raise BadRequestException("PDF file is required.")
        if not filename.lower().endswith(".pdf"):
            raise BadRequestException("Only PDF upload is supported.")

    def _validate_version_dates(
        self,
        effective_from: date | None,
        effective_to: date | None,
    ) -> None:
        if effective_from and effective_to and effective_to < effective_from:
            raise BadRequestException("effective_to must be greater than or equal to effective_from.")

    def _normalize_status(self, status: str | None) -> str:
        if status and status.strip():
            return status.strip().lower()
        return "active"

    def _is_active_status(self, status: str) -> bool:
        return status in self.ACTIVE_STATUSES

    async def _get_guideline_for_update(self, guideline_id: int) -> Guideline:
        guideline = (
            await self.db.execute(
                select(Guideline)
                .where(Guideline.guideline_id == guideline_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if guideline is None:
            raise NotFoundException("Guideline", guideline_id)
        return guideline

    async def _resolve_version_label(
        self,
        guideline_id: int,
        version_label: str | None,
    ) -> str:
        if version_label and version_label.strip():
            return version_label.strip()
        version_count = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(GuidelineVersion)
                    .where(GuidelineVersion.guideline_id == guideline_id)
                )
            ).scalar_one()
        )
        return str(version_count + 1)

    def _build_storage_path(
        self,
        guideline_id: int,
        version_id: int,
        original_filename: str,
    ) -> Path:
        extension = Path(original_filename).suffix.lower() or ".pdf"
        filename = f"source{extension}"
        storage_root = Path(settings.LOCAL_STORAGE_ROOT)
        return (
            storage_root
            / "guidelines"
            / str(guideline_id)
            / str(version_id)
            / filename
        )

    async def _write_upload_file(self, upload_file: UploadFile, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        await upload_file.seek(0)

    def _cleanup_file(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
