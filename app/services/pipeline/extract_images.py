from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from build_toc import DEPTH_CHILD_KEYS
from parse_models import ParseResult, flatten_leaves

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_COLOR_MAP: dict[str, tuple[int, int, int, int]] = {
    "text": (0, 120, 255, 80),
    "table": (0, 200, 0, 80),
    "figure": (255, 60, 0, 80),
    "logo": (200, 0, 200, 80),
    "scan_code": (255, 200, 0, 80),
    "attestation": (255, 140, 0, 80),
    "marginalia": (120, 120, 120, 80),
}
_DEFAULT_COLOR = (128, 128, 128, 80)


@dataclass
class ImageConfig:
    pdf_dir: Path = Path("./data/01_raw_pdf")
    source_dir: Path = Path("./data/06_ade_chunks")
    chunks_dir: Path = Path("./data/04_chunked_json")
    output_dir: Path = Path("./data/07_extracted_images")
    dpi: int = 200
    margin: float = 0.0


def _safe_filename(text: str, max_len: int = 60) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("._")
    return s[:max_len] if s else "unnamed"


class PdfCropper:
    def __init__(self, doc, dpi: int, margin: float) -> None:
        self._doc = doc
        self._dpi = dpi
        self._margin = margin

    def crop(self, bbox: dict, out_path: Path) -> bool:
        import fitz

        page_num = bbox.get("page", 0)
        if page_num >= len(self._doc):
            logger.warning("Page %d out of range (doc has %d pages)", page_num, len(self._doc))
            return False

        page = self._doc.load_page(page_num)
        pw, ph = page.rect.width, page.rect.height
        left, top, right, bottom = bbox["left"], bbox["top"], bbox["right"], bbox["bottom"]

        if self._margin > 0:
            dw, dh = (right - left) * self._margin, (bottom - top) * self._margin
            left, top = max(0.0, left - dw), max(0.0, top - dh)
            right, bottom = min(1.0, right + dw), min(1.0, bottom + dh)

        if right <= left or bottom <= top:
            logger.warning("Invalid bbox (l=%.4f t=%.4f r=%.4f b=%.4f), skipping", left, top, right, bottom)
            return False

        rect = fitz.Rect(left * pw, top * ph, right * pw, bottom * ph)
        pix = page.get_pixmap(clip=rect, dpi=self._dpi, colorspace=fitz.csRGB)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return True


class AllLeavesExtractor:
    def __init__(self, config: ImageConfig) -> None:
        self._config = config

    def run(
        self,
        pdf_path: Path,
        source_path: Path,
        out_dir: Path,
        types_filter: set[str] | None,
        *,
        flat: bool = False,
    ) -> dict[str, int]:
        import fitz

        result = ParseResult.load(source_path)
        leaves = flatten_leaves(result)
        doc = fitz.open(str(pdf_path))
        cropper = PdfCropper(doc, self._config.dpi, self._config.margin)
        stats = {"saved": 0, "skipped": 0, "error": 0}

        for leaf in leaves:
            if types_filter and leaf.type not in types_filter:
                continue
            bbox = leaf.normalized_bbox()
            if bbox is None:
                stats["skipped"] += 1
                continue
            if flat:
                fname = f"{_safe_filename(leaf.id)}.png"
                out_path = out_dir / fname
            else:
                fname = f"p{bbox['page'] + 1:03d}_{_safe_filename(leaf.id)}.png"
                out_path = out_dir / leaf.type / fname
            if cropper.crop(bbox, out_path):
                stats["saved"] += 1
            else:
                stats["error"] += 1

        doc.close()
        return stats


class TocSectionExtractor:
    def __init__(self, config: ImageConfig) -> None:
        self._config = config

    def run(
        self,
        pdf_path: Path,
        chunks_path: Path,
        out_dir: Path,
        heading_only: bool,
        *,
        flat: bool = False,
    ) -> dict[str, int]:
        import fitz
        import uuid

        chunks_data = json.loads(chunks_path.read_text(encoding="utf-8"))
        doc = fitz.open(str(pdf_path))
        cropper = PdfCropper(doc, self._config.dpi, self._config.margin)
        stats = {"saved": 0, "skipped": 0, "error": 0}

        def _node_id(node: dict) -> str:
            nid = node.get("node_id")
            if nid:
                return str(nid)
            return uuid.uuid4().hex[:12]

        def process_node(node: dict, parent_dir: Path) -> None:
            if flat:
                node_id = _node_id(node)
                node_dir = out_dir
            else:
                node_dir = parent_dir / _safe_filename(node.get("title", "untitled"))

            heading_bbox = node.get("heading_bbox")
            if heading_bbox:
                if flat:
                    heading_path = node_dir / f"{node_id}_h0.png"
                else:
                    heading_path = node_dir / "_heading.png"
                if cropper.crop(heading_bbox, heading_path):
                    stats["saved"] += 1
                else:
                    stats["error"] += 1
            else:
                stats["skipped"] += 1

            if not heading_only:
                for idx, bbox in enumerate(node.get("content_bboxes", [])):
                    if flat:
                        content_path = node_dir / f"{node_id}_c{idx}.png"
                    else:
                        content_path = node_dir / f"_content_p{bbox['page'] + 1:03d}.png"
                    if cropper.crop(bbox, content_path):
                        stats["saved"] += 1
                    else:
                        stats["error"] += 1

            for key in DEPTH_CHILD_KEYS.values():
                for child in node.get(key, []):
                    process_node(child, node_dir if not flat else out_dir)

        for chapter in chunks_data.get("chapters", []):
            process_node(chapter, out_dir)

        doc.close()
        return stats


class BBoxValidator:
    def __init__(self, config: ImageConfig) -> None:
        self._config = config

    def run(self, pdf_path: Path, source_path: Path, out_dir: Path, sample_pages: list[int] | None) -> None:
        import fitz
        from PIL import Image, ImageDraw

        result = ParseResult.load(source_path)
        leaves = flatten_leaves(result)
        doc = fitz.open(str(pdf_path))
        val_dir = out_dir / "validate"
        val_dir.mkdir(parents=True, exist_ok=True)

        page_to_leaves: dict[int, list] = {}
        for leaf in leaves:
            bbox = leaf.normalized_bbox()
            if bbox is not None:
                page_to_leaves.setdefault(bbox["page"], []).append((bbox, leaf.type))

        pages_to_check = sample_pages if sample_pages is not None else list(range(len(doc)))
        for page_num in pages_to_check:
            if page_num >= len(doc):
                continue
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=self._config.dpi, colorspace=fitz.csRGB)
            pw, ph = pix.width, pix.height

            img = Image.frombytes("RGB", [pw, ph], pix.samples)
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            for bbox, leaf_type in page_to_leaves.get(page_num, []):
                color = _COLOR_MAP.get(leaf_type, _DEFAULT_COLOR)
                x0, y0 = int(bbox["left"] * pw), int(bbox["top"] * ph)
                x1, y1 = int(bbox["right"] * pw), int(bbox["bottom"] * ph)
                draw.rectangle([x0, y0, x1, y1], fill=color, outline=color[:3] + (220,), width=2)

            combined = Image.alpha_composite(img.convert("RGBA"), overlay)
            out_path = val_dir / f"page_{page_num + 1:03d}_overview.png"
            combined.convert("RGB").save(str(out_path))
            logger.info("Validation → %s", out_path.name)

        doc.close()
        logger.info("Validate done: %d pages → %s", len(pages_to_check), val_dir)


class ImageExtractionCli:
    def __init__(self, config: ImageConfig) -> None:
        self._config = config

    def find_pairs_all(self) -> list[tuple[Path, Path, Path, str]]:
        pairs = []
        for source_file in sorted(self._config.source_dir.glob("*_dpt3.json")):
            stem = source_file.stem[: -len("_dpt3")]
            pdf_file = self._config.pdf_dir / f"{stem}.pdf"
            if pdf_file.exists():
                pairs.append((pdf_file, source_file, self._config.output_dir / stem, stem))
            else:
                logger.warning("PDF not found for %s", source_file.name)
        return pairs

    def run(self, args: argparse.Namespace) -> None:
        try:
            import fitz  # noqa: F401
        except ImportError:
            logger.error("PyMuPDF not installed. Run: pip install pymupdf")
            return

        types_filter = set(args.types.split(",")) if args.types else None
        sample_pages = [int(x) for x in args.pages.split(",")] if args.pages else None

        if args.pdf and args.source:
            pdf_path, source_path = Path(args.pdf), Path(args.source)
            out_dir = Path(args.out) if args.out else self._config.output_dir / pdf_path.stem
            work_list = [(pdf_path, source_path, out_dir, pdf_path.stem)]
        else:
            work_list = self.find_pairs_all()

        if not work_list:
            logger.info("Không tìm thấy file cần xử lý trong %s / %s", self._config.pdf_dir, self._config.source_dir)
            return

        for pdf_path, source_path, out_dir, stem in work_list:
            logger.info("=" * 60)
            logger.info("PDF: %s | Mode: %s | DPI: %d", pdf_path.name, args.mode, self._config.dpi)
            out_dir.mkdir(parents=True, exist_ok=True)

            if args.mode == "all":
                stats = AllLeavesExtractor(self._config).run(pdf_path, source_path, out_dir, types_filter)
                logger.info("Done: %d saved, %d skipped, %d error", stats["saved"], stats["skipped"], stats["error"])
            elif args.mode == "toc":
                chunks_path = Path(args.chunks_json) if args.chunks_json else self._config.chunks_dir / f"{stem}_chunks.json"
                if not chunks_path.exists():
                    logger.error("chunks.json not found: %s — chạy build_chunks.py trước", chunks_path)
                    continue
                stats = TocSectionExtractor(self._config).run(pdf_path, chunks_path, out_dir, args.heading_only)
                logger.info("Done: %d saved, %d skipped, %d error", stats["saved"], stats["skipped"], stats["error"])
            elif args.mode == "validate":
                BBoxValidator(self._config).run(pdf_path, source_path, out_dir, sample_pages)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Cắt ảnh từ PDF dùng DPT-3 bounding boxes")
    parser.add_argument("--mode", choices=["all", "toc", "validate"], default="all")
    parser.add_argument("--dpi", type=int, default=ImageConfig().dpi)
    parser.add_argument("--types", default=None)
    parser.add_argument("--heading-only", action="store_true")
    parser.add_argument("--pages", default=None)
    parser.add_argument("--pdf", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--chunks-json", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    config = ImageConfig(dpi=args.dpi)
    ImageExtractionCli(config).run(args)


if __name__ == "__main__":
    main()
