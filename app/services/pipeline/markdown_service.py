from __future__ import annotations

import re

from app.services.pipeline.chunking_service import _find_body_start
from app.services.pipeline.clean_markdown_service import PAGE_BREAK_MARKER, clean_markdown
from app.services.pipeline.toc_service import get_pages

_RE_TOC_MARKER = re.compile(r"MUC\s*LUC|MỤC\s*LỤC|TABLE\s+OF\s+CONTENTS", re.IGNORECASE)
_RE_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)")
_RE_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\s*[\.\)]\s*(.+)")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_PURE_NUM = re.compile(r"^\s*[\d\s,\.\-/]+\s*$")
_RE_MD_MARKUP = re.compile(r"\*{1,3}|_{1,3}")
_RE_ROMAN_HEADING = re.compile(r"^(XIV|XIII|XII|XI|IX|VIII|VII|VI|IV|III|II|XV|I|X|V)\.\s+\S", re.IGNORECASE)
_RE_STRUCT_PREFIX = re.compile(r"^(chương|phần|bước|mục|điều|phụ lục|phụlục|chuyên đề|chuyênđề|phan|chuong|buoc|muc|dieu)\s+\S")
_RE_STRUCT_KEYWORD_LOWER = re.compile(r"^(chương|phần|bước|mục|điều|phụ lục|phụlục|chuyên đề|phan|chuong|buoc|muc|dieu)\s+")


def has_toc_page(text: str, max_pages: int) -> bool:
    scan = get_pages(text, max_pages)
    return bool(_RE_TOC_MARKER.search(scan))


def _is_content_list_item(clean_lower: str, clean: str) -> bool:
    match = _RE_NUMBERED.match(clean)
    if not match:
        return False
    num_part = match.group(1)
    content = match.group(2).strip()
    if "." in num_part:
        return False
    if _RE_STRUCT_KEYWORD_LOWER.match(content.lower()):
        return False
    return True


def extract_heading_outline(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == PAGE_BREAK_MARKER or _RE_PURE_NUM.match(s):
            continue

        plain = _RE_HTML_TAG.sub("", s).strip()
        plain = re.sub(r"<::.*?::>", "", plain, flags=re.DOTALL).strip()
        if not plain:
            continue

        clean = _RE_MD_MARKUP.sub("", plain).strip()
        if not clean:
            continue

        clean_lower = clean.lower()

        md_match = _RE_MD_HEADING.match(s)
        if md_match:
            md_content = _RE_MD_MARKUP.sub("", md_match.group(2)).strip()
            if md_content:
                lines.append(f"{md_match.group(1)} {md_content}")
            continue

        if _RE_STRUCT_PREFIX.match(clean_lower):
            lines.append(clean)
            continue

        if _RE_ROMAN_HEADING.match(clean):
            lines.append(clean)
            continue

        if _RE_NUMBERED.match(clean):
            if not _is_content_list_item(clean_lower, clean):
                lines.append(clean)
            continue

        if (
            len(clean) > 4
            and clean == clean.upper()
            and not re.fullmatch(r"[\-\=\*\_\s\|/\\\.]+", clean)
            and not _RE_PURE_NUM.match(clean)
        ):
            if len(clean) >= 10 or " " in clean:
                lines.append(clean)
            continue

    deduped = []
    previous = None
    for line in lines:
        if line != previous:
            deduped.append(line)
            previous = line
    return "\n".join(deduped)


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
