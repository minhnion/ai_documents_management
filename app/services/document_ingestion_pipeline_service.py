from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.section import Section

logger = logging.getLogger(__name__)

try:
    from landingai_ade import LandingAIADE
except Exception:  # pragma: no cover - optional dependency at runtime
    LandingAIADE = None

PAGE_BREAK_MARKER = "<!-- PAGE_BREAK -->"
OCR_MD_FILENAME = "extraction.md"
CLEAN_MD_FILENAME = "extraction_clean.md"
TOC_FILENAME = "toc_structure.json"
CHUNKS_FILENAME = "chunks.json"

_RE_ANCHOR = re.compile(r"<a\b[^>]*>\s*</a>", flags=re.IGNORECASE)
_RE_HTML_COMMENT_NON_PB = re.compile(
    r"<!--(?!\s*PAGE\s*BREAK\s*-->).*?-->",
    flags=re.DOTALL | re.IGNORECASE,
)
_RE_PAGE_BREAK = re.compile(r"<!--\s*PAGE[\s_]*BREAK\s*-->", flags=re.IGNORECASE)
_RE_MD_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_RE_NUMBERED = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+){0,5})|(?:[IVXLCM]{1,8})|(?:[A-Z]))[.)]?\s+.+$",
    flags=re.IGNORECASE,
)
_RE_CHAPTER_PREFIX = re.compile(
    r"^\s*(?:chương|phần|bước|mục|điều)\s+[\w.-]+",
    flags=re.IGNORECASE,
)
_RE_SPLIT_LINES = re.compile(r".*(?:\n|$)")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_MULTI_SPACE = re.compile(r"\s+")
_RE_PREFIX_WORDS = re.compile(
    r"^\s*(?:chuong|ph\u1ea7n|buoc|muc|dieu)\s*[:.\-\divxlcm]*\s*",
    flags=re.IGNORECASE,
)
_RE_LEADING_NUMBERING = re.compile(r"^\s*[\divxlcm]+(?:\.[\divxlcm]+)*[.)]?\s*")
_RE_TOC_HEADER = re.compile(
    r"^\s*(?:M\u1ee4C\s*L\u1ee4C|MUC\s*LUC|TABLE\s+OF\s+CONTENTS|CONTENTS)\s*$",
    flags=re.IGNORECASE,
)

_TOC_METADATA_KEYS = [
    "title",
    "publisher",
    "decision_number",
    "specialty",
    "date",
    "isbn_electronic",
    "isbn_print",
    "total_pages",
    "source_file",
]


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


class DocumentIngestionPipelineService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def process_document(
        self,
        guideline_id: int,
        version_id: int,
        document: Document,
    ) -> dict[str, object]:
        self._validate_pipeline_settings()

        pdf_path = self._resolve_pdf_path(document)
        artifact_dir = self._build_artifact_dir(guideline_id=guideline_id, version_id=version_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Pipeline start | guideline_id=%s version_id=%s file=%s",
            guideline_id,
            version_id,
            pdf_path.name,
        )

        raw_md = await self._ocr_markdown(pdf_path)
        clean_md = self._clean_markdown(raw_md)
        toc = await self._build_toc(clean_md, source_file=pdf_path.name)
        chunk_payload = self._chunk_with_fuzzy_matching(clean_md, toc)

        self._write_artifacts(
            artifact_dir=artifact_dir,
            raw_md=raw_md,
            clean_md=clean_md,
            toc=toc,
            chunk_payload=chunk_payload,
        )
        persist_stats = await self._persist_chunk_payload(
            version_id=version_id,
            document=document,
            chunk_payload=chunk_payload,
            clean_text=clean_md,
        )
        logger.info(
            "Pipeline done | guideline_id=%s version_id=%s sections=%s chunks=%s artifacts=%s",
            guideline_id,
            version_id,
            persist_stats.get("section_count"),
            persist_stats.get("chunk_count"),
            artifact_dir.as_posix(),
        )
        return {
            "artifact_dir": artifact_dir.as_posix(),
            **persist_stats,
        }

    def _validate_pipeline_settings(self) -> None:
        if not settings.LANDINGAI_API_KEY.strip():
            raise BadRequestException("LANDINGAI_API_KEY is required for OCR pipeline.")
        if not settings.OPENAI_API_KEY.strip():
            raise BadRequestException("OPENAI_API_KEY is required for TOC pipeline.")
        if not settings.LANDINGAI_API_URL.strip():
            raise BadRequestException("LANDINGAI_API_URL is required for OCR pipeline.")
        if not settings.OPENAI_API_URL.strip():
            raise BadRequestException("OPENAI_API_URL is required for TOC pipeline.")
        if not settings.LANDINGAI_MODEL_NAME.strip():
            raise BadRequestException("LANDINGAI_MODEL_NAME is required for OCR pipeline.")
        if not settings.OPENAI_MODEL_NAME.strip():
            raise BadRequestException("OPENAI_MODEL_NAME is required for TOC pipeline.")
        if not 0.0 < float(settings.SCORE_THRESHOLD) < 1.0:
            raise BadRequestException("SCORE_THRESHOLD must be > 0 and < 1.")

    def _resolve_pdf_path(self, document: Document) -> Path:
        if document.storage_uri is None or not document.storage_uri.strip():
            raise UnprocessableEntityException("Document storage_uri is missing.")
        path = Path(document.storage_uri.strip())
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        if not path.exists() or not path.is_file():
            raise UnprocessableEntityException("Uploaded PDF file does not exist on local storage.")
        return path

    def _build_artifact_dir(self, guideline_id: int, version_id: int) -> Path:
        storage_root = Path(settings.LOCAL_STORAGE_ROOT)
        if not storage_root.is_absolute():
            storage_root = (Path.cwd() / storage_root).resolve()
        else:
            storage_root = storage_root.resolve()
        return storage_root / "guidelines" / str(guideline_id) / str(version_id) / "pipeline"

    async def _ocr_markdown(self, pdf_path: Path) -> str:
        sdk_error: Exception | None = None
        if settings.LANDINGAI_USE_SDK:
            try:
                markdown = await self._ocr_markdown_via_sdk(pdf_path)
                if markdown is not None and markdown.strip():
                    logger.info("LandingAI OCR via SDK succeeded.")
                    return markdown
                sdk_error = UnprocessableEntityException(
                    "LandingAI SDK OCR returned empty markdown content."
                )
            except Exception as exc:  # noqa: BLE001 - keep full fallback path
                sdk_error = exc
                logger.warning(
                    "LandingAI SDK OCR failed, fallback to HTTP endpoint. error=%s",
                    getattr(exc, "detail", str(exc)),
                )

        try:
            markdown = await self._ocr_markdown_via_http(pdf_path)
            logger.info("LandingAI OCR via HTTP succeeded.")
            return markdown
        except Exception as http_exc:  # noqa: BLE001 - return combined error
            if sdk_error is not None:
                sdk_msg = getattr(sdk_error, "detail", str(sdk_error))
                http_msg = getattr(http_exc, "detail", str(http_exc))
                raise UnprocessableEntityException(
                    f"LandingAI OCR failed via SDK and HTTP. sdk_error={sdk_msg}; http_error={http_msg}"
                ) from http_exc
            raise

    async def _ocr_markdown_via_sdk(self, pdf_path: Path) -> str | None:
        if LandingAIADE is None:
            raise BadRequestException(
                "LANDINGAI_USE_SDK=true but dependency 'landingai-ade' is missing. "
                "Install requirements or set LANDINGAI_USE_SDK=false."
            )

        def _parse_sync() -> str | None:
            client = LandingAIADE(apikey=settings.LANDINGAI_API_KEY.strip())
            result = client.parse(
                document=pdf_path,
                model=settings.LANDINGAI_MODEL_NAME.strip(),
            )
            return getattr(result, "markdown", None)

        try:
            markdown = await asyncio.to_thread(_parse_sync)
            return markdown if isinstance(markdown, str) else None
        except Exception as exc:
            raise UnprocessableEntityException(
                f"LandingAI SDK OCR failed: {exc}"
            ) from exc

    async def _ocr_markdown_via_http(self, pdf_path: Path) -> str:
        headers = {
            "Authorization": f"Bearer {settings.LANDINGAI_API_KEY.strip()}",
            "apikey": settings.LANDINGAI_API_KEY.strip(),
        }
        data = {"model": settings.LANDINGAI_MODEL_NAME.strip()}
        timeout = httpx.Timeout(300.0, connect=60.0)
        endpoints = self._resolve_landingai_endpoints(settings.LANDINGAI_API_URL.strip())
        errors: list[str] = []

        for endpoint in endpoints:
            file_fields = self._resolve_landingai_file_fields(endpoint)
            for file_field in file_fields:
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        with pdf_path.open("rb") as file_obj:
                            response = await client.post(
                                endpoint,
                                headers=headers,
                                data=data,
                                files={file_field: (pdf_path.name, file_obj, "application/pdf")},
                            )
                except Exception as exc:
                    errors.append(f"{endpoint} [{file_field}]: request failed: {exc}")
                    continue

                if response.status_code in (401, 403):
                    raise UnprocessableEntityException(
                        f"LandingAI OCR unauthorized ({response.status_code}) at {endpoint}: {response.text[:500]}"
                    )

                if response.status_code >= 400:
                    errors.append(
                        f"{endpoint} [{file_field}]: http {response.status_code}: {response.text[:300]}"
                    )
                    continue

                payload = self._safe_json(response.text)
                markdown = self._extract_markdown(payload)
                if markdown is None or not markdown.strip():
                    errors.append(f"{endpoint} [{file_field}]: response does not contain markdown")
                    continue
                return markdown

        raise UnprocessableEntityException(
            f"LandingAI OCR failed for all endpoint/file-field combinations: {' | '.join(errors)[:1500]}"
        )

    def _resolve_landingai_file_fields(self, endpoint: str) -> list[str]:
        endpoint_lower = endpoint.lower()
        if "/v1/ade/parse" in endpoint_lower:
            return ["document", "pdf", "file"]
        if "/v1/tools/document-analysis" in endpoint_lower:
            return ["pdf", "image", "document", "file"]
        return ["document", "pdf", "file", "image"]

    def _resolve_landingai_endpoints(self, configured_endpoint: str) -> list[str]:
        endpoint = configured_endpoint.strip().rstrip("/")
        if not endpoint:
            return []

        candidates = [endpoint]
        if endpoint.endswith("/v1/tools/document-analysis"):
            candidates.append(endpoint[: -len("/v1/tools/document-analysis")] + "/v1/ade/parse")
        elif endpoint.endswith("/v1/ade/parse"):
            candidates.append(endpoint[: -len("/v1/ade/parse")] + "/v1/tools/document-analysis")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = item.rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    async def _build_toc(self, clean_text: str, source_file: str) -> dict[str, Any]:
        phase1_input = self._extract_first_pages(clean_text, max_pages=40)
        phase1_prompt = self._build_phase1_prompt(phase1_input, source_file)
        phase1_result = await self._openai_json_completion(phase1_prompt)
        toc = self._ensure_toc_schema(phase1_result, source_file=source_file)

        if self._toc_is_shallow(toc):
            outline = self._extract_heading_outline(clean_text)
            phase2_prompt = self._build_phase2_prompt(outline=outline, metadata=toc)
            phase2_result = await self._openai_json_completion(phase2_prompt)
            toc = self._ensure_toc_schema(phase2_result, source_file=source_file)

        return toc

    def _clean_markdown(self, raw_text: str) -> str:
        text = _RE_ANCHOR.sub("", raw_text)
        text = _RE_HTML_COMMENT_NON_PB.sub("", text)
        text = _RE_PAGE_BREAK.sub(PAGE_BREAK_MARKER, text)
        return text

    def _chunk_with_fuzzy_matching(self, clean_text: str, toc: dict[str, Any]) -> dict[str, Any]:
        score_threshold = float(settings.SCORE_THRESHOLD)
        chapters = self._normalize_toc_nodes(toc.get("chapters", []))
        body_start = self._find_body_start(clean_text)
        if not chapters:
            chapters = self._fallback_toc_from_text(clean_text, body_start=body_start)
        assigned_roots = [self._to_assigned_node(node) for node in chapters]

        candidates = self._extract_heading_candidates(clean_text, body_start=body_start)
        self._assign_match_positions(assigned_roots, candidates, score_threshold=score_threshold)
        self._infer_missing_positions(assigned_roots, text_len=len(clean_text))
        self._populate_content_and_pages(assigned_roots, clean_text, score_threshold=score_threshold)

        payload = {key: toc.get(key) for key in _TOC_METADATA_KEYS}
        payload["chapters"] = [self._assigned_node_to_json(node) for node in assigned_roots]
        return payload

    def _fallback_toc_from_text(
        self,
        clean_text: str,
        body_start: int = 0,
    ) -> list[dict[str, Any]]:
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

    def _find_body_start(self, text: str) -> int:
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
                or re.fullmatch(r"[\d\s,.\-/]+", stripped)
            ):
                cursor += len(line)
                continue
            return cursor
        return 0

    def _extract_heading_candidates(
        self,
        text: str,
        body_start: int = 0,
    ) -> list[HeadingCandidate]:
        candidates: list[HeadingCandidate] = []
        seen: set[tuple[int, int, str]] = set()
        for match in _RE_SPLIT_LINES.finditer(text):
            start, end = match.start(), match.end()
            if start < body_start:
                continue
            raw_line = match.group().strip()
            if not raw_line:
                continue
            if raw_line == PAGE_BREAK_MARKER:
                continue

            cleaned = self._clean_heading_candidate(raw_line)
            if not cleaned:
                continue

            if not self._looks_like_heading(raw_line):
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
        if _RE_MD_HEADING.match(line):
            return True
        if _RE_NUMBERED.match(line):
            return True
        if _RE_CHAPTER_PREFIX.match(line):
            return True
        stripped = _RE_HTML_TAG.sub(" ", line).strip()
        alpha_only = re.sub(r"[^A-Za-zÀ-ỹ]", "", stripped)
        if 2 <= len(stripped) <= 120 and alpha_only and stripped == stripped.upper():
            return True
        return False

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
        assigned_starts = sorted(
            node.match_start for node in flat_nodes if node.match_start is not None
        )
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
            node.is_suspect = bool(
                node.match_score is not None and node.match_score < score_threshold
            )

    async def _persist_chunk_payload(
        self,
        version_id: int,
        document: Document,
        chunk_payload: dict[str, Any],
        clean_text: str,
    ) -> dict[str, int]:
        await self.db.execute(delete(Chunk).where(Chunk.version_id == version_id))
        await self.db.execute(delete(Section).where(Section.version_id == version_id))
        await self.db.flush()

        section_count = 0
        chunk_count = 0
        for idx, chapter in enumerate(chunk_payload.get("chapters", []), start=1):
            inserted_sections, inserted_chunks = await self._persist_section_tree(
                version_id=version_id,
                node=chapter,
                parent_id=None,
                level=1,
                order_index=idx,
                section_path=str(idx),
            )
            section_count += inserted_sections
            chunk_count += inserted_chunks

        document.page_count = self._estimate_page_count(clean_text)
        return {"section_count": section_count, "chunk_count": chunk_count}

    async def _persist_section_tree(
        self,
        version_id: int,
        node: dict[str, Any],
        parent_id: int | None,
        level: int,
        order_index: int,
        section_path: str,
    ) -> tuple[int, int]:
        section = Section(
            version_id=version_id,
            parent_id=parent_id,
            heading=node.get("title"),
            section_path=section_path,
            level=level,
            order_index=order_index,
            start_char=node.get("start_char"),
            end_char=node.get("end_char"),
            page_start=node.get("page_start"),
            page_end=node.get("page_end"),
            match_score=node.get("match_score"),
            is_suspect=bool(node.get("is_suspect", False)),
            content=node.get("content"),
        )
        self.db.add(section)
        await self.db.flush()

        chunk_count = 0
        section_text = node.get("content")
        if isinstance(section_text, str) and section_text.strip():
            chunk = Chunk(
                version_id=version_id,
                section_id=section.section_id,
                text=section_text,
                token_count=len(section_text.split()),
                page_start=node.get("page_start"),
                page_end=node.get("page_end"),
            )
            self.db.add(chunk)
            chunk_count = 1

        section_count = 1
        for idx, child in enumerate(node.get("sections", []), start=1):
            child_path = f"{section_path}.{idx}"
            child_sections, child_chunks = await self._persist_section_tree(
                version_id=version_id,
                node=child,
                parent_id=section.section_id,
                level=level + 1,
                order_index=idx,
                section_path=child_path,
            )
            section_count += child_sections
            chunk_count += child_chunks

        return section_count, chunk_count

    def _write_artifacts(
        self,
        artifact_dir: Path,
        raw_md: str,
        clean_md: str,
        toc: dict[str, Any],
        chunk_payload: dict[str, Any],
    ) -> None:
        (artifact_dir / OCR_MD_FILENAME).write_text(raw_md, encoding="utf-8")
        (artifact_dir / CLEAN_MD_FILENAME).write_text(clean_md, encoding="utf-8")
        (artifact_dir / TOC_FILENAME).write_text(
            json.dumps(toc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (artifact_dir / CHUNKS_FILENAME).write_text(
            json.dumps(chunk_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _openai_json_completion(self, prompt: str) -> dict[str, Any]:
        base_url = settings.OPENAI_API_URL.rstrip("/")
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY.strip()}",
            "Content-Type": "application/json",
        }
        responses_url = base_url + "/responses"
        responses_body: dict[str, Any] = {
            "model": settings.OPENAI_MODEL_NAME.strip(),
            "input": [
                {
                    "role": "system",
                    "content": "You are a strict JSON generator. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_output_tokens": 12000,
        }
        timeout = httpx.Timeout(120.0, connect=30.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    responses_url,
                    headers=headers,
                    json=responses_body,
                )
        except Exception as exc:
            raise UnprocessableEntityException(f"OpenAI request failed: {exc}") from exc

        if response.status_code < 400:
            payload = self._safe_json(response.text)
            content = self._extract_openai_content_from_responses(payload)
            if not isinstance(content, str) or not content.strip():
                raise UnprocessableEntityException("OpenAI responses API returned empty content.")
            return self._safe_json(self._strip_markdown_fence(content))

        logger.warning(
            "OpenAI responses API failed (%s), fallback to chat/completions.",
            response.status_code,
        )

        chat_url = base_url + "/chat/completions"
        chat_body: dict[str, Any] = {
            "model": settings.OPENAI_MODEL_NAME.strip(),
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a strict JSON generator. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(chat_url, headers=headers, json=chat_body)
            except Exception as exc:
                raise UnprocessableEntityException(f"OpenAI request failed: {exc}") from exc

            if response.status_code >= 400:
                # Retry once without response_format for compatibility.
                chat_body.pop("response_format", None)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(chat_url, headers=headers, json=chat_body)
        except Exception as exc:
            raise UnprocessableEntityException(f"OpenAI request failed: {exc}") from exc

        if response.status_code >= 400:
            raise UnprocessableEntityException(
                f"OpenAI completion failed ({response.status_code}): {response.text[:500]}"
            )

        payload = self._safe_json(response.text)
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            raise UnprocessableEntityException("OpenAI completion did not return content.")
        return self._safe_json(self._strip_markdown_fence(content))

    def _extract_openai_content_from_responses(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        output = payload.get("output")
        if not isinstance(output, list):
            return ""

        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                if not isinstance(content_item, dict):
                    continue
                if content_item.get("type") in ("output_text", "text"):
                    text_value = content_item.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        texts.append(text_value)
        return "\n".join(texts).strip()

    def _build_phase1_prompt(self, text: str, source_file: str) -> str:
        return (
            "Ban la he thong trich xuat cau truc tai lieu y te.\n"
            "Tra ve DUY NHAT JSON hop le, khong markdown, khong giai thich.\n"
            "Output schema gom metadata va chapters (title/sections de quy).\n"
            "Neu metadata khong tim thay thi de null.\n"
            f"source_file phai la '{source_file}'.\n\n"
            "Van ban OCR (40 trang dau):\n"
            f"{text[:120000]}"
        )

    def _build_phase2_prompt(self, outline: list[str], metadata: dict[str, Any]) -> str:
        outline_text = "\n".join(outline[:4000])
        return (
            "Ban la he thong xay dung cay TOC tai lieu y te tu danh sach tieu de.\n"
            "Tra ve DUY NHAT JSON hop le, khong markdown, khong giai thich.\n"
            "Giu nguyen metadata da co, chi tai tao chapters/sections theo outline.\n\n"
            f"CURRENT_METADATA_JSON:\n{json.dumps(metadata, ensure_ascii=False)}\n\n"
            f"OUTLINE_LINES:\n{outline_text}"
        )

    def _extract_first_pages(self, text: str, max_pages: int) -> str:
        parts = text.split(PAGE_BREAK_MARKER)
        if len(parts) <= 1:
            return text[:120000]
        return PAGE_BREAK_MARKER.join(parts[:max_pages])

    def _extract_heading_outline(self, text: str) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        body_start = self._find_body_start(text)
        for line in text[body_start:].splitlines():
            stripped = self._clean_heading_candidate(line) or ""
            if not stripped:
                continue
            if self._looks_like_heading(stripped):
                key = stripped.lower()
                if key in seen:
                    continue
                seen.add(key)
                lines.append(stripped)
        return lines

    def _toc_is_shallow(self, toc: dict[str, Any]) -> bool:
        chapters = toc.get("chapters", [])
        return self._count_sections(chapters) < 3

    def _count_sections(self, nodes: list[dict[str, Any]]) -> int:
        count = 0
        for node in nodes:
            children = node.get("sections", [])
            if isinstance(children, list):
                count += len(children)
                count += self._count_sections(children)
        return count

    def _ensure_toc_schema(self, payload: dict[str, Any], source_file: str) -> dict[str, Any]:
        result: dict[str, Any] = {key: payload.get(key) for key in _TOC_METADATA_KEYS}
        result["source_file"] = result.get("source_file") or source_file
        result["chapters"] = self._normalize_toc_nodes(payload.get("chapters", []))
        return result

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
            for key in ("sections", "subsections", "subsubsections", "children"):
                if isinstance(node.get(key), list):
                    children = node.get(key)
                    break
            normalized.append(
                {
                    "title": title.strip(),
                    "sections": self._normalize_toc_nodes(children or []),
                }
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

    def _estimate_page_count(self, clean_text: str) -> int:
        return clean_text.count(PAGE_BREAK_MARKER) + 1

    def _safe_json(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        raise UnprocessableEntityException("Cannot parse JSON response from external AI service.")

    def _strip_markdown_fence(self, content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()

    def _extract_markdown(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            value = payload.get("markdown")
            if isinstance(value, str):
                return value
            for child in payload.values():
                found = self._extract_markdown(child)
                if found:
                    return found
        if isinstance(payload, list):
            for child in payload:
                found = self._extract_markdown(child)
                if found:
                    return found
        return None
