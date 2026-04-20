from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import Document
from app.models.section import Section
from app.services.pipeline.clean_markdown_service import PAGE_BREAK_MARKER

OCR_MD_FILENAME = 'extraction.md'
CLEAN_MD_FILENAME = 'extraction_clean.md'
TOC_FILENAME = 'toc_structure.json'
CHUNKS_FILENAME = 'chunks.json'

_CHILD_KEYS = (
    'chapters',
    'sections',
    'subsections',
    'subsubsections',
    'subsubsubsections',
    'children',
)


class PipelinePersistenceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def persist_chunk_payload(
        self,
        *,
        version_id: int,
        document: Document,
        chunk_payload: dict[str, Any],
        clean_text: str | None,
        page_count: int | None = None,
    ) -> dict[str, int]:
        await self.db.execute(delete(Section).where(Section.version_id == version_id))
        await self.db.flush()

        section_count = 0
        for index, chapter in enumerate(self._extract_children(chunk_payload), start=1):
            inserted = await self._persist_section_tree(
                version_id=version_id,
                node=chapter,
                parent_id=None,
                level=1,
                order_index=index,
                section_path=str(index),
            )
            section_count += inserted

        document.page_count = page_count if page_count is not None else self._estimate_page_count(clean_text)
        return {
            'section_count': section_count,
            'chunk_count': 0,
        }

    async def _persist_section_tree(
        self,
        *,
        version_id: int,
        node: dict[str, Any],
        parent_id: int | None,
        level: int,
        order_index: int,
        section_path: str,
    ) -> int:
        score = node.get('match_score')
        is_suspect = False
        try:
            if score is not None:
                is_suspect = float(score) < float(settings.SCORE_THRESHOLD)
        except Exception:
            is_suspect = False

        section = Section(
            version_id=version_id,
            parent_id=parent_id,
            heading=node.get('title'),
            section_path=section_path,
            level=level,
            order_index=order_index,
            start_char=node.get('start_char'),
            end_char=node.get('end_char'),
            page_start=node.get('page_start'),
            page_end=node.get('page_end'),
            start_y=node.get('start_y'),
            end_y=node.get('end_y'),
            match_score=score,
            is_suspect=is_suspect,
            content=node.get('content'),
        )
        self.db.add(section)
        await self.db.flush()

        section_count = 1
        for index, child in enumerate(self._extract_children(node), start=1):
            child_path = f'{section_path}.{index}'
            inserted = await self._persist_section_tree(
                version_id=version_id,
                node=child,
                parent_id=section.section_id,
                level=level + 1,
                order_index=index,
                section_path=child_path,
            )
            section_count += inserted
        return section_count

    def write_artifacts(
        self,
        *,
        artifact_dir: Path,
        raw_md: str | None,
        clean_md: str | None,
        toc: Any,
        chunk_payload: dict[str, Any],
    ) -> None:
        if raw_md is not None:
            (artifact_dir / OCR_MD_FILENAME).write_text(raw_md, encoding='utf-8')
        if clean_md is not None:
            (artifact_dir / CLEAN_MD_FILENAME).write_text(clean_md, encoding='utf-8')
        (artifact_dir / TOC_FILENAME).write_text(
            json.dumps(toc, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        (artifact_dir / CHUNKS_FILENAME).write_text(
            json.dumps(chunk_payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def _extract_children(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        for key in _CHILD_KEYS:
            value = node.get(key)
            if not isinstance(value, list):
                continue
            for child in value:
                if isinstance(child, dict) and child.get('title'):
                    children.append(child)
        return children

    def _estimate_page_count(self, clean_text: str | None) -> int:
        if not clean_text:
            return 0
        return clean_text.count(PAGE_BREAK_MARKER) + 1
