from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

PDF_INPUT_DIR   = Path("./data/01_raw_pdf")
ADE_CHUNKS_DIR  = Path("./data/06_ade_chunks")    # raw ADE chunks (bbox chính xác)
CHUNKS_JSON_DIR = Path("./data/04_chunked_json")  # chunks.json theo TOC
OUTPUT_DIR      = Path("./data/07_extracted_images")

DPI    = 200    # Độ phân giải render (150–300). 200 = cân bằng chất lượng / dung lượng
MARGIN = 0.0    # Mở rộng bbox thêm N% mỗi phía (0.0 = không mở rộng)

_CHILD_KEYS = (
    "chapters", "sections", "subsections",
    "subsubsections", "subsubsubsections", "subsubsubsubsections", "children",
)

# Màu overlay theo loại chunk (RGBA)
_COLOR_MAP: dict[str, tuple[int, int, int, int]] = {
    "text":      (0,   120, 255, 80),   # blue
    "table":     (0,   200,   0, 80),   # green
    "figure":    (255,  60,   0, 80),   # red-orange
    "logo":      (200,   0, 200, 80),   # purple
    "scan_code": (255, 200,   0, 80),   # yellow
}
_DEFAULT_COLOR = (128, 128, 128, 80)


# ==============================================================================
# CROP CORE
# ==============================================================================

def _crop_and_save(
    doc: "fitz.Document",
    bbox: dict,
    out_path: Path,
    dpi: int = DPI,
    margin: float = MARGIN,
) -> bool:
    import fitz

    page_num = bbox.get("page", 0)
    if page_num >= len(doc):
        logger.warning("Page %d out of range (doc has %d pages)", page_num, len(doc))
        return False

    page = doc.load_page(page_num)
    pw   = page.rect.width   # PDF points
    ph   = page.rect.height

    left, top, right, bottom = (
        bbox["left"], bbox["top"], bbox["right"], bbox["bottom"]
    )

    if margin > 0:
        dw     = (right - left)  * margin
        dh     = (bottom - top)  * margin
        left   = max(0.0, left   - dw)
        top    = max(0.0, top    - dh)
        right  = min(1.0, right  + dw)
        bottom = min(1.0, bottom + dh)

    if right <= left or bottom <= top:
        logger.warning(
            "Invalid bbox (l=%.4f t=%.4f r=%.4f b=%.4f), skipping",
            left, top, right, bottom,
        )
        return False

    # Normalized → PDF points
    rect = fitz.Rect(left * pw, top * ph, right * pw, bottom * ph)
    pix  = page.get_pixmap(clip=rect, dpi=dpi, colorspace=fitz.csRGB)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    return True


def _safe_filename(text: str, max_len: int = 60) -> str:
    """Tạo tên file an toàn từ title/ID."""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("._")
    return s[:max_len] if s else "unnamed"


# ==============================================================================
# MODE 1: CẮT TẤT CẢ ADE CHUNKS (kiểm tra tọa độ)
# ==============================================================================

def extract_all_chunks(
    pdf_path: Path,
    ade_chunks_path: Path,
    out_dir: Path,
    dpi: int = DPI,
    types_filter: set[str] | None = None,
) -> dict[str, int]:
    """
    Cắt tất cả ADE chunks từ _ade_chunks.json.

    """
    import fitz

    chunks: list[dict] = json.loads(ade_chunks_path.read_text(encoding="utf-8"))
    doc    = fitz.open(str(pdf_path))
    stats  = {"saved": 0, "skipped": 0, "error": 0}

    for i, chunk in enumerate(chunks):
        chunk_type = chunk.get("type", "text")
        chunk_id   = chunk.get("id") or f"chunk_{i:04d}"

        if types_filter and chunk_type not in types_filter:
            continue

        bboxes = chunk.get("bboxes", [])
        if not bboxes:
            stats["skipped"] += 1
            continue

        for j, bbox in enumerate(bboxes):
            suffix   = f"_{j}" if j else ""
            fname    = f"p{bbox['page'] + 1:03d}_{_safe_filename(chunk_id)}{suffix}.png"
            out_path = out_dir / chunk_type / fname

            if _crop_and_save(doc, bbox, out_path, dpi=dpi):
                stats["saved"] += 1
            else:
                stats["error"] += 1

    doc.close()
    return stats


# ==============================================================================
# MODE 2: CẮT THEO CẤU TRÚC TOC (production)
# ==============================================================================

def extract_toc_sections(
    pdf_path: Path,
    chunks_json_path: Path,
    out_dir: Path,
    dpi: int = DPI,
    heading_only: bool = False,
) -> dict[str, int]:
    """
    Cắt ảnh cho từng node trong cây TOC theo _chunks.json.

    """
    import fitz

    chunks_data: dict = json.loads(chunks_json_path.read_text(encoding="utf-8"))
    doc   = fitz.open(str(pdf_path))
    stats = {"saved": 0, "skipped": 0, "error": 0}

    def _process_node(node: dict, parent_dir: Path) -> None:
        title    = node.get("title", "untitled")
        node_dir = parent_dir / _safe_filename(title)

        # ── Heading image ──────────────────────────────────────────────────────
        h_bbox = node.get("heading_bbox")
        if h_bbox:
            if _crop_and_save(doc, h_bbox, node_dir / "_heading.png", dpi=dpi):
                stats["saved"] += 1
            else:
                stats["error"] += 1
        else:
            stats["skipped"] += 1
            logger.debug("No heading_bbox for: %s", title)

        if not heading_only:
            # ── Content images (1 per page) ────────────────────────────────────
            for c_bbox in node.get("content_bboxes", []):
                page_label = f"p{c_bbox['page'] + 1:03d}"
                out_path   = node_dir / f"_content_{page_label}.png"
                if _crop_and_save(doc, c_bbox, out_path, dpi=dpi):
                    stats["saved"] += 1
                else:
                    stats["error"] += 1

        # ── Recurse children ───────────────────────────────────────────────────
        for key in _CHILD_KEYS:
            for child in node.get(key, []):
                _process_node(child, node_dir)

    for chapter in chunks_data.get("chapters", []):
        _process_node(chapter, out_dir)

    doc.close()
    return stats


# ==============================================================================
# MODE 3: VALIDATE — OVERLAY BBOX LÊN TRANG ĐẦY ĐỦ
# ==============================================================================

def validate_bboxes(
    pdf_path: Path,
    ade_chunks_path: Path,
    out_dir: Path,
    sample_pages: list[int] | None = None,
    dpi: int = 150,
) -> None:
    """
    Render từng trang PDF đầy đủ + vẽ overlay màu cho tất cả bbox.
    Giúp xác nhận tọa độ LandingAI khớp với nội dung thực tế.
    """
    import fitz
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("Pillow not installed. Run: pip install Pillow")
        return

    chunks   = json.loads(ade_chunks_path.read_text(encoding="utf-8"))
    doc      = fitz.open(str(pdf_path))
    val_dir  = out_dir / "validate"
    val_dir.mkdir(parents=True, exist_ok=True)

    # Nhóm chunk theo page
    page_to_chunks: dict[int, list[dict]] = {}
    for chunk in chunks:
        for bbox in chunk.get("bboxes", []):
            page_to_chunks.setdefault(bbox["page"], []).append({
                "bbox": bbox,
                "type": chunk.get("type", "text"),
                "id":   chunk.get("id", ""),
            })

    pages_to_check = sample_pages if sample_pages is not None else list(range(len(doc)))

    for page_num in pages_to_check:
        if page_num >= len(doc):
            continue
        page = doc.load_page(page_num)
        pix  = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB)
        pw, ph = pix.width, pix.height

        img     = Image.frombytes("RGB", [pw, ph], pix.samples)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)

        for item in page_to_chunks.get(page_num, []):
            bbox  = item["bbox"]
            color = _COLOR_MAP.get(item["type"], _DEFAULT_COLOR)
            x0 = int(bbox["left"]   * pw)
            y0 = int(bbox["top"]    * ph)
            x1 = int(bbox["right"]  * pw)
            y1 = int(bbox["bottom"] * ph)
            draw.rectangle(
                [x0, y0, x1, y1],
                fill=color,
                outline=color[:3] + (220,),
                width=2,
            )

        combined = Image.alpha_composite(img.convert("RGBA"), overlay)
        out_path = val_dir / f"page_{page_num + 1:03d}_overview.png"
        combined.convert("RGB").save(str(out_path))
        logger.info("Validation → %s", out_path.name)

    doc.close()
    logger.info(
        "Validate done: %d pages → %s", len(pages_to_check), val_dir
    )

def _get_matching_pairs() -> list[tuple[Path, Path, Path, str]]:
    pairs: list[tuple[Path, Path, Path, str]] = []
    for ade_file in sorted(ADE_CHUNKS_DIR.glob("*_ade_chunks.json")):
        stem     = ade_file.stem[: -len("_ade_chunks")]
        pdf_file = PDF_INPUT_DIR / f"{stem}.pdf"
        if pdf_file.exists():
            pairs.append((pdf_file, ade_file, OUTPUT_DIR / stem, stem))
        else:
            logger.warning("PDF not found for ADE chunks: %s", ade_file.name)
    return pairs


# ==============================================================================
# MAIN / CLI
# ==============================================================================

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cắt ảnh từ PDF dùng LandingAI bounding boxes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ví dụ:\n"
            "  python extract_images.py --mode validate\n"
            "  python extract_images.py --mode all --types figure,logo\n"
            "  python extract_images.py --mode toc --heading-only\n"
        ),
    )
    parser.add_argument(
        "--mode", choices=["all", "toc", "validate"], default="all",
        help=(
            "all = cắt tất cả ADE chunks (kiểm tra tọa độ); "
            "toc = cắt theo cấu trúc TOC; "
            "validate = vẽ bbox overlay lên trang đầy đủ"
        ),
    )
    parser.add_argument("--dpi",  type=int, default=DPI,
                        help=f"DPI render (default {DPI})")
    parser.add_argument("--types", default=None,
                        help='Lọc chunk type (mode=all), ví dụ: "figure,logo,table"')
    parser.add_argument("--heading-only", action="store_true",
                        help="(mode=toc) Chỉ cắt heading bbox, bỏ content")
    parser.add_argument("--pages", default=None,
                        help='(mode=validate) Trang 0-indexed, ví dụ: "0,1,2,5"')
    parser.add_argument("--pdf",        default=None,
                        help="Đường dẫn PDF cụ thể (bỏ qua auto-match)")
    parser.add_argument("--ade-chunks", default=None,
                        help="Đường dẫn _ade_chunks.json cụ thể")
    parser.add_argument("--chunks-json", default=None,
                        help="Đường dẫn _chunks.json cụ thể (chỉ cần cho mode=toc)")
    parser.add_argument("--out", default=None,
                        help="Thư mục output (bỏ qua auto)")

    args = parser.parse_args(argv)

    try:
        import fitz  # noqa: F401
    except ImportError:
        logger.error("PyMuPDF not installed. Run: pip install pymupdf")
        sys.exit(1)

    dpi          = args.dpi
    mode         = args.mode
    types_filter = set(args.types.split(",")) if args.types else None
    sample_pages = [int(x) for x in args.pages.split(",")] if args.pages else None

    # ── Xác định danh sách file cần xử lý ─────────────────────────────────────
    if args.pdf and args.ade_chunks:
        pdf_path = Path(args.pdf)
        ade_path = Path(args.ade_chunks)
        out_dir  = Path(args.out) if args.out else OUTPUT_DIR / pdf_path.stem
        work_list = [(pdf_path, ade_path, out_dir, pdf_path.stem)]
    else:
        ADE_CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
        work_list = _get_matching_pairs()

    if not work_list:
        logger.info(
            "Không tìm thấy file cần xử lý.\n"
            "Đặt PDF vào %s và chạy landingai_ocr_test_2.py trước.",
            PDF_INPUT_DIR,
        )
        return

    for pdf_path, ade_path, out_dir, stem in work_list:
        logger.info("=" * 60)
        logger.info("PDF:        %s", pdf_path.name)
        logger.info("ADE chunks: %s", ade_path.name)
        logger.info("Output:     %s", out_dir)
        logger.info("Mode:       %s | DPI: %d", mode, dpi)

        out_dir.mkdir(parents=True, exist_ok=True)

        if mode == "all":
            stats = extract_all_chunks(
                pdf_path, ade_path, out_dir, dpi=dpi, types_filter=types_filter
            )
            logger.info(
                "Done: %d saved, %d skipped, %d error",
                stats["saved"], stats["skipped"], stats["error"],
            )

        elif mode == "toc":
            chunks_json_path = (
                Path(args.chunks_json)
                if args.chunks_json
                else CHUNKS_JSON_DIR / f"{stem}_chunks.json"
            )
            if not chunks_json_path.exists():
                logger.error(
                    "chunks.json not found: %s\n"
                    "Chạy chunk_bbox.py trước để tạo file này.",
                    chunks_json_path,
                )
                continue

            stats = extract_toc_sections(
                pdf_path, chunks_json_path, out_dir,
                dpi=dpi, heading_only=args.heading_only,
            )
            logger.info(
                "Done: %d saved, %d skipped, %d error",
                stats["saved"], stats["skipped"], stats["error"],
            )

        elif mode == "validate":
            validate_bboxes(
                pdf_path, ade_path, out_dir,
                sample_pages=sample_pages, dpi=dpi,
            )

class ExtractImageService:
    async def extract_toc_images(
        self,
        *,
        pdf_path: Path,
        chunks_json_path: Path,
        output_dir: Path,
        dpi: int = DPI,
        heading_only: bool = False,
    ) -> dict[str, int]:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(
                executor,
                partial(
                    extract_toc_sections,
                    pdf_path,
                    chunks_json_path,
                    output_dir,
                    dpi,
                    heading_only,
                ),
            )


if __name__ == "__main__":
    main()
