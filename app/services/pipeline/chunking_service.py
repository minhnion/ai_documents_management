from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

from app.services.pipeline.markdown_service import MarkdownProcessingService, PAGE_BREAK_MARKER
from app.services.pipeline.prompts import TOC_METADATA_KEYS

_RE_MD_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_RE_NUMBERED = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+){0,5})|(?:[IVXLCM]{1,8})|(?:[A-Z]))[.)]?\s+.+$",
    flags=re.IGNORECASE,
)
_RE_CHAPTER_PREFIX = re.compile(
    r"^\s*(?:chuong|phan|buoc|muc|dieu)\s+[\w.-]+",
    flags=re.IGNORECASE,
)
_RE_SPLIT_LINES = re.compile(r".*(?:\n|$)")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_MULTI_SPACE = re.compile(r"\s+")
_RE_PREFIX_WORDS = re.compile(
    r"^\s*(?:chuong|phan|buoc|muc|dieu)\s*[:.\-\divxlcm]*\s*",
    flags=re.IGNORECASE,
)
_RE_LEADING_NUMBERING = re.compile(r"^\s*[\divxlcm]+(?:\.[\divxlcm]+)*[.)]?\s*")


@dataclass
class HeadingCandidate:
    start: int
    end: int
    text: str


@dataclass
class AssignedNode:
    title: str
    children: list["AssignedNode"]
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

            cleaned = self._clean_heading_candidate(raw_line)
            if not cleaned or not self._looks_like_heading(raw_line):
                continue

            key = (start, end, cleaned)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(HeadingCandidate(start=start, end=end, text=cleaned))
        candidates.sort(key=lambda item: (item.start, item.end))
        return candidates

    def _clean_heading_candidate(self, line: str) -> str | None:
        text = _RE_HTML_TAG.sub(" ", line).strip()
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

    def _looks_like_heading(self, line: str) -> bool:
        if _RE_MD_HEADING.match(line) or _RE_NUMBERED.match(line) or _RE_CHAPTER_PREFIX.match(line):
            return True
        stripped = _RE_HTML_TAG.sub(" ", line).strip()
        alpha_only = re.sub(r"[^A-Za-zÀ-ỹ]", "", stripped)
        return bool(2 <= len(stripped) <= 120 and alpha_only and stripped == stripped.upper())

    def _assign_match_positions(
        self,
        roots: list[AssignedNode],
        candidates: list[HeadingCandidate],
        score_threshold: float,
    ) -> None:
        flat_nodes = self._flatten_nodes_preorder(roots)
        next_candidate_idx = 0
        for node in flat_nodes:
            best_idx = -1
            best_score = 0.0
            for idx in range(next_candidate_idx, len(candidates)):
                candidate = candidates[idx]
                score = self._match_score(node.title, candidate.text)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx >= 0 and best_score >= score_threshold:
                chosen = candidates[best_idx]
                node.match_start = chosen.start
                node.heading_end = chosen.end
                node.match_score = round(best_score, 3)
                next_candidate_idx = best_idx + 1

    def _infer_missing_positions(self, roots: list[AssignedNode], text_len: int) -> None:
        flat_nodes = self._flatten_nodes_preorder(roots)
        assigned_starts = sorted(node.match_start for node in flat_nodes if node.match_start is not None)

        for node in reversed(flat_nodes):
            if node.match_start is None and node.children:
                child_starts = [child.match_start for child in node.children if child.match_start is not None]
                if child_starts:
                    node.match_start = min(child_starts)
                    node.heading_end = node.match_start

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
            content_end = max(node.end_char, content_start)
            node.content = clean_text[content_start:content_end].strip()
            node.page_start = self._char_to_page(node.start_char, marker_positions)
            node.page_end = self._char_to_page(max(node.end_char - 1, node.start_char), marker_positions)
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
        if cand_norm.startswith(toc_norm):
            f1 = max(f1, recall)

        return min(max(f1, 0.0), 1.0)

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

    def _char_to_page(self, idx: int, marker_positions: list[int]) -> int:
        return bisect_right(marker_positions, idx) + 1
