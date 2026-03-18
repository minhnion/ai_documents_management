from __future__ import annotations

import re

PAGE_BREAK_MARKER = "<!-- PAGE_BREAK -->"

_RE_ANCHOR = re.compile(r"<a\b[^>]*>\s*</a>", flags=re.IGNORECASE)
_RE_PAGE_BREAK = re.compile(r"<!--\s*PAGE[\s_]*BREAK\s*-->", flags=re.IGNORECASE)
_RE_HTML_COMMENT_NON_PB = re.compile(
    r"<!--(?!\s*PAGE\s*BREAK\s*-->).*?-->",
    flags=re.DOTALL | re.IGNORECASE,
)
_RE_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)")
_RE_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\s*[\.)]\s*(.+)")
_RE_ROMAN_HEADING = re.compile(r"^(I{1,3}|IV|V?I{0,3}|IX|X{0,3})\.\s+\S")
_RE_CHAPTER_PREFIX = re.compile(
    r"^(ph[aan]|phan|chuong|ch[uo]ong|buoc|muc|dieu)\s+\S",
    flags=re.IGNORECASE,
)
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_PURE_NUM = re.compile(r"^\s*[\d\s,\.\-/]+\s*$")
_RE_TOC_HEADER = re.compile(
    r"^\s*(?:MỤC\s*LỤC|MUC\s*LUC|TABLE\s+OF\s+CONTENTS|CONTENTS)\s*$",
    flags=re.IGNORECASE,
)
_RE_TOC_PAGE = re.compile(
    r"MỤC\s*LỤC|MUC\s*LUC|TABLE\s+OF\s+CONTENTS",
    flags=re.IGNORECASE,
)


class MarkdownProcessingService:
    def clean_markdown(self, raw_text: str) -> str:
        text = _RE_ANCHOR.sub("", raw_text)
        text = _RE_HTML_COMMENT_NON_PB.sub("", text)
        text = _RE_PAGE_BREAK.sub(PAGE_BREAK_MARKER, text)
        return text

    def extract_first_pages(self, text: str, max_pages: int) -> str:
        parts = text.split(PAGE_BREAK_MARKER)
        if len(parts) <= 1:
            return text[:120000]
        return PAGE_BREAK_MARKER.join(parts[:max_pages])

    def has_toc_page(self, text: str, max_pages: int) -> bool:
        scan = self.extract_first_pages(text, max_pages=max_pages)
        return bool(_RE_TOC_PAGE.search(scan))

    def find_body_start(self, text: str) -> int:
        cursor = 0
        in_toc = False
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if not in_toc:
                plain = _RE_HTML_TAG.sub("", stripped).strip()
                if _RE_TOC_HEADER.match(stripped) or _RE_TOC_HEADER.match(plain):
                    in_toc = True
                cursor += len(line)
                continue
            if (
                not stripped
                or stripped == PAGE_BREAK_MARKER
                or stripped.startswith("<")
                or _RE_PURE_NUM.fullmatch(stripped)
            ):
                cursor += len(line)
                continue
            return cursor
        return 0

    def extract_heading_outline(self, text: str) -> str:
        lines: list[str] = []

        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped or stripped == PAGE_BREAK_MARKER or _RE_PURE_NUM.match(stripped):
                continue
            plain = _RE_HTML_TAG.sub("", stripped).strip()
            if not plain:
                continue

            if _RE_MD_HEADING.match(stripped):
                lines.append(stripped)
            elif _RE_NUMBERED.match(plain):
                lines.append(plain)
            elif _RE_ROMAN_HEADING.match(plain):
                lines.append(plain)
            elif _RE_CHAPTER_PREFIX.match(plain):
                lines.append(plain)
            elif len(plain) <= 120 and plain == plain.upper() and len(plain) > 4:
                if not re.fullmatch(r"[\-\=\*\s]+", plain):
                    lines.append(plain)

        deduped: list[str] = []
        previous: str | None = None
        for line in lines:
            if line != previous:
                deduped.append(line)
                previous = line
        return "\n".join(deduped)
