from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestException, NotFoundException
from app.models.chunk import Chunk
from app.models.guideline_version import GuidelineVersion
from app.models.section import Section


@dataclass(slots=True)
class SectionContentUpdate:
    section_id: int
    content: str | None = None
    heading: str | None = None


class GuidelineEditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def bulk_update_section_content(
        self,
        *,
        version_id: int,
        updates: list[SectionContentUpdate],
    ) -> dict[str, object]:
        normalized_updates = self._normalize_updates(updates)
        await self._ensure_version_exists(version_id)

        section_ids = [item.section_id for item in normalized_updates]
        section_map = await self._lock_sections_for_update(
            version_id=version_id,
            section_ids=section_ids,
        )

        for item in normalized_updates:
            if item.content is not None:
                section_map[item.section_id].content = item.content
            if item.heading is not None:
                section_map[item.section_id].heading = item.heading.strip() or None
        await self.db.flush()

        content_updated_section_ids = [
            item.section_id for item in normalized_updates if item.content is not None
        ]
        if not content_updated_section_ids:
            return {
                "version_id": version_id,
                "requested_count": len(normalized_updates),
                "updated_count": len(normalized_updates),
                "updated_section_ids": section_ids,
                "deleted_chunk_count": 0,
                "created_chunk_count": 0,
            }

        deleted_chunk_count = await self._delete_section_chunks(
            version_id=version_id,
            section_ids=content_updated_section_ids,
        )
        created_chunk_count = self._rebuild_section_chunks(
            version_id=version_id,
            sections=[
                section_map[section_id]
                for section_id in content_updated_section_ids
            ],
        )
        await self.db.flush()

        return {
            "version_id": version_id,
            "requested_count": len(normalized_updates),
            "updated_count": len(normalized_updates),
            "updated_section_ids": section_ids,
            "deleted_chunk_count": deleted_chunk_count,
            "created_chunk_count": created_chunk_count,
        }

    def _normalize_updates(
        self,
        updates: list[SectionContentUpdate],
    ) -> list[SectionContentUpdate]:
        if not updates:
            raise BadRequestException("At least one section update is required.")

        normalized: list[SectionContentUpdate] = []
        seen_ids: set[int] = set()
        for item in updates:
            if item.section_id in seen_ids:
                raise BadRequestException(
                    f"Duplicate section_id in request: {item.section_id}."
                )
            has_content = item.content is not None
            has_heading = item.heading is not None
            if not has_content and not has_heading:
                raise BadRequestException(
                    f"section_id={item.section_id} must include content or heading."
                )
            seen_ids.add(item.section_id)
            normalized.append(
                SectionContentUpdate(
                    section_id=int(item.section_id),
                    content=item.content,
                    heading=item.heading,
                )
            )
        return normalized

    async def _ensure_version_exists(self, version_id: int) -> None:
        version = (
            await self.db.execute(
                select(GuidelineVersion.version_id).where(
                    GuidelineVersion.version_id == version_id
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundException("GuidelineVersion", version_id)

    async def _lock_sections_for_update(
        self,
        *,
        version_id: int,
        section_ids: list[int],
    ) -> dict[int, Section]:
        rows = (
            await self.db.execute(
                select(Section)
                .where(Section.version_id == version_id)
                .where(Section.section_id.in_(section_ids))
                .with_for_update()
            )
        ).scalars().all()
        section_map = {section.section_id: section for section in rows}

        missing_ids = sorted(set(section_ids) - set(section_map.keys()))
        if missing_ids:
            missing_value = ", ".join(str(section_id) for section_id in missing_ids)
            raise NotFoundException("Section", missing_value)

        return section_map

    async def _delete_section_chunks(
        self,
        *,
        version_id: int,
        section_ids: list[int],
    ) -> int:
        delete_result = await self.db.execute(
            delete(Chunk)
            .where(Chunk.version_id == version_id)
            .where(Chunk.section_id.in_(section_ids))
        )
        return int(delete_result.rowcount or 0)

    def _rebuild_section_chunks(
        self,
        *,
        version_id: int,
        sections: list[Section],
    ) -> int:
        created_count = 0
        for section in sections:
            section_text = section.content
            if not isinstance(section_text, str) or not section_text.strip():
                continue
            self.db.add(
                Chunk(
                    version_id=version_id,
                    section_id=section.section_id,
                    text=section_text,
                    token_count=len(section_text.split()),
                    page_start=section.page_start,
                    page_end=section.page_end,
                )
            )
            created_count += 1
        return created_count
