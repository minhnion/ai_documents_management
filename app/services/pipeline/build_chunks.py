from __future__ import annotations

import bisect
import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from build_toc import DEPTH_CHILD_KEYS, TocTree
from parse_models import (
    LeafElement, MEDIA_TYPES, NOISE_TYPES, ParseResult, TableCellElement,
    flatten_leaves, index_table_cells,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

METADATA_KEYS = [
    "title", "publisher", "decision_number", "specialty", "date",
    "isbn_electronic", "isbn_print", "total_pages", "source_file",
]


@dataclass
class ChunkConfig:
    toc_dir: Path = Path("./data/03_toc_json")
    source_dir: Path = Path("./data/06_ade_chunks")
    output_dir: Path = Path("./data/04_chunked_json")
    files: tuple[str, ...] = ()
    footer_top_threshold: float = 0.85
    header_top_threshold: float = 0.10
    noise_repeat_min_pages: int = 15
    noise_max_chars: int = 200
    heading_match_threshold: float = 0.55


_RE_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_RE_PAGE_NUM_PREFIX = re.compile(r"^\d{1,4}\s*[|/\-]\s*")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_LINE_SPLIT = re.compile(r"\n|<br\s*/?>", re.IGNORECASE)
_RE_ALPHA_PREFIX = re.compile(r"^([A-Za-z])\.")
_RE_NUM_PREFIX = re.compile(r"^(\d[\d.]*)")
_RE_BOLD_SPAN = re.compile(r"^\*\*(.+?)\*\*")
_PARTS_SEGMENT_TYPES = frozenset({"text", "marginalia", "attestation", "logo"})


class TextSimilarity:
    @staticmethod
    def words(text: str) -> set[str]:
        return set(_RE_NONWORD.sub(" ", text.lower()).split())

    @staticmethod
    def words_truncated(text: str, max_w: int) -> set[str]:
        return set(_RE_NONWORD.sub(" ", text.lower()).split()[:max_w])

    @staticmethod
    def dice(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return 2 * len(a & b) / (len(a) + len(b))

    @staticmethod
    def overlap(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / min(len(a), len(b))


class PageLookup:
    def __init__(self, result: ParseResult) -> None:
        pages = sorted(
            ((p.span[0], p.page) for p in result.structure.children if p.type == "page" and p.span is not None)
        )
        self._starts = [s for s, _ in pages]
        self._pages = [p for _, p in pages]

    def page_at(self, char_pos: int) -> int | None:
        if not self._starts:
            return None
        idx = max(0, min(bisect.bisect_right(self._starts, char_pos) - 1, len(self._pages) - 1))
        return self._pages[idx]


_RE_STRUCTURED_MARK = re.compile(r"\[[A-Z_]{3,}\]")


class NoiseDetector:
    def __init__(self, config: ChunkConfig) -> None:
        self._config = config

    def detect(self, result: ParseResult, leaves: list[LeafElement]) -> set[str]:
        text_to_ids: dict[str, list[str]] = {}
        for leaf in leaves:
            if leaf.type != "text":
                continue
            bbox = leaf.normalized_bbox()
            if bbox is None:
                continue
            top = bbox["top"]
            if not (top > self._config.footer_top_threshold or top < self._config.header_top_threshold):
                continue
            text = result.markdown[leaf.span[0]:leaf.span[1]].strip()
            if not text or len(text) > self._config.noise_max_chars:
                continue
            norm = _RE_PAGE_NUM_PREFIX.sub("", text)
            norm = re.sub(r"\s+", " ", norm).strip().lower()
            if norm:
                text_to_ids.setdefault(norm, []).append(leaf.id)
        repeated = {lid for ids in text_to_ids.values() if len(ids) >= self._config.noise_repeat_min_pages for lid in ids}
        always = {leaf.id for leaf in leaves if leaf.type in NOISE_TYPES and self._is_genuine_noise(leaf, result)}
        return repeated | always

    @staticmethod
    def _is_genuine_noise(leaf: LeafElement, result: ParseResult) -> bool:
        if leaf.type != "attestation":
            return True
        text = result.markdown[leaf.span[0]:leaf.span[1]]
        return bool(_RE_STRUCTURED_MARK.search(text))


class HeadingLocator:
    _LOWER_THRESHOLD_FACTOR = 0.82
    _MAX_PART_WINDOW = 3
    _OVERLAP_MIN_TITLE_WORDS = 3
    _OVERLAP_THRESHOLD = 0.9
    _OVERLAP_MAX_MISSING = 1

    def __init__(self, markdown: str, config: ChunkConfig) -> None:
        self._markdown = markdown
        self._config = config

    def locate_in_leaf(
        self, span: tuple[int, int], title: str, search_from: int,
        parts: list | None = None, anchor: str | None = None,
        table_cells: list[TableCellElement] | None = None,
    ) -> tuple[int, int] | None:
        search_from = max(search_from, span[0])
        if search_from >= span[1]:
            return None

        title_words = TextSimilarity.words(title)
        if not title_words:
            return None

        if anchor:
            anchor_result = self._locate_by_anchor(span, search_from, title, title_words, anchor)
            if anchor_result is not None:
                return anchor_result

        extra_segments: list[tuple[int, int]] = []
        if table_cells:
            cell_segments = self._table_cell_segments(table_cells, search_from, span[1])
            if cell_segments:
                extra_segments += cell_segments
        if parts:
            part_segments = self._part_segments(parts, search_from, span[1])
            if part_segments:
                extra_segments += part_segments

        return self._locate_in_range(search_from, span[1], title, title_words, self._prefix_regex(title), extra_segments)

    def search_in_range(self, floor: int, ceiling: int, title: str) -> tuple[int, int] | None:
        title_words = TextSimilarity.words(title)
        if not title_words or floor >= ceiling:
            return None
        return self._locate_in_range(floor, ceiling, title, title_words, self._prefix_regex(title), [])

    def find_anywhere(self, title: str) -> int | None:
        located = self.search_in_range(0, len(self._markdown), title)
        return located[0] if located is not None else None

    def _locate_in_range(
        self, floor: int, ceiling: int, title: str, title_words: set[str],
        prefix_re: re.Pattern | None, extra_segments: list[tuple[int, int]],
    ) -> tuple[int, int] | None:
        window = self._markdown[floor:ceiling]
        segments = self._segments(window) + extra_segments

        result = self._search(window, segments, title, title_words, prefix_re, self._config.heading_match_threshold)
        if result is None:
            result = self._search(
                window, segments, title, title_words, prefix_re,
                self._config.heading_match_threshold * self._LOWER_THRESHOLD_FACTOR,
            )
        if result is None:
            return None
        s, e = result
        return floor + s, floor + e

    def _locate_by_anchor(
        self, span: tuple[int, int], search_from: int, title: str, title_words: set[str], anchor: str
    ) -> tuple[int, int] | None:
        window = self._markdown[search_from:span[1]]
        if window.count(anchor) != 1:
            return None
        pos = window.find(anchor)
        abs_start = search_from + pos
        m = _RE_LINE_SPLIT.search(self._markdown, abs_start, span[1])
        seg_end = m.start() if m else span[1]
        segment = self._markdown[abs_start:seg_end]
        content_start = self._content_cut(segment, abs_start, seg_end, title, title_words)
        return abs_start, content_start

    @staticmethod
    def _segments(window: str) -> list[tuple[int, int]]:
        boundaries = [m.end() for m in _RE_LINE_SPLIT.finditer(window)]
        starts = [0] + boundaries
        ends = boundaries + [len(window)]
        return list(zip(starts, ends))

    @classmethod
    def _part_segments(cls, parts: list, search_from: int, span_end: int) -> list[tuple[int, int]]:
        clipped = sorted(
            (max(p.span[0], search_from) - search_from, min(p.span[1], span_end) - search_from)
            for p in parts
            if p.span[1] > search_from and p.span[0] < span_end
        )
        clipped = [(s, e) for s, e in clipped if e > s]
        if not clipped:
            return []
        segments = list(clipped)
        for window_size in range(2, cls._MAX_PART_WINDOW + 1):
            for i in range(len(clipped) - window_size + 1):
                segments.append((clipped[i][0], clipped[i + window_size - 1][1]))
        return segments

    @staticmethod
    def _table_cell_segments(
        cells: list[TableCellElement], search_from: int, span_end: int
    ) -> list[tuple[int, int]]:
        return [
            (max(c.span[0], search_from) - search_from, min(c.span[1], span_end) - search_from)
            for c in cells
            if c.span[1] > search_from and c.span[0] < span_end and c.span[1] > c.span[0]
        ]

    @staticmethod
    def _extract_prefix_token(text: str) -> str | None:
        stripped = text.strip()
        alpha_m = _RE_ALPHA_PREFIX.match(stripped)
        if alpha_m:
            return alpha_m.group(1).upper()
        num_m = _RE_NUM_PREFIX.match(stripped)
        if num_m:
            return num_m.group(1).rstrip(".")
        return None

    @classmethod
    def _prefix_regex(cls, title: str) -> re.Pattern | None:
        token = cls._extract_prefix_token(title)
        if token is None:
            return None
        if re.match(r"^[A-Za-z]$", token):
            return re.compile(r"(?<![A-Za-z0-9])" + re.escape(token) + r"\.")
        return re.compile(r"(?<![.\d])" + re.escape(token) + r"(?:[.\s]|$)")

    def prefix_conflict(self, title: str, start: int) -> bool:
        title_token = self._extract_prefix_token(title)
        if title_token is None:
            return False
        m = _RE_LINE_SPLIT.search(self._markdown, start)
        seg_end = m.start() if m else len(self._markdown)
        clean = _RE_HTML_TAG.sub(" ", self._markdown[start:seg_end]).strip().lstrip("*").strip()
        line_token = self._extract_prefix_token(clean)
        return line_token is not None and line_token.upper() != title_token.upper()

    def _search(
        self, window: str, segments: list[tuple[int, int]], title: str, title_words: set[str],
        prefix_re: re.Pattern | None, threshold: float,
    ) -> tuple[int, int] | None:
        best: tuple[int, int] | None = None
        best_score = 0.0
        for s, e in segments:
            segment = window[s:e]
            clean = _RE_HTML_TAG.sub(" ", segment).strip()
            if clean:
                score = self._score_candidates(clean, title_words, threshold)
                if score >= threshold and score > best_score:
                    best_score, best = score, (s, self._content_cut(segment, s, e, title, title_words))
            if prefix_re is None:
                continue
            for m in prefix_re.finditer(segment):
                sub_raw = segment[m.start():]
                sub_clean = _RE_HTML_TAG.sub(" ", sub_raw).strip()
                if not sub_clean:
                    continue
                sub_score = self._score_candidates(sub_clean, title_words, threshold)
                if sub_score >= threshold and sub_score > best_score:
                    best_score, best = sub_score, (s + m.start(), self._content_cut(sub_raw, s + m.start(), e, title, title_words))
        return best

    @classmethod
    def _score_candidates(cls, clean: str, title_words: set[str], threshold: float) -> float:
        best = TextSimilarity.dice(title_words, TextSimilarity.words(clean))

        bold = cls._bold_candidate(clean)
        if bold:
            best = max(best, TextSimilarity.dice(title_words, TextSimilarity.words(bold)))

        colon = cls._colon_candidate(clean, title_words)
        if colon:
            best = max(best, TextSimilarity.dice(title_words, TextSimilarity.words(colon)))

        capped = TextSimilarity.words_truncated(clean, max(2 * len(title_words), 10))
        best = max(best, TextSimilarity.dice(title_words, capped))

        if best < threshold and len(title_words) >= cls._OVERLAP_MIN_TITLE_WORDS:
            bounded = [w for w in (
                TextSimilarity.words(bold) if bold else None,
                TextSimilarity.words(colon) if colon else None,
                capped,
            ) if w]
            for candidate_words in bounded:
                missing = len(title_words - candidate_words)
                if missing <= cls._OVERLAP_MAX_MISSING and TextSimilarity.overlap(title_words, candidate_words) >= cls._OVERLAP_THRESHOLD:
                    best = max(best, threshold)
                    break

        return best

    @staticmethod
    def _bold_candidate(clean: str) -> str | None:
        m = _RE_BOLD_SPAN.match(clean)
        return m.group(1).strip() if m else None

    @classmethod
    def _colon_split_pos(cls, segment_text: str, title_words: set[str]) -> int | None:
        colon_pos = segment_text.find(":")
        if colon_pos <= 0:
            return None
        after = segment_text[colon_pos + 1:].strip()
        if not after:
            return None
        before_words = TextSimilarity.words(segment_text[:colon_pos])
        if len(before_words & title_words) < max(1, len(title_words) // 2):
            return None
        remaining = title_words - before_words
        if remaining:
            after_words = TextSimilarity.words(after)
            missing = len(remaining - after_words)
            if missing <= cls._OVERLAP_MAX_MISSING and TextSimilarity.overlap(remaining, after_words) >= cls._OVERLAP_THRESHOLD:
                return None
        return colon_pos

    @classmethod
    def _colon_candidate(cls, clean: str, title_words: set[str]) -> str | None:
        pos = cls._colon_split_pos(clean, title_words)
        return clean[:pos] if pos is not None else None

    @classmethod
    def _content_cut(cls, segment_text: str, abs_start: int, abs_end: int, title: str, title_words: set[str]) -> int:
        end = cls._title_end(segment_text, title)
        if end is not None:
            rest = segment_text[end:]
            stripped = rest.lstrip(" \t")
            if stripped.startswith(":"):
                end += len(rest) - len(stripped) + 1
            return abs_start + end
        pos = cls._colon_split_pos(segment_text, title_words)
        return abs_start + pos + 1 if pos is not None else abs_end

    @staticmethod
    def _title_end(segment: str, title: str) -> int | None:
        sig = [c for c in title.lower() if c.isalnum()]
        if not sig:
            return None
        matched = 0
        for i, ch in enumerate(segment):
            if not ch.isalnum():
                continue
            if ch.lower() != sig[matched]:
                return None
            matched += 1
            if matched == len(sig):
                return i + 1
        return None


class Phase5BoundaryResolver:
    def __init__(
        self, leaves: list[LeafElement], locator: HeadingLocator,
        table_cells_by_leaf: dict[str, list[TableCellElement]], config: ChunkConfig,
    ) -> None:
        self._leaves = leaves
        self._locator = locator
        self._table_cells_by_leaf = table_cells_by_leaf
        self._config = config
        self._id_to_leaf = {leaf.id: i for i, leaf in enumerate(leaves) if leaf.id}

    def resolve(self, chapters: list[dict], doc_end: int) -> None:
        nodes = self._flatten(chapters)
        self._resolve_starts(nodes)
        self._resolve_ends(nodes, chapters, doc_end)

    @staticmethod
    def _flatten(chapters: list[dict]) -> list[dict]:
        out: list[dict] = []

        def walk(nodes: list[dict]) -> None:
            for node in nodes:
                if not node.get("title"):
                    continue
                out.append(node)
                for key in DEPTH_CHILD_KEYS.values():
                    if node.get(key):
                        walk(node[key])

        walk(chapters)
        return out

    def _resolve_starts(self, nodes: list[dict]) -> None:
        by_leaf: dict[int | None, list[dict]] = {}
        for node in nodes:
            by_leaf.setdefault(self._id_to_leaf.get(node.get("heading_element_id")), []).append(node)
        for leaf_idx, group in by_leaf.items():
            if leaf_idx is None:
                for node in group:
                    self._mark_unplaced(node)
            else:
                self._resolve_leaf_group(leaf_idx, group)

    def _resolve_leaf_group(self, leaf_idx: int, group: list[dict]) -> None:
        leaf = self._leaves[leaf_idx]
        parts = leaf.parts if leaf.type in _PARTS_SEGMENT_TYPES else None
        table_cells = self._table_cells_by_leaf.get(leaf.id) if leaf.type == "table" else None
        prelim = [(self._locate(leaf, node, leaf.span[0], parts, table_cells), node) for node in group]
        prelim.sort(key=lambda p: p[0][0] if p[0] else leaf.span[1])
        cursor = leaf.span[0]
        for fallback, node in prelim:
            located = self._locate(leaf, node, cursor, parts, table_cells) or fallback
            if located is None:
                self._mark_unplaced(node)
                continue
            start, content_start = located
            node["_start"], node["_content_start"], node["_leaf_idx"] = start, content_start, leaf_idx
            self._warn_if_prefix_conflict(node.get("title", ""), start)
            cursor = max(cursor, content_start, start + 1)

    def _locate(
        self, leaf: LeafElement, node: dict, frm: int,
        parts: list | None, table_cells: list[TableCellElement] | None,
    ) -> tuple[int, int] | None:
        if frm >= leaf.span[1]:
            return None
        return self._locator.locate_in_leaf(
            (frm, leaf.span[1]), node.get("title", ""), frm, parts, node.get("heading_anchor"), table_cells
        )

    def _resolve_ends(self, nodes: list[dict], chapters: list[dict], doc_end: int) -> None:
        descendants = self._descendant_ids(chapters)
        placed = sorted((n for n in nodes if n.get("_start") is not None), key=lambda n: n["_start"])
        for i, node in enumerate(placed):
            own = descendants[id(node)]
            end = doc_end
            for j in range(i + 1, len(placed)):
                if id(placed[j]) in own:
                    continue
                end = placed[j]["_start"]
                break
            node["_end"] = max(end, node["_content_start"])

    @staticmethod
    def _descendant_ids(chapters: list[dict]) -> dict[int, set[int]]:
        table: dict[int, set[int]] = {}

        def walk(node: dict) -> set[int]:
            acc: set[int] = set()
            for key in DEPTH_CHILD_KEYS.values():
                for child in node.get(key, []):
                    if not child.get("title"):
                        continue
                    acc.add(id(child))
                    acc |= walk(child)
            table[id(node)] = acc
            return acc

        for chapter in chapters:
            if chapter.get("title"):
                walk(chapter)
        return table

    def _mark_unplaced(self, node: dict) -> None:
        title = node.get("title", "")
        found_at = self._locator.find_anywhere(title)
        if found_at is not None:
            node["_unmatched_reason"] = "out_of_window"
            node["_found_at"] = found_at
            logger.warning(
                "Unmatched [NGOÀI CỬA SỔ — không mất chữ]: %r; không định vị được trong element của nó, "
                "text có ở char %d (nghi node thừa/nhầm element)", title, found_at,
            )
        else:
            node["_unmatched_reason"] = "text_not_found"
            logger.warning("Unmatched [TEXT KHÔNG THẤY — nghi node thừa/OCR]: %r", title)

    def _warn_if_prefix_conflict(self, title: str, start: int) -> None:
        if self._locator.prefix_conflict(title, start):
            logger.warning("Prefix mismatch: TOC title %r resolved at char %d has a different structural prefix in the source text", title, start)


class BBoxAggregator:
    @staticmethod
    def union_by_page(bboxes: list[dict]) -> list[dict]:
        by_page: dict[int, list[dict]] = {}
        for b in bboxes:
            by_page.setdefault(b["page"], []).append(b)
        return [
            {
                "page": p,
                "left": round(min(b["left"] for b in group), 6),
                "top": round(min(b["top"] for b in group), 6),
                "right": round(max(b["right"] for b in group), 6),
                "bottom": round(max(b["bottom"] for b in group), 6),
            }
            for p, group in sorted(by_page.items())
        ]


_RE_FIGURE_TAG = re.compile(r"</?figure[^>]*>", re.IGNORECASE)
_RE_DESCRIPTION_BLOCK = re.compile(r"<description>(.*?)</description>", re.DOTALL | re.IGNORECASE)
_RE_ALTTEXT_PREFIX = re.compile(
    r"^(A |An |The |This |Two |Three |Four |Five |Several |Multiple |"
    r"Image |Images |Image showing|A close-up|A composite|A split|A visual|"
    r"A photograph|A grid|A single)",
    re.IGNORECASE,
)
_RE_HTML_TABLE_TAG = re.compile(r"<table[\s>]", re.IGNORECASE)
_RE_FIGURE_SECTION_HEADER = re.compile(
    r"^[ \t]*\d+\.[ \t]*(?:Directory of Nodes[ \t]*\(Blocks\)|Connections[ \t]*\(In[ \t]*&[ \t]*Out Flows\))[ \t]*:?[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_RE_FIGURE_NODE_LINE = re.compile(r"^[ \t]*-?[ \t]*\[[^\]\n]+\][ \t]*(?:\([^)\n]*\)[ \t]*)?:[ \t]*(.+)$")
_RE_FIGURE_WRAPPED_QUOTE = re.compile(r'^(["\'])(.*)\1$')
_RE_BRACKET_GROUP = re.compile(r"\[[^\]\n]*\]")
_RE_PAREN_GROUP = re.compile(r"\([^)\n]*\)")
_RE_FIG_HAS_SCAFFOLD = re.compile(r"Directory of Nodes|Connections\s*\(In", re.IGNORECASE)
_RE_FIG_SCAFFOLD_MARK = re.compile(r"[#*\s]*\d*\.?\s*(?:Directory of Nodes|Connections\s*\(In)|<description>", re.IGNORECASE)
_RE_FIG_FLOWCHART_CAPTION = re.compile(r"^\s*Flowchart\s*:\s*.*?\.\s*", re.IGNORECASE)


class MediaContentRenderer:
    @staticmethod
    def render(leaf_type: str, full_text: str, clipped_text: str, chunk_id: str) -> str:
        if leaf_type == "table":
            return MediaContentRenderer._render_table(full_text, clipped_text, chunk_id)
        if leaf_type == "figure":
            return MediaContentRenderer._render_figure(clipped_text, chunk_id)
        return f"[{leaf_type}:{chunk_id}]"

    @staticmethod
    def _render_table(full_text: str, clipped_text: str, chunk_id: str) -> str:
        if _RE_HTML_TABLE_TAG.search(full_text):
            return f"[table:{chunk_id}]\n{clipped_text}"
        return f"[table:{chunk_id}]"

    @staticmethod
    def _render_figure(clipped_text: str, chunk_id: str) -> str:
        body = _RE_FIGURE_TAG.sub("", clipped_text)

        def _strip_alttext(m: re.Match) -> str:
            inner = m.group(1).strip()
            return "" if _RE_ALTTEXT_PREFIX.match(inner) else inner

        if _RE_FIG_HAS_SCAFFOLD.search(body):
            mark = _RE_FIG_SCAFFOLD_MARK.search(body)
            body = _RE_FIG_FLOWCHART_CAPTION.sub("", body[:mark.start()])
        else:
            body = _RE_DESCRIPTION_BLOCK.sub(_strip_alttext, body)
            body = MediaContentRenderer._strip_flowchart_scaffold(body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        return f"[figure:{chunk_id}]\n{body}" if body else f"[figure:{chunk_id}]"

    @staticmethod
    def _strip_flowchart_scaffold(text: str) -> str:
        text = _RE_FIGURE_SECTION_HEADER.sub("", text)
        lines = []
        for line in text.split("\n"):
            m = _RE_FIGURE_NODE_LINE.match(line)
            if m:
                lines.append(MediaContentRenderer._unwrap_quote(m.group(1).strip()))
                continue
            if MediaContentRenderer._is_connection_line(line):
                continue
            lines.append(line)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    @staticmethod
    def _is_connection_line(line: str) -> bool:
        stripped = line.strip().lstrip("-").strip()
        if not stripped.startswith("[") or stripped.count("[") < 2:
            return False
        if '"' in stripped or "'" in stripped:
            return False
        residual = _RE_PAREN_GROUP.sub("", _RE_BRACKET_GROUP.sub("", stripped))
        return len(residual.split()) <= 4

    @staticmethod
    def _unwrap_quote(text: str) -> str:
        m = _RE_FIGURE_WRAPPED_QUOTE.match(text)
        return m.group(2) if m else text


_RE_ORPHAN_BOLD_PAIR = re.compile(r"\*\*(\s*)\*\*")


class ContentExtractor:
    def __init__(self, result: ParseResult, leaves: list[LeafElement], noise_ids: set[str]) -> None:
        self._result = result
        self._leaves = leaves
        self._noise_ids = noise_ids

    def extract(self, content_start: int, end: int) -> str | None:
        if end <= content_start:
            return None
        substitutions: list[tuple[int, int, str]] = []
        for leaf in self._leaves:
            s, e = leaf.span
            if e <= content_start or s >= end:
                continue
            cs, ce = max(s, content_start), min(e, end)
            if leaf.id in self._noise_ids:
                substitutions.append((cs, ce, ""))
            elif leaf.type in MEDIA_TYPES:
                full_text = self._result.markdown[s:e]
                clipped_text = self._result.markdown[cs:ce]
                substitutions.append((cs, ce, MediaContentRenderer.render(leaf.type, full_text, clipped_text, leaf.id)))
        substitutions.sort()

        parts: list[str] = []
        pos = content_start
        for s, e, repl in substitutions:
            if s > pos:
                parts.append(self._result.markdown[pos:s])
            parts.append(repl)
            pos = max(pos, e)
        if pos < end:
            parts.append(self._result.markdown[pos:end])

        text = re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
        text = _RE_ORPHAN_BOLD_PAIR.sub(lambda m: m.group(1), text)
        if text.count("**") == 1:
            text = text.replace("**", "", 1)
        text = text.strip()
        return text or None


class Phase6NodeAssembler:
    def __init__(self, result: ParseResult, leaves: list[LeafElement], extractor: ContentExtractor, pages: PageLookup) -> None:
        self._result = result
        self._leaves = leaves
        self._extractor = extractor
        self._pages = pages

    def build(self, node: dict, path: str) -> dict:
        node_path = f"{path}/{node.get('title', '')}" if path else node.get("title", "")
        identity = f"{node_path}\x00{node.get('heading_element_id') or ''}"
        node_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]

        start = node.get("_start")
        content_start = node.get("_content_start", start)
        end = node.get("_end")
        leaf_idx = node.get("_leaf_idx")

        children_out: dict[str, list[dict]] = {}
        child_starts: list[int] = []
        for key in DEPTH_CHILD_KEYS.values():
            children = [c for c in node.get(key, []) if c.get("title")]
            if not children:
                continue
            children_out[key] = [self.build(c, node_path) for c in children]
            child_starts += [
                c["_start"] for c in children
                if c.get("_start") is not None and content_start is not None and c["_start"] >= content_start
            ]

        first_child_start = min(child_starts) if child_starts else None
        content_end = first_child_start if first_child_start is not None else end
        content = self._extractor.extract(content_start, content_end) if start is not None and content_end is not None else None

        heading_bbox = (
            self._leaves[leaf_idx].normalized_bbox_for_range(start, content_start)
            if leaf_idx is not None and start is not None and content_start is not None
            else None
        )

        content_bboxes: list[dict] = []
        if content_start is not None and content_end is not None and content_end > content_start:
            raw_boxes = []
            for l in self._leaves:
                if l.type == "marginalia":
                    continue
                lo, hi = max(l.span[0], content_start), min(l.span[1], content_end)
                if lo >= hi:
                    continue
                box = l.normalized_bbox_for_range(lo, hi)
                if box:
                    raw_boxes.append(box)
            content_bboxes = BBoxAggregator.union_by_page(raw_boxes)

        landing_chunks = [
            {"id": l.id, "type": l.type}
            for l in self._leaves
            if start is not None and end is not None and start <= l.span[0] < end and l.type in MEDIA_TYPES
        ]

        page_start = self._pages.page_at(start) if start is not None else None
        page_end = self._pages.page_at(end - 1) if end else None

        out: dict = {
            "node_id": node_id,
            "title": node.get("title", ""),
            "page_start": page_start + 1 if page_start is not None else None,
            "page_end": page_end + 1 if page_end is not None else None,
            "content": content,
            "heading_bbox": heading_bbox,
            "content_bboxes": content_bboxes,
            "landing_chunks": landing_chunks,
        }
        if start is None:
            out["unmatched_reason"] = node.get("_unmatched_reason", "text_not_found")
            if node.get("_found_at") is not None:
                out["unmatched_found_at_char"] = node["_found_at"]
        out.update(children_out)
        return out


class ChunkDocumentBuilder:
    def __init__(self, config: ChunkConfig) -> None:
        self._config = config

    def build(self, toc_path: Path, source_path: Path) -> dict:
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
        result = ParseResult.load(source_path)
        leaves = flatten_leaves(result)
        table_cells_by_leaf = index_table_cells(result)

        chapters = [c for c in toc.get("chapters", []) if c.get("title")]
        node_count = len(TocTree.flatten_refs(chapters))
        logger.info("TOC nodes: %d | elements: %d | markdown chars: %d", node_count, len(leaves), len(result.markdown))

        locator = HeadingLocator(result.markdown, self._config)
        Phase5BoundaryResolver(leaves, locator, table_cells_by_leaf, self._config).resolve(chapters, len(result.markdown))

        noise_ids = NoiseDetector(self._config).detect(result, leaves)
        extractor = ContentExtractor(result, leaves, noise_ids)
        pages = PageLookup(result)
        builder = Phase6NodeAssembler(result, leaves, extractor, pages)

        top_chunks = [builder.build(c, "") for c in chapters]
        matched, reasons, empty = self._count_matches(top_chunks)
        unmatched = sum(reasons.values())
        logger.info(
            "Match: %d ok, %d unmatched | %d NGOÀI-CỬA-SỔ (text CÓ trong tài liệu → KHÔNG mất chữ; do lệch vị trí"
            " cây hoặc node thừa) | %d TEXT-KHÔNG-THẤY (nghi node thừa/OCR — cần rà xem có mất chữ không)",
            matched, unmatched, reasons.get("out_of_window", 0), reasons.get("text_not_found", 0),
        )
        if empty:
            logger.warning(
                "%d node LÁ định vị được nhưng content RỖNG (nghi phân tầng sai: node đáng lẽ là CHA"
                " của node kế tiếp) — không mất chữ, nhưng chunk không dùng được", empty,
            )

        out = {k: toc.get(k) for k in METADATA_KEYS}
        out["source_file"] = out["source_file"] or source_path.name
        out["chapters"] = top_chunks
        return out

    @staticmethod
    def _count_matches(nodes: list[dict]) -> tuple[int, Counter, int]:
        matched = 0
        empty = 0
        reasons: Counter = Counter()
        for n in nodes:
            has_children = any(n.get(k) for k in DEPTH_CHILD_KEYS.values())
            is_null_leaf = not has_children and n.get("content") is None and not n.get("heading_bbox")
            if is_null_leaf:
                reasons[n.get("unmatched_reason", "text_not_found")] += 1
            else:
                matched += 1
                if not has_children and not n.get("content"):
                    empty += 1
            for k in DEPTH_CHILD_KEYS.values():
                m, r, e = ChunkDocumentBuilder._count_matches(n.get(k, []))
                matched += m
                reasons += r
                empty += e
        return matched, reasons, empty


class ChunkPipeline:
    def __init__(self, config: ChunkConfig) -> None:
        self._config = config
        self._builder = ChunkDocumentBuilder(config)

    def run(self) -> None:
        pairs = self._discover_pairs()
        if not pairs:
            logger.info("Không có cặp file nào trong %s / %s", self._config.toc_dir, self._config.source_dir)
            return
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        for toc_path, source_path, out_path in pairs:
            try:
                logger.info("=" * 60)
                logger.info("Processing: %s", toc_path.name)
                result = self._builder.build(toc_path, source_path)
                out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("  Saved → %s", out_path.name)
            except Exception:
                logger.exception("Lỗi khi xử lý %s", toc_path.name)

    def _discover_pairs(self) -> list[tuple[Path, Path, Path]]:
        pairs: list[tuple[Path, Path, Path]] = []
        self._config.toc_dir.mkdir(parents=True, exist_ok=True)
        toc_files = (
            [self._config.toc_dir / f"{stem}_toc_structure.json" for stem in self._config.files]
            if self._config.files
            else sorted(self._config.toc_dir.glob("*_toc_structure.json"))
        )
        for toc_path in toc_files:
            stem = toc_path.stem[: -len("_toc_structure")] if toc_path.stem.endswith("_toc_structure") else toc_path.stem
            source_path = self._config.source_dir / f"{stem}_dpt3.json"
            if not source_path.exists():
                logger.warning("Không tìm thấy %s cho %s", source_path.name, toc_path.name)
                continue
            pairs.append((toc_path, source_path, self._config.output_dir / f"{stem}_chunks.json"))
        return pairs


def main() -> None:
    ChunkPipeline(ChunkConfig()).run()


if __name__ == "__main__":
    main()
