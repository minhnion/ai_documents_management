from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.exceptions import UnprocessableEntityException
from app.services.pipeline.spatial_pdf.processor import SpatialPDFProcessor
from app.services.pipeline.spatial_pdf.schema import ChunkData, DocumentMetadata, TocNode

logger = logging.getLogger(__name__)

_CHILD_KEYS = [
    "sections",
    "subsections",
    "subsubsections",
    "subsubsubsections",
    "subsubsubsubsections",
]


@dataclass(slots=True)
class SpatialPdfPipelineResult:
    toc: list[dict[str, Any]]
    chunk_payload: dict[str, Any]
    page_count: int
    interactive_pdf_path: str | None = None


def _child_key(depth: int) -> str:
    return _CHILD_KEYS[min(depth, len(_CHILD_KEYS) - 1)]


def _strip_leading_title(title: str, content: str) -> str:
    # Preserve the original behaviour from the standalone module.
    return content


def _node_to_toc_dict(node: TocNode, depth: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": node.title,
        "page": node.page,
        "target_y": round(node.target_y, 10),
    }
    child_key = _child_key(depth)
    data[child_key] = [_node_to_toc_dict(child, depth + 1) for child in node.children]
    return data


def _build_chunk_lookup(chunks: list[ChunkData]) -> dict[tuple[str, int], ChunkData]:
    norm = re.compile(r"\s+")
    return {
        (norm.sub(" ", chunk.title).strip().lower(), chunk.start_page): chunk
        for chunk in chunks
    }


def _node_to_chunk_dict(
    node: TocNode,
    chunk_lookup: dict[tuple[str, int], ChunkData],
    depth: int = 0,
) -> dict[str, Any]:
    norm = re.compile(r"\s+")
    key = (norm.sub(" ", node.title).strip().lower(), node.page)
    chunk = chunk_lookup.get(key)

    data: dict[str, Any] = {"title": node.title}
    if chunk:
        content = _strip_leading_title(node.title, chunk.content)
        data.update(
            {
                "page_start": chunk.start_page,
                "page_end": chunk.end_page,
                "start_y": chunk.start_y,
                "end_y": chunk.end_y,
                "content": content,
            }
        )
    else:
        data.update(
            {
                "page_start": node.page,
                "page_end": node.page,
                "start_y": round(node.target_y, 10),
                "end_y": round(node.target_y, 10),
                "content": "",
            }
        )

    child_key = _child_key(depth)
    data[child_key] = [
        _node_to_chunk_dict(child, chunk_lookup, depth + 1) for child in node.children
    ]
    return data


def _build_chunk_payload(
    metadata: DocumentMetadata,
    toc_tree: list[TocNode],
    chunks: list[ChunkData],
) -> dict[str, Any]:
    chunk_lookup = _build_chunk_lookup(chunks)
    chapters = [_node_to_chunk_dict(node, chunk_lookup, depth=0) for node in toc_tree]
    return {
        "title": metadata.title,
        "publisher": metadata.publisher,
        "author": metadata.author,
        "subject": metadata.subject,
        "keywords": metadata.keywords,
        "decision_number": metadata.decision_number,
        "specialty": metadata.specialty,
        "date": metadata.date,
        "isbn_electronic": metadata.isbn_electronic,
        "isbn_print": metadata.isbn_print,
        "issn": metadata.issn,
        "total_pages": metadata.total_pages,
        "source_file": metadata.source_file,
        "chapters": chapters,
    }


def _process_pdf_spatial(pdf_path: Path, artifact_dir: Path) -> SpatialPdfPipelineResult:
    stem = pdf_path.stem
    logger.info("Spatial PDF pipeline start | file=%s", pdf_path.name)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with SpatialPDFProcessor(str(pdf_path)) as processor:
        toc_tree = processor.get_toc_tree()
        toc_payload = [_node_to_toc_dict(node) for node in toc_tree]

        chunks = processor.generate_chunks()
        metadata = processor.extract_metadata(chunks=chunks)
        chunk_payload = _build_chunk_payload(metadata, toc_tree, chunks)

        interactive_pdf_path: str | None = None
        if processor._used_fallback:
            interactive_path = artifact_dir / f"{stem}_with_toc.pdf"
            processor.export_interactive_pdf(str(interactive_path))
            interactive_pdf_path = interactive_path.as_posix()

    return SpatialPdfPipelineResult(
        toc=toc_payload,
        chunk_payload=chunk_payload,
        page_count=metadata.total_pages,
        interactive_pdf_path=interactive_pdf_path,
    )


class SpatialPdfPipelineService:
    async def process_pdf(
        self,
        *,
        pdf_path: Path,
        artifact_dir: Path,
    ) -> SpatialPdfPipelineResult:
        try:
            return await asyncio.to_thread(_process_pdf_spatial, pdf_path, artifact_dir)
        except Exception as exc:
            raise UnprocessableEntityException(f"Spatial PDF pipeline failed: {exc}") from exc
