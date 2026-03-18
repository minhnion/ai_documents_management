from __future__ import annotations

from app.services.pipeline.chunking_service import _find_body_start
from app.services.pipeline.clean_markdown_service import (
    PAGE_BREAK_MARKER,
    clean_markdown,
)
from app.services.pipeline.toc_service import (
    extract_heading_outline,
    get_pages,
    has_toc_page,
)


class MarkdownProcessingService:
    def clean_markdown(self, raw_text: str) -> str:
        return clean_markdown(raw_text)

    def extract_first_pages(self, text: str, max_pages: int) -> str:
        return get_pages(text, max_pages)

    def has_toc_page(self, text: str, max_pages: int) -> bool:
        return has_toc_page(text, max_pages)

    def find_body_start(self, text: str) -> int:
        return _find_body_start(text)

    def extract_heading_outline(self, text: str) -> str:
        return extract_heading_outline(text)
