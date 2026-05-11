from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "LandingAIOcrService",
    "MarkdownProcessingService",
    "TocBuilderService",
    "BBoxChunkingService",
    "FuzzyChunkingService",
    "ExtractImageService",
    "PipelinePersistenceService",
    "PAGE_BREAK_MARKER",
]


if TYPE_CHECKING:
    from app.services.pipeline.chunking_service import BBoxChunkingService
    from app.services.pipeline.extract_image_service import ExtractImageService
    from app.services.pipeline.markdown_service import MarkdownProcessingService, PAGE_BREAK_MARKER
    from app.services.pipeline.persistence_service import PipelinePersistenceService
    from app.services.pipeline.toc_builder_service import TocBuilderService
    from app.services.pipeline.landingai_ocr_service import LandingAIOcrService


def __getattr__(name: str):
    if name in {"BBoxChunkingService", "FuzzyChunkingService"}:
        # ``FuzzyChunkingService`` is kept as a backward-compatible alias —
        # callers continue to import the old name while the chunking core
        # ships a single ``BBoxChunkingService`` class.
        from app.services.pipeline.chunking_service import BBoxChunkingService

        return BBoxChunkingService
    if name in {"MarkdownProcessingService", "PAGE_BREAK_MARKER"}:
        from app.services.pipeline.markdown_service import MarkdownProcessingService, PAGE_BREAK_MARKER

        return {
            "MarkdownProcessingService": MarkdownProcessingService,
            "PAGE_BREAK_MARKER": PAGE_BREAK_MARKER,
        }[name]
    if name == "PipelinePersistenceService":
        from app.services.pipeline.persistence_service import PipelinePersistenceService

        return PipelinePersistenceService
    if name == "TocBuilderService":
        from app.services.pipeline.toc_builder_service import TocBuilderService

        return TocBuilderService
    if name == "LandingAIOcrService":
        from app.services.pipeline.landingai_ocr_service import LandingAIOcrService

        return LandingAIOcrService
    if name == "ExtractImageService":
        from app.services.pipeline.extract_image_service import ExtractImageService

        return ExtractImageService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
