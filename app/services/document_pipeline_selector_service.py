from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import fitz


@dataclass(slots=True)
class DocumentPipelineSelection:
    mode: str
    reason: str
    metrics: dict[str, Any]


class DocumentPipelineSelectorService:
    """Decide between the spatial and OCR pipelines by inspecting the PDF.

    Rule (per partner spec — "Native PDF"):
      1. The PDF Root must contain an /Outlines object.
      2. The outline tree must expose entries with actionable /Dest
         (a destination object pointing at a page or coordinate).
      3. The document must carry a vector text layer that is actually
         extractable (i.e. the PDF is not a pure scan).

    Anything missing one of these characteristics falls back to ocr_llm.
    PDFs with extractable text but weak document structure also fall back to
    OCR, because spatial chunking needs reliable anchors, not just selectable
    text.
    """

    SAMPLE_PAGES = 5
    TOC_SCAN_PAGES = 8
    MIN_ACTIONABLE_OUTLINE_RATIO = 0.7
    MIN_OUTLINE_ENTRIES = 1
    MIN_EXTRACTABLE_WORDS = 30
    MIN_RICH_OUTLINE_ENTRIES = 8
    MIN_RICH_OUTLINE_DENSITY = 0.12
    MIN_VISIBLE_TOC_ENTRIES = 4
    WEAK_OUTLINE_MAX_DEPTH = 1
    WEAK_OUTLINE_MAX_ENTRIES = 6
    POOR_AUTHORING_MARKERS = ("coreldraw",)

    async def select_mode(self, pdf_path: Path) -> DocumentPipelineSelection:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(
                executor,
                partial(self._select_mode_sync, pdf_path),
            )

    def _select_mode_sync(self, pdf_path: Path) -> DocumentPipelineSelection:
        with fitz.open(str(pdf_path)) as document:
            page_count = len(document)
            if page_count == 0:
                return DocumentPipelineSelection(
                    mode="ocr_llm",
                    reason="empty_pdf",
                    metrics={"page_count": 0},
                )

            raw_toc = self._safe_get_toc(document)
            has_outline_root = self._has_outline_root(document)
            outline_entries, actionable_outline_entries, outline_depth = self._summarize_outline_entries(raw_toc)
            actionable_outline_ratio = (
                actionable_outline_entries / outline_entries
                if outline_entries > 0
                else 0.0
            )
            has_native_outline_tree = (
                has_outline_root
                and outline_entries >= self.MIN_OUTLINE_ENTRIES
                and actionable_outline_ratio >= self.MIN_ACTIONABLE_OUTLINE_RATIO
            )

            visible_toc_metrics = self._summarize_visible_toc(document)
            has_visible_toc = bool(visible_toc_metrics["has_visible_toc"])
            has_struct_tree_root = self._has_struct_tree_root(document)
            metadata = document.metadata or {}
            poor_authoring_metadata = self._has_poor_authoring_metadata(metadata)
            has_rich_outline = self._has_rich_outline(
                page_count=page_count,
                outline_entries=outline_entries,
                outline_depth=outline_depth,
            )
            weak_document_structure = self._has_weak_document_structure(
                page_count=page_count,
                outline_entries=outline_entries,
                outline_depth=outline_depth,
                has_native_outline_tree=has_native_outline_tree,
                has_visible_toc=has_visible_toc,
                has_struct_tree_root=has_struct_tree_root,
                has_rich_outline=has_rich_outline,
                poor_authoring_metadata=poor_authoring_metadata,
            )

            sample_pages = min(page_count, self.SAMPLE_PAGES)
            word_counts: list[int] = []
            for page_index in range(sample_pages):
                page = document[page_index]
                words = page.get_text("words")
                word_counts.append(
                    sum(1 for word in words if len(str(word[4]).strip()) >= 2)
                )
            total_sample_words = sum(word_counts)
            text_layer_extractable = total_sample_words >= self.MIN_EXTRACTABLE_WORDS

            metrics = {
                "page_count": page_count,
                "sample_pages": sample_pages,
                "has_outline_root": has_outline_root,
                "outline_entries": outline_entries,
                "actionable_outline_entries": actionable_outline_entries,
                "actionable_outline_ratio": round(actionable_outline_ratio, 3),
                "outline_depth": outline_depth,
                "has_native_outline_tree": has_native_outline_tree,
                "has_visible_toc": has_visible_toc,
                "visible_toc_entry_count": visible_toc_metrics["entry_count"],
                "visible_toc_heading_hits": visible_toc_metrics["heading_hits"],
                "has_struct_tree_root": has_struct_tree_root,
                "poor_authoring_metadata": poor_authoring_metadata,
                "has_rich_outline": has_rich_outline,
                "weak_document_structure": weak_document_structure,
                "creator": metadata.get("creator"),
                "producer": metadata.get("producer"),
                "word_counts": word_counts,
                "total_sample_words": total_sample_words,
                "text_layer_extractable": text_layer_extractable,
            }

            if not has_native_outline_tree:
                return DocumentPipelineSelection(
                    mode="ocr_llm",
                    reason="missing_native_outline_tree",
                    metrics=metrics,
                )

            if not text_layer_extractable:
                return DocumentPipelineSelection(
                    mode="ocr_llm",
                    reason="native_outline_but_text_layer_unextractable",
                    metrics=metrics,
                )

            if weak_document_structure:
                return DocumentPipelineSelection(
                    mode="ocr_llm",
                    reason="extractable_text_but_weak_document_structure",
                    metrics=metrics,
                )

            return DocumentPipelineSelection(
                mode="spatial_pdf",
                reason="native_outline_tree_and_extractable_text",
                metrics=metrics,
            )

    def _safe_get_toc(self, document: fitz.Document) -> list[Any]:
        try:
            raw_toc = document.get_toc(simple=False)
        except Exception:
            return []
        return raw_toc if isinstance(raw_toc, list) else []

    def _has_outline_root(self, document: fitz.Document) -> bool:
        pdf_catalog = getattr(document, "pdf_catalog", None)
        xref_get_key = getattr(document, "xref_get_key", None)
        if not callable(pdf_catalog) or not callable(xref_get_key):
            return False

        try:
            catalog_xref = pdf_catalog()
            if not catalog_xref:
                return False
            key = xref_get_key(catalog_xref, "Outlines")
        except Exception:
            return False

        if not isinstance(key, tuple) or len(key) != 2:
            return False

        key_type, key_value = key
        if str(key_type).strip().lower() == "null":
            return False
        return bool(str(key_value).strip())

    def _has_struct_tree_root(self, document: fitz.Document) -> bool:
        pdf_catalog = getattr(document, "pdf_catalog", None)
        xref_get_key = getattr(document, "xref_get_key", None)
        if not callable(pdf_catalog) or not callable(xref_get_key):
            return False

        try:
            catalog_xref = pdf_catalog()
            if not catalog_xref:
                return False
            key = xref_get_key(catalog_xref, "StructTreeRoot")
        except Exception:
            return False

        if not isinstance(key, tuple) or len(key) != 2:
            return False

        key_type, key_value = key
        if str(key_type).strip().lower() == "null":
            return False
        return bool(str(key_value).strip())

    def _summarize_visible_toc(self, document: fitz.Document) -> dict[str, int | bool]:
        heading_hits = 0
        entry_count = 0
        scan_pages = min(len(document), self.TOC_SCAN_PAGES)

        for page_index in range(scan_pages):
            try:
                page_text = document[page_index].get_text("text")
            except Exception:
                continue

            lines = [line.strip() for line in page_text.splitlines() if line.strip()]
            if any(self._is_visible_toc_heading(line) for line in lines):
                heading_hits += 1
            entry_count += sum(1 for line in lines if self._is_visible_toc_entry(line))

        return {
            "has_visible_toc": heading_hits > 0 and entry_count >= self.MIN_VISIBLE_TOC_ENTRIES,
            "heading_hits": heading_hits,
            "entry_count": entry_count,
        }

    def _is_visible_toc_heading(self, line: str) -> bool:
        normalized = " ".join(line.lower().split())
        return normalized in {
            "mục lục",
            "muc luc",
            "table of contents",
            "contents",
        }

    def _is_visible_toc_entry(self, line: str) -> bool:
        stripped = " ".join(line.split())
        if len(stripped) < 8:
            return False

        if "." * 3 in stripped and stripped[-1].isdigit():
            return True

        return bool(re.search(r"^(?:\d+(?:\.\d+)*|[A-Z]\.?)\s+.+\s+\d{1,4}$", stripped))

    def _has_poor_authoring_metadata(self, metadata: dict[str, Any]) -> bool:
        haystack = " ".join(
            str(metadata.get(key) or "").lower()
            for key in ("creator", "producer", "format")
        )
        return any(marker in haystack for marker in self.POOR_AUTHORING_MARKERS)

    def _has_rich_outline(
        self,
        *,
        page_count: int,
        outline_entries: int,
        outline_depth: int,
    ) -> bool:
        if outline_entries < self.MIN_RICH_OUTLINE_ENTRIES:
            return False
        if outline_depth >= 2:
            return True
        return (outline_entries / max(page_count, 1)) >= self.MIN_RICH_OUTLINE_DENSITY

    def _has_weak_document_structure(
        self,
        *,
        page_count: int,
        outline_entries: int,
        outline_depth: int,
        has_native_outline_tree: bool,
        has_visible_toc: bool,
        has_struct_tree_root: bool,
        has_rich_outline: bool,
        poor_authoring_metadata: bool,
    ) -> bool:
        if not has_native_outline_tree:
            return False

        # Some PDF generators (notably CorelDRAW in document-16.pdf) can leave
        # enough bookmark metadata for the basic outline check to pass while the
        # actual document has no trustworthy visible TOC/semantic structure.
        # In that family, OCR+LLM is more stable than spatial heading heuristics.
        if poor_authoring_metadata and not has_visible_toc:
            return True

        if has_visible_toc or has_struct_tree_root or has_rich_outline:
            return False

        weak_outline_limit = min(
            self.WEAK_OUTLINE_MAX_ENTRIES,
            max(3, page_count // 8),
        )
        return (
            outline_depth <= self.WEAK_OUTLINE_MAX_DEPTH
            and outline_entries <= weak_outline_limit
        )

    def _summarize_outline_entries(self, raw_toc: list[Any]) -> tuple[int, int, int]:
        outline_entries = 0
        actionable_outline_entries = 0
        outline_depth = 0

        for entry in raw_toc:
            if not isinstance(entry, (list, tuple)) or len(entry) < 4:
                continue

            level, title, page, destination = entry[:4]
            if not str(title).strip():
                continue

            outline_entries += 1
            try:
                outline_depth = max(outline_depth, int(level))
            except (TypeError, ValueError):
                pass

            if self._entry_has_actionable_destination(page=page, destination=destination):
                actionable_outline_entries += 1

        return outline_entries, actionable_outline_entries, outline_depth

    def _entry_has_actionable_destination(self, *, page: Any, destination: Any) -> bool:
        if isinstance(page, int) and page > 0:
            return True

        if not isinstance(destination, dict):
            return False

        target = destination.get("to")
        if target is not None:
            return True

        dest_page = destination.get("page")
        if isinstance(dest_page, int) and dest_page >= 0:
            return True

        if destination.get("kind") == getattr(fitz, "LINK_GOTO", None):
            if any(destination.get(key) is not None for key in ("xref", "nameddest", "file", "uri")):
                return True

        return False
