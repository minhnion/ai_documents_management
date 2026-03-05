from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException
from app.models.document import Document
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion


class GuidelineCommandService:
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
            status=(status.strip().lower() if status and status.strip() else "active"),
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

    def _validate_create_payload(self, title: str, upload_file: UploadFile) -> None:
        if not title or not title.strip():
            raise BadRequestException("Guideline title is required.")
        filename = (upload_file.filename or "").strip()
        if not filename:
            raise BadRequestException("PDF file is required.")
        if not filename.lower().endswith(".pdf"):
            raise BadRequestException("Only PDF upload is supported.")

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
