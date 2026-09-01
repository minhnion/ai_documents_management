"""Async TOC-builder service cho DPT-3.

Wrapper async quanh TocBuilder / TocBuilderPipeline từ ``build_toc.py``.
Không sửa gì logic gốc — chỉ:
  1. sys.path được patch bởi dpt3_ocr_service khi đã loaded (dùng chung _PIPELINE_DIR).
  2. Subclass OpenAiJsonCaller để track LLM usage mà không monkey-patch.
  3. Chạy blocking code trong ThreadPoolExecutor.
  4. Nhận ParseResult đã có trong artifact_dir/dpt3_ocr.json.
  5. Ghi artifact toc_structure.json vào artifact_dir.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# sys.path patch — đồng nhất với dpt3_ocr_service
_PIPELINE_DIR = Path(__file__).parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))


TOC_FILENAME = "toc_structure.json"


# ── Tracked caller (subclass OpenAiJsonCaller để ghi usage events) ────────────

def _make_tracked_caller(base_caller_class, usage_events: list[dict[str, Any]]):
    """Tạo instance OpenAiJsonCaller có ghi lại usage tokens."""

    class _TrackedCaller(base_caller_class):
        def _record(self, response: Any) -> None:
            raw = getattr(response, "usage", None)
            if raw is None:
                return
            def _g(obj, key, default=0):
                return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
            details = _g(raw, "input_tokens_details") or _g(raw, "prompt_tokens_details") or {}
            cached = _g(details, "cached_tokens") if details else 0
            usage_events.append({
                "input_tokens": max(0, int(_g(raw, "input_tokens"))),
                "cached_input_tokens": max(0, int(cached)),
                "output_tokens": max(0, int(_g(raw, "output_tokens"))),
            })

        def call(self, system: str, user: str) -> dict:
            result = super().call(system, user)
            self._record(self._last_response)
            return result

        def call_structured(self, system: str, user: str, schema: dict, name: str) -> dict:
            result = super().call_structured(system, user, schema, name)
            self._record(self._last_response)
            return result

    return _TrackedCaller


def _build_toc_sync(
    dpt3_json_path: Path,
    *,
    api_key: str,
    model: str,
    output_path: Path,
    usage_events: list[dict[str, Any]],
) -> dict:
    """Đồng bộ: chạy TocBuilder, lưu artifact, trả về toc dict."""
    from openai import OpenAI
    import build_toc as _bt
    from build_toc import TocBuilder, TocConfig, OpenAiJsonCaller

    config = TocConfig(
        input_dir=dpt3_json_path.parent,
        output_dir=output_path.parent,
        model=model,
    )
    client = OpenAI(api_key=api_key)

    # Tạo tracked caller class
    TrackedCaller = _make_tracked_caller(OpenAiJsonCaller, usage_events)

    # Monkey-patch tạm thời OpenAiJsonCaller trong module build_toc
    original = _bt.OpenAiJsonCaller
    _bt.OpenAiJsonCaller = TrackedCaller
    try:
        builder = TocBuilder(config, client)
        toc = builder.build(dpt3_json_path)
    finally:
        _bt.OpenAiJsonCaller = original

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("DPT-3 TOC artifact → %s", output_path.name)

    return toc


class TocBuilderService:
    """Async front-end cho TocBuilder của DPT-3."""

    def __init__(self) -> None:
        self.last_usage_events: list[dict[str, Any]] = []

    async def build_toc(
        self,
        *,
        parse_result,
        artifact_dir: Path,
        source_filename: str,
    ) -> dict:
        """Dựng TOC từ ParseResult.

        Yêu cầu: ``artifact_dir/dpt3_ocr.json`` tồn tại (từ LandingAIOcrService).
        Ghi ``artifact_dir/toc_structure.json`` làm artifact.
        """
        dpt3_json_path = artifact_dir / "dpt3_ocr.json"
        if not dpt3_json_path.exists():
            # Fallback nếu chưa có
            dpt3_json_path.write_text(
                json.dumps(parse_result.to_json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        toc_out_path = artifact_dir / TOC_FILENAME
        api_key, model = self._read_env()

        usage_events: list[dict[str, Any]] = []
        toc = await self._run_blocking(
            _build_toc_sync,
            dpt3_json_path,
            api_key=api_key,
            model=model,
            output_path=toc_out_path,
            usage_events=usage_events,
        )
        self.last_usage_events = usage_events
        return toc

    @staticmethod
    def _read_env() -> tuple[str, str]:
        import os
        from app.core.config import settings
        api_key = settings.OPENAI_API_KEY.strip() or os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY chưa được set — cần cho DPT-3 TOC pipeline")
        model = settings.OPENAI_MODEL_NAME.strip() or os.environ.get("TOC_MODEL", "gpt-5.1")
        return api_key, model

    @staticmethod
    async def _run_blocking(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, partial(func, *args, **kwargs))
