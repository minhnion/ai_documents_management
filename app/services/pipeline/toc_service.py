from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import UnprocessableEntityException
from app.services.pipeline.markdown_service import MarkdownProcessingService
from app.services.pipeline.prompts import (
    MIN_SECTIONS_THRESHOLD,
    PHASE1_SYSTEM_PROMPT,
    PHASE2_SYSTEM_PROMPT,
    TOC_METADATA_KEYS,
    TOC_SCAN_PAGES,
    build_phase1_user_prompt,
    build_phase2_user_prompt,
)


class TocBuilderService:
    def __init__(self, markdown_service: MarkdownProcessingService) -> None:
        self._markdown_service = markdown_service

    async def build_toc(self, clean_text: str, source_file: str) -> dict[str, Any]:
        phase1_input = self._markdown_service.extract_first_pages(clean_text, max_pages=TOC_SCAN_PAGES)
        found_toc_page = self._markdown_service.has_toc_page(
            clean_text,
            max_pages=TOC_SCAN_PAGES,
        )
        phase1_prompt = build_phase1_user_prompt(
            text=phase1_input,
            source_file=source_file,
            pages=TOC_SCAN_PAGES,
        )
        phase1_result = await self.openai_json_completion(
            system_prompt=PHASE1_SYSTEM_PROMPT,
            user_prompt=phase1_prompt,
        )
        toc = self.ensure_toc_schema(phase1_result, source_file=source_file)

        if (not found_toc_page) or self.toc_is_shallow(toc):
            outline = self._markdown_service.extract_heading_outline(clean_text)
            phase2_prompt = build_phase2_user_prompt(
                metadata=toc,
                outline=outline,
                source_file=source_file,
            )
            phase2_result = await self.openai_json_completion(
                system_prompt=PHASE2_SYSTEM_PROMPT,
                user_prompt=phase2_prompt,
            )
            toc = self.ensure_toc_schema(phase2_result, source_file=source_file)

        return toc

    async def openai_json_completion(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        base_url = settings.OPENAI_API_URL.rstrip("/")
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY.strip()}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(120.0, connect=30.0)

        responses_url = base_url + "/responses"
        responses_body: dict[str, Any] = {
            "model": settings.OPENAI_MODEL_NAME.strip(),
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_output_tokens": 12000,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(responses_url, headers=headers, json=responses_body)
        except Exception as exc:
            raise UnprocessableEntityException(f"OpenAI request failed: {exc}") from exc

        if response.status_code < 400:
            payload = self._safe_json(response.text)
            content = self._extract_openai_content_from_responses(payload)
            if not isinstance(content, str) or not content.strip():
                raise UnprocessableEntityException("OpenAI responses API returned empty content.")
            return self._safe_json(self._strip_markdown_fence(content))

        chat_url = base_url + "/chat/completions"
        chat_body: dict[str, Any] = {
            "model": settings.OPENAI_MODEL_NAME.strip(),
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(chat_url, headers=headers, json=chat_body)
            if response.status_code >= 400:
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
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise UnprocessableEntityException("OpenAI completion did not return content.")
        return self._safe_json(self._strip_markdown_fence(content))

    def ensure_toc_schema(self, payload: dict[str, Any], source_file: str) -> dict[str, Any]:
        result: dict[str, Any] = {key: payload.get(key) for key in TOC_METADATA_KEYS}
        result["source_file"] = result.get("source_file") or source_file
        result["chapters"] = self.normalize_toc_nodes(payload.get("chapters", []))
        total_pages = result.get("total_pages")
        if total_pages is not None:
            try:
                result["total_pages"] = int(total_pages)
            except Exception:
                result["total_pages"] = None
        return result

    def toc_is_shallow(self, toc: dict[str, Any]) -> bool:
        chapters = toc.get("chapters", [])
        return self.count_sections(chapters) < MIN_SECTIONS_THRESHOLD

    def count_sections(self, nodes: list[dict[str, Any]]) -> int:
        count = 0
        for node in nodes:
            children = node.get("sections", [])
            if isinstance(children, list):
                count += len(children)
                count += self.count_sections(children)
        return count

    def normalize_toc_nodes(self, nodes: Any) -> list[dict[str, Any]]:
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
                {
                    "title": title.strip(),
                    "sections": self.normalize_toc_nodes(children or []),
                }
            )
        return normalized

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

    def _safe_json(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        raise UnprocessableEntityException("Cannot parse JSON response from OpenAI service.")

    def _strip_markdown_fence(self, content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        return stripped.strip()
