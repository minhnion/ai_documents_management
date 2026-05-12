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

        pipeline_mode_used = self._resolve_pipeline_mode_used(documents=documents)
        positioning_mode = self._resolve_positioning_mode(
            pipeline_mode_used=pipeline_mode_used
        )

        return {
            "guideline": guideline,
            "version": guideline_version,
            "documents": documents,
            "pipeline_mode_used": pipeline_mode_used,
            "positioning_mode": positioning_mode,
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
            pdf_position = self._build_pdf_viewer_position(section)
            node_map[section.section_id] = {
                "section_id": section.section_id,
                "version_id": section.version_id,
                "parent_id": section.parent_id,
                "heading": section.heading,
                "node_id": section.node_id,
                "section_path": section.section_path,
                "level": section.level,
                "order_index": section.order_index,
                "start_char": section.start_char,
                "end_char": section.end_char,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "start_y": section.start_y,
                "end_y": section.end_y,
                **pdf_position,
                "score": score,
                "is_suspect": bool(section.is_suspect)
                if score is None
                else bool(score < score_threshold),
                "content": section.content,
                "intro_content": section.intro_content,
                "heading_bbox": section.heading_bbox,
                "content_bboxes": section.content_bboxes or [],
                "landing_chunks": section.landing_chunks or [],
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
        self._disambiguate_shared_pdf_anchors(roots)
        return roots

    def _build_pdf_viewer_position(self, section: Section) -> dict[str, float | int | None]:
        """Return PDF-native anchors for the viewer without changing core fields.

        OCR chunking ``page_start`` is derived from markdown page breaks, while
        ADE bboxes are native PDF pages (0-indexed). The viewer should use the
        bbox coordinates whenever present so old ingestions and OCR page drift
        still land on the physical PDF page.
        """
        heading_bbox = section.heading_bbox if isinstance(section.heading_bbox, dict) else None
        content_bboxes = section.content_bboxes if isinstance(section.content_bboxes, list) else []

        first_content_bbox = next(
            (bbox for bbox in content_bboxes if isinstance(bbox, dict)),
            None,
        )

        page_start = self._bbox_pdf_page(heading_bbox)
        if page_start is None:
            page_start = self._bbox_pdf_page(first_content_bbox)

        start_y = self._bbox_float(heading_bbox, "top")
        if start_y is None:
            start_y = self._bbox_float(first_content_bbox, "top")

        content_pages = [
            page
            for bbox in content_bboxes
            if isinstance(bbox, dict)
            for page in [self._bbox_pdf_page(bbox)]
            if page is not None
        ]
        page_end = max(content_pages) if content_pages else page_start
        end_y = self._resolve_pdf_end_y(
            page_end=page_end,
            content_bboxes=content_bboxes,
            heading_bbox=heading_bbox,
        )

        return {
            "pdf_page_start": page_start if page_start is not None else section.page_start,
            "pdf_page_end": page_end if page_end is not None else section.page_end,
            "pdf_start_y": start_y if start_y is not None else section.start_y,
            "pdf_end_y": end_y if end_y is not None else section.end_y,
        }

    def _bbox_pdf_page(self, bbox: dict[str, object] | None) -> int | None:
        if not isinstance(bbox, dict):
            return None
        page = bbox.get("page")
        if isinstance(page, int):
            return page + 1
        return None

    def _bbox_float(self, bbox: dict[str, object] | None, key: str) -> float | None:
        if not isinstance(bbox, dict):
            return None
        value = bbox.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _resolve_pdf_end_y(
        self,
        *,
        page_end: int | None,
        content_bboxes: list[object],
        heading_bbox: dict[str, object] | None,
    ) -> float | None:
        candidates = [
            bbox for bbox in content_bboxes
            if isinstance(bbox, dict)
            and (
                page_end is None
                or self._bbox_pdf_page(bbox) == page_end
            )
        ]
        if not candidates:
            candidates = [bbox for bbox in content_bboxes if isinstance(bbox, dict)]
        if candidates:
            return self._bbox_float(candidates[-1], "bottom")
        return self._bbox_float(heading_bbox, "bottom")

    def _disambiguate_shared_pdf_anchors(self, roots: list[dict[str, object]]) -> None:
        """Spread viewer anchors for headings merged into the same ADE bbox.

        LandingAI can emit several markdown headings inside one text chunk; the
        core correctly maps them to one bbox, but a PDF scroll target then needs
        distinct y positions. This is viewer-only enrichment and leaves the
        core page/bbox fields intact.
        """
        groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
        for node in self._iter_nodes(roots):
            bbox = node.get("heading_bbox")
            if not isinstance(bbox, dict):
                continue
            key = (
                node.get("pdf_page_start"),
                bbox.get("left"),
                bbox.get("top"),
                bbox.get("right"),
                bbox.get("bottom"),
            )
            groups.setdefault(key, []).append(node)

        for nodes in groups.values():
            if len(nodes) <= 1:
                continue

            first_bbox = nodes[0].get("heading_bbox")
            if not isinstance(first_bbox, dict):
                continue
            top = self._bbox_float(first_bbox, "top")
            bottom = self._bbox_float(first_bbox, "bottom")
            if top is None or bottom is None or bottom <= top:
                continue

            weights = [self._node_text_weight(node) for node in nodes]
            positive_total = sum(weight for weight in weights if weight > 0)
            if positive_total <= 0:
                weights = [1.0 for _ in nodes]
                positive_total = float(len(nodes))

            span = bottom - top
            starts: list[float] = []
            cursor = 0.0
            for weight in weights:
                starts.append(min(bottom, top + span * (cursor / positive_total)))
                if weight > 0:
                    cursor += weight

            for index, node in enumerate(nodes):
                start_y = starts[index]
                next_start = starts[index + 1] if index + 1 < len(starts) else bottom
                start_value = round(max(top, min(bottom, start_y)), 6)
                node["pdf_start_y"] = start_value
                node["pdf_end_y"] = round(max(start_value, min(bottom, next_start)), 6)

    def _node_text_weight(self, node: dict[str, object]) -> float:
        text = node.get("content") or node.get("intro_content")
        if isinstance(text, str):
            stripped = text.strip()
            if stripped:
                return float(len(stripped))
        return 0.0

    def _iter_nodes(self, nodes: list[dict[str, object]]):
        for node in nodes:
            yield node
            children = node.get("children")
            if isinstance(children, list):
                yield from self._iter_nodes(children)

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

    def _resolve_pipeline_mode_used(self, *, documents: list[Document]) -> str | None:
        for document in documents:
            mode = (document.pipeline_mode_used or "").strip().lower()
            if mode:
                return mode
        return None

    def _resolve_positioning_mode(self, *, pipeline_mode_used: str | None) -> str:
        if pipeline_mode_used == "spatial_pdf":
            return "spatial_heading_anchor"
        return "page_range"
