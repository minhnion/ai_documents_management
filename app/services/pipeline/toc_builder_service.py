"""Async wrapper around the partner-maintained 3-phase TOC pipeline.

The core logic (phase1 / phase2 / phase3 in ``toc_service.py``) is owned by
the partner team and must NOT be modified here. This module only:
  • Surfaces a stable async API ``TocBuilderService.build_toc(...)``.
  • Hides blocking OpenAI calls behind a thread-pool executor.
  • Hydrates the OpenAI client from environment variables.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from app.services.pipeline import toc_service as _toc

logger = logging.getLogger(__name__)


class TocBuilderService:
    """Async front-end for ``toc_service.phase1/2/3``.

    Args (kwargs only):
      raw_markdown — full OCR markdown (PAGE_BREAK markers + ADE anchors intact).
      source_file  — original PDF filename (used in metadata + LLM prompts).
      ade_chunks   — list of LandingAI ADE chunks (raw, with ``id`` + ``bboxes``).
    """

    _call_ai_patch_lock = threading.Lock()

    def __init__(self, *, markdown_service=None) -> None:
        # markdown_service kept for backward-compatible kwargs; not used here.
        self._markdown_service = markdown_service
        self.last_vlm_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    async def build_toc(
        self,
        *,
        clean_text: str | None = None,
        raw_markdown: str | None = None,
        source_file: str,
        ade_chunks: list[dict] | None = None,
    ) -> dict:
        markdown = raw_markdown if raw_markdown is not None else clean_text
        if markdown is None:
            raise ValueError("build_toc requires raw_markdown (or clean_text)")
        toc, usage = await self._run_blocking(
            self._build_sync, markdown, source_file, ade_chunks or []
        )
        self.last_vlm_usage = usage
        return toc

    async def openai_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        return await self._run_blocking(
            self._json_completion_sync, system_prompt, user_prompt
        )

    # ── Blocking implementations executed inside a worker thread ────────────

    def _build_sync(
        self,
        raw_markdown: str,
        source_file: str,
        ade_chunks: list[dict],
    ) -> tuple[dict, dict[str, int]]:
        client = self._make_client()
        _toc._init_phase2_prompts()
        usage = {"input_tokens": 0, "output_tokens": 0}

        original_call_ai = _toc.call_ai

        def tracked_call_ai(client: OpenAI, system: str, user: str) -> dict:
            response = client.responses.create(
                model=_toc.MODEL,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text={"format": {"type": "json_object"}},
                temperature=0.0,
                max_output_tokens=32000,
            )
            self._accumulate_response_usage(response, usage)
            return _toc.parse_json_response(response.output_text or "")

        with self._call_ai_patch_lock:
            _toc.call_ai = tracked_call_ai
            try:
                raw_toc, found_toc, toc_end = _toc.phase1(client, raw_markdown, source_file)
                toc = _toc.ensure_schema(raw_toc, source_file)

                if not toc.get("total_pages"):
                    toc["total_pages"] = raw_markdown.count(_toc.PAGE_BREAK) + 1

                total_pages = toc.get("total_pages") or 0
                # The toc_service module gates Phase 2 depth heuristics off of a
                # module-level constant, so update it before invoking Phase 2.
                _toc.MIN_SECTION_DEPTH = (
                    _toc.MIN_SECTION_DEPTH_LONG
                    if total_pages >= _toc.PAGE_THRESHOLD_FOR_DEPTH
                    else _toc.MIN_SECTION_DEPTH_SHORT
                )

                if not found_toc or _toc.toc_is_shallow(toc):
                    try:
                        toc = _toc.ensure_schema(
                            _toc.phase2(
                                client,
                                raw_markdown,
                                toc,
                                source_file,
                                body_start_page=toc_end,
                            ),
                            source_file,
                        )
                    except Exception:
                        logger.exception("Phase 2 failed — keeping Phase 1 result")

                if ade_chunks:
                    try:
                        toc_end_ade = _toc._compute_toc_end_ade_page(
                            raw_markdown, ade_chunks, toc_end
                        )
                        toc = _toc.phase3(client, toc, ade_chunks, toc_end_page=toc_end_ade)
                    except Exception:
                        logger.exception(
                            "Phase 3 mapping failed — TOC will have no heading_chunk_id"
                        )
            finally:
                _toc.call_ai = original_call_ai

        return toc, usage

    def _json_completion_sync(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._make_client()
        return _toc.call_ai(client, system_prompt, user_prompt)

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_client() -> OpenAI:
        load_dotenv(override=False)
        api_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY missing — required for the TOC pipeline"
            )
        return OpenAI(api_key=api_key)

    @staticmethod
    def _accumulate_response_usage(response: Any, usage: dict[str, int]) -> None:
        raw_usage = getattr(response, "usage", None)
        if raw_usage is None:
            return
        input_tokens = getattr(raw_usage, "input_tokens", None)
        output_tokens = getattr(raw_usage, "output_tokens", None)
        if isinstance(raw_usage, dict):
            input_tokens = raw_usage.get("input_tokens", input_tokens)
            output_tokens = raw_usage.get("output_tokens", output_tokens)
        usage["input_tokens"] += max(0, int(input_tokens or 0))
        usage["output_tokens"] += max(0, int(output_tokens or 0))

    @staticmethod
    async def _run_blocking(func, /, *args):
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, partial(func, *args))
