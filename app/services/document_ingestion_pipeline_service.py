from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException
from app.models.document import Document
from app.services.document_pipeline_selector_service import (
    DocumentPipelineSelection,
    DocumentPipelineSelectorService,
)
from app.services.pipeline import (
    ExtractImageService,
    FuzzyChunkingService,
    LandingAIOcrService,
    MarkdownProcessingService,
    PipelinePersistenceService,
    TocBuilderService,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.pipeline.spatial_pdf import SpatialPdfPipelineResult, SpatialPdfPipelineService


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
        self._extract_image_service = ExtractImageService()
        self._persistence_service = PipelinePersistenceService(db=db)
        self._pipeline_selector_service = DocumentPipelineSelectorService()
        self._spatial_pdf_service: SpatialPdfPipelineService | None = None

    async def process_document(
        self,
        guideline_id: int,
        version_id: int,
        document: Document,
    ) -> dict[str, object]:
        pdf_path = self._resolve_pdf_path(document)
        artifact_dir = self._build_artifact_dir(guideline_id=guideline_id, version_id=version_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Pipeline start | guideline_id=%s version_id=%s file=%s",
            guideline_id,
            version_id,
            pdf_path.name,
        )

        requested_mode = self._resolve_pipeline_mode()
        selection = await self._select_pipeline_mode(
            requested_mode=requested_mode,
            pdf_path=pdf_path,
        )
        logger.info(
            "Pipeline mode selected | requested=%s selected=%s reason=%s metrics=%s",
            requested_mode,
            selection.mode,
            selection.reason,
            selection.metrics,
        )

        effective_mode = selection.mode
        persist_stats: dict[str, int]

        if effective_mode == "spatial_pdf":
            self._validate_pipeline_settings("spatial_pdf")
            try:
                spatial_result = await self._process_with_spatial_pdf(
                    pdf_path=pdf_path,
                    artifact_dir=artifact_dir,
                )
            except Exception:
                if requested_mode == "auto":
                    logger.warning(
                        "Spatial pipeline failed in auto mode; falling back to ocr_llm | file=%s",
                        pdf_path.name,
                        exc_info=True,
                    )
                    effective_mode = "ocr_llm"
                else:
                    raise
            else:
                if requested_mode == "auto" and not self._is_spatial_result_usable(spatial_result):
                    logger.warning(
                        "Spatial pipeline result deemed low-confidence; falling back to ocr_llm | file=%s",
                        pdf_path.name,
                    )
                    effective_mode = "ocr_llm"
                else:
                    self._write_artifacts(
                        artifact_dir=artifact_dir,
                        raw_md=None,
                        clean_md=None,
                        ade_chunks=None,
                        toc=spatial_result.toc,
                        chunk_payload=spatial_result.chunk_payload,
                    )
                    persist_stats = await self._persist_chunk_payload(
                        version_id=version_id,
                        document=document,
                        chunk_payload=spatial_result.chunk_payload,
                        clean_text=None,
                        page_count=spatial_result.page_count,
                    )

        if effective_mode == "ocr_llm":
            self._validate_pipeline_settings("ocr_llm")
            ocr_result = await self._ocr_document(pdf_path)
            toc = await self._build_toc(
                ocr_result.raw_markdown,
                source_file=pdf_path.name,
                ade_chunks=ocr_result.ade_chunks,
            )
            chunk_payload = self._chunk_with_fuzzy_matching(
                ocr_result.raw_markdown,
                ocr_result.ade_chunks,
                toc,
            )

            self._write_artifacts(
                artifact_dir=artifact_dir,
                raw_md=ocr_result.raw_markdown,
                clean_md=None,
                ade_chunks=ocr_result.ade_chunks,
                toc=toc,
                chunk_payload=chunk_payload,
            )
            await self._extract_images_best_effort(
                pdf_path=pdf_path,
                artifact_dir=artifact_dir,
            )
            persist_stats = await self._persist_chunk_payload(
                version_id=version_id,
                document=document,
                chunk_payload=chunk_payload,
                clean_text=None,
                page_count=ocr_result.page_count,
            )
        document.pipeline_mode_used = effective_mode
        logger.info(
            "Pipeline done | guideline_id=%s version_id=%s mode=%s sections=%s db_chunks=%s artifacts=%s",
            guideline_id,
            version_id,
            effective_mode,
            persist_stats.get("section_count"),
            persist_stats.get("chunk_count"),
            artifact_dir.as_posix(),
        )
        return {
            "artifact_dir": artifact_dir.as_posix(),
            **persist_stats,
        }

    def _validate_pipeline_settings(self, pipeline_mode: str) -> None:
        if pipeline_mode == "spatial_pdf":
            return
        if pipeline_mode not in {"ocr_llm"}:
            raise BadRequestException(
                "DOCUMENT_PIPELINE_MODE must be one of: auto, ocr_llm, spatial_pdf."
            )
        self._hydrate_core_pipeline_env()
        if not settings.LANDINGAI_API_KEY.strip():
            raise BadRequestException("LANDINGAI_API_KEY is required for OCR pipeline.")
        if not settings.LANDINGAI_MODEL_NAME.strip():
            raise BadRequestException("LANDINGAI_MODEL_NAME is required for OCR pipeline.")
        if not settings.OPENAI_API_KEY.strip():
            raise BadRequestException("OPENAI_API_KEY is required for TOC and chunk pipeline.")
        if not settings.OPENAI_MODEL_NAME.strip():
            raise BadRequestException("OPENAI_MODEL_NAME is required for TOC pipeline.")
        if not 0.0 < float(settings.SCORE_THRESHOLD) < 1.0:
            raise BadRequestException("SCORE_THRESHOLD must be > 0 and < 1.")

    def _hydrate_core_pipeline_env(self) -> None:
        if settings.LANDINGAI_API_KEY.strip():
            os.environ["LANDINGAI_API_KEY"] = settings.LANDINGAI_API_KEY.strip()
            os.environ["VISION_AGENT_API_KEY"] = settings.LANDINGAI_API_KEY.strip()
        if settings.LANDINGAI_MODEL_NAME.strip():
            os.environ["LANDINGAI_MODEL_NAME"] = settings.LANDINGAI_MODEL_NAME.strip()
        if settings.OPENAI_API_KEY.strip():
            os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY.strip()
        if settings.OPENAI_API_URL.strip():
            os.environ["OPENAI_API_URL"] = settings.OPENAI_API_URL.strip().strip('"').strip("'")
        if settings.OPENAI_MODEL_NAME.strip():
            os.environ["OPENAI_MODEL_NAME"] = settings.OPENAI_MODEL_NAME.strip()

    def _resolve_pipeline_mode(self) -> str:
        raw_mode = str(settings.DOCUMENT_PIPELINE_MODE).strip().lower()
        if raw_mode in {"", "auto"}:
            return "auto"
        if raw_mode in {"ocr", "ocr_llm"}:
            return "ocr_llm"
        if raw_mode in {"spatial", "spatial_pdf", "native_pdf", "pymupdf"}:
            return "spatial_pdf"
        return raw_mode

    async def _select_pipeline_mode(
        self,
        *,
        requested_mode: str,
        pdf_path: Path,
    ) -> DocumentPipelineSelection:
        if requested_mode == "auto":
            return await self._pipeline_selector_service.select_mode(pdf_path)
        if requested_mode in {"ocr_llm", "spatial_pdf"}:
            return DocumentPipelineSelection(
                mode=requested_mode,
                reason="manual_override",
                metrics={},
            )
        raise BadRequestException(
            "DOCUMENT_PIPELINE_MODE must be one of: auto, ocr_llm, spatial_pdf."
        )

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

    async def _process_with_spatial_pdf(
        self,
        *,
        pdf_path: Path,
        artifact_dir: Path,
    ) -> SpatialPdfPipelineResult:
        if self._spatial_pdf_service is None:
            from app.services.pipeline.spatial_pdf import SpatialPdfPipelineService

            self._spatial_pdf_service = SpatialPdfPipelineService()
        return await self._spatial_pdf_service.process_pdf(
            pdf_path=pdf_path,
            artifact_dir=artifact_dir,
        )

    async def _ocr_document(self, pdf_path: Path):
        return await self._ocr_service.process_pdf(pdf_path)

    async def _ocr_markdown(self, pdf_path: Path) -> str:
        return await self._ocr_service.ocr_markdown(pdf_path)

    async def _build_toc(
        self,
        clean_text: str,
        source_file: str,
        ade_chunks: list[dict] | None = None,
    ) -> dict:
        return await self._toc_service.build_toc(
            clean_text=clean_text,
            source_file=source_file,
            ade_chunks=ade_chunks,
        )

    def _clean_markdown(self, raw_text: str) -> str:
        return self._markdown_service.clean_markdown(raw_text)

    def _chunk_with_fuzzy_matching(
        self,
        clean_text: str,
        ade_chunks: list[dict],
        toc: dict,
    ) -> dict:
        return self._chunking_service.build_chunk_payload(
            ocr_md_text=clean_text,
            ade_chunks=ade_chunks,
            toc_data=toc,
        )

    async def _persist_chunk_payload(
        self,
        version_id: int,
        document: Document,
        chunk_payload: dict,
        clean_text: str | None,
        page_count: int | None = None,
    ) -> dict[str, int]:
        return await self._persistence_service.persist_chunk_payload(
            version_id=version_id,
            document=document,
            chunk_payload=chunk_payload,
            clean_text=clean_text,
            page_count=page_count,
        )

    def _write_artifacts(
        self,
        artifact_dir: Path,
        raw_md: str | None,
        clean_md: str | None,
        ade_chunks: list[dict] | None,
        toc: object,
        chunk_payload: dict,
    ) -> None:
        self._persistence_service.write_artifacts(
            artifact_dir=artifact_dir,
            raw_md=raw_md,
            clean_md=clean_md,
            ade_chunks=ade_chunks,
            toc=toc,
            chunk_payload=chunk_payload,
        )

    async def _extract_images_best_effort(
        self,
        *,
        pdf_path: Path,
        artifact_dir: Path,
    ) -> None:
        chunks_json_path = artifact_dir / "chunks.json"
        if not chunks_json_path.exists():
            return
        try:
            await self._extract_image_service.extract_toc_images(
                pdf_path=pdf_path,
                chunks_json_path=chunks_json_path,
                output_dir=artifact_dir / "images",
            )
        except Exception:
            logger.warning(
                "Pipeline image extraction failed | file=%s artifact_dir=%s",
                pdf_path.name,
                artifact_dir.as_posix(),
                exc_info=True,
            )

    def _is_spatial_result_usable(self, spatial_result: SpatialPdfPipelineResult) -> bool:
        chapters = []
        if isinstance(spatial_result.chunk_payload, dict):
            maybe_chapters = spatial_result.chunk_payload.get("chapters")
            if isinstance(maybe_chapters, list):
                chapters = maybe_chapters

        if not chapters:
            return False

        stats = self._summarize_chunk_tree(chapters)
        total_nodes = stats["total_nodes"]
        grounded_nodes = stats["grounded_nodes"]
        textual_nodes = stats["textual_nodes"]

        if total_nodes <= 0 or grounded_nodes <= 0 or textual_nodes <= 0:
            return False

        if spatial_result.page_count >= 5:
            grounded_ratio = grounded_nodes / total_nodes
            textual_ratio = textual_nodes / total_nodes
            if grounded_ratio < 0.4 or textual_ratio < 0.25:
                logger.warning(
                    "Spatial validation ratios too low | total=%s grounded=%s textual=%s grounded_ratio=%.3f textual_ratio=%.3f",
                    total_nodes,
                    grounded_nodes,
                    textual_nodes,
                    grounded_ratio,
                    textual_ratio,
                )
                return False
        return True

    def _summarize_chunk_tree(self, nodes: list[dict]) -> dict[str, int]:
        total_nodes = 0
        grounded_nodes = 0
        textual_nodes = 0

        def walk(items: list[dict]) -> None:
            nonlocal total_nodes, grounded_nodes, textual_nodes
            for node in items:
                total_nodes += 1
                if node.get("page_start") is not None and node.get("page_end") is not None:
                    grounded_nodes += 1
                if (
                    isinstance(node.get("content"), str) and node.get("content", "").strip()
                ) or (
                    isinstance(node.get("intro_content"), str) and node.get("intro_content", "").strip()
                ):
                    textual_nodes += 1
                for child_key in (
                    "sections",
                    "subsections",
                    "subsubsections",
                    "subsubsubsections",
                    "subsubsubsubsections",
                    "children",
                ):
                    children = node.get(child_key)
                    if isinstance(children, list) and children:
                        walk(children)

        walk(nodes)
        return {
            "total_nodes": total_nodes,
            "grounded_nodes": grounded_nodes,
            "textual_nodes": textual_nodes,
        }

    async def _openai_json_completion(self, prompt: str) -> dict:
        # Backward-compatible helper used by diagnostics/tests.
        return await self._toc_service.openai_json_completion(
            system_prompt="You are a strict JSON generator. Return valid JSON only.",
            user_prompt=prompt,
        )
