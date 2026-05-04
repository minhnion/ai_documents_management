from __future__ import annotations

import asyncio
import statistics
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
    SAMPLE_PAGES = 5
    STRONG_TEXT_MEDIAN_WORDS = 80
    LOW_TEXT_MEDIAN_WORDS = 20
    MEANINGFUL_WORDS_PER_PAGE = 30
    MEANINGFUL_TEXT_BLOCKS_PER_PAGE = 2
    STRONG_TEXT_RATIO = 0.6
    LOW_TEXT_RATIO = 0.3
    LARGE_IMAGE_COVERAGE = 0.7
    MAX_IMAGE_RATIO_FOR_SPATIAL = 0.4

    async def select_mode(self, pdf_path: Path) -> DocumentPipelineSelection:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(
                executor,
                partial(self._select_mode_sync, pdf_path),
            )

    def _select_mode_sync(self, pdf_path: Path) -> DocumentPipelineSelection:
        with fitz.open(str(pdf_path)) as document:
            sample_pages = min(len(document), self.SAMPLE_PAGES)
            if sample_pages == 0:
                return DocumentPipelineSelection(
                    mode="ocr_llm",
                    reason="empty_pdf",
                    metrics={"sample_pages": 0},
                )

            word_counts: list[int] = []
            text_block_counts: list[int] = []
            image_coverages: list[float] = []
            meaningful_text_pages = 0
            large_image_pages = 0

            for page_index in range(sample_pages):
                page = document[page_index]
                page_area = max(page.rect.width * page.rect.height, 1.0)
                words = page.get_text("words")
                word_count = sum(
                    1
                    for word in words
                    if len(str(word[4]).strip()) >= 2
                )
                word_counts.append(word_count)

                text_blocks = 0
                image_area = 0.0
                raw_dict = page.get_text("dict")
                for block in raw_dict.get("blocks", []):
                    block_type = block.get("type")
                    bbox = block.get("bbox")
                    if (
                        isinstance(bbox, (list, tuple))
                        and len(bbox) == 4
                    ):
                        x0, y0, x1, y1 = bbox
                        block_area = max(float(x1) - float(x0), 0.0) * max(float(y1) - float(y0), 0.0)
                    else:
                        block_area = 0.0

                    if block_type == 0:
                        has_text = any(
                            span.get("text", "").strip()
                            for line in block.get("lines", [])
                            for span in line.get("spans", [])
                        )
                        if has_text:
                            text_blocks += 1
                    elif block_type == 1:
                        image_area += block_area

                text_block_counts.append(text_blocks)
                image_coverage = min(image_area / page_area, 1.0)
                image_coverages.append(image_coverage)

                if (
                    word_count >= self.MEANINGFUL_WORDS_PER_PAGE
                    and text_blocks >= self.MEANINGFUL_TEXT_BLOCKS_PER_PAGE
                ):
                    meaningful_text_pages += 1
                if image_coverage >= self.LARGE_IMAGE_COVERAGE:
                    large_image_pages += 1

            median_words = int(statistics.median(word_counts)) if word_counts else 0
            avg_words = float(sum(word_counts) / len(word_counts)) if word_counts else 0.0
            meaningful_ratio = meaningful_text_pages / sample_pages
            large_image_ratio = large_image_pages / sample_pages
            max_image_coverage = max(image_coverages) if image_coverages else 0.0

            metrics = {
                "sample_pages": sample_pages,
                "median_words": median_words,
                "avg_words": round(avg_words, 2),
                "meaningful_text_ratio": round(meaningful_ratio, 3),
                "large_image_ratio": round(large_image_ratio, 3),
                "max_image_coverage": round(max_image_coverage, 3),
                "word_counts": word_counts,
                "text_block_counts": text_block_counts,
            }

            if (
                median_words >= self.STRONG_TEXT_MEDIAN_WORDS
                and meaningful_ratio >= self.STRONG_TEXT_RATIO
                and large_image_ratio <= self.MAX_IMAGE_RATIO_FOR_SPATIAL
            ):
                return DocumentPipelineSelection(
                    mode="spatial_pdf",
                    reason="strong_text_layer",
                    metrics=metrics,
                )

            if (
                median_words < self.LOW_TEXT_MEDIAN_WORDS
                or meaningful_ratio <= self.LOW_TEXT_RATIO
                or large_image_ratio >= self.STRONG_TEXT_RATIO
            ):
                return DocumentPipelineSelection(
                    mode="ocr_llm",
                    reason="likely_scanned_pdf",
                    metrics=metrics,
                )

            return DocumentPipelineSelection(
                mode="spatial_pdf",
                reason="borderline_text_layer_prefer_spatial",
                metrics=metrics,
            )
