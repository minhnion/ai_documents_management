"""Async extract-image service cho DPT-3.

Wrapper async quanh ``TocSectionExtractor`` từ ``extract_images.py``.
Chỉ dùng mode "toc" (cắt ảnh heading + content theo từng section trong chunk_payload)
— tương đương chức năng extract_landing_chunk_images của DPT-2.

Không sửa logic gốc của extract_images.py.
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

# ── sys.path patch ─────────────────────────────────────────────────────────────
_PIPELINE_DIR = Path(__file__).parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))


def _extract_images_sync(
    pdf_path: Path,
    chunks_path: Path,
    source_path: Path,
    output_dir: Path,
    *,
    dpi: int = 200,
) -> dict[str, int]:
    """Đồng bộ: cắt ảnh landing chunks (table, figure, ...) từ dpt3_ocr.json."""
    from extract_images import AllLeavesExtractor, ImageConfig
    from parse_models import MEDIA_TYPES

    config = ImageConfig(dpi=dpi)
    extractor = AllLeavesExtractor(config)
    stats = extractor.run(
        pdf_path=pdf_path,
        source_path=source_path,
        out_dir=output_dir,
        types_filter=MEDIA_TYPES,
        flat=True,  # lưu flat để VersionAssetService phục vụ
    )
    return stats


def _enrich_landing_chunks(
    payload: dict[str, Any],
    *,
    version_id: int,
    images_dir: Path,
) -> None:
    """Điền ``image_url`` vào ``landing_chunks`` có sẵn (table, figure, ...)."""
    from build_toc import DEPTH_CHILD_KEYS

    def _children(node: dict) -> list[dict]:
        out: list[dict] = []
        for key in DEPTH_CHILD_KEYS.values():
            out.extend(node.get(key) or [])
        return out

    def _walk(node: dict) -> None:
        for child in _children(node):
            _walk(child)

        for entry in node.get("landing_chunks") or []:
            if not isinstance(entry, dict):
                continue
            asset_id = entry.get("id")
            if not asset_id or not (images_dir / f"{asset_id}.png").is_file():
                continue
            entry["image_url"] = f"/versions/{version_id}/assets/{asset_id}"

    for chapter in payload.get("chapters", []):
        _walk(chapter)


class ExtractImageService:
    """Async wrapper quanh TocSectionExtractor cho DPT-3."""

    async def extract_images(
        self,
        *,
        pdf_path: Path,
        artifact_dir: Path,
        output_dir: Path,
        version_id: int | None = None,
        dpi: int = 200,
    ) -> dict[str, int]:
        """Cắt ảnh section từ PDF dựa trên chunks.json.

        Yêu cầu:
          - ``artifact_dir/chunks.json`` đã tồn tại (ghi bởi BBoxChunkingService)
          - ``artifact_dir/dpt3_ocr.json`` đã tồn tại (dùng để fallback validate)

        Ảnh được ghi flat vào ``output_dir/`` với tên ``<landing_chunk_id>.png``.
        Nếu ``version_id`` được truyền, ``chunks.json`` sẽ được enrich ``image_url`` cho các ``landing_chunks`` có sẵn.
        """
        chunks_path = artifact_dir / "chunks.json"
        source_path = artifact_dir / "dpt3_ocr.json"

        if not chunks_path.exists():
            logger.warning("chunks.json chưa có ở %s — bỏ qua extract image", artifact_dir)
            return {"saved": 0, "skipped": 0, "error": 0}

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            stats = await self._run_blocking(
                _extract_images_sync,
                pdf_path,
                chunks_path,
                source_path,
                output_dir,
                dpi=dpi,
            )
        except Exception:
            logger.warning("DPT-3 extract images failed | file=%s", pdf_path.name, exc_info=True)
            stats = {"saved": 0, "skipped": 0, "error": 1}

        if version_id is not None and stats.get("error", 0) < stats.get("saved", 0) + stats.get("skipped", 0):
            try:
                payload = json.loads(chunks_path.read_text(encoding="utf-8"))
                _enrich_landing_chunks(
                    payload,
                    version_id=version_id,
                    images_dir=output_dir,
                )
                chunks_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                logger.warning("DPT-3 enrich landing_chunks failed | file=%s", pdf_path.name, exc_info=True)

        logger.info(
            "DPT-3 extract images done | file=%s saved=%s skipped=%s error=%s",
            pdf_path.name, stats.get("saved"), stats.get("skipped"), stats.get("error"),
        )
        return stats

    @staticmethod
    async def _run_blocking(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, partial(func, *args, **kwargs))
