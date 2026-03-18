from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "LandingAIOcrService",
    "MarkdownProcessingService",
    "TocBuilderService",
    "FuzzyChunkingService",
    "PipelinePersistenceService",
    "PAGE_BREAK_MARKER",
]


if TYPE_CHECKING:
    from app.services.pipeline.chunking_service import FuzzyChunkingService
    from app.services.pipeline.markdown_service import MarkdownProcessingService, PAGE_BREAK_MARKER
    from app.services.pipeline.ocr_service import LandingAIOcrService
    from app.services.pipeline.persistence_service import PipelinePersistenceService
    from app.services.pipeline.toc_service import TocBuilderService


def __getattr__(name: str):
    if name == "FuzzyChunkingService":
        from app.services.pipeline.chunking_service import FuzzyChunkingService

        return FuzzyChunkingService
    if name in {"MarkdownProcessingService", "PAGE_BREAK_MARKER"}:
        from app.services.pipeline.markdown_service import MarkdownProcessingService, PAGE_BREAK_MARKER

        exports = {
            "MarkdownProcessingService": MarkdownProcessingService,
            "PAGE_BREAK_MARKER": PAGE_BREAK_MARKER,
        }
        return exports[name]
    if name == "LandingAIOcrService":
        from app.services.pipeline.ocr_service import LandingAIOcrService

        return LandingAIOcrService
    if name == "PipelinePersistenceService":
        from app.services.pipeline.persistence_service import PipelinePersistenceService

        return PipelinePersistenceService
    if name == "TocBuilderService":
        from app.services.pipeline.toc_service import TocBuilderService

        return TocBuilderService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
