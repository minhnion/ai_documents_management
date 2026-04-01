from __future__ import annotations
import re
import sys
from pathlib import Path

# ==============================================================================
# PATTERNS — khớp hoàn toàn với _preprocess() trong chunking_service.py
# ==============================================================================

# Bước 1: xoá thẻ neo rỗng
_RE_ANCHOR = re.compile(r"<a\s[^>]*></a>", re.IGNORECASE)

# Bước 2: xoá HTML comment trừ PAGE_BREAK — regex giống hệt chunk script
_RE_HTML_CMT_NO_PB = re.compile(
    r"<!--(?!\s*PAGE\s*BREAK\s*-->).*?-->",
    re.DOTALL | re.IGNORECASE,
)

# Bước 3: chuẩn hoá PAGE_BREAK → marker cố định 19 ký tự
_RE_PAGE_BREAK = re.compile(r"<!--\s*PAGE\s*BREAK\s*-->", re.IGNORECASE)

PAGE_BREAK_MARKER = "<!-- PAGE_BREAK -->"
assert len(PAGE_BREAK_MARKER) == 19, "marker length must stay 19 to preserve offsets"


# ==============================================================================
# CORE CLEAN — logic đồng nhất với _preprocess() trong chunking_service.py
# ==============================================================================

def clean_markdown(raw: str) -> str:

    text = _RE_ANCHOR.sub("", raw)
    text = _RE_HTML_CMT_NO_PB.sub("", text)
    text = _RE_PAGE_BREAK.sub(PAGE_BREAK_MARKER, text)
    return text


def clean_markdown_file(
    src: Path,
    out_dir: Path | None = None,
    suffix: str = "_clean",
) -> Path:
    raw = src.read_text(encoding="utf-8", errors="ignore")
    clean = clean_markdown(raw)

    target_dir = out_dir if out_dir else src.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    out_path = target_dir / f"{src.stem}{suffix}.md"
    out_path.write_text(clean, encoding="utf-8")
    return out_path


# ==============================================================================
# VERIFY — tính expected_len theo đúng thứ tự 3 bước của _preprocess()
# ==============================================================================

def verify_offsets(raw: str, clean: str) -> dict:
    """
    Kiểm tra độ dài và số PAGE_BREAK của clean text.
    Tính toán theo đúng thứ tự 3 bước: anchor → html_comment → page_break.
    """
    anchor_chars = sum(len(m.group()) for m in _RE_ANCHOR.finditer(raw))

    text_after_anchor = _RE_ANCHOR.sub("", raw)
    html_cmt_chars = sum(len(m.group()) for m in _RE_HTML_CMT_NO_PB.finditer(text_after_anchor))

    text_after_cmt = _RE_HTML_CMT_NO_PB.sub("", text_after_anchor)
    pb_delta = 0
    for m in _RE_PAGE_BREAK.finditer(text_after_cmt):
        pb_delta += len(PAGE_BREAK_MARKER) - len(m.group())

    expected_len = len(raw) - anchor_chars - html_cmt_chars + pb_delta

    pb_raw   = len(_RE_PAGE_BREAK.findall(raw))
    pb_clean = clean.count(PAGE_BREAK_MARKER)

    return {
        "raw_len":        len(raw),
        "clean_len":      len(clean),
        "anchor_chars":   anchor_chars,
        "html_cmt_chars": html_cmt_chars,
        "pb_delta":       pb_delta,
        "expected_len":   expected_len,
        "len_ok":         len(clean) == expected_len,
        "null_bytes":     clean.count("\x00"),
        "pb_raw":         pb_raw,
        "pb_clean":       pb_clean,
        "pb_ok":          pb_raw == pb_clean,
    }


# ==============================================================================
# CẤU HÌNH ĐẦU VÀO / ĐẦU RA (WEB SERVICE)
# ==============================================================================

# 1. ĐẦU VÀO: Thư mục chứa các file Markdown OCR thô
#    Trỏ vào thư mục mà web service đọc file OCR từ đó
INPUT_DIR = Path("./data/02_ocr_markdown")

# 2. ĐẦU RA: Thư mục lưu Markdown sạch
#    chunking_service_fixed.py sẽ đọc từ đây nếu MD_INPUT_DIR được trỏ vào thư mục này
OUTPUT_DIR_DEFAULT: str = "./data/05_clean_markdown"

# Danh sách file cụ thể (để rỗng [] để chạy toàn bộ INPUT_DIR)
INPUT_FILES: list[str] = []


# ==============================================================================
# CLI
# ==============================================================================

def _parse_args() -> tuple[list[Path], Path | None]:
    import argparse
    parser = argparse.ArgumentParser(
        description="Làm sạch markdown gốc để start_char/end_char trong chunk JSON (web) chính xác.",
    )
    parser.add_argument(
        "files", nargs="*",
        help="File(s) markdown gốc (.md). Nếu bỏ trống, dùng toàn bộ file trong INPUT_DIR.",
    )
    parser.add_argument(
        "--out-dir", "-o",
        metavar="DIR",
        help="Thư mục lưu file đầu ra. Nếu bỏ trống, dùng OUTPUT_DIR_DEFAULT trong config.",
    )
    args = parser.parse_args()

    if args.files:
        files = [Path(f) for f in args.files]
    elif INPUT_FILES:
        files = [Path(f) for f in INPUT_FILES]
    else:
        if not INPUT_DIR.exists():
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            print(f"[*] Đã tạo thư mục đầu vào: {INPUT_DIR}. Vui lòng copy file Markdown vào đây!")
            files = []
        else:
            files = list(INPUT_DIR.glob("*.md"))

    out_dir = Path(args.out_dir) if args.out_dir else Path(OUTPUT_DIR_DEFAULT)
    return files, out_dir


def main() -> None:
    paths, out_dir = _parse_args()
    ok = err = 0

    for src in paths:
        if not src.exists():
            print(f"[SKIP] không tìm thấy: {src}", file=sys.stderr)
            err += 1
            continue

        raw   = src.read_text(encoding="utf-8", errors="ignore")
        clean = clean_markdown(raw)
        stats = verify_offsets(raw, clean)

        if not stats["len_ok"]:
            print(
                f"[WARN] {src.name}: độ dài không khớp "
                f"(expected {stats['expected_len']}, got {stats['clean_len']})",
                file=sys.stderr,
            )
            err += 1

        if not stats["pb_ok"]:
            print(
                f"[WARN] {src.name}: số PAGE_BREAK không khớp "
                f"(raw {stats['pb_raw']}, clean {stats['pb_clean']})",
                file=sys.stderr,
            )

        out_path = clean_markdown_file(src, out_dir=out_dir)
        print(
            f"[OK] {src.name} → {out_path.name}"
            f"  ({stats['raw_len']:,} → {stats['clean_len']:,} chars,"
            f"  -{stats['anchor_chars']:,} anchors,"
            f"  -{stats['html_cmt_chars']:,} html_cmt,"
            f"  {stats['pb_delta']:+d} pb_delta,"
            f"  {stats['pb_clean']} page breaks)"
        )
        ok += 1

    print(f"\n{ok} file OK, {err} lỗi.")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
