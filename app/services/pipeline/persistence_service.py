from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.section import Section
from app.services.pipeline.markdown_service import PAGE_BREAK_MARKER

OCR_MD_FILENAME = "extraction.md"
CLEAN_MD_FILENAME = "extraction_clean.md"
TOC_FILENAME = "toc_structure.json"
CHUNKS_FILENAME = "chunks.json"


class PipelinePersistenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def persist_chunk_payload(
        self,
        *,
        version_id: int,
        document: Document,
        chunk_payload: dict[str, Any],
        clean_text: str,
    ) -> dict[str, int]:
        await self.db.execute(delete(Chunk).where(Chunk.version_id == version_id))
        await self.db.execute(delete(Section).where(Section.version_id == version_id))
        await self.db.flush()

        section_count = 0
        chunk_count = 0
        for idx, chapter in enumerate(chunk_payload.get("chapters", []), start=1):
            inserted_sections, inserted_chunks = await self._persist_section_tree(
                version_id=version_id,
                node=chapter,
                parent_id=None,
                level=1,
                order_index=idx,
                section_path=str(idx),
            )
            section_count += inserted_sections
            chunk_count += inserted_chunks

        document.page_count = self._estimate_page_count(clean_text)
        return {"section_count": section_count, "chunk_count": chunk_count}

    async def _persist_section_tree(
        self,
        *,
        version_id: int,
        node: dict[str, Any],
        parent_id: int | None,
        level: int,
        order_index: int,
        section_path: str,
    ) -> tuple[int, int]:
        section = Section(
            version_id=version_id,
            parent_id=parent_id,
            heading=node.get("title"),
            section_path=section_path,
            level=level,
            order_index=order_index,
            start_char=node.get("start_char"),
            end_char=node.get("end_char"),
            page_start=node.get("page_start"),
            page_end=node.get("page_end"),
            match_score=node.get("match_score"),
            is_suspect=bool(node.get("is_suspect", False)),
            content=node.get("content"),
        )
        self.db.add(section)
        await self.db.flush()

        chunk_count = 0
        section_text = node.get("content")
        if isinstance(section_text, str) and section_text.strip():
            chunk = Chunk(
                version_id=version_id,
                section_id=section.section_id,
                text=section_text,
                token_count=len(section_text.split()),
                page_start=node.get("page_start"),
                page_end=node.get("page_end"),
            )
            self.db.add(chunk)
            chunk_count = 1

        section_count = 1
        for idx, child in enumerate(node.get("sections", []), start=1):
            child_path = f"{section_path}.{idx}"
            child_sections, child_chunks = await self._persist_section_tree(
                version_id=version_id,
                node=child,
                parent_id=section.section_id,
                level=level + 1,
                order_index=idx,
                section_path=child_path,
            )
            section_count += child_sections
            chunk_count += child_chunks

        return section_count, chunk_count

    def write_artifacts(
        self,
        *,
        artifact_dir: Path,
        raw_md: str,
        clean_md: str,
        toc: dict[str, Any],
        chunk_payload: dict[str, Any],
    ) -> None:
        (artifact_dir / OCR_MD_FILENAME).write_text(raw_md, encoding="utf-8")
        (artifact_dir / CLEAN_MD_FILENAME).write_text(clean_md, encoding="utf-8")
        (artifact_dir / TOC_FILENAME).write_text(
            json.dumps(toc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (artifact_dir / CHUNKS_FILENAME).write_text(
            json.dumps(chunk_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _estimate_page_count(self, clean_text: str) -> int:
        return clean_text.count(PAGE_BREAK_MARKER) + 1
