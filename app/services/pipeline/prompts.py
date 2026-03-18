from __future__ import annotations

import json

TOC_SCAN_PAGES = 40
MIN_SECTIONS_THRESHOLD = 3

TOC_METADATA_KEYS = [
    "title",
    "publisher",
    "decision_number",
    "specialty",
    "date",
    "isbn_electronic",
    "isbn_print",
    "total_pages",
    "source_file",
]

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
  chapters -> sections -> subsections -> subsubsections -> subsubsubsections
  Mỗi node chỉ có "title" và key mảng con tương ứng. Mảng con rỗng thì để [].

NHẬN DIỆN TIÊU ĐỀ (Tiếng Việt):
  - Cấp 1 (chapters): "Phần X", "Chương X", các mục lớn không có cha.
  - Cấp 2 (sections): "Bước X", "Mục X", "I, II, III", tiêu đề in đậm dưới chapter.
  - Cấp 3+ (subsections…): đánh số thập phân (2.1, 2.1.1…).
  - Phụ lục có số -> lồng dưới chapter tương ứng. Phụ lục không số -> chapter riêng.
  - Loại bỏ: số trang, dòng chân trang, tên tác giả, đoạn văn bản nội dung."""

PHASE1_SYSTEM_PROMPT = f"""\
Bạn là hệ thống trích xuất cấu trúc tài liệu y tế.
Trả về DUY NHẤT một JSON hợp lệ, không markdown, không giải thích.

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

METADATA - trích xuất từ văn bản, không tìm thấy -> null:
{_METADATA_SCHEMA}

MỤC LỤC (key "chapters") - HAI TRƯỜNG HỢP:

TRƯỜNG HỢP 1 - TÌM THẤY PHẦN MỤC LỤC/TABLE OF CONTENTS:
  - CHỈ dùng các dòng/hàng nằm BÊN TRONG phần MỤC LỤC đó.
  - TUYỆT ĐỐI KHÔNG suy luận thêm mục con từ nội dung chương, tiêu đề body, hay bất kỳ phần nào khác của văn bản.
  - TUYỆT ĐỐI KHÔNG thêm bất kỳ mục nào không xuất hiện trong MỤC LỤC.
  - Nếu MỤC LỤC chỉ có 2 cấp -> chỉ trả về 2 cấp, không tự thêm cấp 3.
  - Kết quả nông (ít sections) là ĐÚNG nếu MỤC LỤC gốc nông - hệ thống sẽ tự bổ sung ở bước tiếp theo.

TRƯỜNG HỢP 2 - KHÔNG TÌM THẤY MỤC LỤC:
  - Suy luận từ các tiêu đề lớn trong phần văn bản đã cung cấp.
  - Áp dụng quy tắc nhận diện tiêu đề bên dưới.

{_STRUCTURE_RULES}"""

PHASE2_SYSTEM_PROMPT = f"""\
Bạn là hệ thống xây dựng cây TOC tài liệu y tế từ danh sách tiêu đề.
Trả về DUY NHẤT một JSON hợp lệ, không markdown, không giải thích.

OUTPUT SCHEMA: giống hệt Phase 1 (metadata + chapters đầy đủ).
NHIỆM VỤ: nhận METADATA đã biết + OUTLINE TIÊU ĐỀ trích từ toàn văn bản,
xây dựng cây chapters đầy đủ chiều sâu.

{_STRUCTURE_RULES}

XÁC ĐỊNH CẤP DỰA TRÊN SỐ THẬP PHÂN:
  "2." hoặc "2. Tiêu đề" -> chapter cấp 1
  "2.1." hoặc "2.1 Tiêu đề" -> section cấp 2
  "2.1.1." -> subsection cấp 3
  "2.1.1.1." -> subsubsection cấp 4
  Dòng in hoa không số -> chapter. Markdown ## -> cấp theo số dấu #.
  Giữ nguyên tiêu đề gốc. KHÔNG thêm mục không có trong outline."""


def build_phase1_user_prompt(text: str, source_file: str, pages: int = TOC_SCAN_PAGES) -> str:
    return (
        f"source_file = {source_file}\n\n"
        f"Nội dung văn bản ({pages} trang đầu):\n"
        f"{text}"
    )


def build_phase2_user_prompt(
    *,
    metadata: dict,
    outline: str,
    source_file: str,
) -> str:
    meta_only = {k: metadata.get(k) for k in TOC_METADATA_KEYS if k != "chapters"}
    return (
        f"source_file = {source_file}\n\n"
        f"METADATA:\n{json.dumps(meta_only, ensure_ascii=False, indent=2)}\n\n"
        f"OUTLINE TIÊU ĐỀ:\n{outline}"
    )
