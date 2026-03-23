from __future__ import annotations

"""
Landing AI – OCR lấy Markdown
==============================
Cài đặt:
  pip install landingai-ade python-dotenv pypdf

.env:
  VISION_AGENT_API_KEY=land_sk_xxxxxxxxxxxxxxxxxxxxxxxx
"""

import asyncio
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
from landingai_ade import LandingAIADE

load_dotenv()

_client: LandingAIADE | None = None

# ==============================================================================
# CẤU HÌNH
# ==============================================================================
PDF_DIR = Path("./data/01_raw_pdf")
OUTPUT_DIR = Path("./data/02_ocr_markdown")
PDF_FILES = []  # Để rỗng [] để tự động lấy tất cả file trong PDF_DIR

MAX_PAGES = 50  # Giới hạn trang/lần gửi Landing AI
OVERLAP_PAGES = 3  # Số trang overlap giữa các chunk
DELAY_SECONDS = 10  # Delay giữa các chunk để tránh rate limit

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _get_pdf_io():
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The new LandingAI OCR pipeline requires `pypdf`. Install it with `pip install pypdf`."
        ) from exc
    return PdfReader, PdfWriter


def _get_client() -> LandingAIADE:
    global _client
    if _client is None:
        _client = LandingAIADE(apikey=os.environ.get("VISION_AGENT_API_KEY"))
    return _client


def get_pdf_files() -> list[str]:
    if PDF_FILES:
        return PDF_FILES
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    return [f.name for f in PDF_DIR.glob("*.pdf")]


def split_pdf(pdf_path: Path, tmp_dir: Path) -> list[tuple[Path, int, int]]:
    """Tách PDF thành các chunk có overlap, trả về [(chunk_path, start, end), ...]."""
    PdfReader, PdfWriter = _get_pdf_io()
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    chunks = []
    start = 0

    while start < total:
        end = min(start + MAX_PAGES, total)
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])

        chunk_path = tmp_dir / f"{pdf_path.stem}_part{len(chunks) + 1:03d}.pdf"
        with open(chunk_path, "wb") as handle:
            writer.write(handle)

        chunks.append((chunk_path, start, end))
        if end == total:
            break
        start = end - OVERLAP_PAGES

    return chunks


def find_overlap_cutoff(prev_md: str, curr_md: str, search_chars: int = 4000) -> int:
    """Tìm vị trí kết thúc phần trùng lặp trong current md so với previous md."""
    tail = prev_md[-search_chars:].strip()
    head = curr_md[: search_chars * 2]
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


def find_table_offset(prev_md: str, curr_md: str) -> int:
    """
    Tính offset cần cộng vào table id của curr_md để tiếp nối prev_md.
    """
    page_break = "<!-- PAGE BREAK -->"
    prev_breaks = [m.start() for m in re.finditer(re.escape(page_break), prev_md)]
    curr_breaks = [m.start() for m in re.finditer(re.escape(page_break), curr_md)]

    overlap_prev = (
        prev_md[prev_breaks[-OVERLAP_PAGES] :] if len(prev_breaks) >= OVERLAP_PAGES else prev_md
    )
    overlap_curr = (
        curr_md[: curr_breaks[OVERLAP_PAGES - 1]] if len(curr_breaks) >= OVERLAP_PAGES else curr_md
    )

    t_prev = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', overlap_prev)]
    t_curr = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', overlap_curr)]

    if t_prev and t_curr:
        return t_prev[0] - t_curr[0]

    all_prev = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', prev_md)]
    all_curr = [int(m.group(1)) for m in re.finditer(r'<table id="(\d+)-', curr_md)]
    if all_prev and all_curr:
        return max(all_prev) + 1 - min(all_curr)
    return 0


def apply_table_offset(markdown: str, offset: int) -> str:
    """Cộng offset vào tất cả table/td id dạng số trong markdown."""
    if offset == 0:
        return markdown

    def replace(match: re.Match[str]) -> str:
        full_id = match.group(1)
        if "-" not in full_id:
            return match.group(0)
        dash = full_id.index("-")
        try:
            return f'id="{int(full_id[:dash]) + offset}{full_id[dash:]}"'
        except ValueError:
            return match.group(0)

    return re.sub(r'id="([^"]+)"', replace, markdown)


def merge_markdowns(parts: list[str]) -> str:
    """Gộp các phần markdown (có overlap) thành 1 văn bản, cắt bỏ nội dung trùng."""
    if len(parts) == 1:
        return parts[0].strip()

    merged = parts[0].strip()

    for idx, curr in enumerate(parts[1:], start=2):
        curr = curr.strip()
        cutoff = find_overlap_cutoff(merged, curr)

        if cutoff > 0:
            newline = curr.find("\n", cutoff)
            cut_at = newline + 1 if newline != -1 else cutoff
            new_content = curr[cut_at:].strip()
        else:
            print(f"  ⚠  chunk {idx}: không tìm được overlap, nối toàn bộ")
            new_content = curr

        if new_content:
            merged += "\n\n" + new_content

    return merged


# ==============================================================================
# OCR
# ==============================================================================
def ocr_single(pdf_path: Path) -> str:
    result = _get_client().parse(document=pdf_path, model="dpt-2-latest")
    return result.markdown


def ocr_pdf_to_markdown(pdf_path: Path) -> str:
    PdfReader, _ = _get_pdf_io()
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)

    print(f"\n[{pdf_path.name}] {total_pages} trang")

    if total_pages <= MAX_PAGES:
        return ocr_single(pdf_path)

    tmp_dir = Path(tempfile.mkdtemp(prefix="landingai_"))
    try:
        chunks = split_pdf(pdf_path, tmp_dir)
        print(f"  Tách thành {len(chunks)} chunk (overlap {OVERLAP_PAGES} trang)")

        parts: list[str] = []
        for idx, (chunk_path, p_start, p_end) in enumerate(chunks, start=1):
            print(f"  [{idx}/{len(chunks)}] trang {p_start + 1}–{p_end} ...", end=" ", flush=True)
            md = ocr_single(chunk_path)
            if parts:
                md = apply_table_offset(md, find_table_offset(parts[-1], md))
            parts.append(md)
            print("ok")
            if idx < len(chunks):
                time.sleep(DELAY_SECONDS)

        return merge_markdowns(parts)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def ocr_pdf(pdf_path: Path) -> None:
    markdown = ocr_pdf_to_markdown(pdf_path)
    out_md = OUTPUT_DIR / f"{pdf_path.stem}_ocr.md"
    out_md.write_text(markdown, encoding="utf-8")
    print(f"  → {out_md.name}")


class LandingAIOcrService:
    """Thin async adapter around the core OCR script implementation."""

    async def ocr_markdown(self, pdf_path: Path) -> str:
        return await asyncio.to_thread(ocr_pdf_to_markdown, pdf_path)


# ==============================================================================
# MAIN
# ==============================================================================
def main() -> None:
    files = get_pdf_files()
    if not files:
        print("Không có file PDF nào để xử lý.")
        return

    for name in files:
        path = PDF_DIR / name
        if path.exists():
            ocr_pdf(path)
        else:
            print(f"✗ không tìm thấy: {name}")

    print(f"\nDone → {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
