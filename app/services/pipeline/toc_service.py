from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

# ── Directories ───────────────────────────────────────────────────────────────
INPUT_DIR      = Path("./data/02_ocr_markdown")
OUTPUT_DIR     = Path("./data/03_toc_json")
ADE_CHUNKS_DIR = Path("./data/06_ade_chunks")
MD_FILES: list[str] = []

# ── Tuning constants ──────────────────────────────────────────────────────────
PAGE_BREAK               = "<!-- PAGE BREAK -->"
MODEL                    = "gpt-5.1"
TOC_SCAN_PAGES           = 20
MIN_SECTION_DEPTH_SHORT  = 3
MIN_SECTION_DEPTH_LONG   = 4
PAGE_THRESHOLD_FOR_DEPTH = 99
MIN_SECTION_DEPTH        = MIN_SECTION_DEPTH_LONG
PHASE2_CHUNK_PAGES       = 30
PHASE3_PREVIEW_CHARS     = 1500
PHASE3_MAX_USER_CHARS    = 200_000
PHASE3_BATCH_TOC_SIZE    = 15
PHASE3_ADE_WINDOW_SIZE   = 100
PHASE3_CHAPTER_BUFFER    = 25
PHASE3_SUBGROUP_SIZE     = 8
PHASE3_EDGE_RATIO        = 0.15
PHASE3_EXPAND_FACTOR     = 2
PHASE3_MAX_EXPAND_WINDOW = 600   # giới hạn kích thước cửa sổ expand (chunks) tránh vượt context
PHASE3_MAX_EXPAND_TRIES  = 2     # số lần expand tối đa mỗi sub-batch
LANDMARK_BATCH_SIZE      = 100
LANDMARK_OVERLAP         = 10

# ── TOC schema ────────────────────────────────────────────────────────────────
_METADATA_KEYS = [
    "title", "publisher", "decision_number", "specialty",
    "date", "isbn_electronic", "isbn_print", "total_pages",
    "source_file", "chapters",
]

_DEPTH_CHILD_KEYS: dict[int, str] = {
    1: "sections",
    2: "subsections",
    3: "subsubsections",
    4: "subsubsubsections",
    5: "subsubsubsubsections",
}


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

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

_PROMPT_PHASE3_SYS = (
    "Bạn là hệ thống mapping TOC heading → ADE chunk trong tài liệu y tế OCR. "
    'Trả về DUY NHẤT JSON hợp lệ: {"mappings": [{"toc_idx": int, "chunk_id": str_or_null}]}\n\n'
    "ĐẶC ĐIỂM QUAN TRỌNG CỦA ADE CHUNKS TRONG TÀI LIỆU NÀY:\n"
    "• Nhiều tiêu đề cấp 2 (BƯỚC X, MỤC X, I/II/III...) không xuất hiện như chunk text riêng — "
    "chúng nằm BÊN TRONG một table/figure chunk, thường là cell đầu tiên hoặc header của bảng.\n"
    "• Preview chunk được hiển thị đầy đủ. Hãy scan TOÀN BỘ nội dung, không chỉ phần mở đầu.\n"
    "• Nếu tiêu đề cần tìm xuất hiện ở giữa hoặc cuối preview của một chunk → chunk đó là kết quả đúng.\n"
    "• SỐ LA MÃ vs SỐ THƯỜNG: Mục lục có thể dùng số thường (Phần 4, Chương 3) trong khi nội dung thực tế "
    "dùng số La Mã (PHẦN IV, CHƯƠNG III) hoặc ngược lại. Hãy nhận diện linh hoạt: Phần 4 = PHẦN IV, "
    "Chương 2 = CHƯƠNG II, v.v. Đây KHÔNG phải là chương khác — hãy map vào chunk có tiêu đề tương đương."
)

_PROMPT_LANDMARK_SYS = (
    "Bạn là hệ thống định vị chương cấp 1 trong tài liệu y tế OCR. "
    'Trả về DUY NHẤT JSON hợp lệ: {"mappings": [{"toc_idx": int, "chunk_id": str_or_null}]}\n\n'
    "QUY TẮC QUAN TRỌNG:\n"
    "• Đây chỉ là MỘT ĐOẠN của tài liệu — nếu heading không có trong đoạn này thì null là bình thường.\n"
    "• Chỉ tìm trong ĐOẠN THỰC ĐƯỢC CUNG CẤP — KHÔNG phải bảng mục lục đầu sách.\n"
    "• Nếu tìm thấy heading chính xác → gán chunk_id đó.\n"
    "• Nếu heading chương không xuất hiện là chunk text riêng — hãy tìm chunk gần nhất "
    "có nội dung đầu chương đó (chunk đầu tiên của phần nội dung mới, được in đậm hoặc tiêu đề). \n"
    "• SỐ LA MÃ = SỐ THƯỜNG: Phần 4 = PHẦN IV, Chương 3 = CHƯƠNG III. Nhận diện linh hoạt.\n"
    "• Heading đôi khi bị OCR tách thành 2 chunk liên tiếp ngắn "
    "(ví dụ chunk A: 'CHƯƠNG 7', chunk B: 'Tiêu đề nội dung'). "
    "Chọn chunk_id của chunk ĐẦU TIÊN chứa 'CHƯƠNG 7'.\n"
    "• Heading có thể nằm BÊN TRONG table/figure chunk — scan toàn bộ nội dung, không chỉ đầu chunk.\n"
    "• GIÁ TRỊ chunk_id phải là UUID 36 ký tự CHÍNH XÁC sau 'chunk_id=' trong danh sách ADE. "
    "Sai format → null."
)

_PROMPT_PHASE3_USER = """\
TOC NODES (toc_idx — title):
{toc_list}

ADE CHUNKS (mỗi dòng gồm: số thứ tự | chunk_id=UUID-36-ký-tự | nội dung):
{chunk_list}

NHIỆM VỤ: Với mỗi toc_idx, tìm chunk ADE có TEXT khớp tốt nhất với tiêu đề đó.

QUY TẮC:
1. Chunk phải là nơi heading XUẤT HIỆN TRONG NỘI DUNG THỰC của tài liệu — KHÔNG phải bảng mục lục đầu sách.
2. Khớp dựa trên số mục (5.3, CHƯƠNG 4…) VÀ tiêu đề. Số mục (CHƯƠNG 4) phải khớp CHÍNH XÁC. Không gán nhầm sang chương khác.
3. Nếu tiêu đề bị ngắt dòng hoặc phân tách thành nhiều chunk liên tiếp (ví dụ chunk 1: "CHƯƠNG 7", chunk 2: "Tiêu đề"), hãy chọn chunk_id của phần ĐẦU TIÊN (chunk 1 chứa "CHƯƠNG 7"). Chấp nhận tiêu đề bị cắt cụt.
4. Nhiều toc_idx có thể được gán cùng một chunk_id khi nhiều tiêu đề nằm trong cùng một chunk (thường gặp khi heading bị gộp vào chunk kề trước).
5. GIÁ TRỊ chunk_id trong kết quả phải là UUID 36 ký tự CHÍNH XÁC sau 'chunk_id=' trên dòng ADE CHUNKS.
   KHÔNG đưa số thứ tự vào trước UUID. Sai format → trả null.
6. HEADING TRONG TABLE/FIGURE: Tiêu đề cấp 2 (BƯỚC X, MỤC X, I/II/III...) thường nằm BÊN TRONG
   một chunk dạng table hoặc figure — không phải chunk text riêng. Tiêu đề có thể ở giữa hoặc
   cuối preview, không nhất thiết ở đầu. Scan TOÀN BỘ nội dung preview của mỗi chunk.
   Khi tìm thấy tiêu đề khớp bên trong một table chunk → đó là chunk đúng, hãy gán nó.
7. Thà để null hơn là assign sai chunk — không đoán mò khi không thấy text khớp rõ ràng."""

# Lazy-initialized Phase 2 system prompts (depend on _PHASE2_SCHEMA / _PHASE2_READING_RULES)
_PROMPT_PHASE2_SINGLE:    str = ""
_PROMPT_PHASE2_ITERATIVE: str = ""


def _init_phase2_prompts() -> None:
    global _PROMPT_PHASE2_SINGLE, _PROMPT_PHASE2_ITERATIVE
    _PROMPT_PHASE2_SINGLE = (
        'Bạn là hệ thống xây dựng cây TOC tài liệu y tế từ văn bản OCR (Landing AI format).'
        ' Trả về DUY NHẤT JSON hợp lệ với key "chapters".\n\n'
        f'OUTPUT SCHEMA:\n{_PHASE2_SCHEMA}\n\n'
        f'{_PHASE2_READING_RULES}'
    )
    _PROMPT_PHASE2_ITERATIVE = (
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


# ══════════════════════════════════════════════════════════════════════════════
# LLM UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def parse_json_response(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            raise
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            candidate = re.sub(r",\s*$", "", candidate.rstrip())
            open_sq = candidate.count("[") - candidate.count("]")
            open_br = candidate.count("{") - candidate.count("}")
            candidate += "]" * max(0, open_sq) + "}" * max(0, open_br)
            print(f"  [Warning] JSON response was truncated! Auto-closed {open_sq} arrays and {open_br} objects.")
            return json.loads(candidate)


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


# ══════════════════════════════════════════════════════════════════════════════
# TOC SCHEMA & TREE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_nodes(items: list, depth: int) -> list:
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


def count_sections(chapters: list) -> int:
    return sum(len(ch.get("sections", [])) for ch in chapters if isinstance(ch, dict))


def get_toc_depth(nodes: list, node_depth: int = 1) -> int:
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
    chapters = toc.get("chapters", [])
    return not chapters or get_toc_depth(chapters) < MIN_SECTION_DEPTH


def _merge_nodes(base: list, updated: list, depth: int) -> list:
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
            merged_children  = (
                _merge_nodes(base_children, updated_children, depth + 1)
                if base_children else updated_children
            )
            merged.append({**node, child_key: merged_children})
        else:
            merged.append(node)
    if not base:
        for node in updated:
            if isinstance(node, dict) and node.get("title", "") not in base_titles:
                merged.append(node)
    return merged


def _merge_chapters(base: list, updated: list) -> list:
    return _merge_nodes(base, updated, depth=1)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — TOC PAGE DETECTION & METADATA EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

_RE_ANCHOR_STRIP_LEAD = re.compile(r"^(\s*<a\s+[^>]+>\s*</a>\s*)+", re.IGNORECASE)
_RE_TOC_MARKER        = re.compile(r"MUC\s*LUC|MỤC\s*LỤC|TABLE\s+OF\s+CONTENTS", re.IGNORECASE)
_RE_TOC_ENTRY_LINE    = re.compile(r"^\s*\d+[\.\)]\s+\S.{3,}\s+\d{1,4}\s*$", re.MULTILINE)
_RE_TOC_HEADING_LINE  = re.compile(
    r"^\s*(?:\*{1,2})?(?:PHẦN|CHƯƠNG|PHỤ LỤC)\s+\d+.{0,80}\d{1,4}\s*(?:\*{1,2})?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_RE_TOC_DOTTED_LINE = re.compile(
    r"^\s*\S.{3,}[.\u2026]{3,}\s*\d{1,4}\s*$",
    re.MULTILINE,
)


def _is_toc_continuation(page_text: str) -> bool:
    stripped = _RE_ANCHOR_STRIP_LEAD.sub("", page_text.lstrip()).lstrip()
    if stripped.startswith("<table"):
        return True
    entry_matches = _RE_TOC_ENTRY_LINE.findall(stripped)
    if len(entry_matches) >= 3:
        return True
    dotted_matches = _RE_TOC_DOTTED_LINE.findall(stripped)
    if len(dotted_matches) >= 3:
        return True
    heading_matches = _RE_TOC_HEADING_LINE.findall(stripped)
    if heading_matches and (len(entry_matches) >= 1 or len(dotted_matches) >= 1):
        return True
    return False


def get_pages(text: str, n: int) -> str:
    if n <= 0:
        return text
    parts = text.split(PAGE_BREAK)
    return PAGE_BREAK.join(parts[:n]) if len(parts) > n else text


def get_scan_for_phase1(text: str, n_pages: int) -> tuple[str, int | None, int | None]:
    pages = text.split(PAGE_BREAK)
    total = len(pages)
    toc_start: int | None = None
    for i in range(min(n_pages, total)):
        if _RE_TOC_MARKER.search(pages[i]):
            toc_start = i
            break
    if toc_start is None:
        return get_pages(text, n_pages), None, None

    toc_end = toc_start
    for j in range(toc_start + 1, min(toc_start + 15, total)):
        if _is_toc_continuation(pages[j]):
            toc_end = j
        else:
            break

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


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — BODY TEXT SCAN & DEEP TOC BUILD
# ══════════════════════════════════════════════════════════════════════════════

_RE_ANCHOR_EMPTY  = re.compile(r"<a\s+id=['\"][^'\"]*['\"]>\s*</a>", re.IGNORECASE)
_RE_ATTRIB_BLOCK  = re.compile(r"<::.*?::>", re.DOTALL)
_RE_TABLE_STRUCT  = re.compile(r"</?(?:table|thead|tbody|tfoot|tr)(?:\s[^>]*)?>", re.IGNORECASE)
_RE_CELL_OPEN     = re.compile(r"<(?:td|th)(?:\s[^>]*)?>", re.IGNORECASE)
_RE_CELL_CLOSE    = re.compile(r"</(?:td|th)>", re.IGNORECASE)
_RE_PAGE_NUM_LINE = re.compile(r"^\s*\d{1,4}\s*$")
_RE_MULTI_BLANK   = re.compile(r"\n{3,}")


def clean_text_for_phase2(text: str) -> str:
    text = _RE_ANCHOR_EMPTY.sub("", text)
    text = _RE_ATTRIB_BLOCK.sub("", text)
    text = _RE_TABLE_STRUCT.sub("\n", text)
    text = _RE_CELL_OPEN.sub("", text)
    text = _RE_CELL_CLOSE.sub("  |  ", text)
    lines = [ln for ln in text.splitlines() if not (_RE_PAGE_NUM_LINE.match(ln) and ln.strip())]
    text  = _RE_MULTI_BLANK.sub("\n\n", "\n".join(lines))
    return text.strip()


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — ADE CHUNK MAPPING
# ══════════════════════════════════════════════════════════════════════════════

_SKIP_ADE_TYPES  = {"marginalia", "logo", "scan_code", "attestation"}
_RE_ANCHOR_STRIP = re.compile(r"<a[^>]+>.*?</a>", re.DOTALL | re.IGNORECASE)
_RE_TAG_STRIP    = re.compile(r"<[^>]+>")
_RE_UUID         = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def _find_ade_chunks_path(md_path: Path, ade_dir: Path) -> Path:
    stem = md_path.stem
    base = stem[: -len("_ocr")] if stem.endswith("_ocr") else stem
    return ade_dir / f"{base}_ade_chunks.json"


def _build_ade_summary(ade_chunks: list[dict], toc_end_page: int | None = None) -> list[dict]:
    out = []
    for i, ch in enumerate(ade_chunks):
        if ch.get("type") in _SKIP_ADE_TYPES:
            continue
        if toc_end_page is not None:
            bboxes = ch.get("bboxes", [])
            if bboxes and bboxes[0]["page"] <= toc_end_page:
                continue
        md      = ch.get("markdown", "")
        text    = _RE_ANCHOR_STRIP.sub("", md)
        text    = _RE_TAG_STRIP.sub(" ", text)
        text    = re.sub(r"\s+", " ", text).strip()
        limit   = 1100 if ch.get("type") in ("figure", "table") else PHASE3_PREVIEW_CHARS
        preview = text[:limit]
        out.append({"i": i, "id": ch["id"], "t": preview, "type": ch.get("type", "text")})
    return out


def _flatten_toc_refs(nodes: list, path: str = "") -> list[tuple[str, dict]]:
    result = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title     = node.get("title", "")
        full_path = f"{path}/{title}" if path else title
        result.append((full_path, node))
        for key in _DEPTH_CHILD_KEYS.values():
            children = node.get(key, [])
            if children:
                result.extend(_flatten_toc_refs(children, full_path))
    return result


def _sanitize_chunk_id(cid, valid_ids: set[str]) -> str | None:
    if not isinstance(cid, str):
        return None
    if cid in valid_ids:
        return cid
    m = _RE_UUID.search(cid)
    if m and m.group(0) in valid_ids:
        stripped = m.group(0)
        print(f"  Phase 3: sanitized chunk_id {cid!r} → {stripped!r}")
        return stripped
    return None


def _phase3_build_user(
    batch_refs: list[tuple],
    ade_window: list[dict],
    global_offset: int,
    batch_no: int,
    n_batches: int,
    n_toc_total: int,
    toc_indices: list[int] | None = None,
) -> str:
    toc_list_str = "\n".join(
        f"[{toc_indices[li] if toc_indices else global_offset + li}] {node.get('title', '')}"
        for li, (_, node) in enumerate(batch_refs)
    )
    chunk_list_str = "\n".join(
        f"{ch['i']:4d} | chunk_id={ch['id']} | {ch['t']}" for ch in ade_window
    )
    first_idx = toc_indices[0] if toc_indices else global_offset
    last_idx  = toc_indices[-1] if toc_indices else global_offset + len(batch_refs) - 1
    user = _PROMPT_PHASE3_USER.format(
        toc_list   = toc_list_str,
        chunk_list = chunk_list_str,
        preview    = PHASE3_PREVIEW_CHARS,
    )
    user += (
        f"\n\n[BATCH {batch_no}/{n_batches}: "
        f"TOC index {first_idx}–{last_idx} "
        f"(tổng {n_toc_total} nodes). "
        f"ADE seq {ade_window[0]['i'] if ade_window else '?'}–"
        f"{ade_window[-1]['i'] if ade_window else '?'} "
        f"({len(ade_window)} chunks trong cửa sổ này). "
        f"Chỉ assign toc_idx cho các node trong batch này.]"
    )
    return user


def _phase3_apply_mappings(
    mappings: list[dict],
    flat_refs: list[tuple],
    valid_ids: set[str],
) -> tuple[int, int]:
    applied = nulled = 0
    for m in mappings:
        idx = m.get("toc_idx")
        cid = m.get("chunk_id")
        if idx is None or not (0 <= idx < len(flat_refs)):
            continue
        _, node = flat_refs[idx]
        if node.get("heading_chunk_id"):
            continue
        clean_id = _sanitize_chunk_id(cid, valid_ids)
        node["heading_chunk_id"] = clean_id
        if clean_id:
            applied += 1
        else:
            nulled += 1
    return applied, nulled


def _validate_and_apply_mappings(
    mappings: list[dict],
    win_s: int,
    win_e: int,
    id_to_ade_pos: dict[str, int],
    valid_ids: set[str],
    flat_refs: list[tuple],
) -> tuple[int, int, list[int]]:
    filtered: list[dict] = []
    mapped_positions: list[int] = []
    for m in mappings:
        idx      = m.get("toc_idx")
        cid      = m.get("chunk_id")
        clean_id = _sanitize_chunk_id(cid, valid_ids) if cid else None
        if clean_id and clean_id in id_to_ade_pos:
            ade_pos = id_to_ade_pos[clean_id]
            if (win_s - 3) <= ade_pos < (win_e + 3):
                filtered.append(m)
                mapped_positions.append(ade_pos)
            else:
                filtered.append({**m, "chunk_id": None})
        else:
            filtered.append(m)
    app, nul = _phase3_apply_mappings(filtered, flat_refs, valid_ids)
    return app, nul, mapped_positions


def _lis_anchors(anchors: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Longest Increasing Subsequence filter on (toc_idx, ade_pos) pairs."""
    if len(anchors) <= 1:
        return list(anchors)
    tails:    list[int] = []
    tail_idx: list[int] = []
    parent: list[int]   = [-1] * len(anchors)
    for i, (_, ade_pos) in enumerate(anchors):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < ade_pos:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(ade_pos)
            tail_idx.append(i)
        else:
            tails[lo] = ade_pos
            tail_idx[lo] = i
        parent[i] = tail_idx[lo - 1] if lo > 0 else -1
    result: list[tuple[int, int]] = []
    idx = tail_idx[-1]
    while idx >= 0:
        result.append(anchors[idx])
        idx = parent[idx]
    result.reverse()
    return result


def _get_bounded_window(
    toc_s: int,
    toc_e: int,
    landmarks: dict[int, int],
    n_ade: int,
    buffer: int = PHASE3_CHAPTER_BUFFER,
) -> tuple[int, int]:
    if not landmarks:
        return 0, n_ade
    sorted_lm = sorted(landmarks.items())
    
    prev_ade  = max((pos for t, pos in sorted_lm if t <= toc_s), default=0)
    next_ade  = min((pos for t, pos in sorted_lm if t >= toc_e), default=n_ade)
    
    # Asymmetrical windowing logic:
    # 1. Overlapping backwards is generally safe (hits the end/appendices of previous chapter).
    #    We bound it by the midpoint to avoid hitting the START of the previous chapter.
    prev_chap_ade = max((pos for t, pos in sorted_lm if t < toc_s), default=0)
    mid_prev = (prev_chap_ade + prev_ade) // 2 if prev_chap_ade > 0 else 0
    win_s = max(mid_prev, prev_ade - buffer)
    
    # 2. Overlapping forwards is FATAL because identical subheadings (e.g. "Bước 1", "Bước 2")
    #    are always located right at the start of the next chapter.
    #    We use a very tight bound (+2) to just include the boundary shared chunk.
    win_e = min(n_ade, next_ade + 2)

    return win_s, win_e


def _phase3_expand_if_edge(
    mapped_positions: list[int],
    win_s: int,
    win_e: int,
    n_ade: int,
    edge_ratio: float = PHASE3_EDGE_RATIO,
    expand_factor: int = PHASE3_EXPAND_FACTOR,
) -> tuple[int, int] | None:
    if not mapped_positions:
        return None
    win_size  = max(1, win_e - win_s)
    edge_size = max(5, int(win_size * edge_ratio))
    near_left  = any(p < win_s + edge_size for p in mapped_positions)
    near_right = any(p > win_e - 1 - edge_size for p in mapped_positions)
    if not (near_left or near_right):
        return None
    expansion = win_size * (expand_factor - 1)
    new_win_s = max(0,     win_s - expansion) if near_left  else win_s
    new_win_e = min(n_ade, win_e + expansion) if near_right else win_e
    if new_win_s == win_s and new_win_e == win_e:
        return None
    # Giới hạn kích thước cửa sổ mở rộng để không vượt context window LLM.
    # Nếu expansion quá lớn, thu hẹp đối xứng xung quanh tâm vùng mapped.
    new_size = new_win_e - new_win_s
    if new_size > PHASE3_MAX_EXPAND_WINDOW:
        center   = (min(mapped_positions) + max(mapped_positions)) // 2
        half     = PHASE3_MAX_EXPAND_WINDOW // 2
        new_win_s = max(0,     center - half)
        new_win_e = min(n_ade, new_win_s + PHASE3_MAX_EXPAND_WINDOW)
        new_win_s = max(0,     new_win_e - PHASE3_MAX_EXPAND_WINDOW)  # re-align nếu clamp cuối
    return new_win_s, new_win_e


def _phase3_get_landmarks(
    client: OpenAI,
    flat_refs: list[tuple],
    ade_summary: list[dict],
    valid_ids: set[str],
) -> dict[int, int]:
    """Exhaustive sliding-window scan over all ADE chunks to locate top-level
    chapter positions.  Every chunk is shown to the LLM in overlapping batches
    (size=LANDMARK_BATCH_SIZE, overlap=LANDMARK_OVERLAP) so no chapter can slip
    through regardless of OCR quality or keyword presence.  Results are
    deduplicated (earliest position wins) and LIS-filtered to remove
    hallucinated out-of-order placements."""
    id_to_ade_pos: dict[str, int] = {ch["id"]: j for j, ch in enumerate(ade_summary)}
    n_ade = len(ade_summary)

    chapter_indices = [
        i for i, (path, node) in enumerate(flat_refs)
        if "/" not in path and not node.get("heading_chunk_id")
    ]
    if not chapter_indices:
        print("  Phase 3 [Landmark]: no chapters to map")
        return {}

    chapter_list_str = "\n".join(f"[{i}] {flat_refs[i][0]}" for i in chapter_indices)
    step      = LANDMARK_BATCH_SIZE - LANDMARK_OVERLAP
    starts    = list(range(0, n_ade, step))
    n_batches = len(starts)
    raw_hits: dict[int, list[int]] = {idx: [] for idx in chapter_indices}

    print(
        f"  Phase 3 [Landmark]: exhaustive scan — {n_ade} chunks, {n_batches} batches "
        f"(size={LANDMARK_BATCH_SIZE}, overlap={LANDMARK_OVERLAP})"
    )

    for b_no, b_start in enumerate(starts):
        b_end  = min(b_start + LANDMARK_BATCH_SIZE, n_ade)
        window = ade_summary[b_start:b_end]
        chunk_list_str = "\n".join(
            f"{ch['i']:4d} | chunk_id={ch['id']} | {ch['t']}" for ch in window
        )
        user = (
            f"DANH SÁCH CHƯƠNG CẤP 1 CẦN TÌM (toc_idx — title):\n"
            f"{chapter_list_str}\n\n"
            f"ĐOẠN TÀI LIỆU [{b_start}–{b_end - 1}] "
            f"(đoạn {b_no + 1}/{n_batches}, tổng {n_ade} chunks):\n"
            f"{chunk_list_str}\n\n"
            "NHIỆM VỤ: Rà soát toàn bộ đoạn trên. "
            "Với mỗi toc_idx: nếu thấy heading chương xuất hiện → ghi chunk_id; "
            "nếu không thấy → null (bình thường vì đây chỉ là một đoạn). "
            "Trả về đủ tất cả toc_idx."
        )
        try:
            result         = call_ai(client, _PROMPT_LANDMARK_SYS, user)
            found_in_batch = 0
            for m in result.get("mappings", []):
                idx = m.get("toc_idx")
                cid = m.get("chunk_id")
                if idx not in chapter_indices:
                    continue
                clean_id = _sanitize_chunk_id(cid, valid_ids)
                if clean_id and clean_id in id_to_ade_pos:
                    raw_hits[idx].append(id_to_ade_pos[clean_id])
                    found_in_batch += 1
            print(f"    Batch {b_no + 1}/{n_batches} [{b_start}:{b_end}]: {found_in_batch} hit(s)")
        except Exception as e:
            print(f"    Batch {b_no + 1}/{n_batches} failed: {e}")

    # Dedup: take earliest position per chapter
    deduped: dict[int, int] = {}
    for idx, positions in raw_hits.items():
        if positions:
            best = min(positions)
            deduped[idx] = best
            if len(positions) > 1:
                print(f"    Dedup [{idx}] {flat_refs[idx][0]!r}: {len(positions)} hits → ADE[{best}]")

    # LIS filter: remove hallucinated out-of-order placements
    anchor_pairs = sorted(deduped.items())
    valid_pairs  = _lis_anchors(anchor_pairs) if anchor_pairs else []
    removed = len(anchor_pairs) - len(valid_pairs)
    if removed:
        print(f"    LIS filtered {removed} out-of-order landmark(s)")

    landmarks: dict[int, int] = {}
    for idx, ade_pos in valid_pairs:
        flat_refs[idx][1]["heading_chunk_id"] = ade_summary[ade_pos]["id"]
        landmarks[idx] = ade_pos
        print(f"    Landmark [{idx}] {flat_refs[idx][0]!r} → ADE[{ade_pos}]")

    print(f"  Phase 3 [Landmark]: {len(landmarks)}/{len(chapter_indices)} chapters mapped")
    return landmarks


def _phase3_deterministic_fallbacks(
    flat_refs: list[tuple],
    ade_summary: list[dict],
    total_applied: int = 0,
    total_nulled: int = 0,
) -> tuple[int, int]:
    """Three deterministic fallback passes that require no API calls.

    1. Inherit:      null node inherits parent's chunk_id (headings sharing a chunk).
    2. ChildFallback: null chapter inherits earliest-mapped descendant's chunk_id.
    3. PredFallback: absolute safety net — inherit nearest previously-mapped predecessor.
    """
    n_toc         = len(flat_refs)
    id_to_ade_pos = {ch["id"]: j for j, ch in enumerate(ade_summary)}

    # Pass 1 — Parent Inherit
    inherited = 0
    for i, (path, node) in enumerate(flat_refs):
        if node.get("heading_chunk_id") or "/" not in path:
            continue
        parent_path = path.rsplit("/", 1)[0]
        for fpath, fnode in flat_refs:
            if fpath == parent_path and fnode.get("heading_chunk_id"):
                node["heading_chunk_id"] = fnode["heading_chunk_id"]
                total_applied += 1
                total_nulled  -= 1
                inherited     += 1
                print(f"    Inherit [{i}] {path!r} ← parent chunk {fnode['heading_chunk_id']}")
                break
    if inherited:
        print(f"  Phase 3 [Inherit]: {inherited} node(s) resolved via parent chunk")

    # Pass 2 — First Child Fallback
    child_fallback = 0
    for i, (path, node) in enumerate(flat_refs):
        if node.get("heading_chunk_id"):
            continue
        prefix = path + "/"
        desc_positions = [
            (id_to_ade_pos[cid], cid)
            for fpath, fnode in flat_refs
            if fpath.startswith(prefix)
            for cid in [fnode.get("heading_chunk_id")]
            if cid and cid in id_to_ade_pos
        ]
        if desc_positions:
            _, best_cid = min(desc_positions, key=lambda x: x[0])
            node["heading_chunk_id"] = best_cid
            total_applied  += 1
            total_nulled   -= 1
            child_fallback += 1
            print(f"    ChildFallback [{i}] {path!r} ← first child chunk {best_cid}")
    if child_fallback:
        print(f"  Phase 3 [ChildFallback]: {child_fallback} node(s) resolved via first child")

    # Pass 3 — Nearest Predecessor Fallback (absolute safety net)
    predecessor_fixed = 0
    for i, (path, node) in enumerate(flat_refs):
        if node.get("heading_chunk_id"):
            continue
        for j in range(i - 1, -1, -1):
            pred_cid = flat_refs[j][1].get("heading_chunk_id")
            if pred_cid:
                node["heading_chunk_id"] = pred_cid
                total_applied     += 1
                total_nulled      -= 1
                predecessor_fixed += 1
                print(f"    PredFallback [{i}] {path!r} ← [{j}] chunk {pred_cid}")
                break
    if predecessor_fixed:
        print(f"  Phase 3 [PredFallback]: {predecessor_fixed} node(s) resolved via nearest predecessor")

    return total_applied, total_nulled


def _phase3_run_batched(
    client: OpenAI,
    flat_refs: list[tuple],
    ade_summary: list[dict],
    valid_ids: set[str],
) -> tuple[int, int]:
    """Full batched pipeline: Landmark → Mini-Landmark → Bounded → Orphan → Fallbacks."""
    n_toc         = len(flat_refs)
    n_ade         = len(ade_summary)
    id_to_ade_pos = {ch["id"]: j for j, ch in enumerate(ade_summary)}

    print("  Phase 3 [Landmark]: mapping chapters...")
    landmarks     = _phase3_get_landmarks(client, flat_refs, ade_summary, valid_ids)
    total_applied = sum(1 for _, node in flat_refs if node.get("heading_chunk_id"))
    total_nulled  = 0

    # ── Chapter boundary ranges ───────────────────────────────────────────────
    chapter_boundaries = sorted([
        i for i, (path, _) in enumerate(flat_refs) if "/" not in path
    ]) or [0]

    chapter_ranges: list[tuple[int, int]] = []
    for ci, chap_s in enumerate(chapter_boundaries):
        chap_e = chapter_boundaries[ci + 1] if ci + 1 < len(chapter_boundaries) else n_toc
        chapter_ranges.append((chap_s, chap_e))

    total_chapters = len(chapter_ranges)

    # ── Mini-Landmark: retry missed top-level chapters ─────────────────────────
    missed_chapters = [
        (ci, chap_s, chap_e)
        for ci, (chap_s, chap_e) in enumerate(chapter_ranges)
        if chap_s not in landmarks and not flat_refs[chap_s][1].get("heading_chunk_id")
    ]
    if missed_chapters:
        print(f"  Phase 3 [Mini-Landmark]: {len(missed_chapters)} chapters missed → retry...")
        for ci, chap_s, chap_e in missed_chapters:
            m_win_s, m_win_e = _get_bounded_window(chap_s, chap_e, landmarks, n_ade, buffer=30)
            m_window = ade_summary[m_win_s:m_win_e]
            m_str    = "\n".join(f"{ch['i']:4d} | chunk_id={ch['id']} | {ch['t']}" for ch in m_window)
            m_title  = flat_refs[chap_s][0]
            mini_user = (
                f"TÌM VỊ TRÍ CHO CHƯƠNG: [{chap_s}] {m_title}\n\n"
                "CÁC CHUNK ADE TRONG VÙNG DỰ KIẾN (đã thu hẹp quanh vị trí dự kiến của chương):\n"
                f"{m_str}\n\n"
                "NHIỆM VỤ: Tìm chunk ADE chứa heading hoặc nội dung đầu của CHƯƠNG TRÊN "
                "(không phải mục lục). Nếu không có chunk heading riêng, chọn chunk đầu tiên "
                "của phần nội dung mới. Chỉ trả về 1 kết quả cho toc_idx này."
            )
            try:
                mini_result = call_ai(client, _PROMPT_LANDMARK_SYS, mini_user)
                for m in mini_result.get("mappings", []):
                    idx = m.get("toc_idx")
                    cid = m.get("chunk_id")
                    if idx != chap_s:
                        continue
                    clean_id = _sanitize_chunk_id(cid, valid_ids)
                    if clean_id and clean_id in id_to_ade_pos:
                        ade_pos = id_to_ade_pos[clean_id]
                        if m_win_s <= ade_pos < m_win_e:
                            flat_refs[idx][1]["heading_chunk_id"] = clean_id
                            landmarks[idx] = ade_pos
                            total_applied += 1
                            print(f"    Mini-Landmark [{idx}] {m_title!r} → ADE[{ade_pos}]")
            except Exception as e:
                print(f"  Mini-Landmark failed [{chap_s}]: {e}")

    # ── Bounded window pass per chapter ──────────────────────────────────────
    initial_landmarks  = dict(landmarks)
    chapter_windows = {
        ci: _get_bounded_window(chap_s, chap_e, initial_landmarks, n_ade)
        for ci, (chap_s, chap_e) in enumerate(chapter_ranges)
    }
    print(f"  Phase 3 [Bounded]: {n_toc} nodes, {n_ade} chunks, {total_chapters} chapters")

    for ci, (chap_s, chap_e) in enumerate(chapter_ranges):
        chap_label = flat_refs[chap_s][0][:50] if chap_s < n_toc else "?"
        pending    = [i for i in range(chap_s, chap_e) if not flat_refs[i][1].get("heading_chunk_id")]
        if not pending:
            continue

        win_s, win_e = chapter_windows[ci]
        win_total    = win_e - win_s
        ade_window   = ade_summary[win_s:win_e]
        is_large_chapter = win_total > PHASE3_MAX_EXPAND_WINDOW
        print(f"    Ch {ci+1}/{total_chapters} [{chap_label}]: {len(pending)} nodes, ADE[{win_s}:{win_e}]"
              + (f" — large chapter, sub-windows x{PHASE3_MAX_EXPAND_WINDOW}" if is_large_chapter else ""))

        subgroups = [pending[k : k + PHASE3_SUBGROUP_SIZE] for k in range(0, len(pending), PHASE3_SUBGROUP_SIZE)]
        n_sg      = len(subgroups)

        for sg_idx, subgroup in enumerate(subgroups):
            batch_nodes = [i for i in subgroup if not flat_refs[i][1].get("heading_chunk_id")]
            if not batch_nodes:
                continue

            # Với chapter lớn (window > MAX_EXPAND_WINDOW): dùng sub-window cục bộ
            # căn theo vị trí tỉ lệ của subgroup trong chapter để tránh overflow.
            if is_large_chapter:
                ratio_s = sg_idx / max(1, n_sg)
                ratio_e = (sg_idx + 1) / max(1, n_sg)
                sw_s = win_s + int(win_total * ratio_s)
                sw_e = win_s + int(win_total * ratio_e)
                # Mở rộng buffer = PHASE3_CHAPTER_BUFFER về 2 phía, cap tại MAX_EXPAND_WINDOW
                sw_s = max(win_s, sw_s - PHASE3_CHAPTER_BUFFER)
                sw_e = min(win_e, sw_e + PHASE3_CHAPTER_BUFFER)
                if sw_e - sw_s > PHASE3_MAX_EXPAND_WINDOW:
                    # Thay vì lấy trung tâm (sẽ làm mất phần đầu của block nơi TOC thường xuất hiện),
                    # ta giữ nguyên điểm bắt đầu (sw_s) và cắt bớt phần đuôi.
                    sw_e = sw_s + PHASE3_MAX_EXPAND_WINDOW
                cur_window = ade_summary[sw_s:sw_e]
                sg_win_s, sg_win_e = sw_s, sw_e
            else:
                cur_window  = list(ade_window)
                sg_win_s, sg_win_e = win_s, win_e

            batch_refs  = [flat_refs[i] for i in batch_nodes]
            user        = _phase3_build_user(batch_refs, cur_window, batch_nodes[0], sg_idx + 1, n_sg, n_toc, toc_indices=batch_nodes)
            shrink_step = max(10, len(cur_window) // 5)
            while len(user) > PHASE3_MAX_USER_CHARS and len(cur_window) > shrink_step:
                cur_window = cur_window[: max(shrink_step, len(cur_window) - shrink_step)]
                user = _phase3_build_user(batch_refs, cur_window, batch_nodes[0], sg_idx + 1, n_sg, n_toc, toc_indices=batch_nodes)

            try:
                result = call_ai(client, _PROMPT_PHASE3_SYS, user)
                app, nul, mapped_positions = _validate_and_apply_mappings(
                    result.get("mappings", []), sg_win_s, sg_win_e, id_to_ade_pos, valid_ids, flat_refs
                )
                total_applied += app
                total_nulled  += nul

                still_null = [i for i in batch_nodes if not flat_refs[i][1].get("heading_chunk_id")]
                if still_null and mapped_positions:
                    expanded = _phase3_expand_if_edge(mapped_positions, sg_win_s, sg_win_e, n_ade)
                    if expanded:
                        exp_win_s, exp_win_e = expanded
                        exp_window  = ade_summary[exp_win_s:exp_win_e]
                        exp_refs    = [flat_refs[i] for i in still_null]
                        exp_user    = _phase3_build_user(exp_refs, exp_window, still_null[0], sg_idx + 1, n_sg, n_toc, toc_indices=still_null)
                        # Shrink nếu exp_user vượt context window (chương lớn)
                        exp_shrink  = max(10, len(exp_window) // 5)
                        exp_tries   = 0
                        while len(exp_user) > PHASE3_MAX_USER_CHARS and len(exp_window) > exp_shrink and exp_tries < PHASE3_MAX_EXPAND_TRIES:
                            exp_window = exp_window[: max(exp_shrink, len(exp_window) - exp_shrink)]
                            exp_user   = _phase3_build_user(exp_refs, exp_window, still_null[0], sg_idx + 1, n_sg, n_toc, toc_indices=still_null)
                            exp_tries += 1
                        print(f"      Expand [{sg_win_s}:{sg_win_e}] → [{exp_win_s}:{exp_win_e}] ({len(exp_window)} chunks), {len(still_null)} null")
                        try:
                            exp_result = call_ai(client, _PROMPT_PHASE3_SYS, exp_user)
                            ea, en, _  = _validate_and_apply_mappings(
                                exp_result.get("mappings", []), exp_win_s, exp_win_e, id_to_ade_pos, valid_ids, flat_refs
                            )
                            total_applied += ea
                            total_nulled  += en
                        except Exception as e:
                            print(f"      Expand failed: {e}")

                for i in batch_nodes:
                    cid = flat_refs[i][1].get("heading_chunk_id")
                    if cid and cid in id_to_ade_pos and i not in landmarks:
                        landmarks[i] = id_to_ade_pos[cid]

            except Exception as e:
                print(f"    Sub {sg_idx+1} ch {ci+1} failed: {e}")

    # ── Orphan pass ───────────────────────────────────────────────────────────
    orphans = [i for i in range(n_toc) if not flat_refs[i][1].get("heading_chunk_id")]
    if orphans:
        print(f"  Phase 3 [Orphan]: {len(orphans)} nodes still null")
        for k in range(0, len(orphans), PHASE3_SUBGROUP_SIZE):
            sub      = orphans[k : k + PHASE3_SUBGROUP_SIZE]
            o_win_s, o_win_e = _get_bounded_window(sub[0], sub[-1] + 1, landmarks, n_ade, buffer=PHASE3_CHAPTER_BUFFER * 2)
            o_window = ade_summary[o_win_s:o_win_e]
            # Giới hạn cứng cửa sổ mồ côi nếu nó quá to
            if len(o_window) > PHASE3_MAX_EXPAND_WINDOW:
                # Tuân thủ đúng logic: giữ nguyên điểm bắt đầu (nơi TOC có khả năng xuất hiện cao nhất
                # ngay sau node tiền nhiệm) và cắt bớt phần đuôi.
                o_win_e = min(n_ade, o_win_s + PHASE3_MAX_EXPAND_WINDOW)
                o_window = ade_summary[o_win_s:o_win_e]

            o_refs   = [flat_refs[i] for i in sub]
            o_user   = _phase3_build_user(o_refs, o_window, sub[0], 1, 1, n_toc, toc_indices=sub)
            
            # Shrink loop an toàn cho Orphan pass
            o_shrink = max(10, len(o_window) // 5)
            while len(o_user) > PHASE3_MAX_USER_CHARS and len(o_window) > o_shrink:
                o_window = o_window[: max(o_shrink, len(o_window) - o_shrink)]
                o_user   = _phase3_build_user(o_refs, o_window, sub[0], 1, 1, n_toc, toc_indices=sub)
                
            try:
                o_result = call_ai(client, _PROMPT_PHASE3_SYS, o_user)
                oa, on   = _phase3_apply_mappings(o_result.get("mappings", []), flat_refs, valid_ids)
                total_applied += oa
                total_nulled  += on
            except Exception as e:
                print(f"    Orphan batch failed: {e}")

    # ── Deterministic fallbacks ───────────────────────────────────────────────
    total_applied, total_nulled = _phase3_deterministic_fallbacks(
        flat_refs, ade_summary, total_applied, total_nulled
    )

    return total_applied, total_nulled


# ══════════════════════════════════════════════════════════════════════════════
# PHASE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def phase1(client: OpenAI, text: str, filename: str) -> tuple[dict, bool, int | None]:
    scan, toc_start, toc_end = get_scan_for_phase1(text, TOC_SCAN_PAGES)
    found_toc = toc_start is not None
    label     = "(trang đầu + MỤC LỤC ghép đủ)" if found_toc else f"({TOC_SCAN_PAGES} trang đầu)"
    user      = f"source_file = {filename}\n\nNội dung văn bản {label}:\n{scan}"
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
    pages = text.split(PAGE_BREAK)
    if body_start_page is not None:
        skip  = body_start_page + 1
        pages = pages[skip:]
        print(
            f"  Phase 2: có MỤC LỤC → bỏ {skip} trang đầu, "
            f"đọc từ trang {skip + 1} ({len(pages)} trang còn lại)"
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
            clean = clean_text_for_phase2(PAGE_BREAK.join(pages))
            print(f"  Phase 2 (single pass): {n_pages} trang, {len(clean):,} ký tự")
            shallow_chapters = metadata.get("chapters", [])
            if shallow_chapters:
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
            n_chunks    = (n_pages + PHASE2_CHUNK_PAGES - 1) // PHASE2_CHUNK_PAGES
            print(
                f"  Phase 2 (iterative): {n_pages} trang → "
                f"{n_chunks} chunks (≤{PHASE2_CHUNK_PAGES} trang/chunk)"
            )
            accumulated: list = list(metadata.get("chapters", []))
            for i in range(n_chunks):
                start = i * PHASE2_CHUNK_PAGES
                end   = min(start + PHASE2_CHUNK_PAGES, n_pages)
                clean = clean_text_for_phase2(PAGE_BREAK.join(pages[start:end]))
                toc_label = "TOC NỀN (Phase 1)" if i == 0 else f"TOC tích lũy (sau {i} chunk)"
                print(
                    f"    Chunk {i+1}/{n_chunks}: trang {start+1}–{end} / {n_pages},"
                    f" {len(clean):,} ký tự, TOC: {len(accumulated)} chapters"
                )
                user = (
                    _user_header()
                    + f"CÂY TOC ĐÃ TÍCH LŨY ({toc_label} — GIỮ NGUYÊN title, chỉ thêm mục con):\n"
                    + json.dumps({"chapters": accumulated}, ensure_ascii=False, indent=2)
                    + f"\n\nĐOẠN VĂN BẢN MỚI — chunk {i+1}/{n_chunks}"
                    + f" (trang {start+1}–{end}, tổng {n_pages} trang):\n\n{clean}"
                )
                try:
                    result      = call_ai(client, _PROMPT_PHASE2_ITERATIVE, user)
                    accumulated = _merge_chapters(accumulated, result.get("chapters", []))
                except Exception as chunk_err:
                    print(
                        f"    Chunk {i+1}/{n_chunks} failed: {chunk_err} "
                        f"— bỏ qua chunk này, giữ TOC tích lũy hiện tại"
                    )
            chapters = accumulated
            print(f"  Phase 2 iterative done: {len(chapters)} chapters")

        return {**metadata, "chapters": chapters}

    except Exception as e:
        print(f"  Phase 2 failed: {e} — giữ lại kết quả Phase 1")
        return metadata


def phase3(
    client: OpenAI,
    toc: dict,
    ade_chunks: list[dict],
    toc_end_page: int | None = None,
) -> dict:
    chapters = toc.get("chapters", [])
    if not chapters or not ade_chunks:
        return toc

    flat_refs   = _flatten_toc_refs(chapters)
    ade_summary = _build_ade_summary(ade_chunks, toc_end_page=toc_end_page)
    if not flat_refs or not ade_summary:
        return toc

    valid_ids: set[str] = {ch["id"] for ch in ade_chunks if ch.get("id")}
    n_toc = len(flat_refs)
    n_ade = len(ade_summary)
    print(f"  Phase 3: {n_toc} TOC nodes, {n_ade} ADE chunks")

    applied, nulled = _phase3_run_batched(client, flat_refs, ade_summary, valid_ids)
    print(f"  Phase 3: {applied}/{n_toc} matched, {nulled} null")

    still_null = sum(1 for _, nd in flat_refs if not nd.get("heading_chunk_id"))
    if still_null:
        print(f"  Phase 3: {still_null}/{n_toc} nodes still null — running fallbacks")

    # Final deterministic fallbacks (runs for both single-call and batched paths)
    _phase3_deterministic_fallbacks(flat_refs, ade_summary)

    return toc


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════

def process_file(
    md_path: Path,
    client: OpenAI,
    output_dir: Path,
    ade_dir: Path | None = None,
) -> None:
    if not md_path.exists():
        print(f"File not found: {md_path}")
        return

    print(f"Processing: {md_path.name}")
    text = md_path.read_text(encoding="utf-8", errors="ignore")

    raw_toc, found_toc, toc_end = phase1(client, text, md_path.name)
    toc = ensure_schema(raw_toc, md_path.name)
    print(
        f"  Phase 1 result: {len(toc.get('chapters', []))} chapters, "
        f"{count_sections(toc.get('chapters', []))} sections"
    )

    if not toc.get("total_pages"):
        inferred = text.count(PAGE_BREAK) + 1
        toc["total_pages"] = inferred
        print(f"  total_pages: inferred {inferred} from PAGE_BREAK count")

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

    if ade_dir is not None:
        ade_path = _find_ade_chunks_path(md_path, ade_dir)
        if ade_path.exists():
            try:
                ade_chunks = json.loads(ade_path.read_text(encoding="utf-8"))
                toc = phase3(client, toc, ade_chunks, toc_end_page=toc_end)
            except Exception as e:
                print(f"  Phase 3 error: {e} — bỏ qua")
        else:
            print(f"  Phase 3: không tìm thấy {ade_path.name} — bỏ qua")

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


def run(args) -> None:
    load_dotenv(override=False)
    api_key = os.getenv("OPENAI_API_KEY", "").strip().strip("\"'")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")
    client = OpenAI(api_key=api_key)

    global TOC_SCAN_PAGES, MIN_SECTION_DEPTH, MIN_SECTION_DEPTH_SHORT, \
           MIN_SECTION_DEPTH_LONG, PAGE_THRESHOLD_FOR_DEPTH, MODEL, PHASE2_CHUNK_PAGES, \
           PHASE3_BATCH_TOC_SIZE, PHASE3_ADE_WINDOW_SIZE
    TOC_SCAN_PAGES           = args.pages
    MIN_SECTION_DEPTH_SHORT  = args.min_depth_short
    MIN_SECTION_DEPTH_LONG   = args.min_depth_long
    PAGE_THRESHOLD_FOR_DEPTH = args.depth_page_threshold
    MODEL                    = args.model
    PHASE2_CHUNK_PAGES       = args.chunk_pages
    PHASE3_BATCH_TOC_SIZE    = args.p3_batch_toc
    PHASE3_ADE_WINDOW_SIZE   = args.p3_ade_window

    _init_phase2_prompts()

    input_dir  = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    ade_dir    = Path(args.ade_dir) if args.ade_dir else ADE_CHUNKS_DIR
    if not ade_dir.exists():
        print(f"  Phase 3: thư mục ADE không tồn tại ({ade_dir}) — bỏ qua")
        ade_dir = None

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
        process_file(md_path, client, output_dir, ade_dir=ade_dir)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trích xuất cây TOC từ file Markdown OCR (Landing AI format)."
    )
    parser.add_argument("--input-dir",           default=str(INPUT_DIR),
                        help=f"Thư mục chứa file .md (default: {INPUT_DIR})")
    parser.add_argument("--output-dir",          default=str(OUTPUT_DIR),
                        help=f"Thư mục xuất file JSON (default: {OUTPUT_DIR})")
    parser.add_argument("--ade-dir",             default=str(ADE_CHUNKS_DIR),
                        help=f"Thư mục chứa *_ade_chunks.json (default: {ADE_CHUNKS_DIR})")
    parser.add_argument("--files",               nargs="*",
                        help="Danh sách file cụ thể cần xử lý")
    parser.add_argument("--pages",               type=int, default=TOC_SCAN_PAGES,
                        help=f"Số trang đầu quét Phase 1 (default: {TOC_SCAN_PAGES})")
    parser.add_argument("--min-depth-short",     type=int, default=MIN_SECTION_DEPTH_SHORT,
                        help=f"Độ sâu TOC tối thiểu, tài liệu ngắn (default: {MIN_SECTION_DEPTH_SHORT})")
    parser.add_argument("--min-depth-long",      type=int, default=MIN_SECTION_DEPTH_LONG,
                        help=f"Độ sâu TOC tối thiểu, tài liệu dài (default: {MIN_SECTION_DEPTH_LONG})")
    parser.add_argument("--depth-page-threshold",type=int, default=PAGE_THRESHOLD_FOR_DEPTH,
                        help=f"Ngưỡng số trang để chọn min-depth (default: {PAGE_THRESHOLD_FOR_DEPTH})")
    parser.add_argument("--model",               default=MODEL,
                        help=f"Model OpenAI (default: {MODEL})")
    parser.add_argument("--chunk-pages",         type=int, default=PHASE2_CHUNK_PAGES,
                        help=f"Số trang mỗi chunk iterative Phase 2 (default: {PHASE2_CHUNK_PAGES})")
    parser.add_argument("--p3-batch-toc",        type=int, default=PHASE3_BATCH_TOC_SIZE,
                        help=f"Số TOC nodes mỗi batch Phase 3 (default: {PHASE3_BATCH_TOC_SIZE})")
    parser.add_argument("--p3-ade-window",       type=int, default=PHASE3_ADE_WINDOW_SIZE,
                        help=f"Kích thước cửa sổ ADE Phase 3 (default: {PHASE3_ADE_WINDOW_SIZE})")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC SERVICE WRAPPER (used by the FastAPI ingestion pipeline)
# ══════════════════════════════════════════════════════════════════════════════

class TocBuilderService:
    """Async wrapper around the 3-phase TOC pipeline.

    Phase 1 & 2 build the TOC tree from the raw OCR markdown (anchors and
    PAGE_BREAK markers must be intact). Phase 3 maps each TOC heading to an
    ADE chunk via heading_chunk_id.
    """

    def __init__(self, *, markdown_service=None) -> None:
        # markdown_service kept for backward-compatible kwargs; not used.
        self._markdown_service = markdown_service

    async def build_toc(
        self,
        *,
        clean_text: str | None = None,
        raw_markdown: str | None = None,
        source_file: str,
        ade_chunks: list[dict] | None = None,
    ) -> dict:
        markdown = raw_markdown if raw_markdown is not None else clean_text
        if markdown is None:
            raise ValueError("build_toc requires raw_markdown (or clean_text)")
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(
                executor,
                partial(self._build_sync, markdown, source_file, ade_chunks or []),
            )

    async def openai_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(
                executor,
                partial(self._json_completion_sync, system_prompt, user_prompt),
            )

    def _build_sync(
        self,
        raw_markdown: str,
        source_file: str,
        ade_chunks: list[dict],
    ) -> dict:
        client = self._make_client()
        _init_phase2_prompts()

        global MIN_SECTION_DEPTH

        raw_toc, found_toc, toc_end = phase1(client, raw_markdown, source_file)
        toc = ensure_schema(raw_toc, source_file)

        if not toc.get("total_pages"):
            toc["total_pages"] = raw_markdown.count(PAGE_BREAK) + 1

        total_pages = toc.get("total_pages") or 0
        MIN_SECTION_DEPTH = (
            MIN_SECTION_DEPTH_LONG if total_pages >= PAGE_THRESHOLD_FOR_DEPTH
            else MIN_SECTION_DEPTH_SHORT
        )

        if not found_toc or toc_is_shallow(toc):
            try:
                toc = ensure_schema(
                    phase2(client, raw_markdown, toc, source_file, body_start_page=toc_end),
                    source_file,
                )
            except Exception:
                logger.exception("Phase 2 failed — keeping Phase 1 result")

        if ade_chunks:
            try:
                toc = phase3(client, toc, ade_chunks, toc_end_page=toc_end)
            except Exception:
                logger.exception("Phase 3 mapping failed — TOC will have no heading_chunk_id")

        return toc

    def _json_completion_sync(self, system_prompt: str, user_prompt: str) -> dict:
        client = self._make_client()
        return call_ai(client, system_prompt, user_prompt)

    @staticmethod
    def _make_client() -> OpenAI:
        load_dotenv(override=False)
        api_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY missing — required for the TOC pipeline")
        return OpenAI(api_key=api_key)
