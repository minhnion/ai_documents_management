from __future__ import annotations

import logging
import os
import re
from collections import Counter
from typing import List, Optional, Tuple

import fitz  # PyMuPDF

from app.services.pipeline.spatial_pdf.schema import (
    ChapterMeta,
    ChunkData,
    DocumentMetadata,
    TextBlock,
    TocNode,
)

log = logging.getLogger(__name__)

TOLERANCE = 3.0  # Y-coordinate matching tolerance in points

_HEADING_PATTERNS = [
    re.compile(r'^CHƯƠNG\s+[IVX\d]+', re.IGNORECASE | re.UNICODE),
    re.compile(r'^PHẦN\s+[IVX\d]+', re.IGNORECASE | re.UNICODE),
    re.compile(r'^\d+(\.\d+)*[\s\.\)]'),
]

# Patterns for scraping metadata from front-matter text
_RE_ISBN_ELECTRONIC = re.compile(r'ISBN\s*[\(（]?(?:electronic|online|e-?book|epub)[\)）]?\s*[:\-]?\s*([\d\-X]{10,17})', re.IGNORECASE)
_RE_ISBN_PRINT      = re.compile(r'ISBN\s*[\(（]?(?:print|hardcover|softcover|pbk)?[\)）]?\s*[:\-]?\s*([\d\-X]{10,17})', re.IGNORECASE)
_RE_ISBN_BARE       = re.compile(r'ISBN[:\s]+([\d\-X]{10,17})')
_RE_ISSN            = re.compile(r'ISSN[:\s]+([\d]{4}-[\d]{3}[\dX])', re.IGNORECASE)
_RE_DATE            = re.compile(r'\b(19|20)\d{2}\b')
_RE_DECISION        = re.compile(
    r'(?:No\.?|Number|Report|WHO/|Decision|Decree|Circular|Thông tư|Nghị định|Quyết định)\s*[:\-]?\s*([\w/\-\.]+)',
    re.IGNORECASE
)


def _clean_text(raw: str) -> str:
    text = raw.replace('\xad', '')
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def _normalise(s: str) -> str:
    return re.sub(r'\s+', ' ', s).lower().strip()


def _heading_level_from_text(text: str) -> Optional[int]:
    for pat in _HEADING_PATTERNS:
        if pat.match(text):
            prefix = text.split()[0].rstrip('.')
            return prefix.count('.') + 1
    return None


class SpatialPDFProcessor:


    def __init__(self, pdf_path: str) -> None:
        self.pdf_path = pdf_path
        self.doc: fitz.Document = fitz.open(pdf_path)
        self._blocks: Optional[List[TextBlock]] = None
        self._toc_flat: Optional[List[TocNode]] = None
        self._toc_tree: Optional[List[TocNode]] = None
        self._used_fallback: bool = False
        # Cache page heights to avoid repeated lookups
        self._page_heights: dict[int, float] = {}


    def _page_height(self, pno: int) -> float:
        """Return the height (in points) of page pno (1-based). Cached."""
        if pno not in self._page_heights:
            try:
                self._page_heights[pno] = self.doc[pno - 1].rect.height
            except Exception:
                self._page_heights[pno] = 1.0  # fallback: avoid division by zero
        return self._page_heights[pno]

    def _norm_y(self, y: float, pno: int) -> float:
        """Normalise an absolute Y coordinate to [0.0, 1.0] relative to page height."""
        h = self._page_height(pno)
        if h <= 0:
            return 0.0
        return round(min(max(y / h, 0.0), 1.0), 6)

    def _parse_spatial_blocks(self) -> List[TextBlock]:
        result: List[TextBlock] = []
        for page in self.doc:
            pno = page.number + 1
            raw_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            for blk in raw_dict.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                parts: List[str] = []
                for line in blk.get("lines", []):
                    line_parts = [_clean_text(sp["text"]) for sp in line.get("spans", []) if sp["text"].strip()]
                    if line_parts:
                        parts.append(" ".join(line_parts))
                text = " ".join(parts).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = blk["bbox"]
                result.append(TextBlock(page_num=pno, text=text, x0=x0, y0=y0, x1=x1, y1=y1))
        result.sort(key=lambda b: (b.page_num, b.y0, b.x0))
        log.debug("Extracted %d text blocks from %d pages", len(result), len(self.doc))
        return result

    @property
    def blocks(self) -> List[TextBlock]:
        if self._blocks is None:
            self._blocks = self._parse_spatial_blocks()
        return self._blocks


    def _extract_toc(self) -> Tuple[List[TocNode], List[TocNode]]:
        raw_toc = self.doc.get_toc(simple=False)
        if raw_toc:
            return self._toc_from_metadata(raw_toc)
        log.info("No TOC metadata — activating typography fallback")
        self._used_fallback = True
        return self._toc_from_typography()

    # --- Strategy A ---

    def _toc_from_metadata(self, raw_toc: list) -> Tuple[List[TocNode], List[TocNode]]:
        flat: List[TocNode] = []
        for entry in raw_toc:
            level, title, page, dest = entry
            title = _clean_text(title.replace('\xa0', ' ').replace('\n', ' '))
            target_y = 0.0
            if isinstance(dest, dict):
                to = dest.get("to")
                if to is not None:
                    try:
                        raw_y = float(to.y)
                        if raw_y != 0.0:
                            # PDF bookmark destinations use PDF coordinate space:
                            # y=0 at the BOTTOM of the page, increasing UPWARD.
                            # PyMuPDF block coordinates use y=0 at the TOP, increasing DOWNWARD.
                            # Convert: y_mupdf = page_height - y_pdf
                            h = self._page_height(page)
                            target_y = max(0.0, h - raw_y)
                        # else: raw_y == 0.0 means a /Fit destination — leave as 0.0
                        # so _resolve_fit_positions() will locate it via text search.
                    except (AttributeError, TypeError):
                        target_y = 0.0
            flat.append(TocNode(level=level, title=title, page=page, target_y=target_y))  # raw — normalised below

        self._resolve_fit_positions(flat)
        self._normalize_toc_levels(flat)
        # Normalise target_y to [0.0, 1.0] after all raw-Y lookups are complete
        for node in flat:
            node.target_y = self._norm_y(node.target_y, node.page)
        tree = self._build_tree(flat)
        log.info("Metadata TOC: %d entries, depth %d",
                 len(flat), max((n.level for n in flat), default=0))
        return flat, tree

    @staticmethod
    def _normalize_toc_levels(flat) -> None:
        """
        Fix wrong levels from PDF bookmark metadata using title numbering.
        1.1, 1.2, 1.3 all get level=2 (1 dot → depth 2), never nested under each other.
        Non-numeric entries keep their original PDF level.
        """
        import re as _re
        _NUM = _re.compile(r"^(\d+(?:\.\d+)*)[.\s\u00a0]")
        _ANNEX = _re.compile(r"^Annex\s+\d+", _re.IGNORECASE)
        for node in flat:
            m = _NUM.match(node.title)
            if m:
                node.level = m.group(1).count(".") + 1
            elif _ANNEX.match(node.title):
                node.level = 1

    def _resolve_fit_positions(self, flat: List[TocNode]) -> None:
        """Resolve target_y = 0.0 (/Fit dests) via text search on the target page."""
        cache: dict[int, List[Tuple[float, str]]] = {}

        def page_rows(pno: int) -> List[Tuple[float, str]]:
            if pno not in cache:
                try:
                    rows = []
                    for blk in self.doc[pno - 1].get_text("dict")["blocks"]:
                        if blk.get("type") != 0:
                            continue
                        parts = [_clean_text(sp["text"])
                                 for line in blk["lines"]
                                 for sp in line["spans"]
                                 if sp["text"].strip()]
                        if parts:
                            rows.append((blk["bbox"][1], " ".join(parts)))
                    cache[pno] = rows
                except Exception:
                    cache[pno] = []
            return cache[pno]

        for node in flat:
            if node.target_y != 0.0:
                continue
            key = _normalise(node.title)[:30]
            if not key:
                continue
            for y0, block_text in page_rows(node.page):
                if key in _normalise(block_text):
                    node.target_y = y0
                    log.debug("Resolved Y '%s' p%d → %.1f", node.title[:40], node.page, y0)
                    break

    # --- Strategy B ---

    def _toc_from_typography(self) -> Tuple[List[TocNode], List[TocNode]]:
        all_spans = []
        for page in self.doc:
            for blk in page.get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    for sp in line.get("spans", []):
                        if sp["text"].strip():
                            all_spans.append({
                                "page":  page.number + 1,
                                "text":  _clean_text(sp["text"]),
                                "size":  round(sp["size"], 1),
                                "flags": sp["flags"],
                                "y0":    blk["bbox"][1],
                            })

        body_size = Counter(s["size"] for s in all_spans).most_common(1)[0][0]
        log.debug("Body text size (mode): %.1f", body_size)

        candidates, seen = [], set()
        for sp in all_spans:
            is_large = sp["size"] > body_size + 1.5
            is_bold  = bool(sp["flags"] & 0x10)
            lvl = _heading_level_from_text(sp["text"])
            if (is_large or is_bold) and lvl is not None:
                key = (sp["page"], sp["y0"])
                if key not in seen:
                    seen.add(key)
                    candidates.append({**sp, "level": lvl})

        candidates.sort(key=lambda s: (s["page"], s["y0"]))
        flat = [
            TocNode(
                level=c["level"], title=c["text"], page=c["page"],
                target_y=self._norm_y(c["y0"], c["page"]),  # normalised [0.0, 1.0]
            )
            for c in candidates
        ]
        tree = self._build_tree(flat)
        log.info("Fallback TOC: %d entries", len(flat))
        return flat, tree

    @staticmethod
    def _build_tree(flat: List[TocNode]) -> List[TocNode]:
        roots: List[TocNode] = []
        stack: List[TocNode] = []
        for node in flat:
            while stack and stack[-1].level >= node.level:
                stack.pop()
            (stack[-1].children if stack else roots).append(node)
            stack.append(node)
        return roots

    def get_toc_tree(self) -> List[TocNode]:
        if self._toc_tree is None:
            self._toc_flat, self._toc_tree = self._extract_toc()
        return self._toc_tree

    def _ensure_toc(self) -> List[TocNode]:
        if self._toc_flat is None:
            self._toc_flat, self._toc_tree = self._extract_toc()
        return self._toc_flat


    def generate_chunks(self) -> List[ChunkData]:
        flat = self._ensure_toc()
        if not flat:
            log.warning("Empty TOC — returning single whole-document chunk")
            all_text = "\n\n".join(b.text for b in self.blocks)
            page_count = len(self.doc)
            if self.blocks:
                last_blk = self.blocks[-1]
                last_y_norm = self._norm_y(last_blk.y1, last_blk.page_num)
            else:
                last_y_norm = 0.0
            return [ChunkData(
                title="(Full document)", content=all_text,
                start_page=1, end_page=page_count,
                start_y=0.0, end_y=last_y_norm,
            )]

        by_page: dict[int, List[TextBlock]] = {}
        for b in self.blocks:
            by_page.setdefault(b.page_num, []).append(b)

        chunks: List[ChunkData] = []
        n = len(flat)

        for i, node_a in enumerate(flat):
            node_b = flat[i + 1] if i + 1 < n else None
            start_pg = node_a.page
            end_pg   = node_b.page if node_b else len(self.doc)
            # target_y is already normalised [0.0, 1.0] by _norm_y during TOC extraction
            y_start  = node_a.target_y
            y_end    = node_b.target_y if node_b else float("inf")

            parts: List[str] = []
            actual_end_y_norm = y_start  # track real bottom Y (normalised) for output

            # Normalised TOLERANCE per page (TOLERANCE pts / page height)
            h_start = self._page_height(start_pg)
            tol_start = TOLERANCE / h_start if h_start > 0 else 0.0

            # — Start page: normalise blk coords before comparison —
            for blk in by_page.get(start_pg, []):
                blk_y0_norm = self._norm_y(blk.y0, start_pg)
                blk_y1_norm = self._norm_y(blk.y1, start_pg)
                if blk_y0_norm + tol_start < y_start:
                    continue
                if start_pg == end_pg and node_b and blk_y0_norm >= y_end - tol_start:
                    break
                parts.append(blk.text)
                actual_end_y_norm = blk_y1_norm

            # — Intermediate pages —
            for pg in range(start_pg + 1, end_pg):
                for blk in by_page.get(pg, []):
                    parts.append(blk.text)
                    actual_end_y_norm = self._norm_y(blk.y1, pg)

            # — End page (up to y_end) —
            if end_pg > start_pg and node_b:
                h_end = self._page_height(end_pg)
                tol_end = TOLERANCE / h_end if h_end > 0 else 0.0
                for blk in by_page.get(end_pg, []):
                    blk_y0_norm = self._norm_y(blk.y0, end_pg)
                    blk_y1_norm = self._norm_y(blk.y1, end_pg)
                    # Stop at the first block that STARTS at or after the next heading.
                    # Using blk_y0 (top of block) is correct: a block that starts before
                    # y_end belongs to this chunk even if its bottom extends past y_end.
                    if blk_y0_norm >= y_end - tol_end:
                        break
                    parts.append(blk.text)
                    actual_end_y_norm = blk_y1_norm

            content = "\n\n".join(p for p in parts if p.strip())

            # Both start and end are normalised [0.0, 1.0]
            norm_start = y_start
            norm_end = actual_end_y_norm if y_end == float("inf") else y_end

            chunks.append(ChunkData(
                title=node_a.title,
                content=content,
                start_page=start_pg,
                end_page=end_pg,
                start_y=round(norm_start, 6),
                end_y=round(norm_end, 6),
            ))
            log.debug("Chunk [%d/%d] '%s' p%d(y=%.4f)–p%d(y=%.4f) %d chars",
                      i + 1, n, node_a.title[:40],
                      start_pg, norm_start, end_pg, norm_end, len(content))

        log.info("Generated %d chunks", len(chunks))
        return chunks


    def extract_metadata(self, chunks: Optional[List[ChunkData]] = None) -> DocumentMetadata:
        """
        Extract document-level metadata from PDF properties + front-matter text,
        then embed chapter information from the chunk list.
        """
        pdf_meta = self.doc.metadata or {}

        # --- Basic fields from PDF metadata dict ---
        title     = pdf_meta.get("title") or None
        author    = pdf_meta.get("author") or None
        subject   = pdf_meta.get("subject") or None
        keywords  = pdf_meta.get("keywords") or None
        publisher = None
        date_str  = None

        # Parse creation/modification date (format: D:YYYYMMDDHHmmSS)
        raw_date = pdf_meta.get("creationDate") or pdf_meta.get("modDate") or ""
        m = re.search(r'D:(\d{4})(\d{2})(\d{2})', raw_date)
        if m:
            date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        # --- Scan first 4 pages for richer metadata ---
        front_text = self._front_matter_text(pages=4)

        isbn_e = self._extract_isbn(front_text, electronic=True)
        isbn_p = self._extract_isbn(front_text, electronic=False)
        issn   = self._extract_issn(front_text)

        # Publisher heuristic: "Published by X" / "© X" / known WHO pattern
        pub_match = re.search(
            r'(?:Published by|©\s*\d{4}\s+|World Health Organization|WHO\b)',
            front_text, re.IGNORECASE
        )
        if pub_match:
            if re.search(r'World Health Organization|WHO\b', front_text, re.IGNORECASE):
                publisher = "World Health Organization"
            else:
                snippet = front_text[pub_match.start():pub_match.start() + 80]
                publisher = snippet.split('\n')[0].strip()

        # Decision / report number
        decision = None
        dn_match = re.search(
            r'(?:WHO/[\w/\-\.]+|No\.?\s*[\w/\-\.]+(?:/\d{4})|'
            r'(?:Thông tư|Nghị định|Quyết định)\s+[\d/\-]+(?:/\w+)*)',
            front_text, re.IGNORECASE
        )
        if dn_match:
            decision = dn_match.group(0).strip()

        # Date fallback from front text
        if not date_str:
            yr = _RE_DATE.search(front_text)
            if yr:
                date_str = yr.group(0)

        # Specialty / subject: use PDF subject, else first meaningful sentence from abstract/intro
        specialty = subject
        if not specialty:
            m2 = re.search(r'(?:sexually transmitted|STI|HIV|antibiotic|dược lâm sàng|oncology)', front_text, re.IGNORECASE)
            if m2:
                specialty = m2.group(0)

        # Build chapter list from chunks
        chapters: List[ChapterMeta] = []
        if chunks:
            for c in chunks:
                chapters.append(ChapterMeta(
                    title=c.title,
                    page_start=c.start_page,
                    page_end=c.end_page,
                    start_y=c.start_y,
                    end_y=c.end_y,
                    content=c.content,
                ))

        return DocumentMetadata(
            title=title,
            publisher=publisher,
            author=author,
            subject=subject,
            keywords=keywords,
            decision_number=decision,
            specialty=specialty,
            date=date_str,
            isbn_electronic=isbn_e,
            isbn_print=isbn_p,
            issn=issn,
            total_pages=len(self.doc),
            source_file=os.path.basename(self.pdf_path),
            chapters=chapters,
        )

    def _front_matter_text(self, pages: int = 4) -> str:
        """Return concatenated plain text of the first N pages."""
        parts = []
        for i in range(min(pages, len(self.doc))):
            parts.append(self.doc[i].get_text("text"))
        return "\n".join(parts)

    @staticmethod
    def _extract_isbn(text: str, electronic: bool) -> Optional[str]:
        if electronic:
            m = _RE_ISBN_ELECTRONIC.search(text)
        else:
            m = _RE_ISBN_PRINT.search(text)
        if m:
            return m.group(1)
        # Bare ISBN fallback — return first match for print, second for electronic
        all_isbns = _RE_ISBN_BARE.findall(text)
        if electronic:
            return all_isbns[1] if len(all_isbns) > 1 else None
        return all_isbns[0] if all_isbns else None

    @staticmethod
    def _extract_issn(text: str) -> Optional[str]:
        m = _RE_ISSN.search(text)
        return m.group(1) if m else None


    def export_interactive_pdf(self, output_path: str) -> None:
        if not self._used_fallback:
            log.info("Original TOC intact — skipping PDF re-export")
            return
        flat = self._ensure_toc()
        # target_y is normalised [0.0, 1.0]; fitz.set_toc needs raw pixel y
        toc = []
        for node in flat:
            h = self._page_height(node.page)
            # target_y is normalised PyMuPDF [0=top, 1=bottom]; fitz.set_toc expects
            # PDF coordinates (y=0 at bottom, increasing up), so invert:
            raw_y_pdf = h - (node.target_y * h)
            toc.append([node.level, node.title, node.page, raw_y_pdf])
        self.doc.set_toc(toc)
        self.doc.save(output_path, deflate=True, garbage=3)
        log.info("Saved interactive PDF → %s (%d entries)", output_path, len(flat))

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "SpatialPDFProcessor":
        return self

    def __exit__(self, *_) -> None:
        self.close()