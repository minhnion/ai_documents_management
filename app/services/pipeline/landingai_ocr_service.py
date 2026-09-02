"""Async OCR service cho DPT-3.

Wrapper async quanh LandingOcrPipeline từ ``landing_ocr_engine.py``.
Không sửa gì logic gốc — chỉ:
  1. Patch sys.path để bare-import hoạt động.
  2. Chạy blocking code trong ThreadPoolExecutor.
  3. Nhận pdf_path, trả về LandingAIOcrResult (ParseResult + usage_events).
  4. Ghi artifact ``dpt3_ocr.json`` vào artifact_dir để debug.

Điểm khác DPT-2 LandingAIOcrService:
  • Gọi HTTP POST trực tiếp (httpx), KHÔNG dùng landingai-ade SDK.
  • Không overlap trang; ghép bằng dịch offset số học (ParseResultMerger).
  • Trả về ParseResult dataclass thay vì (raw_markdown str, ade_chunks list).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

logger = logging.getLogger(__name__)

# ── sys.path patch: thêm thư mục pipeline để các bare-import trong parse_models,
#    build_toc, build_chunks, landing_ocr_engine hoạt động đúng.
_PIPELINE_DIR = Path(__file__).parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

# Import sau khi patch sys.path
from parse_models import ParseResult  # noqa: E402  # type: ignore[import]


# ── DPT-2 legacy cost helpers ─────────────────────────────────────────────────

MAX_PAGES = 50          # Giới hạn trang / lần gọi LandingAI
OVERLAP_PAGES = 3       # Overlap trang giữa các split chunk


def billable_page_count(page_count: int, *, max_pages: int = MAX_PAGES, overlap_pages: int = OVERLAP_PAGES) -> int:
    """Return pages sent to OCR, including intentional split overlap."""
    total = max(0, int(page_count))
    if total <= max_pages:
        return total
    overlap = max(0, min(int(overlap_pages), max_pages - 1))
    start = 0
    billable = 0
    while start < total:
        end = min(start + max_pages, total)
        billable += end - start
        if end == total:
            break
        start = end - overlap
    return billable


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass(slots=True)
class OcrRequestUsage:
    page_count: int
    request_count: int = 1
    output_chars: int = 0
    credit_usage: float = 0.0
    usage_source: str = "provider_reported"
    status: str = "succeeded"


@dataclass
class LandingAIOcrResult:
    parse_result: ParseResult
    page_count: int
    usage_events: list[OcrRequestUsage] = field(default_factory=list)


class OcrProcessingError(RuntimeError):
    def __init__(self, message: str, usage_events: list[OcrRequestUsage] | None = None) -> None:
        super().__init__(message)
        self.usage_events = usage_events or []


# ── Core sync function ────────────────────────────────────────────────────────

def _ocr_pdf_sync(
    pdf_path: Path,
    *,
    api_key: str,
    model: str,
    environment: str,
) -> LandingAIOcrResult:
    """Chạy OCR DPT-3 đồng bộ, trả về LandingAIOcrResult (ParseResult in-memory)."""
    # Import trực tiếp từ file landing_ocr_engine (đã copy vào cùng thư mục,
    # đổi tên bỏ dấu cách để import bình thường được)
    from landing_ocr_engine import LandingOcrConfig, LandingOcrClient, LandingOcrPipeline  # type: ignore[import]

    config = LandingOcrConfig(
        api_key=api_key,
        model=model,
        environment=environment,
    )

    with LandingOcrClient(config) as client:
        pipeline = LandingOcrPipeline(config, client)
        parse_result: ParseResult = pipeline._parse_document(pdf_path)

    usage_events = [
        OcrRequestUsage(
            page_count=parse_result.metadata.page_count,
            output_chars=parse_result.metadata.markdown_chars,
            credit_usage=parse_result.metadata.credit_usage,
            usage_source="provider_reported",
        )
    ]

    return LandingAIOcrResult(
        parse_result=parse_result,
        page_count=parse_result.metadata.page_count,
        usage_events=usage_events,
    )


# ── Async service class ───────────────────────────────────────────────────────

class LandingAIOcrService:
    """Async wrapper quanh DPT-3 OCR pipeline."""

    DPT3_OCR_FILENAME = "dpt3_ocr.json"

    async def process_pdf(
        self,
        pdf_path: Path,
        *,
        artifact_dir: Path | None = None,
    ) -> LandingAIOcrResult:
        """OCR một file PDF bằng DPT-3, trả về LandingAIOcrResult.

        Nếu artifact_dir được truyền, ghi ParseResult ra
        ``artifact_dir/dpt3_ocr.json`` để debug/cache.
        """
        api_key, model, environment = self._read_env()
        result = await self._run_blocking(
            _ocr_pdf_sync, pdf_path,
            api_key=api_key, model=model, environment=environment,
        )
        if artifact_dir is not None:
            self._write_artifact(artifact_dir, result.parse_result)
        return result

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _read_env() -> tuple[str, str, str]:
        from app.core.config import settings
        api_key = (
            settings.LANDINGAI_API_KEY.strip()
            or os.environ.get("VISION_AGENT_API_KEY", "").strip()
        )
        if not api_key:
            raise RuntimeError("LANDINGAI_API_KEY (hoặc VISION_AGENT_API_KEY) chưa được set")
        model = settings.DPT3_MODEL.strip() or "dpt-3-pro-latest"
        environment = settings.LANDINGAI_ADE_ENVIRONMENT.strip() or "production"
        return api_key, model, environment

    @staticmethod
    def _write_artifact(artifact_dir: Path, parse_result: ParseResult) -> None:
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            out = artifact_dir / LandingAIOcrService.DPT3_OCR_FILENAME
            out.write_text(
                json.dumps(parse_result.to_json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("DPT-3 OCR artifact → %s", out)
        except Exception:
            logger.warning("Không ghi được DPT-3 OCR artifact", exc_info=True)

    @staticmethod
    async def _run_blocking(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, partial(func, *args, **kwargs))
