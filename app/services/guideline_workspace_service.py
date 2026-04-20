from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.models.document import Document
from app.models.guideline import Guideline
from app.models.guideline_version import GuidelineVersion
from app.models.section import Section


class GuidelineWorkspaceService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_workspace(
        self,
        version_id: int,
        include_full_text: bool = True,
        suspect_threshold: float | None = None,
    ) -> dict[str, object]:
        version_row = (
            await self.db.execute(
                select(GuidelineVersion, Guideline)
                .join(
                    Guideline,
                    Guideline.guideline_id == GuidelineVersion.guideline_id,
                )
                .where(GuidelineVersion.version_id == version_id)
            )
        ).first()
        if version_row is None:
            raise NotFoundException("GuidelineVersion", version_id)

        guideline_version, guideline = version_row

        documents = list(
            (
                await self.db.execute(
                    select(Document)
                    .where(Document.version_id == version_id)
                    .order_by(Document.document_id.asc())
                )
            )
            .scalars()
            .all()
        )
        sections = list(
            (
                await self.db.execute(
                    select(Section)
                    .where(Section.version_id == version_id)
                    .order_by(
                        Section.order_index.asc().nullslast(),
                        Section.section_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

        score_threshold = self._resolve_suspect_threshold(suspect_threshold)
        section_score_map = self._build_section_score_map(sections=sections)

        toc_tree = self._build_toc_tree(
            sections=sections,
            section_score_map=section_score_map,
            score_threshold=score_threshold,
        )
        suspect_section_count = self._count_suspect_sections(toc_tree)

        full_text = None
        if include_full_text:
            full_text = "\n\n".join(
                section.content for section in sections if section.content
            )

        return {
            "guideline": guideline,
            "version": guideline_version,
            "documents": documents,
            "toc": toc_tree,
            "section_count": len(sections),
            "suspect_score_threshold": score_threshold,
            "suspect_section_count": suspect_section_count,
            "full_text": full_text,
        }

    def _build_section_score_map(self, sections: list[Section]) -> dict[int, float]:
        score_map: dict[int, float] = {}
        for section in sections:
            if section.match_score is None:
                continue
            score_map[section.section_id] = float(section.match_score)
        return score_map

    def _resolve_suspect_threshold(self, suspect_threshold: float | None) -> float:
        if suspect_threshold is None:
            return float(settings.SCORE_THRESHOLD)
        return float(suspect_threshold)

    def _build_toc_tree(
        self,
        sections: list[Section],
        section_score_map: dict[int, float],
        score_threshold: float,
    ) -> list[dict[str, object]]:
        node_map: dict[int, dict[str, object]] = {}
        roots: list[dict[str, object]] = []

        for section in sections:
            score = section_score_map.get(section.section_id)
            node_map[section.section_id] = {
                "section_id": section.section_id,
                "version_id": section.version_id,
                "parent_id": section.parent_id,
                "heading": section.heading,
                "section_path": section.section_path,
                "level": section.level,
                "order_index": section.order_index,
                "start_char": section.start_char,
                "end_char": section.end_char,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "start_y": section.start_y,
                "end_y": section.end_y,
                "score": score,
                "is_suspect": bool(section.is_suspect)
                if score is None
                else bool(score < score_threshold),
                "content": section.content,
                "children": [],
            }

        for section in sections:
            current_node = node_map[section.section_id]
            if section.parent_id and section.parent_id in node_map:
                parent_node = node_map[section.parent_id]
                parent_node["children"].append(current_node)
            else:
                roots.append(current_node)

        self._sort_nodes(roots)
        return roots

    def _count_suspect_sections(self, nodes: list[dict[str, object]]) -> int:
        count = 0
        for node in nodes:
            if bool(node.get("is_suspect")):
                count += 1
            children = node.get("children", [])
            if isinstance(children, list) and children:
                count += self._count_suspect_sections(children)
        return count

    def _sort_nodes(self, nodes: list[dict[str, object]]) -> None:
        nodes.sort(
            key=lambda item: (
                item["order_index"] is None,
                item["order_index"] if item["order_index"] is not None else 0,
                item["section_id"],
            )
        )
        for node in nodes:
            children = node.get("children", [])
            if isinstance(children, list) and children:
                self._sort_nodes(children)
