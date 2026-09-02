from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

PAGE_BREAK = "<!-- PAGE BREAK -->"
LEAF_TYPES = frozenset({"text", "table", "figure", "marginalia", "logo", "card", "scan_code", "attestation"})
NOISE_TYPES = frozenset({"marginalia", "logo", "scan_code", "attestation"})
MEDIA_TYPES = frozenset({"table", "figure"})


@dataclass
class GroundingBox:
    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def from_json(cls, raw: list[float]) -> "GroundingBox":
        return cls(left=raw[0], top=raw[1], right=raw[2], bottom=raw[3])

    def to_json(self) -> list[float]:
        return [self.left, self.top, self.right, self.bottom]

    def normalized(self, width: float, height: float) -> dict[str, float]:
        return {
            "left": round(self.left / width, 6),
            "top": round(self.top / height, 6),
            "right": round(self.right / width, 6),
            "bottom": round(self.bottom / height, 6),
        }


@dataclass
class GroundingPart:
    span: tuple[int, int]
    box: GroundingBox

    @classmethod
    def from_json(cls, raw: dict) -> "GroundingPart":
        return cls(span=tuple(raw["span"]), box=GroundingBox.from_json(raw["box"]))

    def to_json(self) -> dict:
        return {"span": list(self.span), "box": self.box.to_json()}


@dataclass
class ElementGrounding:
    page: int
    span: tuple[int, int]
    box: GroundingBox
    parts: list[GroundingPart] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict) -> "ElementGrounding":
        return cls(
            page=raw["page"],
            span=tuple(raw["span"]),
            box=GroundingBox.from_json(raw["box"]),
            parts=[GroundingPart.from_json(p) for p in (raw.get("parts") or [])],
        )

    def to_json(self) -> dict:
        return {
            "page": self.page,
            "span": list(self.span),
            "box": self.box.to_json(),
            "parts": [p.to_json() for p in self.parts],
        }


@dataclass
class StructureNode:
    type: str
    id: str | None = None
    span: tuple[int, int] | None = None
    page: int | None = None
    width: float | None = None
    height: float | None = None
    dpi: int | None = None
    unit: str | None = None
    status: str | None = None
    reason: str | None = None
    row: int | None = None
    col: int | None = None
    rowspan: int | None = None
    colspan: int | None = None
    children: list["StructureNode"] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict) -> "StructureNode":
        span = raw.get("span")
        return cls(
            type=raw["type"],
            id=raw.get("id"),
            span=tuple(span) if span is not None else None,
            page=raw.get("page"),
            width=raw.get("width"),
            height=raw.get("height"),
            dpi=raw.get("dpi"),
            unit=raw.get("unit"),
            status=raw.get("status"),
            reason=raw.get("reason"),
            row=raw.get("row"),
            col=raw.get("col"),
            rowspan=raw.get("rowspan"),
            colspan=raw.get("colspan"),
            children=[cls.from_json(c) for c in (raw.get("children") or [])],
        )

    def to_json(self) -> dict:
        return {
            "type": self.type,
            "id": self.id,
            "span": list(self.span) if self.span is not None else None,
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "unit": self.unit,
            "status": self.status,
            "reason": self.reason,
            "row": self.row,
            "col": self.col,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "children": [c.to_json() for c in self.children],
        }


@dataclass
class ParseMetadata:
    job_id: str
    filename: str
    version: str
    page_count: int
    failed_pages: list[int]
    duration_ms: int
    markdown_chars: int
    credit_usage: float

    @classmethod
    def from_json(cls, raw: dict) -> "ParseMetadata":
        return cls(
            job_id=raw.get("job_id", ""),
            filename=raw.get("filename", ""),
            version=raw.get("version") or "",
            page_count=raw.get("page_count", 0),
            failed_pages=list(raw.get("failed_pages") or []),
            duration_ms=raw.get("duration_ms", 0),
            markdown_chars=raw.get("markdown_chars", 0),
            credit_usage=raw.get("credit_usage", 0.0),
        )

    def to_json(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "version": self.version,
            "page_count": self.page_count,
            "failed_pages": list(self.failed_pages),
            "duration_ms": self.duration_ms,
            "markdown_chars": self.markdown_chars,
            "credit_usage": self.credit_usage,
        }


@dataclass
class ParseResult:
    markdown: str
    structure: StructureNode
    grounding: dict[str, ElementGrounding]
    metadata: ParseMetadata

    @classmethod
    def from_json(cls, raw: dict) -> "ParseResult":
        return cls(
            markdown=raw["markdown"],
            structure=StructureNode.from_json(raw["structure"]),
            grounding={k: ElementGrounding.from_json(v) for k, v in (raw.get("grounding") or {}).items()},
            metadata=ParseMetadata.from_json(raw["metadata"]),
        )

    @classmethod
    def load(cls, path: Path) -> "ParseResult":
        import json
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def to_json(self) -> dict:
        return {
            "markdown": self.markdown,
            "structure": self.structure.to_json(),
            "grounding": {k: v.to_json() for k, v in self.grounding.items()},
            "metadata": self.metadata.to_json(),
        }

    def text_of(self, node: StructureNode) -> str:
        if node.span is None:
            return ""
        return self.markdown[node.span[0]:node.span[1]]


@dataclass
class LeafElement:
    index: int
    id: str
    type: str
    page: int
    span: tuple[int, int]
    box: GroundingBox | None
    parts: list[GroundingPart]
    page_width: float | None
    page_height: float | None

    def normalized_bbox(self) -> dict | None:
        if self.box is None or not self.page_width or not self.page_height:
            return None
        return {"page": self.page, **self.box.normalized(self.page_width, self.page_height)}

    def normalized_bbox_for_range(self, start: int, end: int) -> dict | None:
        if not self.page_width or not self.page_height:
            return None
        matching = [p.box for p in self.parts if p.span[0] < end and p.span[1] > start]
        if not matching:
            return self.normalized_bbox()
        box = GroundingBox(
            left=min(b.left for b in matching),
            top=min(b.top for b in matching),
            right=max(b.right for b in matching),
            bottom=max(b.bottom for b in matching),
        )
        return {"page": self.page, **box.normalized(self.page_width, self.page_height)}


@dataclass
class TableCellElement:
    id: str
    span: tuple[int, int]
    row: int | None
    col: int | None
    box: GroundingBox | None
    page: int | None
    page_width: float | None
    page_height: float | None

    def normalized_bbox(self) -> dict | None:
        if self.box is None or not self.page_width or not self.page_height:
            return None
        return {"page": self.page, **self.box.normalized(self.page_width, self.page_height)}


def index_table_cells(result: ParseResult) -> dict[str, list[TableCellElement]]:
    index: dict[str, list[TableCellElement]] = {}

    def walk(node: StructureNode, page_ctx: StructureNode | None) -> None:
        if node.type == "page":
            page_ctx = node
        if node.type == "table" and node.id:
            cells: list[TableCellElement] = []
            for child in node.children:
                if child.type != "table_cell" or child.span is None:
                    continue
                grounding = result.grounding.get(child.id) if child.id else None
                cells.append(TableCellElement(
                    id=child.id or "",
                    span=child.span,
                    row=child.row,
                    col=child.col,
                    box=grounding.box if grounding is not None else None,
                    page=grounding.page if grounding is not None else (page_ctx.page if page_ctx is not None else None),
                    page_width=page_ctx.width if page_ctx is not None else None,
                    page_height=page_ctx.height if page_ctx is not None else None,
                ))
            if cells:
                index[node.id] = sorted(cells, key=lambda c: c.span[0])
            return
        for child in node.children:
            walk(child, page_ctx)

    walk(result.structure, None)
    return index


def flatten_leaves(result: ParseResult) -> list[LeafElement]:
    leaves: list[LeafElement] = []

    def walk(node: StructureNode, page_ctx: StructureNode | None) -> None:
        if node.type == "page":
            page_ctx = node
        elif node.type in LEAF_TYPES:
            grounding = result.grounding.get(node.id) if node.id is not None else None
            page = grounding.page if grounding is not None else (page_ctx.page if page_ctx is not None else 0)
            leaves.append(LeafElement(
                index=len(leaves),
                id=node.id or "",
                type=node.type,
                page=page,
                span=node.span or (0, 0),
                box=grounding.box if grounding is not None else None,
                parts=grounding.parts if grounding is not None else [],
                page_width=page_ctx.width if page_ctx is not None else None,
                page_height=page_ctx.height if page_ctx is not None else None,
            ))
            return
        for child in node.children:
            walk(child, page_ctx)

    walk(result.structure, None)
    return leaves


def page_count(result: ParseResult) -> int:
    return sum(1 for c in result.structure.children if c.type == "page")


def iter_pages(result: ParseResult) -> Iterator[StructureNode]:
    for c in result.structure.children:
        if c.type == "page":
            yield c
