from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException
from app.models.document import Document
from app.services.pipeline import (
    FuzzyChunkingService,
    LandingAIOcrService,
    MarkdownProcessingService,
    PipelinePersistenceService,
    TocBuilderService,
)

logger = logging.getLogger(__name__)


class DocumentIngestionPipelineService:
    """End-to-end pipeline orchestrator.

    The implementation is intentionally split into dedicated sub-services:
    OCR -> markdown cleanup -> TOC build -> fuzzy chunking -> persistence.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

        self._markdown_service = MarkdownProcessingService()
        self._ocr_service = LandingAIOcrService()
        self._toc_service = TocBuilderService(markdown_service=self._markdown_service)
        self._chunking_service = FuzzyChunkingService(markdown_service=self._markdown_service)
        self._persistence_service = PipelinePersistenceService(db=db)

    async def process_document(
        self,
        guideline_id: int,
        version_id: int,
        document: Document,
    ) -> dict[str, object]:
        self._validate_pipeline_settings()

        pdf_path = self._resolve_pdf_path(document)
        artifact_dir = self._build_artifact_dir(guideline_id=guideline_id, version_id=version_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Pipeline start | guideline_id=%s version_id=%s file=%s",
            guideline_id,
            version_id,
            pdf_path.name,
        )

        raw_md = await self._ocr_markdown(pdf_path)
        clean_md = self._clean_markdown(raw_md)
        toc = await self._build_toc(clean_md, source_file=pdf_path.name)
        chunk_payload = self._chunk_with_fuzzy_matching(clean_md, toc)

        self._write_artifacts(
            artifact_dir=artifact_dir,
            raw_md=raw_md,
            clean_md=clean_md,
            toc=toc,
            chunk_payload=chunk_payload,
        )
        persist_stats = await self._persist_chunk_payload(
            version_id=version_id,
            document=document,
            chunk_payload=chunk_payload,
            clean_text=clean_md,
        )
        logger.info(
            "Pipeline done | guideline_id=%s version_id=%s sections=%s chunks=%s artifacts=%s",
            guideline_id,
            version_id,
            persist_stats.get("section_count"),
            persist_stats.get("chunk_count"),
            artifact_dir.as_posix(),
        )
        return {
            "artifact_dir": artifact_dir.as_posix(),
            **persist_stats,
        }

    def _validate_pipeline_settings(self) -> None:
        if not settings.LANDINGAI_API_KEY.strip():
            raise BadRequestException("LANDINGAI_API_KEY is required for OCR pipeline.")
        if not settings.OPENAI_API_KEY.strip():
            raise BadRequestException("OPENAI_API_KEY is required for TOC pipeline.")
        if not settings.LANDINGAI_API_URL.strip():
            raise BadRequestException("LANDINGAI_API_URL is required for OCR pipeline.")
        if not settings.OPENAI_API_URL.strip():
            raise BadRequestException("OPENAI_API_URL is required for TOC pipeline.")
        if not settings.LANDINGAI_MODEL_NAME.strip():
            raise BadRequestException("LANDINGAI_MODEL_NAME is required for OCR pipeline.")
        if not settings.OPENAI_MODEL_NAME.strip():
            raise BadRequestException("OPENAI_MODEL_NAME is required for TOC pipeline.")
        if not 0.0 < float(settings.SCORE_THRESHOLD) < 1.0:
            raise BadRequestException("SCORE_THRESHOLD must be > 0 and < 1.")

    def _resolve_pdf_path(self, document: Document) -> Path:
        if document.storage_uri is None or not document.storage_uri.strip():
            raise UnprocessableEntityException("Document storage_uri is missing.")
        path = Path(document.storage_uri.strip())
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        if not path.exists() or not path.is_file():
            raise UnprocessableEntityException("Uploaded PDF file does not exist on local storage.")
        return path

    def _build_artifact_dir(self, guideline_id: int, version_id: int) -> Path:
        storage_root = Path(settings.LOCAL_STORAGE_ROOT)
        if not storage_root.is_absolute():
            storage_root = (Path.cwd() / storage_root).resolve()
        else:
            storage_root = storage_root.resolve()
        return storage_root / "guidelines" / str(guideline_id) / str(version_id) / "pipeline"

    async def _ocr_markdown(self, pdf_path: Path) -> str:
        return await self._ocr_service.ocr_markdown(pdf_path)

    async def _build_toc(self, clean_text: str, source_file: str) -> dict:
        return await self._toc_service.build_toc(clean_text=clean_text, source_file=source_file)

    def _clean_markdown(self, raw_text: str) -> str:
        return self._markdown_service.clean_markdown(raw_text)

    def _chunk_with_fuzzy_matching(self, clean_text: str, toc: dict) -> dict:
        return self._chunking_service.build_chunk_payload(
            clean_text=clean_text,
            toc=toc,
            score_threshold=float(settings.SCORE_THRESHOLD),
        )

    async def _persist_chunk_payload(
        self,
        version_id: int,
        document: Document,
        chunk_payload: dict,
        clean_text: str,
    ) -> dict[str, int]:
        return await self._persistence_service.persist_chunk_payload(
            version_id=version_id,
            document=document,
            chunk_payload=chunk_payload,
            clean_text=clean_text,
        )

    def _write_artifacts(
        self,
        artifact_dir: Path,
        raw_md: str,
        clean_md: str,
        toc: dict,
        chunk_payload: dict,
    ) -> None:
        self._persistence_service.write_artifacts(
            artifact_dir=artifact_dir,
            raw_md=raw_md,
            clean_md=clean_md,
            toc=toc,
            chunk_payload=chunk_payload,
        )

    async def _openai_json_completion(self, prompt: str) -> dict:
        # Backward-compatible helper used by diagnostics/tests.
        return await self._toc_service.openai_json_completion(
            system_prompt="You are a strict JSON generator. Return valid JSON only.",
            user_prompt=prompt,
        )
