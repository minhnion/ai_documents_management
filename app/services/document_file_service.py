from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.models.document import Document


@dataclass
class FileStreamResult:
    stream: Iterator[bytes]
    status_code: int
    headers: dict[str, str]
    media_type: str


class DocumentFileService:
    CHUNK_SIZE = 1024 * 1024

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_document_file_stream(
        self,
        document_id: int,
        range_header: str | None,
    ) -> FileStreamResult:
        document = await self._get_document(document_id=document_id)
        file_path = self._resolve_storage_path(storage_uri=document.storage_uri)

        if not file_path.exists() or not file_path.is_file():
            raise NotFoundException("Document file", document_id)

        file_size = file_path.stat().st_size
        media_type = self._resolve_media_type(document=document, file_path=file_path)

        if file_size == 0:
            if range_header:
                self._raise_range_not_satisfiable(file_size=file_size)
            return FileStreamResult(
                stream=iter(()),
                status_code=status.HTTP_200_OK,
                headers={"Accept-Ranges": "bytes", "Content-Length": "0"},
                media_type=media_type,
            )

        start, end, is_partial = self._parse_range_header(
            range_header=range_header,
            file_size=file_size,
        )

        content_length = end - start + 1
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'inline; filename="{file_path.name}"',
        }
        status_code = status.HTTP_200_OK
        if is_partial:
            status_code = status.HTTP_206_PARTIAL_CONTENT
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

        return FileStreamResult(
            stream=self._iter_file_bytes(file_path=file_path, start=start, end=end),
            status_code=status_code,
            headers=headers,
            media_type=media_type,
        )

    async def _get_document(self, document_id: int) -> Document:
        document = (
            await self.db.execute(
                select(Document).where(Document.document_id == document_id)
            )
        ).scalar_one_or_none()
        if document is None:
            raise NotFoundException("Document", document_id)
        return document

    def _resolve_storage_path(self, storage_uri: str | None) -> Path:
        if storage_uri is None or not storage_uri.strip():
            raise NotFoundException("Document file", "missing_storage_uri")

        raw_path = Path(storage_uri.strip())
        if raw_path.is_absolute():
            resolved_file_path = raw_path.resolve()
        else:
            resolved_file_path = (Path.cwd() / raw_path).resolve()

        storage_root = Path(settings.LOCAL_STORAGE_ROOT)
        if not storage_root.is_absolute():
            storage_root = (Path.cwd() / storage_root).resolve()
        else:
            storage_root = storage_root.resolve()

        try:
            resolved_file_path.relative_to(storage_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Document file path is outside local storage root.",
            ) from exc

        return resolved_file_path

    def _resolve_media_type(self, document: Document, file_path: Path) -> str:
        doc_type = (document.doc_type or "").strip().lower()
        suffix = file_path.suffix.lower()
        if doc_type == "pdf" or suffix == ".pdf":
            return "application/pdf"
        return "application/octet-stream"

    def _parse_range_header(
        self,
        range_header: str | None,
        file_size: int,
    ) -> tuple[int, int, bool]:
        if range_header is None or not range_header.strip():
            return 0, file_size - 1, False

        value = range_header.strip()
        if not value.lower().startswith("bytes="):
            self._raise_range_not_satisfiable(file_size=file_size)

        range_spec = value[6:].strip()
        if not range_spec or "," in range_spec:
            self._raise_range_not_satisfiable(file_size=file_size)

        start_raw, end_raw = (part.strip() for part in range_spec.split("-", 1))
        if not start_raw:
            if not end_raw.isdigit():
                self._raise_range_not_satisfiable(file_size=file_size)
            suffix_length = int(end_raw)
            if suffix_length <= 0:
                self._raise_range_not_satisfiable(file_size=file_size)
            if suffix_length >= file_size:
                return 0, file_size - 1, True
            return file_size - suffix_length, file_size - 1, True

        if not start_raw.isdigit():
            self._raise_range_not_satisfiable(file_size=file_size)
        start = int(start_raw)
        if start >= file_size:
            self._raise_range_not_satisfiable(file_size=file_size)

        if not end_raw:
            return start, file_size - 1, True
        if not end_raw.isdigit():
            self._raise_range_not_satisfiable(file_size=file_size)

        end = int(end_raw)
        if end < start:
            self._raise_range_not_satisfiable(file_size=file_size)
        if end >= file_size:
            end = file_size - 1
        return start, end, True

    def _raise_range_not_satisfiable(self, file_size: int) -> None:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Requested range not satisfiable.",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    def _iter_file_bytes(self, file_path: Path, start: int, end: int) -> Iterator[bytes]:
        with file_path.open("rb") as file_obj:
            file_obj.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk_size = min(self.CHUNK_SIZE, remaining)
                chunk = file_obj.read(chunk_size)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
