from __future__ import annotations
import re
import sys
from pathlib import Path

_RE_ANCHOR     = re.compile(r"<a\s[^>]*></a>", re.IGNORECASE)
_RE_PAGE_BREAK = re.compile(r"<!--\s*PAGE\s*BREAK\s*-->", re.IGNORECASE)

_RE_HTML_CMT_NO_PB = re.compile(
    r"<!--(?!\s*PAGE\s*BREAK\s*-->).*?-->",
    re.DOTALL | re.IGNORECASE,
)

PAGE_BREAK_MARKER = "<!-- PAGE_BREAK -->"
assert len(PAGE_BREAK_MARKER) == 19, "marker length must stay 19 to preserve offsets"


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
    raw  = src.read_text(encoding="utf-8", errors="ignore")
    clean = clean_markdown(raw)

    target_dir = out_dir if out_dir else src.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    out_path = target_dir / f"{src.stem}{suffix}.md"
    out_path.write_text(clean, encoding="utf-8")
    return out_path


def verify_offsets(raw: str, clean: str) -> dict:
    anchor_chars = sum(len(m.group()) for m in _RE_ANCHOR.finditer(raw))
    pb_raw       = len(_RE_PAGE_BREAK.findall(raw))
    pb_clean     = clean.count(PAGE_BREAK_MARKER)
    expected_len = len(raw) - anchor_chars

    return {
        "raw_len":        len(raw),
        "clean_len":      len(clean),
        "anchor_chars":   anchor_chars,
        "expected_len":   expected_len,
        "len_ok":         len(clean) == expected_len,
        "null_bytes":     clean.count("\x00"),
        "pb_raw":         pb_raw,
        "pb_clean":       pb_clean,
        "pb_ok":          pb_raw == pb_clean,
    }


# ==============================================================================
# CẤU HÌNH ĐẦU VÀO / ĐẦU RA 
# ==============================================================================

# 1. ĐẦU VÀO: Thư mục chứa các file Markdown OCR thô ban đầu (Tạo từ Bước 1)
INPUT_DIR = Path("./data/02_ocr_markdown")

# 2. ĐẦU RA: Thư mục lưu các file Markdown Sạch phục vụ hiển thị web
OUTPUT_DIR_DEFAULT: str = "./data/05_clean_markdown"

# Danh sách file cụ thể (Để rỗng [] để chạy toàn bộ thư mục INPUT_DIR)
INPUT_FILES: list[str] = []


def _parse_args() -> tuple[list[Path], Path | None]:
    import argparse
    parser = argparse.ArgumentParser(
        description="Làm sạch markdown gốc để start_char/end_char trong chunk JSON chính xác.",
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

        out_path = clean_markdown_file(src, out_dir=out_dir)
        print(
            f"[OK] {src.name} → {out_path.name}"
            f"  ({stats['raw_len']:,} → {stats['clean_len']:,} chars,"
            f"  -{stats['anchor_chars']:,} anchors,"
            f"  {stats['pb_clean']} page breaks)"
        )
        ok += 1

    print(f"\n{ok} file OK, {err} lỗi.")
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()