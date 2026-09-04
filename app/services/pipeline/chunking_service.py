"""Async chunking service cho DPT-3.

Wrapper async quanh ``ChunkDocumentBuilder`` từ ``build_chunks.py``.
Không sửa logic gốc — chỉ:
  1. Patch sys.path.
  2. Chạy blocking trong ThreadPoolExecutor.
  3. Ghi artifact chunks.json vào artifact_dir.
  4. Trả về chunk_payload dict (format y hệt DPT-2 để persistence_service dùng được).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

logger = logging.getLogger(__name__)

# ── sys.path patch ─────────────────────────────────────────────────────────────
_PIPELINE_DIR = Path(__file__).parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from parse_models import ParseResult  # noqa: E402


CHUNKS_FILENAME = "chunks.json"


def _build_chunks_sync(
    toc_path: Path,
    dpt3_json_path: Path,
    *,
    output_path: Path,
) -> dict:
    """Đồng bộ: chạy ChunkDocumentBuilder, lưu artifact, trả về chunk_payload."""
    from build_chunks import ChunkDocumentBuilder, ChunkConfig

    config = ChunkConfig(
        toc_dir=toc_path.parent,
        source_dir=dpt3_json_path.parent,
        output_dir=output_path.parent,
    )
    builder = ChunkDocumentBuilder(config)
    chunk_payload = builder.build(toc_path, dpt3_json_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(chunk_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("DPT-3 chunks artifact → %s", output_path.name)

    return chunk_payload


class BBoxChunkingService:
    """Async front-end cho ChunkDocumentBuilder của DPT-3."""

    async def build_chunk_payload(
        self,
        *,
        parse_result: ParseResult,
        toc: dict,
        artifact_dir: Path,
    ) -> dict:
        """Cắt chunk từ ParseResult + TOC.

        Yêu cầu:
          - ``artifact_dir/dpt3_ocr.json`` đã tồn tại (ghi bởi LandingAIOcrService)
          - ``artifact_dir/toc_structure.json`` đã tồn tại (ghi bởi TocBuilderService)

        Ghi ``artifact_dir/chunks.json`` làm artifact.
        Trả về chunk_payload dict.
        """
        dpt3_json_path = artifact_dir / "dpt3_ocr.json"
        toc_path = artifact_dir / "toc_structure.json"
        chunks_out_path = artifact_dir / CHUNKS_FILENAME

        # Đảm bảo các file input tồn tại
        if not dpt3_json_path.exists():
            dpt3_json_path.write_text(
                json.dumps(parse_result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if not toc_path.exists():
            toc_path.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")

        return await self._run_blocking(
            _build_chunks_sync,
            toc_path,
            dpt3_json_path,
            output_path=chunks_out_path,
        )

    @staticmethod
    async def _run_blocking(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, partial(func, *args, **kwargs))
