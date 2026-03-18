from __future__ import annotations

import html
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.services.pipeline.markdown_service import MarkdownProcessingService, PAGE_BREAK_MARKER
from app.services.pipeline.prompts import TOC_METADATA_KEYS

_RE_MD_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_RE_NUMBERED = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+){0,5})|(?:[IVXLCM]{1,8})|(?:[A-Z]))[.)]?\s+.+$",
    flags=re.IGNORECASE,
)
_RE_CHAPTER_PREFIX = re.compile(
    r"^\s*(?:chuong|phan|buoc|muc|dieu|phu\s+luc)\s+[\w.-]+",
    flags=re.IGNORECASE,
)
_RE_SPLIT_LINES = re.compile(r".*(?:\n|$)")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_TABLE_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", flags=re.IGNORECASE | re.DOTALL)
_RE_INLINE_BOLD_HEADING = re.compile(r"^\s*\*\*(.+?)\*\*")
_RE_MULTI_SPACE = re.compile(r"\s+")
_RE_PREFIX_WORDS = re.compile(
    r"^\s*(?:chuong|phan|buoc|muc|dieu|phu\s+luc)\s*[:.\-\divxlcm]*\s*",
    flags=re.IGNORECASE,
)
_RE_LEADING_NUMBERING = re.compile(r"^\s*[\divxlcm]+(?:\.[\divxlcm]+)*[.)]?\s*")
_RE_PAGE_LABEL = re.compile(r"^\s*(?:\d{1,4}|[ivxlcdm]{1,8})\s*$", flags=re.IGNORECASE)
_RE_SECTION_SIGNATURE = re.compile(
    r"^\s*(?:(phu\s+luc|chuong|phan|buoc|muc|dieu)\s+([a-z0-9ivxlcdm.]+)|([a-z0-9ivxlcdm]+(?:\.[a-z0-9ivxlcdm]+)*))[.)]?\b",
    flags=re.IGNORECASE,
)

_TITLE_CONNECTOR_WORDS = {
    "va",
    "và",
    "ve",
    "về",
    "cua",
    "của",
    "cho",
    "theo",
    "tai",
    "tại",
    "o",
    "ở",
    "duoi",
    "dưới",
    "tren",
    "trên",
}

_GENERIC_TITLE_TOKENS = {
    "huong",
    "dan",
    "chan",
    "doan",
    "dieu",
    "tri",
    "quan",
    "ly",
    "tai",
    "tram",
    "y",
    "te",
    "xa",
    "nguoi",
    "lon",
    "doi",
    "tuong",
    "ap",
    "dung",
    "kham",
    "lam",
    "sang",
    "xet",
    "nghiem",
    "va",
    "buoc",
    "phan",
    "chuong",
    "muc",
    "dieu",
    "phu",
    "luc",
    "quy",
    "trinh",
    "so",
    "do",
    "cac",
    "nguye",
    "nguyen",
    "tac",
    "cho",
    "tuyen",
    "tren",
    "ve",
}


@dataclass
class HeadingCandidate:
    start: int
    end: int
    text: str


@dataclass
class AssignedNode:
    title: str
    children: list["AssignedNode"]
    match_candidate_index: int | None = None
    match_start: int | None = None
    heading_end: int | None = None
    match_score: float | None = None
    start_char: int | None = None
    end_char: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    is_suspect: bool = False
    content: str | None = None


class FuzzyChunkingService:
    def __init__(self, markdown_service: MarkdownProcessingService) -> None:
        self._markdown_service = markdown_service

    def build_chunk_payload(self, clean_text: str, toc: dict[str, Any], score_threshold: float) -> dict[str, Any]:
        chapters = self._normalize_toc_nodes(toc.get("chapters", []))
        body_start = self._markdown_service.find_body_start(clean_text)
        if not chapters:
            chapters = self._fallback_toc_from_text(clean_text, body_start=body_start)

        assigned_roots = [self._to_assigned_node(node) for node in chapters]
        candidates = self._extract_heading_candidates(clean_text, body_start=body_start)
        self._assign_match_positions(assigned_roots, candidates, score_threshold=score_threshold)
        self._infer_missing_positions(assigned_roots, text_len=len(clean_text))
        self._populate_content_and_pages(assigned_roots, clean_text, score_threshold=score_threshold)

        payload = {key: toc.get(key) for key in TOC_METADATA_KEYS}
        payload["chapters"] = [self._assigned_node_to_json(node) for node in assigned_roots]
        return payload

    def _fallback_toc_from_text(self, clean_text: str, body_start: int = 0) -> list[dict[str, Any]]:
        candidates = self._extract_heading_candidates(clean_text, body_start=body_start)
        titles: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.text.lower()
            if key in seen:
                continue
            seen.add(key)
            titles.append(candidate.text)
            if len(titles) >= 60:
                break
        return [{"title": title, "sections": []} for title in titles]

    def _extract_heading_candidates(self, text: str, body_start: int = 0) -> list[HeadingCandidate]:
        candidates: list[HeadingCandidate] = []
        seen: set[tuple[int, int, str]] = set()
        for match in _RE_SPLIT_LINES.finditer(text):
            start, end = match.start(), match.end()
            if start < body_start:
                continue
            raw_line = match.group().strip()
            if not raw_line or raw_line == PAGE_BREAK_MARKER:
                continue

            table_cells = self._extract_table_cells(raw_line, line_start=start)
            if table_cells:
                if self._is_probable_toc_row(table_cells):
                    continue
                for cell_start, cell_end, cell_text in table_cells:
                    cleaned = self._clean_heading_candidate(cell_text)
                    if not cleaned or not (
                        self._looks_like_heading(cleaned)
                        or self._looks_like_table_cell_heading(cleaned)
                    ):
                        continue
                    key = (cell_start, cell_end, cleaned)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(
                        HeadingCandidate(start=cell_start, end=cell_end, text=cleaned)
                    )
                continue

            inline_bold = _RE_INLINE_BOLD_HEADING.match(raw_line)
            if inline_bold:
                cleaned = self._clean_heading_candidate(inline_bold.group(1))
                if cleaned and (
                    self._looks_like_heading(cleaned)
                    or self._looks_like_table_cell_heading(cleaned)
                ):
                    key = (
                        start + inline_bold.start(1),
                        start + inline_bold.end(1),
                        cleaned,
                    )
                    if key not in seen:
                        seen.add(key)
                        candidates.append(
                            HeadingCandidate(
                                start=start + inline_bold.start(1),
                                end=start + inline_bold.end(1),
                                text=cleaned,
                            )
                        )
                    continue

            cleaned = self._clean_heading_candidate(raw_line)
            if not cleaned or not self._looks_like_heading(cleaned):
                continue
            key = (start, end, cleaned)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(HeadingCandidate(start=start, end=end, text=cleaned))
        return self._augment_multiline_candidates(candidates, text)

    def _clean_heading_candidate(self, line: str) -> str | None:
        text = self._clean_text_fragment(line)
        text = text.strip("*_# ")
        text = _RE_MULTI_SPACE.sub(" ", text).strip()
        if len(text) < 2:
            return None
        if re.fullmatch(r"[\d.,\-\s]+", text):
            return None
        if re.match(r"^\s*[-+•>|]", text):
            return None
        if re.search(r"\.{4,}", text):
            return None
        if len(text) > 250:
            text = text[:250].strip()
        return text

    def _clean_text_fragment(self, value: str) -> str:
        text = html.unescape(_RE_HTML_TAG.sub(" ", value))
        return _RE_MULTI_SPACE.sub(" ", text).strip()

    def _looks_like_heading(self, text: str) -> bool:
        plain = self._clean_text_fragment(text)
        normalized = self._remove_accents(plain).lower()
        if _RE_MD_HEADING.match(text) or _RE_NUMBERED.match(plain) or _RE_CHAPTER_PREFIX.match(normalized):
            return True
        if self._is_short_title_phrase(plain):
            return True
        alpha_only = re.sub(r"[^A-Za-zÀ-ỹ]", "", plain)
        return bool(2 <= len(plain) <= 120 and alpha_only and plain == plain.upper())

    def _is_short_title_phrase(self, text: str) -> bool:
        plain = text.strip()
        if not plain or len(plain) > 120:
            return False
        if plain.count(",") > 1 or re.search(r"[;!?]", plain):
            return False

        words = [word.strip("()[]{}.,:;-") for word in plain.replace("/", " ").split()]
        alpha_words = [word for word in words if re.search(r"[A-Za-zÀ-ỹ]", word)]
        if not (2 <= len(alpha_words) <= 12):
            return False

        capitalized = 0
        for word in alpha_words:
            normalized = self._remove_accents(word).lower()
            if normalized in _TITLE_CONNECTOR_WORDS:
                capitalized += 1
            elif word[:1].isupper() or word.upper() == word:
                capitalized += 1
        return capitalized / max(len(alpha_words), 1) >= 0.7

    def _looks_like_table_cell_heading(self, text: str) -> bool:
        plain = text.strip()
        if not plain or len(plain) > 80:
            return False
        if plain.endswith(".") or plain.count(",") > 0 or re.search(r"[;!?]", plain):
            return False

        words = [word.strip("()[]{}.,:;-") for word in plain.split()]
        alpha_words = [word for word in words if re.search(r"[A-Za-zÀ-ỹ]", word)]
        if not (2 <= len(alpha_words) <= 8):
            return False
        if not (alpha_words[0][:1].isupper() or alpha_words[0].upper() == alpha_words[0]):
            return False
        return True

    def _extract_table_cells(
        self,
        raw_line: str,
        *,
        line_start: int,
    ) -> list[tuple[int, int, str]]:
        cells: list[tuple[int, int, str]] = []
        for cell_match in _RE_TABLE_CELL.finditer(raw_line):
            cell_text = self._clean_text_fragment(cell_match.group(1))
            if not cell_text:
                continue
            cells.append(
                (
                    line_start + cell_match.start(1),
                    line_start + cell_match.end(1),
                    cell_text,
                )
            )
        return cells

    def _is_probable_toc_row(
        self,
        cells: list[tuple[int, int, str]],
    ) -> bool:
        if len(cells) < 2:
            return False
        trailing_texts = [text for _, _, text in cells[1:] if text]
        if not trailing_texts:
            return False
        return all(_RE_PAGE_LABEL.fullmatch(text) for text in trailing_texts)

    def _augment_multiline_candidates(
        self,
        candidates: list[HeadingCandidate],
        text: str,
    ) -> list[HeadingCandidate]:
        sorted_candidates = sorted(candidates, key=lambda item: (item.start, item.end))
        augmented = list(sorted_candidates)
        seen = {(item.start, item.end, item.text) for item in sorted_candidates}

        for index, candidate in enumerate(sorted_candidates):
            merged = candidate
            for next_index in range(index + 1, min(index + 3, len(sorted_candidates))):
                next_candidate = sorted_candidates[next_index]
                if not self._can_merge_candidates(merged, next_candidate, text):
                    break
                merged = HeadingCandidate(
                    start=merged.start,
                    end=next_candidate.end,
                    text=f"{merged.text} {next_candidate.text}".strip(),
                )
                key = (merged.start, merged.end, merged.text)
                if key not in seen:
                    seen.add(key)
                    augmented.append(merged)

        augmented.sort(key=lambda item: (item.start, item.end, len(item.text)))
        return augmented

    def _can_merge_candidates(
        self,
        first: HeadingCandidate,
        second: HeadingCandidate,
        text: str,
    ) -> bool:
        gap = text[first.end:second.start]
        if PAGE_BREAK_MARKER in gap:
            return False
        if gap.strip():
            return False
        if second.start - first.start > 260:
            return False
        if len(first.text) + len(second.text) > 260:
            return False
        return self._looks_like_heading(first.text) and self._looks_like_heading(second.text)

    def _assign_match_positions(
        self,
        roots: list[AssignedNode],
        candidates: list[HeadingCandidate],
        score_threshold: float,
    ) -> None:
        self._assign_level_positions(
            nodes=roots,
            candidates=candidates,
            start_idx=0,
            end_idx=len(candidates),
            score_threshold=score_threshold,
        )

    def _assign_level_positions(
        self,
        *,
        nodes: list[AssignedNode],
        candidates: list[HeadingCandidate],
        start_idx: int,
        end_idx: int,
        score_threshold: float,
    ) -> None:
        search_idx = start_idx
        for node in nodes:
            best_idx = -1
            best_score = 0.0
            for idx in range(search_idx, end_idx):
                candidate = candidates[idx]
                score = self._match_score(node.title, candidate.text)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx >= 0 and best_score >= score_threshold:
                chosen = candidates[best_idx]
                node.match_candidate_index = best_idx
                node.match_start = chosen.start
                node.heading_end = chosen.end
                node.match_score = round(best_score, 3)
                search_idx = best_idx + 1

        fallback_start = start_idx
        for index, node in enumerate(nodes):
            next_sibling_idx = self._find_next_matched_sibling_index(
                nodes=nodes,
                start_position=index + 1,
            )
            child_start = (
                node.match_candidate_index + 1
                if node.match_candidate_index is not None
                else fallback_start
            )
            child_end = next_sibling_idx if next_sibling_idx is not None else end_idx
            if node.children:
                self._assign_level_positions(
                    nodes=node.children,
                    candidates=candidates,
                    start_idx=child_start,
                    end_idx=child_end,
                    score_threshold=score_threshold,
                )
            if node.match_candidate_index is not None:
                fallback_start = node.match_candidate_index + 1

    def _find_next_matched_sibling_index(
        self,
        *,
        nodes: list[AssignedNode],
        start_position: int,
    ) -> int | None:
        for node in nodes[start_position:]:
            if node.match_candidate_index is not None:
                return node.match_candidate_index
        return None

    def _infer_missing_positions(self, roots: list[AssignedNode], text_len: int) -> None:
        flat_nodes = self._flatten_nodes_preorder(roots)

        for node in reversed(flat_nodes):
            if node.match_start is None and node.children:
                child_starts = [child.match_start for child in node.children if child.match_start is not None]
                if child_starts:
                    node.match_start = min(child_starts)
                    node.heading_end = node.match_start
        assigned_starts = sorted(
            node.match_start for node in flat_nodes if node.match_start is not None
        )

        for node in flat_nodes:
            if node.match_start is None:
                continue
            next_start = self._find_next_start(assigned_starts, node.match_start)
            node.start_char = node.match_start
            node.end_char = next_start if next_start is not None else text_len

        for node in reversed(flat_nodes):
            if node.start_char is None and node.children:
                starts = [child.start_char for child in node.children if child.start_char is not None]
                ends = [child.end_char for child in node.children if child.end_char is not None]
                if starts:
                    node.start_char = min(starts)
                if ends:
                    node.end_char = max(ends)

    def _populate_content_and_pages(
        self,
        roots: list[AssignedNode],
        clean_text: str,
        score_threshold: float,
    ) -> None:
        marker_positions = [m.start() for m in re.finditer(re.escape(PAGE_BREAK_MARKER), clean_text)]

        for node in self._flatten_nodes_preorder(roots):
            if node.start_char is None or node.end_char is None:
                node.content = None
                node.page_start = None
                node.page_end = None
                node.is_suspect = False
                continue

            content_start = node.heading_end if node.heading_end is not None else node.start_char
            content_start = max(content_start, node.start_char)
            child_starts = [
                child.start_char
                for child in node.children
                if child.start_char is not None and child.start_char >= content_start
            ]
            content_end = min(child_starts) if child_starts else node.end_char
            content_end = max(min(content_end, node.end_char), content_start)

            content = clean_text[content_start:content_end].strip()
            node.content = content or None
            node.page_start = self._char_to_page(node.start_char, marker_positions)
            page_end_idx = max(content_end - 1, node.start_char)
            node.page_end = self._char_to_page(page_end_idx, marker_positions)
            node.is_suspect = bool(node.match_score is not None and node.match_score < score_threshold)

    def _normalize_toc_nodes(self, nodes: Any) -> list[dict[str, Any]]:
        if not isinstance(nodes, list):
            return []
        normalized: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            title = node.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            children = None
            for key in ("sections", "subsections", "subsubsections", "subsubsubsections", "children"):
                if isinstance(node.get(key), list):
                    children = node.get(key)
                    break
            normalized.append(
                {"title": title.strip(), "sections": self._normalize_toc_nodes(children or [])}
            )
        return normalized

    def _to_assigned_node(self, node: dict[str, Any]) -> AssignedNode:
        return AssignedNode(
            title=node.get("title", "").strip(),
            children=[self._to_assigned_node(child) for child in node.get("sections", [])],
        )

    def _assigned_node_to_json(self, node: AssignedNode) -> dict[str, Any]:
        return {
            "title": node.title,
            "start_char": node.start_char,
            "end_char": node.end_char,
            "page_start": node.page_start,
            "page_end": node.page_end,
            "match_score": node.match_score,
            "is_suspect": node.is_suspect,
            "content": node.content,
            "sections": [self._assigned_node_to_json(child) for child in node.children],
        }

    def _flatten_nodes_preorder(self, roots: list[AssignedNode]) -> list[AssignedNode]:
        result: list[AssignedNode] = []
        stack = list(reversed(roots))
        while stack:
            node = stack.pop()
            result.append(node)
            for child in reversed(node.children):
                stack.append(child)
        return result

    def _find_next_start(self, starts: list[int], current_start: int) -> int | None:
        idx = bisect_right(starts, current_start)
        if idx >= len(starts):
            return None
        return starts[idx]

    def _match_score(self, toc_title: str, candidate: str) -> float:
        toc_tokens = self._normalize_for_match(toc_title)
        cand_tokens = self._normalize_for_match(candidate)
        if not toc_tokens or not cand_tokens:
            return 0.0

        toc_set = set(toc_tokens)
        cand_set = set(cand_tokens)
        inter = len(toc_set & cand_set)
        if inter == 0:
            return 0.0

        recall = inter / len(toc_set)
        precision = inter / len(cand_set)
        f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0

        toc_norm = " ".join(toc_tokens)
        cand_norm = " ".join(cand_tokens)
        seq_ratio = SequenceMatcher(None, toc_norm, cand_norm).ratio()
        score = max(f1, (0.65 * f1) + (0.35 * seq_ratio))
        if cand_norm.startswith(toc_norm):
            score = max(score, recall)

        toc_core = self._core_tokens(toc_tokens)
        cand_core = self._core_tokens(cand_tokens)
        if toc_core:
            core_overlap = len(set(toc_core) & set(cand_core)) / len(set(toc_core))
            if core_overlap == 0:
                score *= 0.3
            elif core_overlap < 0.5:
                score *= 0.7
            elif core_overlap >= 0.8:
                score = min(1.0, score + 0.05)

        if len(toc_tokens) >= 6 and len(cand_tokens) < int(len(toc_tokens) * 0.55):
            score *= 0.7

        toc_signature = self._extract_signature(toc_title)
        candidate_signature = self._extract_signature(candidate)
        if toc_signature and candidate_signature and toc_signature[0] == candidate_signature[0]:
            if toc_signature[1] == candidate_signature[1]:
                score = min(1.0, score + 0.12)
            else:
                score *= 0.2

        return min(max(score, 0.0), 1.0)

    def _normalize_for_match(self, value: str) -> list[str]:
        text = value.strip().lower()
        text = self._remove_accents(text)
        text = _RE_PREFIX_WORDS.sub("", text)
        text = _RE_LEADING_NUMBERING.sub("", text)
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = _RE_MULTI_SPACE.sub(" ", text).strip()
        return [token for token in text.split(" ") if token]

    def _remove_accents(self, text: str) -> str:
        normalized = unicodedata.normalize("NFD", text)
        return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

    def _extract_signature(self, value: str) -> tuple[str, str] | None:
        normalized = self._remove_accents(value).lower().strip()
        match = _RE_SECTION_SIGNATURE.match(normalized)
        if match is None:
            return None
        if match.group(1) and match.group(2):
            return (match.group(1).replace(" ", "_"), match.group(2))
        if match.group(3):
            return ("number", match.group(3))
        return None

    def _core_tokens(self, tokens: list[str]) -> list[str]:
        return [token for token in tokens if token not in _GENERIC_TITLE_TOKENS]

    def _char_to_page(self, idx: int, marker_positions: list[int]) -> int:
        return bisect_right(marker_positions, idx) + 1
