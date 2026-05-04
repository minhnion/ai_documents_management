from __future__ import annotations

import asyncio
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
    """

    SAMPLE_PAGES = 5
    MIN_ACTIONABLE_OUTLINE_RATIO = 0.7
    MIN_OUTLINE_ENTRIES = 1
    MIN_EXTRACTABLE_WORDS = 30

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
