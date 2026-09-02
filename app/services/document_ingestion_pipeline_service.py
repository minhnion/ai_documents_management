from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException
from app.models.document import Document
from app.services.cost_calculation_service import CostCalculationService, UsageEventInput
from app.services.document_pipeline_selector_service import (
    DocumentPipelineSelection,
    DocumentPipelineSelectorService,
)
from app.services.pipeline import (
    BBoxChunkingService,
    ExtractImageService,
    LandingAIOcrService,
    PipelinePersistenceService,
    TocBuilderService,
)
from app.services.pipeline.landingai_ocr_service import OcrProcessingError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.pipeline.spatial_pdf import SpatialPdfPipelineResult, SpatialPdfPipelineService


class DocumentIngestionPipelineService:
    """End-to-end pipeline orchestrator.

    The implementation is split into dedicated sub-services:
    OCR -> TOC build -> chunking -> image extraction -> persistence.

    Supported pipeline modes:
      - ``dpt3``      : default OCR + span-based chunking (formerly DPT-3)
      - ``spatial_pdf``: pymupdf native pipeline
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

        self._ocr_service = LandingAIOcrService()
        self._toc_service = TocBuilderService()
        self._chunking_service = BBoxChunkingService()
        self._extract_image_service = ExtractImageService()
        self._persistence_service = PipelinePersistenceService(db=db)
        self._pipeline_selector_service = DocumentPipelineSelectorService()
        self._spatial_pdf_service: SpatialPdfPipelineService | None = None

    async def process_document(
        self,
        guideline_id: int,
        version_id: int,
        document: Document,
        *,
        source_job_id: int | None = None,
        actor_user_id: int | None = None,
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

        # ── DPT-3 branch ─────────────────────────────────────────────────────
        if effective_mode == "dpt3":
            self._validate_pipeline_settings("dpt3")
            self._hydrate_core_pipeline_env()
            persist_stats = await self._process_with_dpt3(
                pdf_path=pdf_path,
                artifact_dir=artifact_dir,
                document=document,
                version_id=version_id,
                source_job_id=source_job_id,
                actor_user_id=actor_user_id,
            )

        # ── Spatial PDF branch ───────────────────────────────────────────────
        elif effective_mode == "spatial_pdf":
            self._validate_pipeline_settings("spatial_pdf")
            try:
                spatial_result = await self._process_with_spatial_pdf(
                    pdf_path=pdf_path,
                    artifact_dir=artifact_dir,
                )
            except Exception:
                if requested_mode == "auto":
                    logger.warning(
                        "Spatial pipeline failed in auto mode; falling back to dpt3 | file=%s",
                        pdf_path.name,
                        exc_info=True,
                    )
                    persist_stats = await self._process_with_dpt3(
                        pdf_path=pdf_path,
                        artifact_dir=artifact_dir,
                        document=document,
                        version_id=version_id,
                        source_job_id=source_job_id,
                        actor_user_id=actor_user_id,
                    )
                    effective_mode = "dpt3"
                else:
                    raise
            else:
                if requested_mode == "auto" and not self._is_spatial_result_usable(spatial_result):
                    logger.warning(
                        "Spatial pipeline result deemed low-confidence; falling back to dpt3 | file=%s",
                        pdf_path.name,
                    )
                    persist_stats = await self._process_with_dpt3(
                        pdf_path=pdf_path,
                        artifact_dir=artifact_dir,
                        document=document,
                        version_id=version_id,
                        source_job_id=source_job_id,
                        actor_user_id=actor_user_id,
                    )
                    effective_mode = "dpt3"
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

        # ── Removed / unknown modes ──────────────────────────────────────────
        else:
            raise BadRequestException(
                "DOCUMENT_PIPELINE_MODE must be one of: auto, dpt3, spatial_pdf."
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



    # ── DPT-3 private helpers ────────────────────────────────────────────────

    async def _process_with_dpt3(
        self,
        *,
        pdf_path: Path,
        artifact_dir: Path,
        document: Document,
        version_id: int,
        source_job_id: int | None,
        actor_user_id: int | None,
    ) -> dict[str, int]:
        """Chạy toàn bộ DPT-3 pipeline: OCR → TOC → Chunk → Image → Persist."""
        from app.services.pipeline.landingai_ocr_service import LandingAIOcrService, OcrProcessingError
        from app.services.pipeline.toc_builder_service import TocBuilderService
        from app.services.pipeline.chunking_service import BBoxChunkingService
        from app.services.pipeline.extract_image_service import ExtractImageService

        dpt3_ocr = LandingAIOcrService()
        dpt3_toc = TocBuilderService()
        dpt3_chunk = BBoxChunkingService()
        dpt3_img = ExtractImageService()

        # ── Bước 1: OCR ─────────────────────────────────────────────────────
        try:
            ocr_result = await dpt3_ocr.process_pdf(pdf_path, artifact_dir=artifact_dir)
        except Exception as exc:
            raise OcrProcessingError(str(exc)) from exc

        # Record OCR usage
        for sequence, usage in enumerate(ocr_result.usage_events, start=1):
            await self._record_model_usage(
                UsageEventInput(
                    idempotency_key=f"ingestion:{source_job_id or version_id}:dpt3_ocr:{sequence}",
                    model_type="ocr",
                    operation="ocr_parse",
                    document_id=int(document.document_id),
                    version_id=version_id,
                    job_type="version_ingestion",
                    source_job_id=str(source_job_id or version_id),
                    actor_user_id=actor_user_id or document.created_by_user_id,
                    account_id=document.owner_user_id,
                    status=usage.status,
                    page_count=usage.page_count,
                    request_count=usage.request_count,
                    output_chars=usage.output_chars,
                    usage_source=usage.usage_source,
                    request_sequence=sequence,
                )
            )

        # ── Bước 2: TOC ─────────────────────────────────────────────────────
        source_filename = (
            Path(document.original_filename or "").stem or pdf_path.stem
        ) + "_dpt3.json"
        try:
            toc = await dpt3_toc.build_toc(
                parse_result=ocr_result.parse_result,
                artifact_dir=artifact_dir,
                source_filename=source_filename,
            )
        finally:
            for sequence, usage in enumerate(dpt3_toc.last_usage_events, start=1):
                await self._record_model_usage(
                    UsageEventInput(
                        idempotency_key=f"ingestion:{source_job_id or version_id}:dpt3_toc:{sequence}",
                        model_type="llm",
                        operation="toc_generation",
                        document_id=int(document.document_id),
                        version_id=version_id,
                        job_type="version_ingestion",
                        source_job_id=str(source_job_id or version_id),
                        actor_user_id=actor_user_id or document.created_by_user_id,
                        account_id=document.owner_user_id,
                        status=str(usage.get("status", "succeeded")),
                        request_count=1,
                        input_tokens=usage.get("input_tokens", 0),
                        cached_input_tokens=usage.get("cached_input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        usage_source="provider_reported" if usage.get("input_tokens", 0) or usage.get("output_tokens", 0) else "estimated",
                        request_sequence=sequence,
                    )
                )

        # ── Bước 3: Chunking ────────────────────────────────────────────────
        chunk_payload = await dpt3_chunk.build_chunk_payload(
            parse_result=ocr_result.parse_result,
            toc=toc,
            artifact_dir=artifact_dir,
        )

        # ── Bước 4: Extract images (best-effort) ────────────────────────────
        try:
            await dpt3_img.extract_images(
                pdf_path=pdf_path,
                artifact_dir=artifact_dir,
                output_dir=artifact_dir / "images",
                version_id=version_id,
            )
            # Reload chunk payload vì extract_images đã enrich landing_chunks + image_url
            chunks_path = artifact_dir / "chunks.json"
            if chunks_path.exists():
                chunk_payload = json.loads(chunks_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "DPT-3 extract images failed (best-effort) | file=%s",
                pdf_path.name, exc_info=True,
            )

        # ── Bước 5: Persist DB ──────────────────────────────────────────────
        persist_stats = await self._persist_chunk_payload(
            version_id=version_id,
            document=document,
            chunk_payload=chunk_payload,
            clean_text=None,
            page_count=ocr_result.page_count,
        )
        return persist_stats

    # ── Shared helpers (giữ nguyên từ DPT-2) ─────────────────────────────────

    async def _record_model_usage(self, usage: UsageEventInput) -> None:
        try:
            await CostCalculationService(self.db).record_usage_event(usage)
        except Exception:
            # Accounting must not make a user document impossible to ingest;
            # the error is explicit in logs and can be retried from the request key.
            logger.exception("Unable to persist cost usage event | key=%s", usage.idempotency_key)


    def _validate_pipeline_settings(self, pipeline_mode: str) -> None:
        if pipeline_mode == "spatial_pdf":
            return
        self._hydrate_core_pipeline_env()
        if not settings.LANDINGAI_API_KEY.strip():
            raise BadRequestException("LANDINGAI_API_KEY is required for DPT-3 pipeline.")
        if not settings.OPENAI_API_KEY.strip():
            raise BadRequestException("OPENAI_API_KEY is required for DPT-3 TOC pipeline.")
        if not settings.OPENAI_MODEL_NAME.strip():
            raise BadRequestException("OPENAI_MODEL_NAME is required for DPT-3 TOC pipeline.")

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
        # DPT-3 specific env
        if settings.DPT3_MODEL.strip():
            os.environ["DPT3_MODEL"] = settings.DPT3_MODEL.strip()
        if settings.LANDINGAI_ADE_ENVIRONMENT.strip():
            os.environ["LANDINGAI_ADE_ENVIRONMENT"] = settings.LANDINGAI_ADE_ENVIRONMENT.strip()

    def _resolve_pipeline_mode(self) -> str:
        raw_mode = str(settings.DOCUMENT_PIPELINE_MODE).strip().lower()
        if raw_mode in {"", "auto"}:
            return "auto"
        if raw_mode in {"dpt3", "dpt-3"}:
            return "dpt3"
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
        if requested_mode in {"spatial_pdf", "dpt3"}:
            return DocumentPipelineSelection(
                mode=requested_mode,
                reason="manual_override",
                metrics={},
            )
        raise BadRequestException(
            "DOCUMENT_PIPELINE_MODE must be one of: auto, dpt3, spatial_pdf."
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

