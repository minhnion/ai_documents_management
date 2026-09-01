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
    """Đồng bộ: cắt ảnh heading + content theo section tree trong chunks.json."""
    from extract_images import TocSectionExtractor, ImageConfig

    config = ImageConfig(dpi=dpi)
    extractor = TocSectionExtractor(config)
    stats = extractor.run(
        pdf_path=pdf_path,
        chunks_path=chunks_path,
        out_dir=output_dir,
        heading_only=False,   # lấy cả heading + content bboxes
        flat=True,            # lưu flat để VersionAssetService phục vụ
    )
    return stats


def _enrich_landing_chunks(
    payload: dict[str, Any],
    *,
    version_id: int,
    images_dir: Path,
) -> None:
    """Điền ``landing_chunks`` với ``image_url`` cho các leaf node DPT-3."""
    from build_toc import DEPTH_CHILD_KEYS

    def _children(node: dict) -> list[dict]:
        out: list[dict] = []
        for key in DEPTH_CHILD_KEYS.values():
            out.extend(node.get(key) or [])
        return out

    def _walk(node: dict) -> None:
        children = _children(node)
        if children:
            for child in children:
                _walk(child)
            return

        node_id = node.get("node_id")
        if not node_id:
            return

        landing: list[dict[str, Any]] = []
        heading_bbox = node.get("heading_bbox")
        if heading_bbox:
            asset_id = f"{node_id}_h0"
            if (images_dir / f"{asset_id}.png").is_file():
                landing.append({
                    "id": asset_id,
                    "type": "heading",
                    "image_url": f"/versions/{version_id}/assets/{asset_id}",
                    "bbox": heading_bbox,
                })

        for idx, bbox in enumerate(node.get("content_bboxes") or []):
            asset_id = f"{node_id}_c{idx}"
            if (images_dir / f"{asset_id}.png").is_file():
                landing.append({
                    "id": asset_id,
                    "type": "content",
                    "image_url": f"/versions/{version_id}/assets/{asset_id}",
                    "bbox": bbox,
                })

        node["landing_chunks"] = landing

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

        Ảnh được ghi flat vào ``output_dir/`` với tên ``<node_id>_h.png`` / ``<node_id>_c{idx}.png``.
        Nếu ``version_id`` được truyền, ``chunks.json`` sẽ được enrich thêm ``landing_chunks`` với ``image_url``.
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
