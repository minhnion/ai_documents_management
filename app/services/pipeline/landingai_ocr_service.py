"""
Landing AI – OCR lấy Markdown
==============================
Cài đặt:
  pip install landingai-ade python-dotenv

.env:
  VISION_AGENT_API_KEY=land_sk_xxxxxxxxxxxxxxxxxxxxxxxx
"""

import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from landingai_ade import LandingAIADE

from app.core.exceptions import BadRequestException, UnprocessableEntityException

load_dotenv()

DEFAULT_MODEL = os.environ.get("LANDINGAI_MODEL_NAME", "dpt-2-latest").strip() or "dpt-2-latest"

# ==============================================================================
# CẤU HÌNH ĐẦU VÀO / ĐẦU RA 
# ==============================================================================
# 1. Thư mục chứa các file PDF gốc (Đầu vào: File .pdf)
PDF_DIR = Path("./data/01_raw_pdf")

# 2. Thư mục lưu kết quả Markdown sau khi OCR (Đầu ra: File .md)
OUTPUT_DIR = Path("./data/02_ocr_markdown")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Danh sách các file cần chạy (Để rỗng [] nếu muốn tự động chạy tất cả file trong PDF_DIR)
PDF_FILES = []


def _resolve_api_key() -> str:
    return (
        os.environ.get("LANDINGAI_API_KEY", "").strip()
        or os.environ.get("VISION_AGENT_API_KEY", "").strip()
    )


def _get_client() -> LandingAIADE:
    api_key = _resolve_api_key()
    if not api_key:
        raise BadRequestException("Missing LANDINGAI_API_KEY or VISION_AGENT_API_KEY.")
    return LandingAIADE(apikey=api_key)


def _resolve_model(model: str | None) -> str:
    return (model or os.environ.get("LANDINGAI_MODEL_NAME", "").strip() or DEFAULT_MODEL)


def ocr_pdf_to_markdown(pdf_path: Path, model: str | None = None) -> str:
    result = _get_client().parse(document=pdf_path, model=_resolve_model(model))
    return result.markdown


class LandingAIOcrService:
    async def ocr_markdown(self, pdf_path: Path) -> str:
        try:
            markdown = await asyncio.to_thread(ocr_pdf_to_markdown, pdf_path, None)
        except BadRequestException:
            raise
        except Exception as exc:
            raise UnprocessableEntityException(f"LandingAI OCR failed: {exc}") from exc

        if not isinstance(markdown, str) or not markdown.strip():
            raise UnprocessableEntityException("LandingAI OCR returned empty markdown.")
        return markdown

def get_pdf_files():
    if PDF_FILES:
        return PDF_FILES
    if not PDF_DIR.exists():
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[*] Đã tạo thư mục đầu vào: {PDF_DIR}. Vui lòng copy file PDF vào đây!")
        return []
    return [f.name for f in PDF_DIR.glob("*.pdf")]


def ocr_pdf(pdf_path: Path) -> None:
    print(f"\n[OCR] {pdf_path.name}")
    print("  processing...", end=" ", flush=True)

    result = _get_client().parse(document=pdf_path, model=_resolve_model(None))
    print(f"{len(result.chunks)} chunks")

    out_md = OUTPUT_DIR / f"{pdf_path.stem}_ocr.md"
    out_md.write_text(result.markdown, encoding="utf-8")
    print(f"  saved → {out_md.name}")


def main():
    print("━" * 50)
    print("  Landing AI OCR")
    print("━" * 50)
    
    files_to_process = get_pdf_files()
    if not files_to_process:
        print("\n  ✗ Không có file PDF nào để xử lý.")
        return

    for name in files_to_process:
        path = PDF_DIR / name
        if not path.exists():
            print(f"\n  ✗ not found: {name}")
            continue
        try:
            ocr_pdf(path)
        except Exception:
            import traceback; traceback.print_exc()
    print("\n" + "━" * 50)
    print("  done —", OUTPUT_DIR)
    print("━" * 50)


if __name__ == "__main__":
    main()
