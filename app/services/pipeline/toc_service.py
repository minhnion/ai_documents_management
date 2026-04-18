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

INPUT_DIR  = Path("./data/02_ocr_markdown")
OUTPUT_DIR = Path("./data/03_toc_json")
MD_FILES   = []

PAGE_BREAK                = "<!-- PAGE BREAK -->"
MODEL                     = "gpt-4.1"
TOC_SCAN_PAGES            = 40
MIN_SECTION_DEPTH_SHORT   = 3
MIN_SECTION_DEPTH_LONG    = 4
PAGE_THRESHOLD_FOR_DEPTH  = 99
MIN_SECTION_DEPTH         = MIN_SECTION_DEPTH_LONG
PHASE2_CHUNK_PAGES        = 50



_METADATA_KEYS = [
    "title", "publisher", "decision_number", "specialty",
    "date", "isbn_electronic", "isbn_print", "total_pages",
    "source_file", "chapters",
]

# ──────────────────────────────────────────────────────────────────────────────
# PROMPTS — PHASE 1
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

  ⚠ MỤC LỤC PHÂN TRANG — CỰC KỲ QUAN TRỌNG:
  Bảng MỤC LỤC trong PDF OCR (Landing AI) thường bị TÁCH thành nhiều trang vật lý,
  phân cách bởi <!-- PAGE BREAK -->. Các trang tiếp theo không có tiêu đề "MỤC LỤC"
  nhưng ĐƯỢC ĐÁNH NHÃN [MỤC LỤC - tiếp theo, trang N] bởi hệ thống.
  - Đọc VÀ SỬ DỤNG tất cả các trang mang nhãn [MỤC LỤC - tiếp theo, trang N].
  - Ghép nội dung toàn bộ các trang đó vào cùng một bảng MỤC LỤC thống nhất.
  - TUYỆT ĐỐI KHÔNG bỏ qua bất kỳ hàng nào trong các trang tiếp theo này.

{_STRUCTURE_RULES}"""

# ──────────────────────────────────────────────────────────────────────────────
# PROMPTS — PHASE 2
# ──────────────────────────────────────────────────────────────────────────────

_DEPTH_CHILD_KEYS: dict[int, str] = {
    1: "sections",
    2: "subsections",
    3: "subsubsections",
    4: "subsubsubsections",
    5: "subsubsubsubsections",
}

_PHASE2_SCHEMA = """\
{
  "chapters": [
    {
      "title": "...",
      "sections": [
        {
          "title": "...",
          "subsections": [
            {
              "title": "...",
              "subsubsections": [
                {
                  "title": "...",
                  "subsubsubsections": [
                    {
                      "title": "...",
                      "subsubsubsubsections": []
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}"""

_PHASE2_READING_RULES = """\
══════════════════════════════════════════════
FORMAT OCR (Landing AI) — LƯU Ý TRƯỚC KHI ĐỌC
══════════════════════════════════════════════
  • = ngắt trang vật lý, KHÔNG phải ranh giới cấu trúc.
  • <a id='...'></a> = anchor kỹ thuật, bỏ qua hoàn toàn.
  • Heading xuất hiện dưới dạng Markdown ##/### HOẶC plain ALL CAPS.
  • Chữ hoa hay vị trí đầu trang KHÔNG xác định cấp — chỉ nội dung và định dạng đánh số xác định cấp.
  • Số trang in (dạng "123" đơn hoặc "Tên tài liệu / 123") → loại bỏ.

══════════════════════════════════════════════
BƯỚC 1: XÁC ĐỊNH CẤP BẬC TỪNG HEADING (LINH HOẠT THEO TÀI LIỆU)
══════════════════════════════════════════════
Tài liệu y tế có nhiều định dạng (Hướng dẫn lâm sàng, Quyết định, Sách chuyên khảo). Phân cấp như sau:

CẤP 1 — chapters (Phần lớn nhất của tài liệu):
  Từ khoá: "PHẦN X", "CHƯƠNG X", hoặc các tiêu đề ALL CAPS cực lớn, độc lập không phụ thuộc ai.
  Ví dụ: "LỜI GIỚI THIỆU", "QUYẾT ĐỊNH", "PHỤ LỤC" (nếu là phụ lục độc lập, không gắn với Phần nào).

CẤP 2 — sections (Chia nhỏ Chapter):
  Từ khoá: "MỤC X", "BƯỚC X", Số La Mã (I, II, III...), hoặc tiêu đề in đậm/ALL CAPS là chủ đề con của Cấp 1.
  Ví dụ: "BƯỚC 1. HỎI BỆNH", "BƯỚC 5. ĐIỀU TRỊ", "I. ĐẠI CƯƠNG".
  ⚠ LƯU Ý QUAN TRỌNG: Sections hoàn toàn CÓ THỂ CÓ ngay đoạn văn nội dung bên dưới nó.

CẤP 3, 4+ — subsections, subsubsections... (Chi tiết hoá Section):
  Từ khoá: Đánh số chữ cái in hoa (A., B., C.), Đánh số thập phân (1.1, 1.1.1...).
  Đánh số Ả rập (1., 2., 3.) CHỈ được coi là heading khi đồng thời thoả MỌI điều kiện:
    (a) Tiêu đề ngắn (≤ 60 ký tự).
    (b) KHÔNG kết thúc bằng dấu ";" hoặc ",".
    (c) KHÔNG xuất hiện ngay sau một câu dẫn nhập kết thúc bằng ":" (ví dụ: "... có các nhiệm vụ sau:").
    (d) Các mục cùng cấp trong nhóm đó đều có cùng dạng ngắn gọn (không phải câu văn mô tả).
  Ví dụ ĐÚNG (là heading): "A. Phân độ THA", "B. Tuyến trên chuyển về", "1. Đại cương".
  Ví dụ SAI (là nội dung liệt kê, KHÔNG đưa vào TOC):
    "1. Tham gia phân tích, đánh giá tình hình sử dụng thuốc;"
    "2. Tham gia tư vấn trong quá trình xây dựng danh mục thuốc của đơn vị, đưa ra ý kiến..."
    (vì xuất hiện sau câu "Dược sĩ lâm sàng có các nhiệm vụ chung sau:" và kết thúc bằng ";")
  Các "Phụ lục 1.1", "Phụ lục 1.2" thường là subsections thuộc về "PHẦN 1" tương ứng.

══════════════════════════════════════════════
BƯỚC 2: DUY TRÌ HIERARCHY VÀ LOẠI BỎ NHIỄU
══════════════════════════════════════════════
  • LUÔN DUY TRÌ TÍNH KẾ THỪA: Khi gặp "BƯỚC 1", ghi nhớ đang ở Bước 1. Các mục "A., B., 1., 2." tiếp theo sẽ là con của Bước 1. Chỉ thoát ra khi gặp "BƯỚC 2" hoặc "PHẦN MỚI".
  • KHÔNG đưa vào TOC: số trang, anchor tag, tên hình/bảng (Ví dụ: "Bảng 1:", "Hình 2:"), câu hỏi lượng giá, đoạn văn bản nội dung bình thường.
  • KHÔNG đưa vào TOC — DANH SÁCH LIỆT KÊ NỘI DUNG: Các mục đánh số (1., 2., 3., a., b., c.)
    xuất hiện ngay sau câu dẫn nhập kết thúc bằng ":" là danh sách liệt kê nội dung của section cha,
    KHÔNG phải tiêu đề con. Dấu hiệu nhận biết: câu dài (> 80 ký tự), kết thúc bằng ";" hoặc ",",
    hoặc mang tính mô tả/quy định chi tiết. Những mục này là CONTENT của section, để nguyên.
  • GHÉP TIÊU ĐỀ BỊ CẮT: Nếu "PHẦN 1" ở dòng trên, "HƯỚNG DẪN..." ở dòng dưới → Ghép thành 1 node ("PHẦN 1. HƯỚNG DẪN...").

══════════════════════════════════════════════
CHUẨN HÓA ĐẦU RA
══════════════════════════════════════════════
  • Giữ nguyên tiêu đề gốc (KHÔNG viết lại, KHÔNG dịch).
  • KHÔNG thêm mục không có trong văn bản.
  • Mảng con rỗng → [].
  • Chỉ trả về key "chapters".


══════════════════════════════════════════════
QUY TẮC BẮT BUỘC KHI CÓ CÂY TOC NỀN (Phase 1)
══════════════════════════════════════════════
  • GIỮ NGUYÊN HOÀN TOÀN title của mọi node đã có trong cây TOC nền.
    TUYỆT ĐỐI không đổi tên, không tách, không gộp node đã có.
  • KHÔNG TÁCH TIÊU ĐỀ CHAPTER ĐÃ XÁC LẬP: Nếu TOC nền đã có chapter
    "CHƯƠNG 7 CAN THIỆP DỰ PHÒNG TIÊN PHÁT Ở CẤP ĐỘ CỘNG ĐỒNG",
    TUYỆT ĐỐI không tách thành chapter "CHƯƠNG 7" + section "CAN THIỆP...".
    Dù văn bản OCR in "CHƯƠNG 7" trên 1 dòng và phần còn lại ở dòng sau,
    title chapter phải giữ nguyên y hệt TOC nền.
  • CHỈ THÊM mục con (subsections, subsubsections…) vào đúng chapter/section
    tương ứng khi tìm thấy trong văn bản, không làm gì khác.

"""


def _make_phase2_single_prompt() -> str:
    return (
        'Bạn là hệ thống xây dựng cây TOC tài liệu y tế từ văn bản OCR (Landing AI format).'
        ' Trả về DUY NHẤT JSON hợp lệ với key "chapters".\n\n'
        f'OUTPUT SCHEMA:\n{_PHASE2_SCHEMA}\n\n'
        f'{_PHASE2_READING_RULES}'
    )


def _make_phase2_iterative_prompt() -> str:
    return (
        'Bạn là hệ thống build và merge cây TOC tài liệu y tế theo từng chunk.'
        ' Trả về DUY NHẤT JSON hợp lệ với key "chapters".\n\n'
        f'OUTPUT SCHEMA:\n{_PHASE2_SCHEMA}\n\n'
        'NHIỆM VỤ — thực hiện tuần tự:\n'
        'A) GIỮ NGUYÊN cây TOC tích lũy: KHÔNG xóa, đổi tên, hay di chuyển node đã có.\n'
        'B) ĐỌC văn bản mới, nhận diện tiêu đề theo quy tắc bên dưới.\n'
        'C) MERGE SÂU vào TOC tích lũy:\n'
        '   • Chapter/section/subsection mới → thêm vào đúng vị trí.\n'
        '   • Title đã tồn tại → KHÔNG tạo node trùng, merge con vào node đó.\n'
        'D) SỬA HIERARCHY nếu văn bản mới tiết lộ cấp sai từ chunk trước\n'
        '   (ví dụ: node đang là chapter nhưng thực ra là section của chapter khác).\n\n'
        f'{_PHASE2_READING_RULES}'
    )


_PROMPT_PHASE2_SINGLE:    str = ""
_PROMPT_PHASE2_ITERATIVE: str = ""


def _init_phase2_prompts() -> None:
    """Gọi một lần sau khi config được xác định (từ args hoặc default)."""
    global _PROMPT_PHASE2_SINGLE, _PROMPT_PHASE2_ITERATIVE
    _PROMPT_PHASE2_SINGLE    = _make_phase2_single_prompt()
    _PROMPT_PHASE2_ITERATIVE = _make_phase2_iterative_prompt()


# ──────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def get_pages(text: str, n: int) -> str:
    """Trả về n trang đầu của text (phân cách bởi PAGE_BREAK)."""
    if n <= 0:
        return text
    parts = text.split(PAGE_BREAK)
    return PAGE_BREAK.join(parts[:n]) if len(parts) > n else text


_RE_ANCHOR_STRIP_LEAD = re.compile(r"^(\s*<a\s+[^>]+>\s*</a>\s*)+", re.IGNORECASE)
_RE_TOC_MARKER        = re.compile(r"MUC\s*LUC|MỤC\s*LỤC|TABLE\s+OF\s+CONTENTS", re.IGNORECASE)

# Nhận dạng dòng dạng "16. Tên mục ... 207" hoặc "16) Tên mục ... 207" (dòng mục lục)
_RE_TOC_ENTRY_LINE = re.compile(r"^\s*\d+[\.\)]\s+\S.{3,}\s+\d{1,4}\s*$", re.MULTILINE)
# Nhận dạng heading mục lục dạng "PHẦN X ... 87" hoặc "**PHẦN X ...**" có số cuối
_RE_TOC_HEADING_LINE = re.compile(
    r"^\s*(?:\*{1,2})?(?:PHẦN|CHƯƠNG|PHỤ LỤC)\s+\d+.{0,80}\d{1,4}\s*(?:\*{1,2})?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _is_toc_continuation(page_text: str) -> bool:
    """
    Kiểm tra một trang có phải là trang tiếp theo của MỤC LỤC không.
    Hỗ trợ cả hai dạng OCR Landing AI:
      - Dạng HTML: trang bắt đầu bằng <table ...>
      - Dạng plain/bold text: có nhiều dòng "số. tên mục ... số trang"
        hoặc heading PHẦN/CHƯƠNG + danh sách mục có số trang cuối.
    """
    stripped = _RE_ANCHOR_STRIP_LEAD.sub("", page_text.lstrip()).lstrip()

    # Trường hợp 1 (cũ): trang bắt đầu bằng <table
    if stripped.startswith("<table"):
        return True

    # Trường hợp 2: có ≥ 3 dòng dạng "N. text ... <số trang>"
    entry_matches = _RE_TOC_ENTRY_LINE.findall(stripped)
    if len(entry_matches) >= 3:
        return True

    # Trường hợp 3: có heading PHẦN/CHƯƠNG kèm số trang VÀ ít nhất 1 dòng mục
    heading_matches = _RE_TOC_HEADING_LINE.findall(stripped)
    if heading_matches and len(entry_matches) >= 1:
        return True

    return False


def get_scan_for_phase1(text: str, n_pages: int) -> tuple[str, int | None, int | None]:
    """
    Trả về (scan_text, toc_start, toc_end):
      - scan_text: nội dung gửi cho Phase 1 (n trang đầu, kèm nhãn MỤC LỤC nếu bị phân trang).
      - toc_start: index trang (0-based) của trang MỤC LỤC đầu tiên, hoặc None nếu không có.
      - toc_end:   index trang (0-based) của trang MỤC LỤC cuối cùng, hoặc None nếu không có.
                   Phase 2 sẽ bỏ qua tất cả trang <= toc_end để tránh nhầm nội dung MỤC LỤC.
    """
    pages = text.split(PAGE_BREAK)
    total = len(pages)

    toc_start: int | None = None
    for i in range(min(n_pages, total)):
        if _RE_TOC_MARKER.search(pages[i]):
            toc_start = i
            break

    if toc_start is None:
        return get_pages(text, n_pages), None, None

    # Tìm các trang tiếp theo của MỤC LỤC bằng heuristic mở rộng
    # (hỗ trợ cả dạng <table> và plain/bold text có số trang)
    toc_end = toc_start
    for j in range(toc_start + 1, min(toc_start + 15, total)):
        if _is_toc_continuation(pages[j]):
            toc_end = j
        else:
            break

    # Ghép các trang trong scan window, dán nhãn cho trang MỤC LỤC tiếp theo
    base_pages = list(pages[:min(n_pages, total)])
    if toc_end > toc_start:
        for k in range(toc_start + 1, toc_end + 1):
            labeled = f"\n[MỤC LỤC - tiếp theo, trang {k + 1}]\n" + pages[k]
            if k < len(base_pages):
                base_pages[k] = labeled
            else:
                base_pages.append(labeled)
        print(
            f"  Phase 1: MỤC LỤC trang {toc_start + 1}–{toc_end + 1} "
            f"({toc_end - toc_start} trang tiếp theo được ghép)"
        )

    return PAGE_BREAK.join(base_pages), toc_start, toc_end


def parse_json_response(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Fallback: tìm JSON object đầu tiên trong chuỗi
        m = re.search(r"\{[\s\S]*\}", s)
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
        text={"format": {"type": "json_object"}},
        temperature=0.0,
        max_output_tokens=32000,
    )
    return parse_json_response(response.output_text or "")


# ──────────────────────────────────────────────────────────────────────────────
# NORMALIZATION
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_nodes(items: list, depth: int) -> list:
    """Chuẩn hóa đệ quy danh sách node TOC tại depth cho trước (1 = chapters)."""
    if not isinstance(items, list):
        return []
    child_key = _DEPTH_CHILD_KEYS.get(depth)
    out = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                out.append({"title": item.strip()})
            continue
        if not isinstance(item, dict):
            continue
        node: dict = {"title": str(item.get("title", "")).strip()}
        if child_key:
            node[child_key] = _normalize_nodes(item.get(child_key, []), depth + 1)
        out.append(node)
    return out


def ensure_schema(toc: dict, filename: str) -> dict:
    """Đảm bảo toc có đủ các key metadata và chapters được chuẩn hóa."""
    for k in _METADATA_KEYS:
        if k not in toc:
            toc[k] = None
    toc["source_file"] = filename
    toc["chapters"]    = _normalize_nodes(toc.get("chapters", []), depth=1)
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
    """Tổng số sections trực tiếp trong tất cả chapters."""
    return sum(len(ch.get("sections", [])) for ch in chapters if isinstance(ch, dict))


def get_toc_depth(nodes: list, node_depth: int = 1) -> int:
    """Trả về độ sâu thực tế tối đa của cây TOC (1-indexed, chapters = 1)."""
    child_key = _DEPTH_CHILD_KEYS.get(node_depth)
    if not child_key:
        return node_depth
    max_d = node_depth
    for node in nodes:
        if isinstance(node, dict):
            children = node.get(child_key, [])
            if children:
                max_d = max(max_d, get_toc_depth(children, node_depth + 1))
    return max_d


def toc_is_shallow(toc: dict) -> bool:
    """True nếu TOC chưa có chapters hoặc độ sâu thực tế < MIN_SECTION_DEPTH."""
    chapters = toc.get("chapters", [])
    return not chapters or get_toc_depth(chapters) < MIN_SECTION_DEPTH


def _merge_nodes(base: list, updated: list, depth: int) -> list:
    """
    Recursive merge tại bất kỳ cấp nào của cây TOC.

    Quy tắc:
      • Node khớp title (base ∩ updated):
          - Base đã có con ở cấp này → đệ quy sâu hơn, KHÔNG thêm anh em mới từ AI
            (ngăn body-text noise ghi đè cấu trúc Phase 1 đã đúng)
          - Base chưa có con      → AI fill tự do (đây là mục đích của Phase 2)
      • Node chỉ có trong base       → giữ nguyên.
      • Node chỉ có trong updated:
          - base rỗng → thêm vào (chapter/section mới khám phá)
          - base đã có nội dung → BỎ QUA (Phase 1 là nguồn tin cậy ở cấp này)
    """
    if not updated:
        return base

    child_key        = _DEPTH_CHILD_KEYS.get(depth)
    updated_by_title = {n.get("title", ""): n for n in updated if isinstance(n, dict)}
    base_titles      = {n.get("title", "") for n in base    if isinstance(n, dict)}

    merged: list = []
    for node in base:
        if not isinstance(node, dict):
            continue
        title = node.get("title", "")
        if title in updated_by_title and child_key:
            base_children    = node.get(child_key, [])
            updated_children = updated_by_title[title].get(child_key, [])
            if base_children:
                # Đã có con → chỉ đệ quy sâu hơn, không thêm sibling mới
                merged_children = _merge_nodes(base_children, updated_children, depth + 1)
            else:
                # Chưa có con → AI được fill
                merged_children = updated_children
            merged.append({**node, child_key: merged_children})
        else:
            merged.append(node)

    # Thêm node mới chỉ khi base rỗng (không có Phase 1 content ở cấp này)
    if not base:
        for node in updated:
            if isinstance(node, dict) and node.get("title", "") not in base_titles:
                merged.append(node)

    return merged


def _merge_chapters(base: list, updated: list) -> list:
    """Entry point: merge chapter list từ AI vào TOC tích lũy."""
    return _merge_nodes(base, updated, depth=1)


# ──────────────────────────────────────────────────────────────────────────────
# TEXT CLEANER (Phase 2)
# ──────────────────────────────────────────────────────────────────────────────

_RE_ANCHOR_EMPTY  = re.compile(r"<a\s+id=['\"][^'\"]*['\"]>\s*</a>", re.IGNORECASE)
_RE_ATTRIB_BLOCK  = re.compile(r"<::.*?::>", re.DOTALL)
_RE_TABLE_STRUCT  = re.compile(r"</?(?:table|thead|tbody|tfoot|tr)(?:\s[^>]*)?>", re.IGNORECASE)
_RE_CELL_OPEN     = re.compile(r"<(?:td|th)(?:\s[^>]*)?>", re.IGNORECASE)
_RE_CELL_CLOSE    = re.compile(r"</(?:td|th)>", re.IGNORECASE)
_RE_PAGE_NUM_LINE = re.compile(r"^\s*\d{1,4}\s*$")
_RE_MULTI_BLANK   = re.compile(r"\n{3,}")


def clean_text_for_phase2(text: str) -> str:
    """Làm sạch OCR Markdown cho Phase 2: bỏ anchor, ảnh, cấu trúc HTML bảng, số trang."""
    text = _RE_ANCHOR_EMPTY.sub("", text)
    text = _RE_ATTRIB_BLOCK.sub("", text)
    text = _RE_TABLE_STRUCT.sub("\n", text)
    text = _RE_CELL_OPEN.sub("", text)
    text = _RE_CELL_CLOSE.sub("  |  ", text)
    lines = [ln for ln in text.splitlines() if not (_RE_PAGE_NUM_LINE.match(ln) and ln.strip())]
    text  = _RE_MULTI_BLANK.sub("\n\n", "\n".join(lines))
    return text.strip()


# ──────────────────────────────────────────────────────────────────────────────
# PHASE RUNNERS
# ──────────────────────────────────────────────────────────────────────────────

def phase1(client: OpenAI, text: str, filename: str) -> tuple[dict, bool, int | None]:
    """
    Trả về (toc_dict, found_toc, toc_end):
      - toc_dict:   kết quả Phase 1.
      - found_toc:  True nếu phát hiện trang MỤC LỤC trong n trang đầu.
      - toc_end:    index trang (0-based) của trang MỤC LỤC cuối cùng, hoặc None.
    """
    scan, toc_start, toc_end = get_scan_for_phase1(text, TOC_SCAN_PAGES)
    found_toc = toc_start is not None
    label = "(trang đầu + MỤC LỤC ghép đủ)" if found_toc else f"({TOC_SCAN_PAGES} trang đầu)"
    user  = f"source_file = {filename}\n\nNội dung văn bản {label}:\n{scan}"
    print(f"  Phase 1: {len(scan):,} chars ...")
    try:
        return call_ai(client, PROMPT_PHASE1, user), found_toc, toc_end
    except Exception as e:
        print(f"  Phase 1 failed: {e}")
        return {"chapters": [], "source_file": filename}, found_toc, toc_end


def phase2(
    client: OpenAI,
    text: str,
    metadata: dict,
    filename: str,
    body_start_page: int | None = None,
) -> dict:
    """
    Phase 2: đọc nội dung thực để xây TOC chi tiết.

    body_start_page (0-based, inclusive): index trang MỤC LỤC cuối cùng.
      - Nếu được cung cấp (tài liệu có MỤC LỤC): bỏ qua tất cả trang <= body_start_page
        để tránh nhầm nội dung trong MỤC LỤC thành chapters thật.
      - Nếu None (không có MỤC LỤC): đọc toàn bộ văn bản.
    """
    pages = text.split(PAGE_BREAK)

    if body_start_page is not None:
        skip = body_start_page + 1          # số trang bị bỏ (0 .. toc_end)
        pages = pages[skip:]
        print(
            f"  Phase 2: có MỤC LỤC → bỏ {skip} trang đầu (trang 1–{skip}), "
            f"đọc từ trang {skip + 1} trở xuống ({len(pages)} trang còn lại)"
        )
    else:
        print(f"  Phase 2: không có MỤC LỤC → đọc toàn bộ {len(pages)} trang")
    n_pages  = len(pages)
    meta_str = json.dumps(
        {k: metadata.get(k) for k in _METADATA_KEYS if k != "chapters"},
        ensure_ascii=False, indent=2,
    )

    def _user_header() -> str:
        return f"source_file = {filename}\n\nMETADATA đã biết:\n{meta_str}\n\n"

    try:
        if n_pages <= PHASE2_CHUNK_PAGES:
            # ── Single pass ───────────────────────────────────────────────
            # Dùng pages (đã slice bỏ phần MỤC LỤC), KHÔNG dùng text gốc toàn file
            clean = clean_text_for_phase2(PAGE_BREAK.join(pages))
            print(f"  Phase 2 (single pass): {n_pages} trang, {len(clean):,} ký tự")

            shallow_chapters = metadata.get("chapters", [])
            if shallow_chapters:
                # Có shallow TOC từ Phase 1 → truyền vào để AI dùng làm nền,
                # chỉ bổ sung mục con, KHÔNG rebuild lại toàn bộ.
                shallow_toc_str = json.dumps(
                    {"chapters": shallow_chapters}, ensure_ascii=False, indent=2
                )
                user = (
                    _user_header()
                    + "CÂY TOC NÔNG (Phase 1 — DÙNG LÀM NỀN, GIỮ NGUYÊN title mọi node đã có):\n"
                    + shallow_toc_str
                    + "\n\nNHIỆM VỤ: Giữ nguyên tất cả chapter/section đã có ở trên."
                    " Đọc văn bản để TÌM VÀ THÊM các mục con còn thiếu (subsections, subsubsections…)"
                    " vào đúng chỗ. TUYỆT ĐỐI không đổi tên, tách, hay gộp node đã có.\n\n"
                    + f"TOÀN BỘ VĂN BẢN ({n_pages} trang):\n\n{clean}"
                )
            else:
                user = _user_header() + f"TOÀN BỘ VĂN BẢN ({n_pages} trang):\n\n{clean}"

            result   = call_ai(client, _PROMPT_PHASE2_SINGLE, user)
            chapters = result.get("chapters", metadata.get("chapters", []))

        else:
            # ── Iterative chunking ────────────────────────────────────────
            n_chunks = (n_pages + PHASE2_CHUNK_PAGES - 1) // PHASE2_CHUNK_PAGES
            print(
                f"  Phase 2 (iterative): {n_pages} trang → "
                f"{n_chunks} chunks (≤{PHASE2_CHUNK_PAGES} trang/chunk)"
            )
            # Khởi tạo từ shallow TOC (Phase 1) thay vì rỗng,
            # để AI dùng làm nền và chỉ thêm mục con, không rebuild lại từ đầu.
            accumulated: list = list(metadata.get("chapters", []))

            for i in range(n_chunks):
                start = i * PHASE2_CHUNK_PAGES
                end   = min(start + PHASE2_CHUNK_PAGES, n_pages)
                clean = clean_text_for_phase2(PAGE_BREAK.join(pages[start:end]))
                toc_label = (
                    "TOC NỀN (Phase 1)" if i == 0
                    else f"TOC tích lũy (sau {i} chunk)"
                )
                print(
                    f"    Chunk {i+1}/{n_chunks}: trang {start+1}–{end} / {n_pages},"
                    f" {len(clean):,} ký tự, TOC tích lũy: {len(accumulated)} chapters"
                )
                user = (
                    _user_header()
                    + f"CÂY TOC ĐÃ TÍCH LŨY ({toc_label} — GIỮ NGUYÊN title mọi node đã có, chỉ thêm mục con):\n"
                    + json.dumps({"chapters": accumulated}, ensure_ascii=False, indent=2)
                    + f"\n\nĐOẠN VĂN BẢN MỚI — chunk {i+1}/{n_chunks}"
                    + f" (trang {start+1}–{end}, tổng {n_pages} trang):\n\n{clean}"
                )
                result      = call_ai(client, _PROMPT_PHASE2_ITERATIVE, user)
                # Dùng merge thay vì ghi đè trực tiếp — đảm bảo chapters từ
                # base không bao giờ bị mất dù AI trả về danh sách thiếu.
                accumulated = _merge_chapters(accumulated, result.get("chapters", []))

            chapters = accumulated
            print(f"  Phase 2 iterative done: {len(chapters)} chapters")

        return {**metadata, "chapters": chapters}

    except Exception as e:
        print(f"  Phase 2 failed: {e} — giữ lại kết quả Phase 1")
        return metadata


# ──────────────────────────────────────────────────────────────────────────────
# MAIN PROCESSING
# ──────────────────────────────────────────────────────────────────────────────

def process_file(md_path: Path, client: OpenAI, output_dir: Path) -> None:
    if not md_path.exists():
        print(f"File not found: {md_path}")
        return

    print(f"Processing: {md_path.name}")
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    raw_toc, found_toc, toc_end = phase1(client, text, md_path.name)
    toc = ensure_schema(raw_toc, md_path.name)
    n_ch  = len(toc.get("chapters", []))
    n_sec = count_sections(toc.get("chapters", []))
    print(f"  Phase 1 result: {n_ch} chapters, {n_sec} sections")

    global MIN_SECTION_DEPTH
    total_pages = toc.get("total_pages") or 0
    MIN_SECTION_DEPTH = (
        MIN_SECTION_DEPTH_LONG if total_pages >= PAGE_THRESHOLD_FOR_DEPTH
        else MIN_SECTION_DEPTH_SHORT
    )
    print(
        f"  MIN_SECTION_DEPTH = {MIN_SECTION_DEPTH}"
        f" (total_pages={total_pages}, ngưỡng={PAGE_THRESHOLD_FOR_DEPTH})"
    )

    # Phase 2: bắt buộc khi không có MỤC LỤC, hoặc TOC quá nông
    run_phase2 = not found_toc or toc_is_shallow(toc)
    if not found_toc:
        print("  Không có MỤC LỤC → Phase 2 bắt buộc")
    elif toc_is_shallow(toc):
        print(
            f"  TOC shallow (depth {get_toc_depth(toc.get('chapters', []))} "
            f"< {MIN_SECTION_DEPTH}) → Phase 2"
        )
    else:
        print("  Có MỤC LỤC, TOC đủ sâu → dùng kết quả Phase 1")

    if run_phase2:
        toc = ensure_schema(
            phase2(client, text, toc, md_path.name, body_start_page=toc_end),
            md_path.name,
        )
        print(
            f"  Phase 2 result: "
            f"{len(toc.get('chapters', []))} chapters, "
            f"{count_sections(toc.get('chapters', []))} sections"
        )

    print(
        f"  title={toc.get('title')!r} | "
        f"decision={toc.get('decision_number')!r} | "
        f"pages={toc.get('total_pages')}"
    )

    stem = md_path.stem
    if stem.endswith(".extraction"):
        stem = stem[: -len(".extraction")]
    out_path = output_dir / f"{stem}_toc_structure.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved → {out_path.name}")



def _coerce_phase1_result(result: object) -> tuple[dict[str, Any], bool, int | None]:
    if (
        isinstance(result, tuple)
        and len(result) == 3
        and isinstance(result[0], dict)
        and isinstance(result[1], bool)
    ):
        return result[0], result[1], result[2]
    if (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], dict)
        and isinstance(result[1], bool)
    ):
        return result[0], result[1], None
    raise ValueError("phase1() must return (toc_dict, found_toc[, toc_end]).")


def _run_toc_pipeline(client: OpenAI, text: str, filename: str) -> dict[str, Any]:
    global MIN_SECTION_DEPTH

    raw_toc, found_toc, toc_end = _coerce_phase1_result(phase1(client, text, filename))
    toc = ensure_schema(raw_toc, filename)
    n_ch = len(toc.get("chapters", []))
    n_sec = count_sections(toc.get("chapters", []))
    print(f"  Phase 1 result: {n_ch} chapters, {n_sec} sections")

    total_pages = toc.get("total_pages") or 0
    MIN_SECTION_DEPTH = (
        MIN_SECTION_DEPTH_LONG if total_pages >= PAGE_THRESHOLD_FOR_DEPTH
        else MIN_SECTION_DEPTH_SHORT
    )
    print(
        f"  MIN_SECTION_DEPTH = {MIN_SECTION_DEPTH}"
        f" (total_pages={total_pages}, ngưỡng={PAGE_THRESHOLD_FOR_DEPTH})"
    )

    run_phase2 = not found_toc or toc_is_shallow(toc)
    if not found_toc:
        print("  Không có MỤC LỤC → Phase 2 bắt buộc")
    elif toc_is_shallow(toc):
        print(
            f"  TOC shallow (depth {get_toc_depth(toc.get('chapters', []))} "
            f"< {MIN_SECTION_DEPTH}) → Phase 2"
        )
    else:
        print("  Có MỤC LỤC, TOC đủ sâu → dùng kết quả Phase 1")

    if run_phase2:
        toc = ensure_schema(
            phase2(client, text, toc, filename, body_start_page=toc_end),
            filename,
        )
        print(
            f"  Phase 2 result: "
            f"{len(toc.get('chapters', []))} chapters, "
            f"{count_sections(toc.get('chapters', []))} sections"
        )

    return toc


def _configure_runtime_from_env() -> None:
    load_dotenv(override=False)


def _build_client() -> OpenAI:
    _configure_runtime_from_env()
    api_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
    if not api_key:
        raise BadRequestException("Missing OPENAI_API_KEY in environment.")

    model_name = os.getenv("OPENAI_MODEL_NAME", "").strip().strip('"').strip("'")
    if model_name:
        global MODEL
        MODEL = model_name

    base_url = os.getenv("OPENAI_API_URL", "").strip().strip('"').strip("'")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def build_toc_from_text(text: str, filename: str) -> dict[str, Any]:
    _init_phase2_prompts()
    client = _build_client()
    return _run_toc_pipeline(client, text, filename)


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

    global TOC_SCAN_PAGES, MIN_SECTION_DEPTH, MIN_SECTION_DEPTH_SHORT, MIN_SECTION_DEPTH_LONG, PAGE_THRESHOLD_FOR_DEPTH, MODEL, PHASE2_CHUNK_PAGES
    TOC_SCAN_PAGES            = args.pages
    MIN_SECTION_DEPTH_SHORT   = args.min_depth_short
    MIN_SECTION_DEPTH_LONG    = args.min_depth_long
    PAGE_THRESHOLD_FOR_DEPTH  = args.depth_page_threshold
    MODEL                     = args.model
    PHASE2_CHUNK_PAGES        = args.chunk_pages

    _init_phase2_prompts()

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
    parser = argparse.ArgumentParser(
        description="Trích xuất cây TOC từ file Markdown OCR (Landing AI format)."
    )
    parser.add_argument("--input-dir",    default=str(INPUT_DIR),
                        help=f"Thư mục chứa file .md (default: {INPUT_DIR})")
    parser.add_argument("--output-dir",   default=str(OUTPUT_DIR),
                        help=f"Thư mục xuất file JSON (default: {OUTPUT_DIR})")
    parser.add_argument("--files",        nargs="*",
                        help="Danh sách file cụ thể cần xử lý (bỏ qua --input-dir nếu set)")
    parser.add_argument("--pages",        type=int, default=TOC_SCAN_PAGES,
                        help=f"Số trang đầu quét ở Phase 1 (default: {TOC_SCAN_PAGES})")
    parser.add_argument("--min-depth-short",      type=int, default=MIN_SECTION_DEPTH_SHORT,
                        help=(
                            f"Độ sâu TOC tối thiểu cho tài liệu < --depth-page-threshold trang "
                            f"(default: {MIN_SECTION_DEPTH_SHORT})"
                        ))
    parser.add_argument("--min-depth-long",       type=int, default=MIN_SECTION_DEPTH_LONG,
                        help=(
                            f"Độ sâu TOC tối thiểu cho tài liệu >= --depth-page-threshold trang "
                            f"(default: {MIN_SECTION_DEPTH_LONG})"
                        ))
    parser.add_argument("--depth-page-threshold", type=int, default=PAGE_THRESHOLD_FOR_DEPTH,
                        help=(
                            f"Ngưỡng số trang để chọn MIN_SECTION_DEPTH_SHORT hay _LONG "
                            f"(default: {PAGE_THRESHOLD_FOR_DEPTH})"
                        ))
    parser.add_argument("--model",        default=MODEL,
                        help=f"Model OpenAI (default: {MODEL})")
    parser.add_argument("--chunk-pages",  type=int, default=PHASE2_CHUNK_PAGES,
                        help=f"Số trang tối đa mỗi chunk iterative Phase 2 (default: {PHASE2_CHUNK_PAGES})")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()