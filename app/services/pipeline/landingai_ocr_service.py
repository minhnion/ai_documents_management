

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PDF_DIR        = Path("./data/01_raw_pdf")
OUTPUT_MD_DIR  = Path("./data/02_ocr_markdown")
OUTPUT_ADE_DIR = Path("./data/06_ade_chunks")
PDF_FILES: list[str] = []  # Rỗng = tự động lấy tất cả PDF trong PDF_DIR

MAX_PAGES     = 50    # Giới hạn trang / lần gọi LandingAI
OVERLAP_PAGES = 3     # Overlap trang giữa các split chunk
DELAY_SECONDS = 10    # Delay (giây) giữa các API call để tránh rate-limit


@dataclass(slots=True)
class LandingAIOcrResult:
    raw_markdown: str
    ade_chunks: list[dict[str, Any]]
    page_count: int

def _make_client():
    """Khởi tạo LandingAIADE client từ env. Raise nếu thiếu API key."""
    try:
        from landingai_ade import LandingAIADE
    except ImportError as e:
        raise ImportError(
            "landingai-ade chưa được cài đặt. Chạy: pip install landingai-ade"
        ) from e

    api_key = os.environ.get("VISION_AGENT_API_KEY")
    if not api_key:
        raise EnvironmentError("VISION_AGENT_API_KEY chưa được set trong .env")
    return LandingAIADE(apikey=api_key)


def _chunk_to_dict(chunk: Any) -> dict:
    grounding = getattr(chunk, "grounding", None)
    if grounding is None:
        groundings = []
    elif isinstance(grounding, list):
        groundings = grounding
    else:
        groundings = [grounding]

    bboxes: list[dict] = []
    for g in groundings:
        box = getattr(g, "box", None)
        if box is None:
            continue
        bboxes.append({
            "page":   int(getattr(g, "page", 0)),
            "left":   round(float(box.left),   6),
            "top":    round(float(box.top),     6),
            "right":  round(float(box.right),   6),
            "bottom": round(float(box.bottom),  6),
        })

    return {
        "id":       str(getattr(chunk, "id",       "")),
        "type":     str(getattr(chunk, "type",     "text")),
        "markdown": str(getattr(chunk, "markdown", "")),
        "bboxes":   bboxes,
    }


# ==============================================================================
# MARKDOWN MERGE HELPERS (dùng khi PDF > MAX_PAGES trang)
# ==============================================================================

def _find_overlap_cutoff(prev_md: str, curr_md: str, search_chars: int = 4000) -> int:
    """Tìm vị trí kết thúc phần trùng lặp trong curr_md so với prev_md."""
    tail = prev_md[-search_chars:].strip()
    head = curr_md[:search_chars * 2]
    best = 0
    for window in [300, 200, 150, 100, 60]:
        step = max(window // 3, 20)
        for i in range(0, len(tail) - window, step):
            fragment = tail[i : i + window].strip()
            if len(fragment) < 40:
                continue
            pos = head.find(fragment)
            if pos != -1:
                best = max(best, pos + len(fragment))
    return best


def _find_table_offset(prev_md: str, curr_md: str) -> int:
    """Tính offset cần cộng vào table id của curr_md để tiếp nối prev_md."""
    page_break  = "<!-- PAGE BREAK -->"
    prev_breaks = [m.start() for m in re.finditer(re.escape(page_break), prev_md)]
    curr_breaks = [m.start() for m in re.finditer(re.escape(page_break), curr_md)]

    overlap_prev = (prev_md[prev_breaks[-OVERLAP_PAGES]:]
                    if len(prev_breaks) >= OVERLAP_PAGES else prev_md)
    overlap_curr = (curr_md[:curr_breaks[OVERLAP_PAGES - 1]]
                    if len(curr_breaks) >= OVERLAP_PAGES else curr_md)

    t_prev = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', overlap_prev)]
    t_curr = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', overlap_curr)]
    if t_prev and t_curr:
        return t_prev[0] - t_curr[0]

    all_prev = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', prev_md)]
    all_curr = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', curr_md)]
    if all_prev and all_curr:
        return max(all_prev) + 1 - min(all_curr)
    return 0


def _apply_table_offset(markdown: str, offset: int) -> str:
    """Cộng offset vào tất cả table/td id dạng số trong markdown."""
    if offset == 0:
        return markdown

    def replace(m: re.Match) -> str:
        full_id = m.group(1)
        if "-" not in full_id:
            return m.group(0)
        dash = full_id.index("-")
        try:
            return f'id="{int(full_id[:dash]) + offset}{full_id[dash:]}"'
        except ValueError:
            return m.group(0)

    return re.sub(r'id="([^"]+)"', replace, markdown)


def _merge_markdowns(parts: list[str]) -> str:
    """Gộp các phần markdown (có overlap) thành 1 văn bản, cắt bỏ nội dung trùng."""
    if len(parts) == 1:
        return parts[0].strip()

    merged = parts[0].strip()
    for idx, curr in enumerate(parts[1:], start=2):
        curr   = curr.strip()
        cutoff = _find_overlap_cutoff(merged, curr)
        if cutoff > 0:
            newline     = curr.find("\n", cutoff)
            cut_at      = newline + 1 if newline != -1 else cutoff
            new_content = curr[cut_at:].strip()
        else:
            logger.warning("chunk %d: không tìm được overlap, nối toàn bộ", idx)
            new_content = curr
        if new_content:
            merged += "\n\n" + new_content

    return merged


# ==============================================================================
# CORE: GỌI API + SERIALIZE
# ==============================================================================

def _parse_pdf(client, pdf_path: Path) -> tuple[str, list[dict]]:
    result = client.parse(document=pdf_path, model="dpt-2-latest")
    return result.markdown, [_chunk_to_dict(c) for c in result.chunks]


def _get_page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


def _ocr_pdf_in_memory(pdf_path: Path, client) -> LandingAIOcrResult:
    """
    OCR 1 file PDF:
      - Gọi LandingAI ADE (tự split nếu > MAX_PAGES trang)
      - Trả về Markdown + ADE chunks JSON in-memory
    """
    n_pages = _get_page_count(pdf_path)
    logger.info("[%s] %d trang", pdf_path.name, n_pages)

    if n_pages <= MAX_PAGES:
        # ── Single parse ───────────────────────────────────────────────────────
        markdown, ade_chunks = _parse_pdf(client, pdf_path)

    else:
        # ── Multi-chunk parse (split + merge) ──────────────────────────────────
        logger.info(
            "  PDF > %d trang → split %d chunk (overlap %d trang)",
            MAX_PAGES,
            -(-n_pages // (MAX_PAGES - OVERLAP_PAGES)),  # ceil approx
            OVERLAP_PAGES,
        )
        from pypdf import PdfReader, PdfWriter

        reader     = PdfReader(str(pdf_path))
        tmp_dir    = Path(tempfile.mkdtemp(prefix="landingai_"))
        md_parts:   list[str]  = []
        ade_chunks: list[dict] = []

        try:
            start = 0
            part  = 0
            while start < n_pages:
                end    = min(start + MAX_PAGES, n_pages)
                writer = PdfWriter()
                for i in range(start, end):
                    writer.add_page(reader.pages[i])

                part_path = tmp_dir / f"part_{part:03d}.pdf"
                with open(part_path, "wb") as fh:
                    writer.write(fh)

                logger.info(
                    "  [%d] trang %d–%d ...", part + 1, start + 1, end
                )
                part_md, part_chunks = _parse_pdf(client, part_path)
                logger.info("  [%d] ok (%d chunks)", part + 1, len(part_chunks))

                # ── Markdown: fix table IDs trước khi tích lũy ────────────────
                if md_parts:
                    part_md = _apply_table_offset(
                        part_md, _find_table_offset(md_parts[-1], part_md)
                    )
                md_parts.append(part_md)

                # ── ADE chunks: offset page → absolute page index ──────────────
                for ch in part_chunks:
                    for bbox in ch["bboxes"]:
                        bbox["page"] += start

                # Loại bỏ chunks thuộc vùng overlap với phần trước
                if part > 0:
                    cutoff_page = start + OVERLAP_PAGES
                    part_chunks = [
                        ch for ch in part_chunks
                        if not ch["bboxes"]
                        or ch["bboxes"][0]["page"] >= cutoff_page
                    ]

                ade_chunks.extend(part_chunks)
                part += 1

                if end == n_pages:
                    break
                start = end - OVERLAP_PAGES
                time.sleep(DELAY_SECONDS)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # ── Sắp xếp ADE chunks theo thứ tự tài liệu ──────────────────────────
        ade_chunks.sort(key=lambda ch: (
            (ch["bboxes"][0]["page"], ch["bboxes"][0]["top"], ch["bboxes"][0]["left"])
            if ch["bboxes"] else (9999, 9999, 9999)
        ))

        # ── Ghép markdown ─────────────────────────────────────────────────────
        markdown = _merge_markdowns(md_parts)

    return LandingAIOcrResult(
        raw_markdown=markdown,
        ade_chunks=ade_chunks,
        page_count=n_pages,
    )


def ocr_pdf(pdf_path: Path, client) -> None:
    """
    OCR 1 file PDF:
      - Gọi LandingAI ADE (tự split nếu > MAX_PAGES trang)
      - Lưu Markdown → OUTPUT_MD_DIR
      - Lưu ADE chunks JSON → OUTPUT_ADE_DIR (bbox cache cho chunk_bbox.py)
    Bỏ qua nếu cả hai output đã tồn tại (cache hit).
    """
    out_md  = OUTPUT_MD_DIR  / f"{pdf_path.stem}_ocr.md"
    out_ade = OUTPUT_ADE_DIR / f"{pdf_path.stem}_ade_chunks.json"

    if out_md.exists() and out_ade.exists():
        logger.info("Cache hit — bỏ qua: %s", pdf_path.name)
        return

    result = _ocr_pdf_in_memory(pdf_path, client)

    # ── Lưu output ────────────────────────────────────────────────────────────
    OUTPUT_MD_DIR.mkdir(parents=True, exist_ok=True)
    out_md.write_text(result.raw_markdown, encoding="utf-8")
    logger.info("  → %s", out_md.name)

    OUTPUT_ADE_DIR.mkdir(parents=True, exist_ok=True)
    out_ade.write_text(
        json.dumps(result.ade_chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("  → %s  (%d chunks)", out_ade.name, len(result.ade_chunks))


class LandingAIOcrService:
    async def process_pdf(self, pdf_path: Path) -> LandingAIOcrResult:
        return await self._run_blocking(self._process_pdf_sync, pdf_path)

    async def ocr_markdown(self, pdf_path: Path) -> str:
        result = await self.process_pdf(pdf_path)
        return result.raw_markdown

    def _process_pdf_sync(self, pdf_path: Path) -> LandingAIOcrResult:
        client = _make_client()
        return _ocr_pdf_in_memory(pdf_path, client)

    async def _run_blocking(self, func, /, *args):
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, partial(func, *args))


# ==============================================================================
# MAIN
# ==============================================================================

def get_pdf_files() -> list[str]:
    if PDF_FILES:
        return PDF_FILES
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    return [f.name for f in sorted(PDF_DIR.glob("*.pdf"))]


def main() -> None:
    files = get_pdf_files()
    if not files:
        logger.info("Không có file PDF nào trong %s", PDF_DIR)
        return

    client = _make_client()
    for name in files:
        path = PDF_DIR / name
        if path.exists():
            ocr_pdf(path, client)
        else:
            logger.warning("Không tìm thấy: %s", name)

    logger.info(
        "\nDone  md → %s  |  ade → %s",
        OUTPUT_MD_DIR, OUTPUT_ADE_DIR,
    )


if __name__ == "__main__":
    main()
