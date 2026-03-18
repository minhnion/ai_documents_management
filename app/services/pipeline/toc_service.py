from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from app.core.exceptions import BadRequestException, UnprocessableEntityException

# ==============================================================================
# CẤU HÌNH ĐẦU VÀO / ĐẦU RA 
# ==============================================================================

# 1. ĐẦU VÀO: Thư mục chứa các file Markdown đã được OCR (Tạo từ Bước 1)
INPUT_DIR  = Path("./data/02_ocr_markdown")

# 2. ĐẦU RA: Thư mục lưu kết quả Cấu trúc Mục lục (File _toc_structure.json)
OUTPUT_DIR = Path("./data/03_toc_json")

# Danh sách các file cần chạy (Để rỗng [] nếu muốn tự động chạy tất cả file trong INPUT_DIR)
MD_FILES = []

PAGE_BREAK             = "<!-- PAGE BREAK -->"
MODEL                  = "gpt-4.1"
TOC_SCAN_PAGES         = 40
MIN_SECTIONS_THRESHOLD = 3

_METADATA_KEYS = [
    "title", "publisher", "decision_number", "specialty",
    "date", "isbn_electronic", "isbn_print", "total_pages",
    "source_file", "chapters",
]

# ──────────────────────────────────────────────────────────────────────────────
# PROMPTS
# ──────────────────────────────────────────────────────────────────────────────

_METADATA_SCHEMA = """\
| Trường            | Nguồn                                                          |
|-------------------|----------------------------------------------------------------|
| title             | Tên đầy đủ tài liệu, thường ở trang bìa hoặc đầu file.        |
| publisher         | Cơ quan ban hành (Bộ Y tế, Bệnh viện, Hội Y học…).            |
| decision_number   | Số quyết định dạng "XXXX/QĐ-YYY" (ví dụ: "2855/QĐ-BYT").     |
| specialty         | Chuyên khoa (Tim mạch, Nội tiết, Hô hấp, Truyền nhiễm…).      |
| date              | Ngày ban hành ISO 8601: "YYYY-MM-DD".                          |
| isbn_electronic   | ISBN điện tử nếu có, ngược lại null.                           |
| isbn_print        | ISBN in nếu có, ngược lại null.                                |
| total_pages       | Tổng số trang (số nguyên), tìm ở cuối file hoặc trang bìa.    |
| source_file       | Tên file Markdown (đã cung cấp, điền vào đây).                |"""

_STRUCTURE_RULES = """\
CẤU TRÚC PHÂN CẤP (lồng nhau):
  chapters → sections → subsections → subsubsections → subsubsubsections
  Mỗi node chỉ có "title" và key mảng con tương ứng. Mảng con rỗng thì để [].

NHẬN DIỆN TIÊU ĐỀ (Tiếng Việt):
  - Cấp 1 (chapters): "Phần X", "Chương X", các mục lớn không có cha.
  - Cấp 2 (sections): "Bước X", "Mục X", "I, II, III", tiêu đề in đậm dưới chapter.
  - Cấp 3+ (subsections…): đánh số thập phân (2.1, 2.1.1…).
  - Phụ lục có số → lồng dưới chapter tương ứng. Phụ lục không số → chapter riêng.
  - Loại bỏ: số trang, dòng chân trang, tên tác giả, đoạn văn bản nội dung."""

PROMPT_PHASE1 = f"""\
Bạn là hệ thống trích xuất cấu trúc tài liệu y tế. Trả về DUY NHẤT một JSON hợp lệ, không markdown, không giải thích.

OUTPUT SCHEMA:
{{
  "title": "...",
  "publisher": "...",
  "decision_number": "...",
  "specialty": "...",
  "date": "YYYY-MM-DD",
  "isbn_electronic": null,
  "isbn_print": null,
  "total_pages": 0,
  "source_file": "...",
  "chapters": [
    {{
      "title": "...",
      "sections": [
        {{
          "title": "...",
          "subsections": [
            {{"title": "...", "subsubsections": [{{"title": "...", "subsubsubsections": []}}]}}
          ]
        }}
      ]
    }}
  ]
}}

METADATA – trích xuất từ văn bản, không tìm thấy → null:
{_METADATA_SCHEMA}

MỤC LỤC (key "chapters") — HAI TRƯỜNG HỢP:

TRƯỜNG HỢP 1 — TÌM THẤY PHẦN MỤC LỤC/TABLE OF CONTENTS:
  - CHỈ dùng các dòng/hàng nằm BÊN TRONG phần MỤC LỤC đó.
  - TUYỆT ĐỐI KHÔNG suy luận thêm mục con từ nội dung chương, tiêu đề body, hay bất kỳ phần nào khác của văn bản.
  - TUYỆT ĐỐI KHÔNG thêm bất kỳ mục nào không xuất hiện trong MỤC LỤC.
  - Nếu MỤC LỤC chỉ có 2 cấp → chỉ trả về 2 cấp, không tự thêm cấp 3.
  - Kết quả nông (ít sections) là ĐÚNG nếu MỤC LỤC gốc nông — hệ thống sẽ tự bổ sung ở bước tiếp theo.

TRƯỜNG HỢP 2 — KHÔNG TÌM THẤY MỤC LỤC:
  - Suy luận từ các tiêu đề lớn trong phần văn bản đã cung cấp.
  - Áp dụng quy tắc nhận diện tiêu đề bên dưới.

{_STRUCTURE_RULES}"""

PROMPT_PHASE2 = f"""\
Bạn là hệ thống xây dựng cây TOC tài liệu y tế từ danh sách tiêu đề. Trả về DUY NHẤT một JSON hợp lệ, không markdown, không giải thích.

OUTPUT SCHEMA: giống hệt Phase 1 (metadata + chapters đầy đủ).

NHIỆM VỤ: nhận METADATA đã biết + OUTLINE TIÊU ĐỀ trích từ toàn văn bản,
xây dựng cây chapters đầy đủ chiều sâu.

{_STRUCTURE_RULES}

XÁC ĐỊNH CẤP DỰA TRÊN SỐ THẬP PHÂN:
  "2."      hoặc "2. Tiêu đề"    → chapter cấp 1
  "2.1."    hoặc "2.1 Tiêu đề"   → section cấp 2
  "2.1.1."                        → subsection cấp 3
  "2.1.1.1."                      → subsubsection cấp 4
  Dòng in hoa không số → chapter. Markdown ## → cấp theo số dấu #.
  Giữ nguyên tiêu đề gốc. KHÔNG thêm mục không có trong outline."""

# ──────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def get_pages(text: str, n: int) -> str:
    parts = text.split(PAGE_BREAK)
    if n <= 0:
        return text
    return PAGE_BREAK.join(parts[:n]) if len(parts) > n else text


def strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_json_response(text: str) -> dict:
    raw = strip_code_fences(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            raise
        return json.loads(m.group(0))


def call_ai(client: OpenAI, system: str, user: str) -> dict:
    response = client.responses.create(
        model=MODEL,
        input=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_output_tokens=12000,
    )
    return parse_json_response(response.output_text or "")


def _build_client() -> OpenAI:
    load_dotenv(override=False)
    api_key = os.getenv("OPENAI_API_KEY", "").strip().strip("\"'")
    if not api_key:
        raise BadRequestException("Missing OPENAI_API_KEY in environment.")

    model_name = os.getenv("OPENAI_MODEL_NAME", "").strip()
    if model_name:
        global MODEL
        MODEL = model_name

    return OpenAI(api_key=api_key)

# ──────────────────────────────────────────────────────────────────────────────
# NORMALIZATION
# ──────────────────────────────────────────────────────────────────────────────

def normalize_node_list(value) -> list:
    return value if isinstance(value, list) else []


def normalize_subsections(items) -> list:
    out = []
    for item in normalize_node_list(items):
        if isinstance(item, str):
            if item.strip():
                out.append({"title": item.strip()})
        elif isinstance(item, dict):
            node = {"title": str(item.get("title", "")).strip()}
            for k, v in item.items():
                if k in ("title", "subsections"):
                    continue
                if isinstance(v, list) and (k.endswith("sections") or k.startswith("sub")):
                    node[k] = normalize_subsections(v)
            if "subsections" in item:
                node["subsections"] = normalize_subsections(item["subsections"])
            out.append(node)
    return out


def normalize_sections(sections) -> list:
    out = []
    for sec in normalize_node_list(sections):
        if isinstance(sec, dict):
            node = {"title": str(sec.get("title", "")).strip()}
            node["subsections"] = normalize_subsections(sec.get("subsections", []))
            out.append(node)
    return out


def normalize_chapters(chapters) -> list:
    out = []
    for ch in normalize_node_list(chapters):
        if isinstance(ch, dict):
            node = {"title": str(ch.get("title", "")).strip()}
            node["sections"] = normalize_sections(ch.get("sections", []))
            out.append(node)
    return out


def ensure_schema(toc: dict, filename: str) -> dict:
    defaults = {k: None for k in _METADATA_KEYS}
    defaults["source_file"] = filename
    defaults["chapters"]    = []
    for k, v in defaults.items():
        if k not in toc:
            toc[k] = v
    toc["source_file"] = filename
    toc["chapters"]    = normalize_chapters(toc.get("chapters", []))
    if toc.get("total_pages") is not None:
        try:
            toc["total_pages"] = int(toc["total_pages"])
        except (ValueError, TypeError):
            toc["total_pages"] = None
    return {k: toc[k] for k in _METADATA_KEYS if k in toc}

# ──────────────────────────────────────────────────────────────────────────────
# TOC QUALITY CHECK
# ──────────────────────────────────────────────────────────────────────────────

def count_sections(chapters: list) -> int:
    return sum(len(ch.get("sections", [])) for ch in chapters if isinstance(ch, dict))


def toc_is_shallow(toc: dict) -> bool:
    chapters = toc.get("chapters", [])
    return not chapters or count_sections(chapters) < MIN_SECTIONS_THRESHOLD

# ──────────────────────────────────────────────────────────────────────────────
# HEADING OUTLINE EXTRACTOR
# ──────────────────────────────────────────────────────────────────────────────

_RE_MD_HEADING     = re.compile(r"^(#{1,6})\s+(.+)")
_RE_NUMBERED       = re.compile(r"^(\d+(?:\.\d+)*)\s*[\.\)]\s*(.+)")
_RE_ROMAN_HEADING  = re.compile(r"^(I{1,3}|IV|V?I{0,3}|IX|X{0,3})\.\s+\S")
_RE_CHAPTER_PREFIX = re.compile(r"^(ph[aầ]n|phan|chương|chuong|bước|buoc|mục|muc)\s+\S", re.IGNORECASE)
_RE_HTML_TAG       = re.compile(r"<[^>]+>")
_RE_PURE_NUM       = re.compile(r"^\s*[\d\s,\.\-/]+\s*$")


def extract_heading_outline(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == PAGE_BREAK or _RE_PURE_NUM.match(s):
            continue
        plain = _RE_HTML_TAG.sub("", s).strip()
        if not plain:
            continue
        if _RE_MD_HEADING.match(s):
            lines.append(s)
        elif _RE_NUMBERED.match(plain):
            lines.append(plain)
        elif _RE_ROMAN_HEADING.match(plain):
            lines.append(plain)
        elif _RE_CHAPTER_PREFIX.match(plain):
            lines.append(plain)
        elif len(plain) <= 120 and plain == plain.upper() and len(plain) > 4:
            if not re.fullmatch(r"[\-\=\*\s]+", plain):
                lines.append(plain)

    deduped, prev = [], None
    for ln in lines:
        if ln != prev:
            deduped.append(ln)
            prev = ln
    return "\n".join(deduped)

# ──────────────────────────────────────────────────────────────────────────────
# PHASE RUNNERS
# ──────────────────────────────────────────────────────────────────────────────

def phase1(client: OpenAI, text: str, filename: str) -> dict:
    scan = get_pages(text, TOC_SCAN_PAGES)
    user = (
        f"source_file = {filename}\n\n"
        f"Nội dung văn bản ({TOC_SCAN_PAGES} trang đầu):\n{scan}"
    )
    print(f"  Phase 1: {len(scan)} chars ...")
    try:
        return call_ai(client, PROMPT_PHASE1, user)
    except Exception as e:
        print(f"  Phase 1 failed: {e}")
        return {"chapters": [], "source_file": filename}


def phase2(client: OpenAI, text: str, metadata: dict, filename: str) -> dict:
    outline = extract_heading_outline(text)
    print(f"  Phase 2: {outline.count(chr(10)) + 1} heading lines ...")
    meta_only = {k: metadata.get(k) for k in _METADATA_KEYS if k != "chapters"}
    user = (
        f"source_file = {filename}\n\n"
        f"METADATA:\n{json.dumps(meta_only, ensure_ascii=False, indent=2)}\n\n"
        f"OUTLINE TIÊU ĐỀ:\n{outline}"
    )
    try:
        result  = call_ai(client, PROMPT_PHASE2, user)
        merged  = dict(metadata)
        merged["chapters"] = result.get("chapters", metadata.get("chapters", []))
        return merged
    except Exception as e:
        print(f"  Phase 2 failed: {e} — keeping Phase 1 result")
        return metadata

# ──────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING
# ──────────────────────────────────────────────────────────────────────────────

def has_toc_page(text: str, n_pages: int) -> bool:
    """Kiểm tra xem N trang đầu có chứa phần MỤC LỤC/TABLE OF CONTENTS không."""
    scan = get_pages(text, n_pages)
    return bool(re.search(r"MỤC\s*LỤC|TABLE\s+OF\s+CONTENTS", scan, re.IGNORECASE))


def process_file(md_path: Path, client: OpenAI, output_dir: Path) -> None:
    if not md_path.exists():
        print(f"File not found: {md_path}")
        return

    print(f"Processing: {md_path.name}")
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    # Phát hiện sớm: có MỤC LỤC trong phần đầu không?
    found_toc_page = has_toc_page(text, TOC_SCAN_PAGES)

    toc = ensure_schema(phase1(client, text, md_path.name), md_path.name)
    n_ch, n_sec = len(toc.get("chapters", [])), count_sections(toc.get("chapters", []))
    print(f"  Phase 1 result: {n_ch} chapters, {n_sec} sections")

    if not found_toc_page:
        # Không có MỤC LỤC → Phase 1 chỉ đọc được một phần tài liệu,
        # luôn chạy Phase 2 để quét toàn bộ văn bản.
        print(f"  Không có MỤC LỤC → Phase 2 bắt buộc (quét toàn bộ văn bản)")
        toc = ensure_schema(phase2(client, text, toc, md_path.name), md_path.name)
        print(f"  Phase 2 result: {len(toc.get('chapters', []))} chapters, {count_sections(toc.get('chapters', []))} sections")
    elif toc_is_shallow(toc):
        # Có MỤC LỤC nhưng kết quả Phase 1 vẫn quá nông → Phase 2 bổ sung
        print(f"  TOC shallow ({n_sec} sections < {MIN_SECTIONS_THRESHOLD}) → Phase 2")
        toc = ensure_schema(phase2(client, text, toc, md_path.name), md_path.name)
        print(f"  Phase 2 result: {len(toc.get('chapters', []))} chapters, {count_sections(toc.get('chapters', []))} sections")
    else:
        print("  Có MỤC LỤC, TOC đủ sâu → dùng kết quả Phase 1")

    print(f"  title={toc.get('title')!r} | decision={toc.get('decision_number')!r} | pages={toc.get('total_pages')}")

    stem = md_path.stem
    if stem.endswith(".extraction"):
        stem = stem[: -len(".extraction")]
    out_path = output_dir / f"{stem}_toc_structure.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved → {out_path.name}")


def build_toc_from_text(text: str, filename: str) -> dict[str, Any]:
    client = _build_client()
    found_toc_page = has_toc_page(text, TOC_SCAN_PAGES)

    toc = ensure_schema(phase1(client, text, filename), filename)
    if (not found_toc_page) or toc_is_shallow(toc):
        toc = ensure_schema(phase2(client, text, toc, filename), filename)
    return toc


class TocBuilderService:
    def __init__(self, markdown_service=None) -> None:
        self._markdown_service = markdown_service

    async def build_toc(self, clean_text: str, source_file: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(build_toc_from_text, clean_text, source_file)
        except BadRequestException:
            raise
        except Exception as exc:
            raise UnprocessableEntityException(f"TOC build failed: {exc}") from exc

    async def openai_json_completion(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(call_ai, _build_client(), system_prompt, user_prompt)
        except BadRequestException:
            raise
        except Exception as exc:
            raise UnprocessableEntityException(f"OpenAI completion failed: {exc}") from exc


def run(args) -> None:
    load_dotenv(override=False)
    api_key = os.getenv("OPENAI_API_KEY", "").strip().strip("\"'")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")
    client = OpenAI(api_key=api_key)

    global TOC_SCAN_PAGES, MIN_SECTIONS_THRESHOLD, MODEL
    TOC_SCAN_PAGES         = args.pages
    MIN_SECTIONS_THRESHOLD = args.min_sections
    MODEL                  = args.model

    input_dir  = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    file_list = args.files if args.files else MD_FILES
    if not file_list:
        if not input_dir.exists():
            input_dir.mkdir(parents=True, exist_ok=True)
            print(f"[*] Đã tạo thư mục đầu vào: {input_dir}. Vui lòng copy file Markdown vào đây!")
            return
        file_list = [f.name for f in sorted(input_dir.glob("*.md"))]
        
    if not file_list:
        print("\n  ✗ Không có file Markdown nào để xử lý.")
        return

    for fname in file_list:
        md_path = Path(fname) if Path(fname).is_absolute() else input_dir / fname
        process_file(md_path, client, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir",    default=str(INPUT_DIR))
    parser.add_argument("--output-dir",   default=str(OUTPUT_DIR))
    parser.add_argument("--files",        nargs="*")
    parser.add_argument("--pages",        type=int, default=TOC_SCAN_PAGES)
    parser.add_argument("--min-sections", type=int, default=MIN_SECTIONS_THRESHOLD)
    parser.add_argument("--model",        default=MODEL)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
