from datetime import date
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.exceptions import (
    BadRequestException,
    NotFoundException,
    UnprocessableEntityException,
)
from app.models.document import Document
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion


class GuidelineCommandService:
    ACTIVE_STATUSES: tuple[str, ...] = ("active", "dang_hieu_luc", "đang hiệu lực")
    INACTIVE_STATUS: str = "inactive"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_guideline(
        self,
        title: str,
        publisher: str | None,
        chuyen_khoa: str | None,
        version_label: str | None,
        release_date: date | None,
        effective_from: date | None,
        effective_to: date | None,
        status: str | None,
        upload_file: UploadFile,
        doc_type: str = "pdf",
    ) -> tuple[Guideline, GuidelineVersion, Document]:
        self._validate_create_payload(title=title, upload_file=upload_file)
        self._validate_version_dates(effective_from=effective_from, effective_to=effective_to)
        normalized_status = self._normalize_status(status)

        guideline = Guideline(
            title=title.strip(),
            publisher=publisher.strip() if publisher else None,
            chuyen_khoa=chuyen_khoa.strip() if chuyen_khoa else None,
        )
        self.db.add(guideline)
        await self.db.flush()

        guideline_version = GuidelineVersion(
            guideline_id=guideline.guideline_id,
            version_label=version_label.strip() if version_label else None,
            release_date=release_date,
            effective_from=effective_from,
            effective_to=effective_to,
            status=normalized_status,
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
        except Exception as exc:
            self._cleanup_file(storage_path)
            raise UnprocessableEntityException(
                "Cannot persist uploaded guideline."
            ) from exc

        return guideline, guideline_version, document

    async def create_guideline_version(
        self,
        guideline_id: int,
        version_label: str | None,
        release_date: date | None,
        effective_from: date | None,
        effective_to: date | None,
        status: str | None,
        upload_file: UploadFile | None = None,
        doc_type: str = "pdf",
    ) -> tuple[Guideline, GuidelineVersion, Document | None, int]:
        self._validate_version_dates(effective_from=effective_from, effective_to=effective_to)
        normalized_status = self._normalize_status(status)

        guideline = await self._get_guideline_for_update(guideline_id)

        previous_active_versions_updated = 0
        if self._is_active_status(normalized_status):
            previous_active_versions_updated = await self._deactivate_active_versions(
                guideline_id=guideline_id
            )

        guideline_version = GuidelineVersion(
            guideline_id=guideline_id,
            version_label=version_label.strip() if version_label else None,
            release_date=release_date,
            effective_from=effective_from,
            effective_to=effective_to,
            status=normalized_status,
        )
        self.db.add(guideline_version)
        await self.db.flush()

        document: Document | None = None
        storage_path: Path | None = None
        try:
            if self._has_upload_file(upload_file):
                self._validate_pdf_upload(upload_file)
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
        except Exception as exc:
            if storage_path is not None:
                self._cleanup_file(storage_path)
            raise UnprocessableEntityException(
                "Cannot persist guideline version."
            ) from exc

        return guideline, guideline_version, document, previous_active_versions_updated

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

    async def _deactivate_active_versions(self, guideline_id: int) -> int:
        result = await self.db.execute(
            update(GuidelineVersion)
            .where(GuidelineVersion.guideline_id == guideline_id)
            .where(
                func.lower(func.coalesce(GuidelineVersion.status, "")).in_(
                    self.ACTIVE_STATUSES
                )
            )
            .values(status=self.INACTIVE_STATUS)
        )
        return int(result.rowcount or 0)

    def _has_upload_file(self, upload_file: UploadFile | None) -> bool:
        return upload_file is not None and bool((upload_file.filename or "").strip())

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
            # Best-effort cleanup only
            pass
